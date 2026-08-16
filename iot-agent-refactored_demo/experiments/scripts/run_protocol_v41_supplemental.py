from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.adapters.agent_adapter import AgentAdapter
from experiments.baselines.raw_text import build_raw_text_package
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.runner.single_run import _execute_actions, _inject_registry_candidates
from experiments.runner.system_registry import build_system_registry
from experiments.world_model.ha_oracle import HAOracle


RAW_TEXT_SYSTEMS = {"B1", "B4"}
MEMORYLESS_SYSTEMS = {"B0"}


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _usage_totals(usage: dict) -> dict[str, int]:
    prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total = int(usage.get("total_tokens", prompt + completion) or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _same_action(actual: dict | None, expected: dict) -> bool:
    return bool(actual) and all(actual.get(key, {} if key == "args" else None) == value for key, value in expected.items())


def _transport_failure(decision) -> bool:
    return bool(decision.failure_type and decision.failure_type.startswith(("external_call_failed:", "external_init_failed:")))


def _package_for_system(*, system_id: str, trajectory: dict, world: HAOracle, db_path: Path) -> tuple[object, list[dict], list[dict]]:
    utterances = trajectory["utterances"]
    if system_id in MEMORYLESS_SYSTEMS:
        from experiments.memory.schemas import SearchResultPackage
        package = SearchResultPackage(query=trajectory["query"], task_type=trajectory["task_type"])
        return _inject_registry_candidates(world, package, trajectory["query"]), [], []
    if system_id in RAW_TEXT_SYSTEMS:
        package = build_raw_text_package(
            query=trajectory["query"], task_type=trajectory["task_type"], fixture=[],
            conversation_history=utterances, world=world, full_history=system_id == "B4",
        )
        return _inject_registry_candidates(world, package, trajectory["query"]), [], []
    config = build_system_registry()[system_id]
    service = MemoryService(db_path, config=config.__dict__)
    events = []
    for index, text in enumerate(utterances, start=1):
        events.append(ingest_user_text(
            service, text=text, now=world.current_time + timedelta(days=index - 1), turn_id=f"u{index}",
        ))
    package = _inject_registry_candidates(
        world, service.search(trajectory["query"], task_type=trajectory["task_type"], now=world.current_time + timedelta(days=len(utterances))), trajectory["query"],
    )
    return package, events, [record.model_dump(mode="json") for record in service.list_records(include_deleted=True)]


def run_unit(*, root: Path, revision: str, system_id: str, trajectory: dict, replicate_id: int, max_transport_retries: int) -> dict:
    trajectory_id = trajectory["trajectory_id"]
    unit_dir = root / "units" / system_id / trajectory_id
    trace_path = unit_dir / f"{replicate_id}.json"
    prior_attempts = []
    if trace_path.exists():
        existing = json.loads(trace_path.read_text(encoding="utf-8"))
        failures = existing.get("agent_failures", [])
        retryable = any(str(item).startswith(("external_call_failed:", "external_init_failed:")) for item in failures)
        if not retryable:
            return existing
        # Preserve a failed canonical transport attempt rather than overwriting it.
        repair_dir = unit_dir / "repair_attempts"
        repair_dir.mkdir(exist_ok=True)
        repair_path = repair_dir / f"{replicate_id}.attempt{len(list(repair_dir.glob(f'{replicate_id}.attempt*.json'))) + 1}.json"
        repair_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        prior_attempts = list(existing.get("transport_attempts", []))
    unit_dir.mkdir(parents=True, exist_ok=True)
    world = HAOracle()
    db_path = unit_dir / f"{replicate_id}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    package, ingestion_events, records = _package_for_system(
        system_id=system_id, trajectory=trajectory, world=world, db_path=db_path,
    )
    attempts = []
    decision = None
    for attempt in range(1, max_transport_retries + 2):
        decision = AgentAdapter().plan(package, trajectory["query"], requested_seed=replicate_id)
        attempts.append({
            "attempt": attempt, "agent_backend": decision.backend, "failure_type": decision.failure_type,
            "usage": _usage_totals(decision.usage), "raw_output": decision.raw_output,
        })
        if not _transport_failure(decision):
            break
    assert decision is not None
    actions = list(decision.actions or ([decision.action] if decision.action else []))
    execution_success, final_state, execution = _execute_actions(world, actions) if actions else (False, {}, [])
    expected = trajectory["expected_action"]
    action_success = len(actions) == 1 and _same_action(actions[0], expected)
    trace = {
        "protocol": "v4.1-supplemental-ingestion-20260816",
        "git_revision": revision,
        "system_id": system_id,
        "trajectory_id": trajectory_id,
        "replicate_id": replicate_id,
        "world_version": world.definition.get("world_version"),
        "input_mode": "raw_user_text",
        "raw_utterances": trajectory["utterances"],
        "query": trajectory["query"],
        "task_type": trajectory["task_type"],
        "forbidden_runtime_inputs_absent": True,
        "baseline_context_source": package.retrieval_metadata.get("baseline_context_source"),
        "retrieval_metadata": package.retrieval_metadata,
        "matched_memories": [item.model_dump(mode="json") for item in package.matched_memories],
        "candidate_devices": [item.model_dump(mode="json") for item in package.candidate_devices],
        "ingestion_events": ingestion_events,
        "memory_records_after": records,
        "agent_backend": decision.backend,
        "agent_provider": decision.provider,
        "agent_model": decision.model,
        "agent_seed_protocol": decision.seed_protocol,
        "agent_requested_seed": decision.requested_seed,
        "agent_raw_output": decision.raw_output,
        "raw_planner_decision": decision.structured_output.get("raw_plan") if decision.structured_output else None,
        "guarded_planner_decision": decision.structured_output.get("guarded_plan") if decision.structured_output else None,
        "agent_failures": [decision.failure_type] if decision.failure_type else [],
        "transport_attempts": prior_attempts + attempts,
        "transport_repair": bool(prior_attempts),
        "usage": _usage_totals(decision.usage),
        "actions": actions,
        "execution": execution,
        "execution_success": execution_success,
        "final_state": final_state,
        "evaluator": {"action_success": action_success, "expected_action": expected},
        "task_success": bool(action_success and execution_success),
    }
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return trace


def main() -> None:
    parser = argparse.ArgumentParser(description="Run preregistered v4.1 raw-text ingestion supplement.")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "experiments/configs/protocol_v4_1_supplemental_ingestion.json")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--replicate", type=int, action="append", required=True)
    parser.add_argument("--backend", choices=["external"], default="external")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    allowed = set(config["replicate_ids"])
    if not set(args.replicate) <= allowed:
        raise SystemExit("replicate is not preregistered")
    args.results_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.results_root / "freeze_manifest.json"
    requested_replicates = sorted(set(args.replicate))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("git_revision") != _revision() or manifest.get("config") != config:
            raise SystemExit("results root belongs to a different frozen revision or protocol")
        executed_replicates = sorted(set(manifest.get("executed_replicates", [])) | set(requested_replicates))
    else:
        executed_replicates = requested_replicates
    manifest_path.write_text(json.dumps({
        "protocol": config["protocol"], "git_revision": _revision(), "config": config,
        "preregistered_replicates": config["replicate_ids"],
        "executed_replicates": executed_replicates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    previous = os.environ.get("EXPERIMENT_AGENT_BACKEND")
    os.environ["EXPERIMENT_AGENT_BACKEND"] = "external"
    try:
        rows = []
        for replicate in requested_replicates:
            for trajectory in config["trajectories"]:
                for system_id in config["systems"]:
                    try:
                        rows.append(run_unit(
                            root=args.results_root, revision=_revision(), system_id=system_id,
                            trajectory=trajectory, replicate_id=replicate,
                            max_transport_retries=config["requirements"]["max_transport_retries"],
                        ))
                    except Exception as exc:
                        # Persist a non-evaluable engineering failure instead of silently truncating a batch.
                        unit_dir = args.results_root / "units" / system_id / trajectory["trajectory_id"]
                        unit_dir.mkdir(parents=True, exist_ok=True)
                        failure = {
                            "protocol": config["protocol"], "git_revision": _revision(),
                            "system_id": system_id, "trajectory_id": trajectory["trajectory_id"],
                            "replicate_id": replicate, "runner_exception": f"{type(exc).__name__}:{str(exc)[:240]}",
                            "runner_traceback": traceback.format_exc(limit=3), "task_success": False,
                            "agent_backend": "runner_exception", "agent_failures": ["runner_exception"],
                            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                            "forbidden_runtime_inputs_absent": True, "memory_records_after": [],
                        }
                        (unit_dir / f"{replicate}.json").write_text(
                            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                        )
                        rows.append(failure)
    finally:
        if previous is None:
            os.environ.pop("EXPERIMENT_AGENT_BACKEND", None)
        else:
            os.environ["EXPERIMENT_AGENT_BACKEND"] = previous
    print(json.dumps({"unit_count": len(rows), "success_count": sum(row["task_success"] for row in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
