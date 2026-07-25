from __future__ import annotations

from experiments.planners.agent_planner import AgentPlanner


class AgentAdapter:
    """Thin wrapper kept separate so experiment code does not directly depend on smartHome internals."""

    def __init__(self):
        self.planner = AgentPlanner()

    def plan(self, package, task: str):
        return self.planner.decide(package, task)

