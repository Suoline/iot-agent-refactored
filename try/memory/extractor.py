from __future__ import annotations

import re

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

