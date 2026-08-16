from __future__ import annotations

from typing import Any


def validate_v4_agent_scenario(scenario: dict[str, Any]) -> list[str]:
    """Return protocol violations that would leak evaluator truth to Agent."""
    violations: list[str] = []
    for step in scenario.get("steps", []):
        if step.get("type") != "say":
            continue
        oracle_input = step.get("oracle_input") or {}
        if oracle_input.get("memory_ops"):
            violations.append(f"{step.get('step_id', 'unknown')}:gold_memory_ops")
        if oracle_input.get("action_template"):
            violations.append(f"{step.get('step_id', 'unknown')}:action_template")
    return violations


def v4_external_assertion_kinds() -> set[str]:
    return {"action", "clarification", "final_state", "query"}
