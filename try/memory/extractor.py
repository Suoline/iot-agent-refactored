from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .llm_support import StructuredOutputInvoker
from .schemas import EvidenceRef, ExtractedMemoryCandidate, MemorySource


ROOM_PATTERN = r"(客厅|卧室|书房|厨房)"


class HeuristicMemoryExtractor:
    alias_pattern = re.compile(r"以后我把(?P<subject>.+?)叫做(?P<alias>[^，。,；;]+)")
    routine_pattern = re.compile(r"以后我说(?P<trigger>.+?)[，,]就帮我(?P<action>.+)")
    preference_condition_pattern = re.compile(r"(?P<condition>睡觉时|看书时|接电话时)(?:，|,)?(?P<content>.+)")
    color_temp_pattern = re.compile(r"(?:一般都是|喜欢|常用).{0,5}(?P<temp>\d{3,4})")
    constraint_pattern = re.compile(r"除非(?P<condition>.+?)否则不能(?P<action>.+)")
    location_pattern = re.compile(r"(?P<subject>.+?)在(?P<room>" + ROOM_PATTERN + r")")
    correction_pattern = re.compile(r"(不是这个灯|不对|搞错了)")

    def extract_from_turn(
        self,
        user_text: str,
        *,
        source: MemorySource,
        turn_id: str,
    ) -> list[ExtractedMemoryCandidate]:
        text = user_text.strip()
        evidence = [EvidenceRef(ref_type="turn", ref_id=turn_id)]
        candidates: list[ExtractedMemoryCandidate] = []

        alias_match = self.alias_pattern.search(text)
        if alias_match:
            subject = alias_match.group("subject").strip()
            alias = alias_match.group("alias").strip()
            room = self._extract_room(subject)
            candidates.append(
                ExtractedMemoryCandidate(
                    memory_type="alias",
                    scope_hint="device",
                    subject_text=subject,
                    predicate="alias_of",
                    object_text=alias,
                    room_text=room,
                    alias_text=alias,
                    raw_mentions=[subject, alias],
                    source=source,
                    operation_hint="add_active" if source != "llm_inference" else "add",
                    evidence_refs=evidence,
                    natural_text=f"用户把{subject}称为{alias}",
                )
            )

        routine_match = self.routine_pattern.search(text)
        if routine_match:
            trigger = routine_match.group("trigger").strip()
            action = routine_match.group("action").strip()
            room = self._extract_room(trigger) or self._extract_room(action)
            candidates.append(
                ExtractedMemoryCandidate(
                    memory_type="routine",
                    scope_hint="home" if room is None else "room",
                    subject_text="用户",
                    predicate="trigger_routine",
                    object_text=trigger,
                    action=action,
                    condition=trigger,
                    room_text=room,
                    raw_mentions=[trigger, action],
                    source=source,
                    operation_hint="add_active" if source != "llm_inference" else "add",
                    evidence_refs=evidence,
                    natural_text=f"用户说{trigger}时，需要执行{action}",
                )
            )

        preference_match = self.preference_condition_pattern.search(text)
        if preference_match:
            condition = preference_match.group("condition").strip()
            content = preference_match.group("content").strip()
            room = self._extract_room(content)
            candidates.append(
                ExtractedMemoryCandidate(
                    memory_type="preference",
                    scope_hint="home" if room is None else "room",
                    subject_text="用户",
                    predicate="prefers",
                    object_text=content,
                    condition=condition,
                    room_text=room,
                    raw_mentions=[condition, content],
                    source=source,
                    operation_hint="add_active" if source != "llm_inference" else "add",
                    evidence_refs=evidence,
                    natural_text=f"用户在{condition}时偏好{content}",
                )
            )

        if "色温" in text:
            temp_match = self.color_temp_pattern.search(text)
            if temp_match:
                value = temp_match.group("temp")
                candidates.append(
                    ExtractedMemoryCandidate(
                        memory_type="preference",
                        scope_hint="home",
                        subject_text="用户",
                        predicate="favorite_color_temperature",
                        object_text=value,
                        raw_mentions=[value, "色温"],
                        source=source,
                        operation_hint="add_active" if source != "llm_inference" else "add",
                        evidence_refs=evidence,
                        natural_text=f"用户偏好的色温通常为{value}",
                    )
                )

        constraint_match = self.constraint_pattern.search(text)
        if constraint_match:
            condition = constraint_match.group("condition").strip()
            action = constraint_match.group("action").strip()
            room = self._extract_room(action)
            candidates.append(
                ExtractedMemoryCandidate(
                    memory_type="constraint",
                    scope_hint="home" if room is None else "room",
                    subject_text="用户",
                    predicate="must_not",
                    object_text=action,
                    condition=condition,
                    room_text=room,
                    raw_mentions=[condition, action],
                    source=source,
                    operation_hint="add_active" if source != "llm_inference" else "add",
                    evidence_refs=evidence,
                    natural_text=f"除非{condition}，否则不能{action}",
                )
            )

        if source == "user_correction" or self.correction_pattern.search(text):
            candidates.append(
                ExtractedMemoryCandidate(
                    memory_type="reflection",
                    scope_hint="home",
                    subject_text="本次任务",
                    predicate="correction",
                    object_text=text,
                    raw_mentions=[text],
                    source="user_correction" if source != "llm_inference" else source,
                    operation_hint="revise",
                    evidence_refs=evidence,
                    natural_text=f"用户纠错：{text}",
                )
            )

        if not candidates:
            location_match = self.location_pattern.search(text)
            if location_match and source != "llm_inference":
                subject = location_match.group("subject").strip()
                room = location_match.group("room").strip()
                candidates.append(
                    ExtractedMemoryCandidate(
                        memory_type="location",
                        scope_hint="device",
                        subject_text=subject,
                        predicate="located_in",
                        object_text=room,
                        room_text=room,
                        raw_mentions=[subject, room],
                        source=source,
                        operation_hint="add_active",
                        evidence_refs=evidence,
                        natural_text=f"{subject}位于{room}",
                    )
                )

        return candidates

    @staticmethod
    def _extract_room(text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(ROOM_PATTERN, text)
        return match.group(1) if match else None


class LLMExtractedCandidatePayload(BaseModel):
    memory_type: str
    scope_hint: str
    subject_text: str
    predicate: str
    object_text: str
    condition: str | None = None
    action: str | None = None
    room_text: str | None = None
    alias_text: str | None = None
    raw_mentions: list[str] = Field(default_factory=list)
    operation_hint: str = "add"
    natural_text: str | None = None
    structured_payload: dict = Field(default_factory=dict)


class LLMExtractionResult(BaseModel):
    candidates: list[LLMExtractedCandidatePayload] = Field(default_factory=list)


class LLMStructuredMemoryExtractor:
    def __init__(
        self,
        invoker: StructuredOutputInvoker | None = None,
        *,
        fallback: HeuristicMemoryExtractor | None = None,
    ) -> None:
        self.invoker = invoker
        self.fallback = fallback or HeuristicMemoryExtractor()

    def extract_from_turn(
        self,
        user_text: str,
        *,
        source: MemorySource,
        turn_id: str,
    ) -> list[ExtractedMemoryCandidate]:
        if not user_text.strip():
            return []
        evidence = [EvidenceRef(ref_type="turn", ref_id=turn_id)]
        if self.invoker is None:
            return self.fallback.extract_from_turn(user_text, source=source, turn_id=turn_id)

        try:
            result = self.invoker.invoke(
                LLMExtractionResult,
                self._build_messages(user_text, source),
            )
            candidates = [
                ExtractedMemoryCandidate(
                    memory_type=item.memory_type,  # type: ignore[arg-type]
                    scope_hint=item.scope_hint,  # type: ignore[arg-type]
                    subject_text=item.subject_text.strip(),
                    predicate=item.predicate.strip(),
                    object_text=item.object_text.strip(),
                    condition=item.condition.strip() if item.condition else None,
                    action=item.action.strip() if item.action else None,
                    room_text=item.room_text.strip() if item.room_text else None,
                    alias_text=item.alias_text.strip() if item.alias_text else None,
                    raw_mentions=[mention.strip() for mention in item.raw_mentions if mention.strip()],
                    source=source,
                    operation_hint=item.operation_hint,  # type: ignore[arg-type]
                    evidence_refs=evidence,
                    natural_text=item.natural_text.strip() if item.natural_text else None,
                    structured_payload=item.structured_payload,
                )
                for item in result.candidates
                if item.subject_text.strip() and item.predicate.strip() and item.object_text.strip()
            ]
            if candidates:
                return candidates
        except Exception:
            pass
        return self.fallback.extract_from_turn(user_text, source=source, turn_id=turn_id)

    @staticmethod
    def _build_messages(user_text: str, source: MemorySource) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是智能家居长期记忆抽取器。"
                    "只抽取适合长期保存的记忆，不要抽取当前瞬时状态。"
                    "允许的 memory_type 只有：capability, alias, location, preference, habit, constraint, routine, "
                    "episode, reflection, layout_relation, safety_rule, stable_state_fact。"
                    "scope_hint 只能是：entity, device, room, user, home。"
                    "如果输入里没有长期记忆价值，返回空 candidates。"
                    "尽量抽取用户明确表达、纠错、规则、偏好、别名、位置、触发场景；"
                    "不要编造 device_id 或 entity_id，只保留文本指代。"
                ),
            },
            {
                "role": "user",
                "content": f"source={source}\n对话内容：{user_text}",
            },
        ]
