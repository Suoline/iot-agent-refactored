from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts._artifact_paths import configured_run_id, reports_root, results_root


DIAGNOSTIC_SCENARIOS = {
    "-Decay": ["C3", "A4", "A6"],
    "-AsymFeedback": ["A5", "E3"],
    "-Governance": ["A4", "A6", "G2"],
    "-CandidateGate": ["B1", "A3", "G4"],
    "-ConflictHandling": ["A2", "F2"],
    "-FeatureAbsorption": ["F1", "F5", "F6"],
    "-Ripple": ["E3", "F3"],
    "-Split": ["F5"],
}

BEHAVIOR_METRICS = {
    "TSR",
    "State TSR",
    "SRR",
    "WDR",
    "CB",
    "PM",
    "UAA",
    "UC",
    "MP",
    "DMR",
    "RRR",
    "action_success",
    "clarification_success",
    "memory_assertion_success",
    "final_state_success",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    root = results_root() / "aggregated_metrics"
    ours_path = root / configured_run_id("oracle") / "Ours" / "oracle" / "metrics.by_scenario.json"
    rows = []
    if ours_path.exists():
        ours = _load(ours_path)
        for system_id, diagnostic_ids in DIAGNOSTIC_SCENARIOS.items():
            path = root / configured_run_id("ablation") / system_id / "oracle" / "metrics.by_scenario.json"
            if not path.exists():
                continue
            other = _load(path)
            changed = []
            for scenario_id in sorted(set(ours) & set(other)):
                metric_deltas = {
                    metric: other[scenario_id][metric] - ours[scenario_id][metric]
                    for metric in set(ours[scenario_id]) & set(other[scenario_id]) & BEHAVIOR_METRICS
                    if isinstance(ours[scenario_id][metric], (int, float))
                    and isinstance(other[scenario_id][metric], (int, float))
                    and other[scenario_id][metric] != ours[scenario_id][metric]
                }
                if metric_deltas:
                    changed.append({"scenario_id": scenario_id, "metric_deltas": metric_deltas})
            rows.append(
                {
                    "system_id": system_id,
                    "diagnostic_scenario_ids": diagnostic_ids,
                    "changed_scenarios": changed,
                    "status": "observed_effect" if changed else "no_observed_effect",
                    "interpretation": (
                        "该机制在当前确定性测试集中产生可观察差异。"
                        if changed
                        else "当前测试集对该机制不敏感；不得声称有边际性能贡献。"
                    ),
                }
            )
    out = reports_root() / "mechanism_sensitivity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
