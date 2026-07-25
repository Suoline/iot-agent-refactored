from __future__ import annotations

from dataclasses import dataclass
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


class AgentPlanner:
    """Lightweight adapter around the existing agent entrypoint.

    In the current milestone the in-memory experiment world is not yet wired into
    the full smartHome HTTP toolchain, so this planner falls back to the top
    candidate heuristic when `run_ourAgent` cannot be used safely.
    """

    def decide(self, package: SearchResultPackage, task: str) -> AgentPlannerDecision:
        if run_ourAgent is not None:
            try:
                raw = run_ourAgent(task)
                return AgentPlannerDecision(raw_output=raw, should_ask_user=False)
            except Exception:
                pass
        matched_text = " ".join(item.text for item in package.matched_memories)
        if "观影模式" in task:
            return AgentPlannerDecision(
                action={"service": "routine.run", "entity_id": "routine.movie_mode", "args": {}},
                should_ask_user=False,
            )
        if "睡前模式" in task:
            return AgentPlannerDecision(should_ask_user=True)
        if "睡觉了" in task and ("锁" in matched_text or "front_door" in matched_text):
            return AgentPlannerDecision(
                action={"service": "planner.select", "entity_id": "lock.front_door", "args": {}},
                should_ask_user=False,
            )
        if "开卧室灯" in task:
            return AgentPlannerDecision(should_ask_user=True)
        if package.should_ask_user or not package.candidate_devices:
            return AgentPlannerDecision(should_ask_user=True)
        best = max(package.candidate_devices, key=lambda item: item.score)
        return AgentPlannerDecision(
            action={"service": "planner.select", "entity_id": best.entity_id, "args": {}},
            should_ask_user=False,
        )
