from __future__ import annotations

import argparse
import json
import os
import subprocess
import traceback
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines.raw_text import build_raw_text_package
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.planners.agent_planner import ExternalLLMClient, _extract_json_payload
from experiments.adapters.agent_adapter import AgentAdapter
from experiments.runner.single_run import _execute_actions, _inject_registry_candidates
from experiments.runner.system_registry import build_system_registry
from experiments.world_model.ha_oracle import HAOracle


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _usage(usage: dict) -> dict[str, int]:
    prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": int(usage.get("total_tokens", prompt + completion) or prompt + completion)}


def _extract_prompt(item: dict, *, turn_index: int = 1, records: list[dict] | None = None) -> str:
    record_context = records or []
    return (
        "你是智能家居长期记忆系统的真实写入代理。只根据用户原话生成 JSON，不要执行设备动作。\n"
        "返回 schema: {\"operations\":[...]}; 每一项必须使用键名 op，其值只能为 add_active/add_candidate/revise/merge/split/mark_outcome。\n"
        "所有 add_active/add_candidate/new_record 都必须使用下列严格值：source 只能为 user_explicit 或 user_correction；"
        "scope 只能为 entity；memory_type 只能为 preference；object 必须为字符串；confidence/source_authority 是 0 到 1 的数字；half_life_days 是正整数。\n"
        "add_active/add_candidate 必须含 memory_id、memory_type、scope、subject、predicate、object、natural_text、source、confidence、source_authority、half_life_days、entity_id。\n"
        "更正使用 revise，必须含 old_memory_id 与 new_record，new_record 满足上述 add_active 字段。\n"
        "merge 必须含 source_ids（至少两个字符串）和 merged_record（满足上述 add_active 字段）以及 coverage_proof={\"status\":\"provided\",\"sources\":[source_ids...]};"
        "split 必须含 old_memory_id 与 new_records（两个满足上述字段的对象）。\n"
        "mark_outcome 必须只含 op、memory_id、used_stage（planning）、contribution（helpful 或 misleading）、outcome（success 或 failure）。\n"
        "mark_outcome/revise/merge/split 只能引用当前记忆上下文中已有的 memory_id；如果没有足够上下文，返回 {\"operations\":[]}，不要猜测 ID。\n"
        f"当前第 {turn_index} 轮用户原话：{json.dumps(item['utterances'][turn_index - 1], ensure_ascii=False)}\n"
        f"当前记忆上下文（仅用于引用 ID，不是 evaluator 标签）：{json.dumps(record_context, ensure_ascii=False, indent=2)}"
    )


def _call_extractor(item: dict, replicate: int, *, turn_index: int = 1, records: list[dict] | None = None) -> tuple[dict, dict]:
    client = ExternalLLMClient()
    attempts = []
    for number in (1, 2):
        try:
            response = client.invoke(_extract_prompt(item, turn_index=turn_index, records=records), requested_seed=replicate)
            attempts.append({"attempt": number, "usage": _usage(response.get("usage", {})), "raw_output": response.get("raw_output"), "failure": None})
            payload = _extract_json_payload(str(response.get("raw_output", "")))
            return response, {"payload": payload, "attempts": attempts}
        except Exception as exc:
            attempts.append({"attempt": number, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "raw_output": None, "failure": f"{type(exc).__name__}:{str(exc)[:240]}"})
            if isinstance(exc, ValueError):
                break
    return {}, {"payload": None, "attempts": attempts}


def _apply_operations(service: MemoryService, operations: list[dict], now) -> list[dict]:
    applied = []
    for operation in operations:
        try:
            service.apply_memory_op(operation, now)
            applied.append({"operation": operation, "status": "applied"})
        except Exception as exc:
            applied.append({"operation": operation, "status": "rejected", "reason": f"{type(exc).__name__}:{str(exc)[:200]}"})
    return applied


def _run_mechanism(root: Path, revision: str, item: dict, system_id: str, replicate: int) -> dict:
    path = root / "units" / "mechanism" / system_id / item["trajectory_id"] / f"{replicate}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    world = HAOracle()
    service = MemoryService(path.with_suffix(".sqlite3"), config=build_system_registry()[system_id].__dict__)
    responses = []
    extraction_attempts = []
    operations = []
    applied = []
    turn_records = []
    for turn_index, _utterance in enumerate(item["utterances"], start=1):
        current_records = [record.model_dump(mode="json") for record in service.list_records(include_deleted=True)]
        response, extraction = _call_extractor(item, replicate, turn_index=turn_index, records=current_records)
        responses.append(response)
        extraction_attempts.extend(extraction["attempts"])
        payload = extraction["payload"] or {}
        turn_operations = payload.get("operations", []) if isinstance(payload, dict) else []
        turn_applied = _apply_operations(service, turn_operations, world.current_time + timedelta(minutes=turn_index))
        operations.extend(turn_operations)
        applied.extend(turn_applied)
        turn_records.append({"turn_index": turn_index, "raw_output": response.get("raw_output"), "operations": turn_operations, "applied_operations": turn_applied, "records_before": current_records})
    maintenance = service.maintenance(world.current_time + timedelta(days=1))
    service.search("查询最新智能家居偏好", task_type="control", now=world.current_time + timedelta(days=1))
    target_rows = [row for row in service.activation_log if row.get("mechanism") == item["mechanism"]]
    usage = {key: sum(_usage(response.get("usage", {}))[key] for response in responses) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    row = {"protocol": "v4.2-supplemental-20260816", "experiment_group": "mechanism", "git_revision": revision, "system_id": system_id, "trajectory_id": item["trajectory_id"], "replicate_id": replicate, "input_mode": "raw_user_text", "raw_utterances": item["utterances"], "forbidden_runtime_inputs_present": False, "agent_backend": "external_llm", "agent_provider": next((response.get("provider") for response in responses if response.get("provider")), None), "agent_model": next((response.get("model") for response in responses if response.get("model")), None), "extractor_raw_output": [response.get("raw_output") for response in responses], "turn_traces": turn_records, "transport_attempts": extraction_attempts, "usage": usage, "extracted_operations": operations, "applied_operations": applied, "mechanism_activation": service.activation_log, "target_activation": target_rows, "maintenance": maintenance, "memory_records_after": [record.model_dump(mode="json") for record in service.list_records(include_deleted=True)], "fallback_used": False, "task_success": bool(target_rows and operations and all(item["status"] == "applied" for item in applied))}
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def _run_longitudinal(root: Path, revision: str, item: dict, system_id: str, replicate: int) -> dict:
    path = root / "units" / "longitudinal" / system_id / item["trajectory_id"] / f"{replicate}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    world = HAOracle()
    history = [f"历史对话 {index}: 与本任务无关的设备讨论。" for index in range(max(item["history_lengths"]))]
    history.extend(item["utterances"])
    if system_id in {"B1", "B4"}:
        package = build_raw_text_package(query=item["query"], task_type="control", fixture=[], conversation_history=history, world=world, full_history=system_id == "B4")
        records = []
    else:
        service = MemoryService(path.with_suffix(".sqlite3"), config=build_system_registry()[system_id].__dict__)
        events = [ingest_user_text(service, text=text, now=world.current_time + timedelta(days=index), turn_id=f"u{index}") for index, text in enumerate(item["utterances"], start=1)]
        package = service.search(item["query"], task_type="control", now=world.current_time + timedelta(days=14))
        records = [record.model_dump(mode="json") for record in service.list_records(include_deleted=True)]
    package = _inject_registry_candidates(world, package, item["query"])
    previous = os.environ.get("EXPERIMENT_AGENT_BACKEND")
    os.environ["EXPERIMENT_AGENT_BACKEND"] = "external"
    attempts = []
    try:
        for attempt_number in (1, 2):
            decision = AgentAdapter().plan(package, item["query"], requested_seed=replicate)
            attempts.append({"attempt": attempt_number, "usage": _usage(decision.usage), "raw_output": decision.raw_output, "failure": decision.failure_type})
            if not decision.failure_type or not decision.failure_type.startswith(("external_call_failed:", "external_init_failed:")):
                break
    finally:
        if previous is None:
            os.environ.pop("EXPERIMENT_AGENT_BACKEND", None)
        else:
            os.environ["EXPERIMENT_AGENT_BACKEND"] = previous
    actions = list(decision.actions or ([decision.action] if decision.action else []))
    execution_success, final_state, execution = _execute_actions(world, actions) if actions else (True, {}, [])
    expected = item["expected_action"]
    action_success = (not actions) if expected is None else len(actions) == 1 and actions[0] == expected
    row = {"protocol": "v4.2-supplemental-20260816", "experiment_group": "longitudinal", "git_revision": revision, "system_id": system_id, "trajectory_id": item["trajectory_id"], "replicate_id": replicate, "input_mode": "raw_user_text", "raw_utterances": item["utterances"], "query": item["query"], "history_lengths": item["history_lengths"], "forbidden_runtime_inputs_present": False, "agent_backend": decision.backend, "agent_provider": decision.provider, "agent_model": decision.model, "agent_raw_output": decision.raw_output, "agent_failures": [decision.failure_type] if decision.failure_type else [], "transport_attempts": attempts, "transport_repair": len(attempts) > 1, "usage": _usage(decision.usage), "baseline_context_source": package.retrieval_metadata.get("baseline_context_source"), "retrieval_metadata": package.retrieval_metadata, "memory_records_after": records, "actions": actions, "execution": execution, "execution_success": execution_success, "final_state": final_state, "evaluator": {"expected_action": expected, "action_success": action_success}, "fallback_used": decision.backend != "external_llm", "task_success": bool(action_success and execution_success)}
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def main():
    parser = argparse.ArgumentParser(description="Run preregistered v4.2 supplemental pilots serially.")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "experiments/configs/protocol_v4_2_supplemental.json")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--replicate", type=int, action="append", required=True)
    parser.add_argument("--group", choices=["mechanism", "longitudinal"], required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not set(args.replicate) <= set(config["replicate_ids"]):
        raise SystemExit("replicate is not preregistered")
    args.results_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.results_root / "freeze_manifest.json"
    manifest = {"protocol": config["protocol"], "git_revision": _revision(), "config": config, "preregistered_replicates": config["replicate_ids"], "executed_replicates": sorted(set(args.replicate))}
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if prior["git_revision"] != manifest["git_revision"] or prior["config"] != config:
            raise SystemExit("results root belongs to another revision or protocol")
        manifest["executed_replicates"] = sorted(set(prior["executed_replicates"]) | set(args.replicate))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = []
    systems = config["systems"] if args.group == "mechanism" else config["longitudinal_systems"]
    items = config["mechanism_trajectories"] if args.group == "mechanism" else config["longitudinal_trajectories"]
    for replicate in args.replicate:
        for item in items:
            active_systems = [item["system_id"], "Ours"] if args.group == "mechanism" else systems
            for system_id in active_systems:
                try:
                    rows.append((_run_mechanism if args.group == "mechanism" else _run_longitudinal)(args.results_root, manifest["git_revision"], item, system_id, replicate))
                except Exception as exc:
                    path = args.results_root / "units" / args.group / system_id / item["trajectory_id"] / f"{replicate}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    failure = {
                        "protocol": config["protocol"], "experiment_group": args.group,
                        "git_revision": manifest["git_revision"], "system_id": system_id,
                        "trajectory_id": item["trajectory_id"], "replicate_id": replicate,
                        "agent_backend": "runner_exception", "agent_failures": ["runner_exception"],
                        "runner_exception": f"{type(exc).__name__}:{str(exc)[:240]}",
                        "runner_traceback": traceback.format_exc(limit=5),
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "transport_attempts": [], "forbidden_runtime_inputs_present": False,
                        "fallback_used": False, "task_success": False,
                    }
                    path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    rows.append(failure)
                    print(f"unit_failed:{args.group}:{system_id}:{item['trajectory_id']}:{type(exc).__name__}")
    print(json.dumps({"group": args.group, "unit_count": len(rows), "success_count": sum(bool(row.get("task_success")) for row in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
