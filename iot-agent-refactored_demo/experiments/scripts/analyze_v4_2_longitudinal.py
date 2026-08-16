from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts.analyze_protocol_v4_formal import two_way_cluster_bootstrap


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _wdr(trace: dict) -> float | None:
    expected = (trace.get("evaluator") or {}).get("expected_action")
    actions = trace.get("actions") or []
    if expected is None or not actions:
        return None
    return float(actions[0].get("entity_id") != expected.get("entity_id"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a v4.2 longitudinal root without mixing experiment groups.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = json.loads((args.root / "freeze_manifest.json").read_text(encoding="utf-8"))
    config = freeze["config"]
    traces = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.root / "units" / "longitudinal").glob("*/*/*.json"))]
    by_system: dict[str, list[dict]] = defaultdict(list)
    by_key: dict[tuple[str, str, int], dict] = {}
    for trace in traces:
        by_system[trace["system_id"]].append(trace)
        by_key[(trace["system_id"], trace["trajectory_id"], trace["replicate_id"])] = trace
    systems = {}
    for system_id in config["longitudinal_systems"]:
        rows = by_system[system_id]
        systems[system_id] = {
            "unit_count": len(rows),
            "success_count": sum(bool(row.get("task_success")) for row in rows),
            "tsr": _mean([float(bool(row.get("task_success"))) for row in rows]),
            "wdr": _mean([value for row in rows if (value := _wdr(row)) is not None]),
            "usage": {key: sum(int(row.get("usage", {}).get(key, 0) or 0) for row in rows) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "transport_failure_units": [{"trajectory_id": row["trajectory_id"], "replicate_id": row["replicate_id"], "attempts": row.get("transport_attempts", [])} for row in rows if row.get("agent_failures")],
            "history_context_document_counts": [{"trajectory_id": row["trajectory_id"], "count": len(row.get("retrieval_metadata", {}).get("raw_context_document_ids", []))} for row in rows if row.get("system_id") in {"B1", "B4"}],
        }
    comparisons = []
    for baseline in config["longitudinal_systems"]:
        if baseline == "Ours":
            continue
        paired = []
        for trajectory in config["longitudinal_trajectories"]:
            for replicate in freeze["executed_replicates"]:
                ours = by_key.get(("Ours", trajectory["trajectory_id"], replicate))
                other = by_key.get((baseline, trajectory["trajectory_id"], replicate))
                if ours and other:
                    paired.append({"scenario_id": trajectory["trajectory_id"], "replicate_id": replicate, "delta": float(bool(ours.get("task_success"))) - float(bool(other.get("task_success")))})
        point, low, high = two_way_cluster_bootstrap(paired, samples=2000, rng_seed=20260816)
        comparisons.append({"baseline": baseline, "paired_units": len(paired), "ours_minus_baseline_tsr": point, "bootstrap_95_ci": [low, high]})
    report = {"protocol": freeze["protocol"], "git_revision": freeze["git_revision"], "selected_replicates": freeze["executed_replicates"], "evidence_grade": "supplemental_preliminary" if len(freeze["executed_replicates"]) >= 10 else "engineering_pilot", "systems": systems, "paired_comparisons": comparisons, "scope_limit": "Expanded longitudinal supplemental only; a 30-replicate matrix is required for confirmatory longitudinal claims."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
