from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ablations import ABLATION_IDS
from experiments.runner import load_config, run_batch_multi_seed
from experiments.scripts._artifact_paths import configured_run_id, results_root


def main():
    config = load_config(REPO_ROOT / "experiments" / "configs" / "main_wm_v1.yaml")
    scenario_paths = sorted((REPO_ROOT / "experiments" / "scenarios").rglob("*.yaml"))
    oracle = [
        path
        for path in scenario_paths
        if json.loads(path.read_text(encoding="utf-8"))["planner_mode"] == "oracle"
    ]
    seeds = config["secondary_seeds"]
    max_secondary = int(os.environ.get("MAX_SECONDARY_SEEDS", "0"))
    if max_secondary > 0:
        seeds = seeds[:max_secondary]
    for system_id in ABLATION_IDS:
        result = run_batch_multi_seed(
            oracle,
            seeds=seeds,
            results_root=results_root(),
            system_id=system_id,
            planner_mode="oracle",
            run_id=configured_run_id("ablation"),
        )
        print(system_id, json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
