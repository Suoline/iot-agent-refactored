from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel

from .llm_support import StructuredOutputInvoker
from .matcher import normalize_text
from .schemas import CandidateResolution, ExtractedMemoryCandidate
from .sqlite_store import SqliteMemoryStore


DEVICE_ID_PATTERN = re.compile(r"\b[a-f0-9]{32}\b")
ENTITY_ID_PATTERN = re.compile(r"\b[a-z_]+\.[a-zA-Z0-9_]+\b")
ROOM_ID_MAP = {
    "客厅": "room.living_room",
    "卧室": "room.bedroom",
    "书房": "room.study",
    "厨房": "room.kitchen",
}


class DisambiguationResolver(Protocol):
    def choose_device(
        self,
        mention: str,
        room_text: str | None,
        candidates: list[dict[str, Any]],
    ) -> str | None:
        ...


class HeuristicDisambiguator:
    def choose_device(
        self,
        mention: str,
        room_text: str | None,
        candidates: list[dict[str, Any]],
    ) -> str | None:
        mention_norm = normalize_text(mention)
        if room_text:
            scoped = [item for item in candidates if normalize_text(item.get("room_text")) == normalize_text(room_text)]
            if len(scoped) == 1:
                return scoped[0]["device_id"]
        exact = [item for item in candidates if mention_norm and mention_norm in normalize_text(item.get("display_text", ""))]
        if len(exact) == 1:
            return exact[0]["device_id"]
        return None


class LLMDisambiguationResult(BaseModel):
    chosen_device_id: str | None = None
    unresolved: bool = False
    rationale: str | None = None


class LLMDisambiguator:
    def __init__(
        self,
        invoker: StructuredOutputInvoker | None = None,
        *,
        fallback: HeuristicDisambiguator | None = None,
    ) -> None:
        self.invoker = invoker
        self.fallback = fallback or HeuristicDisambiguator()

    def choose_device(
        self,
        mention: str,
        room_text: str | None,
        candidates: list[dict[str, Any]],
    ) -> str | None:
        heuristic_choice = self.fallback.choose_device(mention, room_text, candidates)
        if heuristic_choice:
            return heuristic_choice
        if self.invoker is None or len(candidates) <= 1:
            return None
        try:
            result = self.invoker.invoke(
                LLMDisambiguationResult,
                self._build_messages(mention, room_text, candidates),
            )
            if result.unresolved:
                return None
            chosen = result.chosen_device_id
            valid_ids = {item["device_id"] for item in candidates}
            if chosen in valid_ids:
                return chosen
        except Exception:
            return None
        return None

    @staticmethod
    def _build_messages(
        mention: str,
        room_text: str | None,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        candidate_lines = "\n".join(
            f"- device_id={item['device_id']}; room={item.get('room_text')}; text={item.get('display_text')}"
            for item in candidates
        )
        return [
            {
                "role": "system",
                "content": (
                    "你是智能家居设备指代消歧器。"
                    "你只能在给定候选设备中选择一个 device_id，或明确返回 unresolved=true。"
                    "如果信息不足、多个候选都合理，必须返回 unresolved=true，不能猜。"
                ),
            },
            {
                "role": "user",
                "content": f"提及对象：{mention}\n房间线索：{room_text or '无'}\n候选列表：\n{candidate_lines}",
            },
        ]


class MemoryResolver:
    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        user_id: str,
        disambiguator: DisambiguationResolver | None = None,
    ) -> None:
        self.store = store
        self.user_id = user_id
        self.disambiguator = disambiguator or HeuristicDisambiguator()

    def resolve(
        self,
        candidate: ExtractedMemoryCandidate,
        *,
        task_candidates: list[dict[str, Any]] | None = None,
    ) -> CandidateResolution:
        explicit_device = self._find_explicit_device_id(candidate)
        if explicit_device:
            return CandidateResolution(
                scope="device",
                resolution_state="bound",
                device_id=explicit_device,
                user_id=self.user_id,
                candidate_device_ids=[explicit_device],
            )

        explicit_entity = self._find_explicit_entity_id(candidate)
        if explicit_entity:
            return CandidateResolution(
                scope="entity",
                resolution_state="bound",
                entity_id=explicit_entity,
                user_id=self.user_id,
                candidate_entity_ids=[explicit_entity],
            )

        room_text = candidate.room_text or self._extract_room(candidate.subject_text) or self._extract_room(candidate.object_text)
        room_id = ROOM_ID_MAP.get(room_text) if room_text else None
        device_candidates = self._collect_device_candidates(candidate, task_candidates=task_candidates, room_text=room_text)

        if len(device_candidates) == 1:
            return CandidateResolution(
                scope="device",
                resolution_state="bound",
                device_id=device_candidates[0]["device_id"],
                room_id=room_id,
                user_id=self.user_id,
                candidate_device_ids=[device_candidates[0]["device_id"]],
            )

        if len(device_candidates) > 1:
            chosen = self.disambiguator.choose_device(candidate.subject_text, room_text, device_candidates)
            if chosen:
                return CandidateResolution(
                    scope="device",
                    resolution_state="bound",
                    device_id=chosen,
                    room_id=room_id,
                    user_id=self.user_id,
                    candidate_device_ids=[item["device_id"] for item in device_candidates],
                )

        downgraded_scope = self._downgraded_scope(candidate, room_id=room_id)
        if downgraded_scope:
            return CandidateResolution(
                scope=downgraded_scope,
                resolution_state="downgraded",
                room_id=room_id,
                user_id=self.user_id if downgraded_scope == "user" else None,
                candidate_device_ids=[item["device_id"] for item in device_candidates],
            )

        return CandidateResolution(
            scope=candidate.scope_hint,
            resolution_state="unresolved",
            room_id=room_id,
            user_id=self.user_id if candidate.scope_hint == "user" else None,
            candidate_device_ids=[item["device_id"] for item in device_candidates],
        )

    def _find_explicit_device_id(self, candidate: ExtractedMemoryCandidate) -> str | None:
        text = " ".join([candidate.subject_text, candidate.object_text, " ".join(candidate.raw_mentions)])
        match = DEVICE_ID_PATTERN.search(text)
        return match.group(0) if match else None

    def _find_explicit_entity_id(self, candidate: ExtractedMemoryCandidate) -> str | None:
        text = " ".join([candidate.subject_text, candidate.object_text, " ".join(candidate.raw_mentions)])
        match = ENTITY_ID_PATTERN.search(text)
        return match.group(0) if match else None

    def _collect_device_candidates(
        self,
        candidate: ExtractedMemoryCandidate,
        *,
        task_candidates: list[dict[str, Any]] | None,
        room_text: str | None,
    ) -> list[dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        mention_texts = [candidate.subject_text, candidate.object_text]
        if candidate.alias_text:
            mention_texts.append(candidate.alias_text)
        for mention in mention_texts:
            if not mention:
                continue
            for record in self.store.search_fts(mention, limit=20):
                matched = self.store.get_record(record)
                if not matched or not matched.device_id or matched.status not in {"active", "stale"}:
                    continue
                if room_text and matched.room_id and ROOM_ID_MAP.get(room_text) not in {matched.room_id, room_text}:
                    continue
                results[matched.device_id] = {
                    "device_id": matched.device_id,
                    "display_text": matched.natural_text,
                    "room_text": matched.room_id or room_text,
                }

        if task_candidates:
            for item in task_candidates:
                device_id = item.get("device_id")
                if not device_id:
                    continue
                display_text = f"{item.get('device_name', '')} {item.get('device_reason', '')}"
                combined = normalize_text(display_text)
                wanted = normalize_text(candidate.subject_text + candidate.object_text + (room_text or ""))
                if wanted and any(token in combined for token in self._tokens(wanted)):
                    results.setdefault(
                        device_id,
                        {"device_id": device_id, "display_text": display_text, "room_text": room_text},
                    )
        return list(results.values())

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [token for token in re.split(r"(?<=.)(?=.)", value) if token.strip()]

    @staticmethod
    def _extract_room(text: str | None) -> str | None:
        if not text:
            return None
        for label in ROOM_ID_MAP:
            if label in text:
                return label
        return None

    @staticmethod
    def _downgraded_scope(candidate: ExtractedMemoryCandidate, *, room_id: str | None) -> str | None:
        if room_id and candidate.memory_type in {"preference", "constraint", "routine", "location"}:
            return "room"
        if candidate.memory_type in {"preference", "constraint", "routine", "reflection", "habit", "safety_rule"}:
            return "home"
        return None
