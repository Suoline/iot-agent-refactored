from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.runner.scenario_loader import iter_scenario_paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", choices=["legacy", "v4"], default="legacy")
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args()
    scenarios_root = REPO_ROOT / "experiments" / "scenarios"
    if args.output_root is None:
        annotation_root = REPO_ROOT / "experiments" / "annotations"
        if args.protocol == "v4":
            annotation_root = annotation_root / "protocol_v4"
    else:
        annotation_root = args.output_root
    gt_root = annotation_root / "scenario_ground_truth"
    ia_root = annotation_root / "inter_annotator"
    gt_root.mkdir(parents=True, exist_ok=True)
    ia_root.mkdir(parents=True, exist_ok=True)

    for scenario_path in iter_scenario_paths(scenarios_root, protocol=args.protocol):
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        expected_actions = []
        expected_final_state = None
        expected_clarify = False
        expected_no_action = False
        expected_memory = []
        expected_absent_memory = []
        for step in scenario["steps"]:
            if step["type"] == "expect_action":
                expected_actions.append(step["assert"])
            elif step["type"] == "expect_final_state":
                expected_final_state = step["assert"]
            elif step["type"] == "expect_clarify":
                expected_clarify = True
            elif step["type"] == "expect_no_action":
                expected_no_action = True
            elif step["type"] == "expect_memory":
                expected_memory.append(
                    {
                        "selector": step.get("selector", {}),
                        "assert": step.get("assert", {}),
                    }
                )
            elif step["type"] == "expect_absent_memory":
                expected_absent_memory.append(
                    {
                        "selector": step.get("selector", {}),
                    }
                )
        payload = {
            "scenario_id": scenario["scenario_id"],
            "title": scenario.get("title"),
            "category": scenario.get("category"),
            "rq_tags": scenario.get("rq_tags", []),
            "planner_mode": scenario["planner_mode"],
            "task_type": scenario.get("task_type"),
            "safety_relevant": scenario.get("safety_relevant", False),
            "label_source": "deterministic_executable_specification",
            **({"evaluation_protocol": "v4"} if args.protocol == "v4" else {}),
            "independent_human_annotation_status": "pending",
            "expected_actions": expected_actions,
            "expected_action": expected_actions[-1] if expected_actions else None,
            "expected_final_state": expected_final_state,
            "final_state_applicable": expected_final_state is not None,
            "expected_clarify": expected_clarify,
            "expected_no_action": expected_no_action,
            "expected_memory": expected_memory,
            "expected_absent_memory": expected_absent_memory,
            "preferred_action": expected_actions[-1] if expected_actions else None,
        }
        out_path = gt_root / f"{scenario['scenario_id']}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        existing = {}
        ia_path = ia_root / f"{scenario['scenario_id']}.json"
        if ia_path.exists():
            existing = json.loads(ia_path.read_text(encoding="utf-8"))
        inter_annotator = {
            "scenario_id": scenario["scenario_id"],
            "status": "pending_human_annotation",
            "instructions": "两位独立标注者分别填写完整 expect_* 标签；不得从系统输出反推标签。",
            "annotator_a": existing.get("annotator_a"),
            "annotator_b": existing.get("annotator_b"),
            "agreement": existing.get("agreement"),
            "adjudication": existing.get("adjudication") or {
                "status": "not_required_yet",
                "adjudicator_id": None,
                "final_label": None,
                "rationale": None,
            },
            "missing_value_policy": "annotator_a/annotator_b 为 null 时不进入 kappa 分母；不得以系统输出或自动标签补全。",
        }
        ia_path.write_text(json.dumps(inter_annotator, ensure_ascii=False, indent=2), encoding="utf-8")
        print(out_path)


if __name__ == "__main__":
    main()
