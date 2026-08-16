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


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize preregistered v4.1 supplemental ingestion results.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = json.loads((args.root / "freeze_manifest.json").read_text(encoding="utf-8"))
    config = freeze["config"]
    traces = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.root / "units").glob("*/*/*.json"))]
    by_system: dict[str, list[dict]] = defaultdict(list)
    by_key: dict[tuple[str, str, int], dict] = {}
    for trace in traces:
        by_system[trace["system_id"]].append(trace)
        by_key[(trace["system_id"], trace["trajectory_id"], trace["replicate_id"])] = trace
    systems = {}
    for system_id in config["systems"]:
        rows = by_system[system_id]
        systems[system_id] = {
            "unit_count": len(rows),
            "success_count": sum(bool(row["task_success"]) for row in rows),
            "task_success_rate": _mean([float(bool(row["task_success"])) for row in rows]),
            "api_calls": len(rows),
            "usage": {key: sum(int(row.get("usage", {}).get(key, 0) or 0) for row in rows)
                      for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "failed_units": [{"trajectory_id": row["trajectory_id"], "replicate_id": row["replicate_id"], "failures": row.get("agent_failures", [])}
                             for row in rows if not row["task_success"]],
        }
    comparisons = []
    for baseline in config["systems"]:
        if baseline == "Ours":
            continue
        paired = []
        for trajectory in config["trajectories"]:
            for replicate in freeze["executed_replicates"]:
                ours = by_key.get(("Ours", trajectory["trajectory_id"], replicate))
                other = by_key.get((baseline, trajectory["trajectory_id"], replicate))
                if ours and other:
                    paired.append({"scenario_id": trajectory["trajectory_id"], "replicate_id": replicate,
                                   "ours": float(bool(ours["task_success"])), "baseline": float(bool(other["task_success"])),
                                   "delta": float(bool(ours["task_success"])) - float(bool(other["task_success"]))})
        point, low, high = two_way_cluster_bootstrap(paired, samples=2000, rng_seed=20260816)
        comparisons.append({"baseline": baseline, "paired_units": len(paired), "ours_minus_baseline": point,
                            "bootstrap_95_ci": [low, high]})
    report = {"protocol": config["protocol"], "git_revision": freeze["git_revision"],
              "selected_replicates": freeze["executed_replicates"],
              "evidence_grade": "supplemental_preliminary" if len(freeze["executed_replicates"]) >= 10 else "engineering_pilot",
              "systems": systems, "paired_comparisons": comparisons,
              "scope_limit": "Rule-based raw-text ingestion supports explicit temperature preference creation/correction only; this is not full smartHome/m_agent runtime validation."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
