from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

from experiments.memory.schemas import SearchResultPackage

try:
    from smartHome.m_agent.agent.base_home_agent import run_ourAgent
except Exception:  # pragma: no cover - optional runtime dependency
    run_ourAgent = None


@dataclass
class AgentPlannerDecision:
    action: dict[str, Any] | None = None
    should_ask_user: bool = False
    raw_output: str | None = None
    backend: str = "heuristic_fallback"


class AgentPlanner:
    """Agent adapter with an explicitly labelled deterministic fallback.

    External Agent output is accepted only when it is JSON and therefore auditable.
    Otherwise the generic fallback consumes the same retrieval package as the oracle;
    it never branches on scenario IDs or exact scenario utterances.
    """

    def decide(self, package: SearchResultPackage, task: str) -> AgentPlannerDecision:
        if os.environ.get("EXPERIMENT_AGENT_BACKEND") == "external" and run_ourAgent is not None:
            try:
                raw = run_ourAgent(task)
                payload = json.loads(raw) if isinstance(raw, str) else raw
                return AgentPlannerDecision(
                    action=payload.get("action"),
                    should_ask_user=bool(payload.get("should_ask_user", False)),
                    raw_output=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                    backend="external_llm",
                )
            except Exception as exc:
                raw_error = f"external_agent_error:{type(exc).__name__}"
            else:  # pragma: no cover
                raw_error = None
        else:
            raw_error = None

        usable = [item for item in package.matched_memories if item.in_usable_set]
        routines = [item for item in usable if item.memory_type == "routine"]
        reflections = [item for item in usable if item.memory_type == "reflection"]
        if reflections:
            return AgentPlannerDecision(should_ask_user=True, raw_output=raw_error)
        if routines:
            routine_devices = [
                candidate
                for candidate in package.candidate_devices
                if candidate.entity_id.startswith("routine.")
            ]
            if routine_devices and package.task_type != "safety":
                return AgentPlannerDecision(
                    action={"service": "routine.run", "entity_id": routine_devices[0].entity_id, "args": {}},
                    raw_output=raw_error,
                )
            return AgentPlannerDecision(should_ask_user=True, raw_output=raw_error)
        if package.should_ask_user or not package.candidate_devices:
            return AgentPlannerDecision(should_ask_user=True, raw_output=raw_error)
        best = max(package.candidate_devices, key=lambda item: item.score)
        if package.task_type == "safety":
            grounding = best.matched_memories[0] if best.matched_memories else None
            if grounding is None or grounding.memory_worth <= 0.8:
                return AgentPlannerDecision(should_ask_user=True, raw_output=raw_error)
        return AgentPlannerDecision(
            action={"service": "planner.select", "entity_id": best.entity_id, "args": {}},
            raw_output=raw_error,
        )
