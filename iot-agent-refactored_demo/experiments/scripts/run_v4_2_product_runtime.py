from __future__ import annotations

import argparse
import os
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _base_row(item: dict[str, Any], replicate: int, db_path: Path, provider: str | None) -> dict[str, Any]:
    return {
        "protocol": "v4.2-supplemental-20260816",
        "experiment_group": "product_runtime",
        "git_revision": _revision(),
        "system_id": None,
        "trajectory_id": item["trajectory_id"],
        "replicate_id": replicate,
        "input_mode": "raw_natural_language",
        "raw_utterances": item["utterances"],
        "task": item["task"],
        "coverage": item["coverage"],
        "agent_backend": "product_runtime",
        "runtime_class": "smartHome.m_agent.memory.runtime_v1.DemoMemoryRuntime",
        "agent_entrypoint": "smartHome.m_agent.agent.base_home_agent.run_ourAgent",
        "configured_provider": provider,
        "sqlite_path": str(db_path),
        "forbidden_runtime_inputs_present": False,
        "fallback_used": False,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "transport_attempts": [],
    }


def _run_worker(item: dict[str, Any], replicate: int, db_path: Path, worker_output: Path, provider: str | None) -> None:
    """Invoke the unmodified product entrypoint and always persist its outcome."""
    if provider:
        os.environ["SMART_HOME_M_AGENT_PROVIDER"] = provider
    from smartHome.m_agent.memory import runtime_v1
    from smartHome.m_agent.agent.base_home_agent import run_ourAgent

    runtime_v1._RUNTIME = runtime_v1.DemoMemoryRuntime(str(db_path))
    runtime = runtime_v1._RUNTIME
    row = _base_row(item, replicate, db_path, provider)
    outputs = []
    try:
        for utterance in item["utterances"]:
            outputs.append({"input": utterance, "output": run_ourAgent(utterance)})
        final_output = run_ourAgent(item["task"])
        outputs.append({"input": item["task"], "output": final_output})
        task_audits = runtime.drain_completed_task_audits()
        responses = [response for task_audit in task_audits for response in task_audit["llm_responses"]]
        usage = {
            key: sum(response["usage"].get(key, 0) for response in responses)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        row.update(
            task_success=True,
            raw_outputs=outputs,
            memory_records_after=[record.model_dump(mode="json") for record in runtime.service.list_records()],
            task_audits=task_audits,
            tool_calls=[tool_call for task_audit in task_audits for tool_call in task_audit["tool_calls"]],
            transport_attempts=[attempt for task_audit in task_audits for attempt in task_audit["transport_attempts"]],
            usage=usage,
            agent_provider=next((response["provider"] for response in responses if response.get("provider")), provider),
            agent_model=next((response["model"] for response in responses if response.get("model")), None),
        )
    except BaseException as exc:
        task_audits = runtime.drain_completed_task_audits()
        responses = [response for task_audit in task_audits for response in task_audit["llm_responses"]]
        usage = {
            key: sum(response["usage"].get(key, 0) for response in responses)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        row.update(
            task_success=False,
            raw_outputs=outputs,
            task_audits=task_audits,
            tool_calls=[tool_call for task_audit in task_audits for tool_call in task_audit["tool_calls"]],
            transport_attempts=[attempt for task_audit in task_audits for attempt in task_audit["transport_attempts"]],
            usage=usage,
            agent_provider=next((response["provider"] for response in responses if response.get("provider")), provider),
            agent_model=next((response["model"] for response in responses if response.get("model")), None),
            runner_exception=f"{type(exc).__name__}:{str(exc)[:300]}",
            runner_traceback=traceback.format_exc(limit=10),
        )
    worker_output.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_trajectory(item: dict[str, Any], replicate: int, result_root: Path, provider: str | None) -> dict[str, Any]:
    path = result_root / "units" / "product_runtime" / item["trajectory_id"] / f"{replicate}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db_path = path.with_suffix(".sqlite3")
    worker_output = path.with_suffix(".worker.json")
    worker_input = path.with_suffix(".worker-input.json")
    worker_input.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-input",
        str(worker_input),
        "--worker-output",
        str(worker_output),
        "--replicate",
        str(replicate),
        "--db-path",
        str(db_path),
        "--provider",
        provider or "",
    ]
    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=300)
        if worker_output.exists():
            row = json.loads(worker_output.read_text(encoding="utf-8"))
        else:
            row = _base_row(item, replicate, db_path, provider)
            row.update(task_success=False, runner_exception="worker_output_missing")
        row["worker_process"] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode != 0 and row.get("task_success"):
            row.update(task_success=False, runner_exception=f"worker_exit:{completed.returncode}")
    except subprocess.TimeoutExpired as exc:
        row = _base_row(item, replicate, db_path, provider)
        row.update(
            task_success=False,
            runner_exception="worker_timeout:300s",
            worker_process={"returncode": None, "stdout": exc.stdout, "stderr": exc.stderr},
        )
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real smartHome/m_agent product runtime in a v4.2 result root.")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "experiments/configs/protocol_v4_2_supplemental.json")
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--replicate", type=int, required=True)
    parser.add_argument("--worker-input", type=Path)
    parser.add_argument("--worker-output", type=Path)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--provider")
    args = parser.parse_args()
    if args.worker_input:
        if not args.worker_output or not args.db_path:
            raise SystemExit("worker mode requires --worker-output and --db-path")
        _run_worker(json.loads(args.worker_input.read_text(encoding="utf-8")), args.replicate, args.db_path, args.worker_output, args.provider or None)
        return
    if not args.results_root:
        raise SystemExit("parent mode requires --results-root")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.replicate not in config["replicate_ids"]:
        raise SystemExit("replicate is not preregistered")
    if args.provider:
        import configparser

        runtime_config = configparser.ConfigParser()
        runtime_config.read(REPO_ROOT / "smartHome/m_agent/common/llm_config.ini", encoding="utf-8")
        if not runtime_config.has_section(args.provider):
            raise SystemExit("provider is not configured")
    for item in config["product_runtime_trajectories"]:
        row = _run_trajectory(item, args.replicate, args.results_root, args.provider)
        print(json.dumps({"trajectory_id": item["trajectory_id"], "task_success": row["task_success"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
