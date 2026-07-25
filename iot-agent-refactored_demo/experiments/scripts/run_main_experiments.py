from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.runner.batch_run import run_batch


def main():
    root = REPO_ROOT / "experiments" / "scenarios"
    scenario_paths = sorted(root.rglob("*.yaml"))
    oracle = []
    agent = []
    for path in scenario_paths:
        planner_mode = json.loads(path.read_text(encoding="utf-8"))["planner_mode"]
        if planner_mode == "agent":
            agent.append(path)
        else:
            oracle.append(path)

    oracle_result = run_batch(
        oracle,
        seed=1001,
        results_root=REPO_ROOT / "experiments" / "results",
        system_id="Ours",
        planner_mode="oracle",
        run_id="dev_oracle",
    )
    agent_result = run_batch(
        agent,
        seed=1001,
        results_root=REPO_ROOT / "experiments" / "results",
        system_id="Ours",
        planner_mode="agent",
        run_id="dev_agent",
    )
    print(
        json.dumps(
            {"oracle": oracle_result["metrics"], "agent": agent_result["metrics"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
