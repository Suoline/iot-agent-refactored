from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines import BASELINE_IDS
from experiments.runner import load_config, run_batch_multi_seed


def main():
    config = load_config(REPO_ROOT / "experiments" / "configs" / "main_wm_v1.yaml")
    scenario_paths = sorted((REPO_ROOT / "experiments" / "scenarios").rglob("*.yaml"))
    oracle = [
        path
        for path in scenario_paths
        if json.loads(path.read_text(encoding="utf-8"))["planner_mode"] == "oracle"
    ]
    seeds = config["primary_seeds"]
    max_primary = int(os.environ.get("MAX_PRIMARY_SEEDS", "0"))
    if max_primary > 0:
        seeds = seeds[:max_primary]
    for system_id in BASELINE_IDS:
        result = run_batch_multi_seed(
            oracle,
            seeds=seeds,
            results_root=REPO_ROOT / "experiments" / "results",
            system_id=system_id,
            planner_mode="oracle",
            run_id="configured_baseline_dev",
        )
        print(system_id, json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
