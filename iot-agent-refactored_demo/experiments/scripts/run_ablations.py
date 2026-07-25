from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ablations import ABLATION_IDS
from experiments.runner.batch_run import run_batch


def main():
    root = REPO_ROOT / "experiments" / "scenarios"
    scenario_paths = sorted(root.rglob("*.yaml"))
    oracle_paths = [
        path
        for path in scenario_paths
        if json.loads(path.read_text(encoding="utf-8"))["planner_mode"] == "oracle"
    ]
    for system_id in ABLATION_IDS:
        result = run_batch(
            oracle_paths[:5],
            seed=1001,
            results_root=REPO_ROOT / "experiments" / "results",
            system_id=system_id,
            planner_mode="oracle",
            run_id="ablation_dev",
        )
        print(system_id, json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()

