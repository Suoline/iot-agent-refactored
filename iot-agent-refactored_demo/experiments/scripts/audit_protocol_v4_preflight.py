from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts.analyze_protocol_v4_formal import analyze


def _load(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def audit(*, root: Path, run_id: str, replicate_id: int, samples: int = 1000) -> dict:
    matrix = _load(REPO_ROOT / "experiments/configs/protocol_v4_formal_agent_matrix.json")
    expected = [unit for unit in matrix.get("units", []) if unit.get("replicate_id") == replicate_id]
    expected_keys = {(unit["system_id"], unit["scenario_id"], unit["seed"]) for unit in expected}
    trace_root = root / "raw_traces" / run_id
    trace_paths = sorted(path for path in trace_root.glob("**/*.json") if not path.name.endswith(".maintenance.json"))
    observed = {}
    issues = []
    revisions = Counter()
    fallback_count = transport_count = usage_missing_count = metric_anomaly_count = 0
    baseline_fidelity = {"B1": True, "B4": True}
    for path in trace_paths:
        trace = _load(path)
        key = (trace.get("system_id"), trace.get("scenario_id"), trace.get("seed"))
        observed[key] = trace
        transport = any(str(item).startswith(("external_call_failed:", "external_init_failed:")) for item in trace.get("agent_failures", []))
        transport_count += int(transport)
        fallback = trace.get("agent_backend") != "external_llm" or any(event.get("kind") == "fallback" for event in trace.get("usage_events", []))
        fallback_count += int(fallback)
        usage_missing_count += int(not trace.get("agent_usage_metadata"))
        if trace.get("evaluation_protocol") != "v4" or trace.get("external_task_success") is None:
            metric_anomaly_count += 1
        if trace.get("scenario_id") == "Q1_v4_behavioral":
            query_results = [item for item in trace.get("assertion_results", []) if item.get("kind") == "query"]
            if not query_results:
                metric_anomaly_count += 1
        if trace.get("system_id") in baseline_fidelity:
            expected_source = "raw_text_rag" if trace["system_id"] == "B1" else "full_raw_history"
            sources = [step.get("retrieval_metadata", {}).get("baseline_context_source") for step in trace.get("steps", [])]
            baseline_fidelity[trace["system_id"]] = baseline_fidelity[trace["system_id"]] and expected_source in sources and not trace.get("memory_records_after")
        manifest = root / "reports" / run_id / trace.get("system_id", "") / "agent" / trace.get("scenario_id", "") / f"{trace.get('seed')}.manifest.json"
        if manifest.exists():
            revisions[_load(manifest).get("git_revision")] += 1
        else:
            issues.append(f"manifest_missing:{key}")
    missing = expected_keys - set(observed)
    unexpected = set(observed) - expected_keys
    if missing:
        issues.append(f"missing_units:{len(missing)}")
    if unexpected:
        issues.append(f"unexpected_units:{len(unexpected)}")
    if transport_count:
        issues.append(f"transport_failures:{transport_count}")
    if fallback_count:
        issues.append(f"fallback_units:{fallback_count}")
    if usage_missing_count:
        issues.append(f"usage_missing:{usage_missing_count}")
    if metric_anomaly_count:
        issues.append(f"metric_anomalies:{metric_anomaly_count}")
    if not all(baseline_fidelity.values()):
        issues.append("baseline_fidelity_failed")
    if len([revision for revision in revisions if revision not in {None, "unknown"}]) != 1:
        issues.append("revision_not_unique")
    statistics = analyze(root, samples=samples)
    comparison_systems = {row["system_id"] for row in statistics.get("comparisons", [])}
    if comparison_systems != {"B0", "B1", "B2", "B3", "B4", "B5"}:
        issues.append("paired_comparisons_incomplete")
    status = "engineering_ready_for_formal_run" if not issues and len(observed) == 70 else "preflight_not_ready"
    return {
        "protocol": "v4",
        "run_id": run_id,
        "replicate_id": replicate_id,
        "status": status,
        "expected_unit_count": len(expected),
        "observed_unit_count": len(observed),
        "missing_unit_count": len(missing),
        "unexpected_unit_count": len(unexpected),
        "transport_failure_count": transport_count,
        "fallback_count": fallback_count,
        "usage_missing_count": usage_missing_count,
        "metric_anomaly_count": metric_anomaly_count,
        "baseline_fidelity": baseline_fidelity,
        "git_revisions": dict(revisions),
        "statistics_status": statistics.get("status"),
        "statistics_note": "One-replicate preflight remains descriptive_only by design.",
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the 70-unit protocol-v4 single-replicate preflight.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--replicate-id", type=int, default=1001)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    args = parser.parse_args()
    report = audit(root=args.root, run_id=args.run_id, replicate_id=args.replicate_id, samples=args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    if report["status"] != "engineering_ready_for_formal_run":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
