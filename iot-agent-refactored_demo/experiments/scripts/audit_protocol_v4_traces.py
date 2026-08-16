from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.metrics.core import task_metrics


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_trace(path: Path) -> dict:
    trace = _load(path)
    issues: list[str] = []
    if trace.get("evaluation_protocol") != "v4":
        issues.append("not_v4")
    if trace.get("agent_backend") != "external_llm":
        issues.append("not_external_llm")
    transport_failures = [
        item for item in trace.get("agent_failures", [])
        if str(item).startswith(("external_call_failed:", "external_init_failed:"))
    ]
    model_behavior_failures = [
        item for item in trace.get("agent_failures", [])
        if item not in transport_failures
    ]
    if transport_failures:
        issues.append("transport_failure_present")
    if not trace.get("agent_raw_outputs"):
        issues.append("raw_model_output_missing")
    structured_decision_required = not model_behavior_failures
    if structured_decision_required and not trace.get("raw_planner_decisions"):
        issues.append("raw_planner_decision_missing")
    if len(trace.get("raw_planner_decisions", [])) != len(trace.get("guarded_planner_decisions", [])):
        issues.append("guarded_decision_count_mismatch")
    if trace.get("external_task_success") is None:
        issues.append("external_tsr_missing")
    if not trace.get("agent_usage_metadata"):
        issues.append("real_usage_missing")
    if trace.get("agent_seed_protocol") not in {"provider_seed", "replicate_id", "no_agent_call_required"}:
        issues.append("seed_protocol_missing")
    forbidden = ("evaluator_", "memory_ops", "action_template")
    raw_text = "\n".join(trace.get("agent_raw_outputs", []))
    if any(token in raw_text for token in forbidden):
        issues.append("evaluator_or_gold_bridge_leaked_to_output")
    metrics = task_metrics(trace)
    return {
        "trace": str(path),
        "system_id": trace.get("system_id"),
        "scenario_id": trace.get("scenario_id"),
        "seed": trace.get("seed"),
        "external_task_success": trace.get("external_task_success"),
        "transport_failures": transport_failures,
        "model_behavior_failures": model_behavior_failures,
        "metrics": metrics,
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit v4 external-LLM task traces without calling the model.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [path for path in sorted(args.root.glob("raw_traces/**/*.json")) if not path.name.endswith(".maintenance.json")]
    rows = [audit_trace(path) for path in paths]
    report = {
        "protocol": "v4",
        "trace_count": len(rows),
        "passed_count": sum(row["status"] == "pass" for row in rows),
        "failed_count": sum(row["status"] != "pass" for row in rows),
        "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "fail",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    if report["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
