from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_IDS = ["Ours", "B0", "B1", "B2", "B3", "B4", "B5"]
PILOT_SCENARIO_IDS = ["H2_v4_behavioral", "C2_v4_behavioral", "B6_v4_behavioral"]
LONGITUDINAL_SCENARIO_IDS = ["L1_v4_longitudinal"]


def build_matrix(seeds: list[int], scenario_ids: list[str], *, group_id: str) -> dict:
    units = []
    for system_id in SYSTEM_IDS:
        for scenario_id in scenario_ids:
            for seed in seeds:
                units.append({
                    "group_id": group_id,
                    "planner_mode": "agent",
                    "system_id": system_id,
                    "scenario_id": scenario_id,
                    "seed": seed,
                    "source_planner_mode": "agent",
                    "scenario_path": f"experiments/scenarios/protocol_v4/{scenario_id.split('_v4_')[0]}.yaml",
                })
    return {
        "matrix_version": "protocol-v4-pilot-20260810",
        "evaluation_protocol": "v4",
        "planner_mode": "agent",
        "world_version": "wm-v1",
        "system_policy_version": "sp-v4",
        "scenario_count": len(scenario_ids),
        "scenario_ids": scenario_ids,
        "system_ids": SYSTEM_IDS,
        "seed_protocol": seeds,
        "unit_count": len(units),
        "requirements": {
            "no_gold_memory_ops": True,
            "no_action_template": True,
            "real_usage_required_for_llm_units": True,
            "max_api_calls_before_review": 200,
        },
        "units": units,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="1001,1002")
    parser.add_argument("--workload", choices=["pilot", "longitudinal"], default="pilot")
    parser.add_argument("--output", default=str(REPO_ROOT / "experiments/configs/protocol_v4_pilot_matrix.json"))
    args = parser.parse_args()
    scenario_ids = PILOT_SCENARIO_IDS if args.workload == "pilot" else LONGITUDINAL_SCENARIO_IDS
    group_id = "protocol_v4_agent_pilot" if args.workload == "pilot" else "protocol_v4_longitudinal"
    payload = build_matrix([int(item) for item in args.seeds.split(",")], scenario_ids, group_id=group_id)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
