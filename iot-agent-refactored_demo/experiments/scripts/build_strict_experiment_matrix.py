from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ablations import ABLATION_IDS
from experiments.baselines import BASELINE_IDS
from experiments.runner.scenario_loader import load_config, load_scenario
from experiments.runner.system_registry import build_system_registry


STRICT_MAIN_SYSTEM_IDS = BASELINE_IDS
STRICT_ABLATION_SYSTEM_IDS = ABLATION_IDS


def _scenario_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((REPO_ROOT / "experiments" / "scenarios").rglob("*.yaml")):
        scenario = load_scenario(path)
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "title": scenario.get("title", scenario["scenario_id"]),
                "category": scenario.get("category"),
                "rq_tags": scenario.get("rq_tags", []),
                "task_type": scenario.get("task_type", "control"),
                "source_planner_mode": scenario.get("planner_mode"),
                "safety_relevant": bool(scenario.get("safety_relevant", False)),
                "scenario_path": str(path.relative_to(REPO_ROOT)),
            }
        )
    return rows


def _build_units(
    *,
    group_id: str,
    planner_mode: str,
    system_ids: list[str],
    seeds: list[int],
    scenario_rows: list[dict],
) -> list[dict]:
    units: list[dict] = []
    for system_id in system_ids:
        for scenario in scenario_rows:
            for seed in seeds:
                units.append(
                    {
                        "group_id": group_id,
                        "planner_mode": planner_mode,
                        "system_id": system_id,
                        "scenario_id": scenario["scenario_id"],
                        "seed": seed,
                        "category": scenario["category"],
                        "rq_tags": scenario["rq_tags"],
                        "task_type": scenario["task_type"],
                        "source_planner_mode": scenario["source_planner_mode"],
                        "scenario_path": scenario["scenario_path"],
                    }
                )
    return units


def build_matrix() -> dict:
    config = load_config(REPO_ROOT / "experiments" / "configs" / "main_wm_v1.yaml")
    scenario_rows = _scenario_rows()
    registry = build_system_registry()
    main_seeds = list(config["primary_seeds"])
    ablation_seeds = list(config["secondary_seeds"])
    main_units = _build_units(
        group_id="strict_main_agent",
        planner_mode="agent",
        system_ids=STRICT_MAIN_SYSTEM_IDS,
        seeds=main_seeds,
        scenario_rows=scenario_rows,
    )
    ablation_units = _build_units(
        group_id="strict_oracle_ablations",
        planner_mode="oracle",
        system_ids=STRICT_ABLATION_SYSTEM_IDS,
        seeds=ablation_seeds,
        scenario_rows=scenario_rows,
    )
    pilot_sequence = [
        {"scenario_id": "A1", "intent": "single_device_action"},
        {"scenario_id": "D3", "intent": "memory_update_then_action"},
        {"scenario_id": "C1", "intent": "automation_execute_then_expire"},
        {"scenario_id": "H2", "intent": "query_then_control_clarification"},
        {"scenario_id": "G1", "intent": "refusal_or_clarification"},
        {"scenario_id": "E1", "intent": "routine_execution"},
        {"scenario_id": "E2", "intent": "safety_multi_action_clarification"},
        {"scenario_id": "E3", "intent": "failure_reflection_clarification"},
        {"scenario_id": "B6", "intent": "high_memory_worth_safety_action"},
    ]
    return {
        "matrix_version": "strict-v1",
        "generated_from_config": "experiments/configs/main_wm_v1.yaml",
        "world_version": config["world_version"],
        "scenario_version": config["scenario_version"],
        "system_policy_version": config["system_policy_version"],
        "retrieval_backend": config["retrieval"]["backend"],
        "vector_database_required": config["retrieval"]["vector_database_required"],
        "llm_requirements": {
            "same_provider_model_temperature_for_main_agent": True,
            "source": config["llm"]["source"],
            "temperature": config["llm"]["temperature"],
            "max_retries": config["llm"]["max_retries"],
        },
        "scenario_count": len(scenario_rows),
        "scenario_ids": [row["scenario_id"] for row in scenario_rows],
        "scenarios": scenario_rows,
        "groups": [
            {
                "group_id": "strict_main_agent",
                "description": "Ours+B0-B5 use the same real external LLM Agent over all 36 scenarios with N=30.",
                "planner_mode": "agent",
                "system_ids": STRICT_MAIN_SYSTEM_IDS,
                "seed_protocol": main_seeds,
                "target_n": len(main_seeds),
                "scenario_count": len(scenario_rows),
                "unit_count": len(main_units),
                "requirements": {
                    "expected_agent_backend": "external_llm",
                    "require_no_heuristic_fallback": True,
                    "require_same_model_provider_temperature": True,
                },
            },
            {
                "group_id": "strict_oracle_ablations",
                "description": "Eight ablations use the Oracle planner over all 36 scenarios with N=20.",
                "planner_mode": "oracle",
                "system_ids": STRICT_ABLATION_SYSTEM_IDS,
                "seed_protocol": ablation_seeds,
                "target_n": len(ablation_seeds),
                "scenario_count": len(scenario_rows),
                "unit_count": len(ablation_units),
                "requirements": {
                    "expected_agent_backend": None,
                    "require_no_heuristic_fallback": False,
                    "require_same_model_provider_temperature": False,
                },
            },
        ],
        "system_contracts": {
            system_id: registry[system_id].__dict__
            for system_id in [*STRICT_MAIN_SYSTEM_IDS, *STRICT_ABLATION_SYSTEM_IDS]
        },
        "pilot_recommendation": {
            "mode": "strict_serial_one_unit_at_a_time",
            "paired_systems": STRICT_MAIN_SYSTEM_IDS,
            "representative_scenarios": pilot_sequence,
        },
        "summary": {
            "main_agent_system_count": len(STRICT_MAIN_SYSTEM_IDS),
            "main_agent_seed_count": len(main_seeds),
            "main_agent_total_units": len(main_units),
            "oracle_ablation_system_count": len(STRICT_ABLATION_SYSTEM_IDS),
            "oracle_ablation_seed_count": len(ablation_seeds),
            "oracle_ablation_total_units": len(ablation_units),
            "overall_total_units": len(main_units) + len(ablation_units),
        },
        "units": [*main_units, *ablation_units],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "experiments" / "configs" / "strict_experiment_matrix.json"),
    )
    args = parser.parse_args()
    payload = build_matrix()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
