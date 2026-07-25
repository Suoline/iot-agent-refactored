from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main():
    scenarios_root = REPO_ROOT / "experiments" / "scenarios"
    gt_root = REPO_ROOT / "experiments" / "annotations" / "scenario_ground_truth"
    ia_root = REPO_ROOT / "experiments" / "annotations" / "inter_annotator"
    gt_root.mkdir(parents=True, exist_ok=True)
    ia_root.mkdir(parents=True, exist_ok=True)

    for scenario_path in sorted(scenarios_root.rglob("*.yaml")):
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
            "adjudication": existing.get("adjudication"),
        }
        ia_path.write_text(json.dumps(inter_annotator, ensure_ascii=False, indent=2), encoding="utf-8")
        print(out_path)


if __name__ == "__main__":
    main()
