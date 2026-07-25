from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.runner import load_config, run_batch_multi_seed


def main():
    config = load_config(REPO_ROOT / "experiments" / "configs" / "main_wm_v1.yaml")
    scenario_paths = sorted((REPO_ROOT / "experiments" / "scenarios").rglob("*.yaml"))
    oracle = []
    agent = []
    for path in scenario_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["planner_mode"] == "agent":
            agent.append(path)
        else:
            oracle.append(path)
    primary = config["primary_seeds"]
    secondary = config["secondary_seeds"]
    max_primary = int(os.environ.get("MAX_PRIMARY_SEEDS", "0"))
    max_secondary = int(os.environ.get("MAX_SECONDARY_SEEDS", "0"))
    if max_primary > 0:
        primary = primary[:max_primary]
    if max_secondary > 0:
        secondary = secondary[:max_secondary]
    oracle_result = run_batch_multi_seed(
        oracle,
        seeds=primary,
        results_root=REPO_ROOT / "experiments" / "results",
        system_id="Ours",
        planner_mode="oracle",
        run_id="configured_oracle_dev",
    )
    agent_result = run_batch_multi_seed(
        agent,
        seeds=secondary,
        results_root=REPO_ROOT / "experiments" / "results",
        system_id="Ours",
        planner_mode="agent",
        run_id="configured_agent_dev",
    )
    print(json.dumps({"oracle": oracle_result["metrics"], "agent": agent_result["metrics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
