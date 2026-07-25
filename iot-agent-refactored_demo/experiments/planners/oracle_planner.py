from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from experiments.memory.schemas import SearchResultPackage


@dataclass
class PlannerDecision:
    action: dict[str, Any] | None = None
    should_ask_user: bool = False
    reason: str | None = None


class OraclePlanner:
    """Deterministic planner; it never reads scenario ground truth."""

    def __init__(self, ambiguity_margin: float = 0.10):
        self.ambiguity_margin = ambiguity_margin

    def decide(self, package: SearchResultPackage) -> PlannerDecision:
        if package.task_type == "query":
            memories = [item for item in package.matched_memories if item.in_usable_set]
            if not memories:
                memories = list(package.matched_memories)
            if not memories:
                return PlannerDecision(should_ask_user=True, reason="无可回答记忆")
            memories.sort(key=lambda item: (-item.score, item.memory_id))
            return PlannerDecision(
                action={
                    "service": "memory.answer",
                    "entity_id": memories[0].memory_id,
                    "args": {},
                }
            )
        usable_memories = [item for item in package.matched_memories if item.in_usable_set]
        if package.task_type == "automation" and not usable_memories:
            return PlannerDecision(action=None, should_ask_user=False, reason="自动化规则当前不可执行")
        if package.task_type == "control" and not usable_memories and not package.candidate_devices:
            return PlannerDecision(should_ask_user=True, reason="控制意图置信度不足")
        if package.should_ask_user:
            return PlannerDecision(should_ask_user=True, reason=package.ask_reason)
        candidates = [
            item for item in package.candidate_devices
            if item.confidence >= package.threshold_used
        ]
        if not candidates:
            memories = [item for item in package.matched_memories if item.in_usable_set]
            if len(memories) != 1:
                return PlannerDecision(should_ask_user=True, reason="无唯一可执行候选")
            candidate = memories[0]
            return PlannerDecision(
                action={
                    "service": "memory.answer",
                    "entity_id": candidate.memory_id,
                    "args": {},
                }
            )
        candidates.sort(key=lambda item: (-item.score, item.entity_id))
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < self.ambiguity_margin:
            return PlannerDecision(should_ask_user=True, reason="top1-top2 差距不足")
        return PlannerDecision(
            action={
                "service": "planner.select",
                "entity_id": candidates[0].entity_id,
                "args": {},
            }
        )
