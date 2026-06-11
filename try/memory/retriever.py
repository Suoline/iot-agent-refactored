from __future__ import annotations

import re

from .confidence import TASK_THRESHOLDS, compute_effective_confidence, compute_memory_worth, now_utc
from .schemas import CandidateDevice, GlobalConstraint, MatchedMemory, SearchResultPackage
from .sqlite_store import SqliteMemoryStore


def _tokenize(value: str) -> set[str]:
    pieces = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", value)
    return {piece.lower() for piece in pieces if piece.strip()}


class MemoryRetriever:
    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        memory_types: list[str] | None = None,
        min_confidence: float = 0.45,
        include_stale: bool = False,
        limit: int = 10,
    ) -> SearchResultPackage:
        statuses = ["active", "conflicted"]
        if include_stale:
            statuses.append("stale")
        candidate_ids = self.store.search_fts(query, limit=max(limit * 3, 10))
        records = []
        for memory_id in candidate_ids:
            record = self.store.get_record(memory_id)
            if record is None or record.status not in statuses:
                continue
            if scope and record.scope != scope:
                continue
            if memory_types and record.memory_type not in memory_types:
                continue
            effective = compute_effective_confidence(record, now=now_utc())
            if effective < min_confidence:
                continue
            score = self._retrieval_score(query, record, effective)
            records.append((record, effective, score))
        records.sort(key=lambda item: item[2], reverse=True)
        records = records[:limit]

        matched_memories = [
            MatchedMemory(
                memory_id=record.memory_id,
                type=record.memory_type,
                text=record.natural_text,
                confidence=effective,
                status=record.status,
                scope=record.scope,
                device_id=record.device_id,
                entity_id=record.entity_id,
                room_id=record.room_id,
                retrieval_score=score,
            )
            for record, effective, score in records
        ]

        grouped_devices: dict[str, list[MatchedMemory]] = {}
        device_labels: dict[str, str] = {}
        for item in matched_memories:
            if not item.device_id:
                continue
            grouped_devices.setdefault(item.device_id, []).append(item)
            record = self.store.get_record(item.memory_id)
            if record is not None:
                device_labels[item.device_id] = record.subject

        candidate_devices = []
        for device_id, memories in grouped_devices.items():
            score = max(memory.retrieval_score for memory in memories)
            confidence = max(memory.confidence for memory in memories)
            candidate_devices.append(
                CandidateDevice(
                    device_id=device_id,
                    name=device_labels.get(device_id, device_id),
                    score=score,
                    confidence=confidence,
                    matched_memories=sorted(memories, key=lambda item: item.retrieval_score, reverse=True),
                )
            )
        candidate_devices.sort(key=lambda item: item.score, reverse=True)

        global_constraints = [
            GlobalConstraint(
                memory_id=item.memory_id,
                text=item.text,
                confidence=item.confidence,
                memory_type=item.type,
                scope=item.scope,
            )
            for item in matched_memories
            if item.scope in {"home", "room", "user"} or item.type in {"preference", "constraint", "routine", "reflection"}
        ]

        should_ask_user = False
        ask_reason = None
        if len(candidate_devices) >= 2:
            diff = candidate_devices[0].score - candidate_devices[1].score
            if diff <= 0.05:
                should_ask_user = True
                ask_reason = "多个候选设备得分接近，需要澄清。"
        if candidate_devices and candidate_devices[0].confidence < TASK_THRESHOLDS["control"]:
            should_ask_user = True
            ask_reason = ask_reason or "最高置信度不足以直接执行普通控制任务。"

        return SearchResultPackage(
            candidate_devices=candidate_devices,
            matched_memories=matched_memories,
            global_constraints=global_constraints,
            should_ask_user=should_ask_user,
            ask_reason=ask_reason,
        )

    @staticmethod
    def _retrieval_score(query: str, record, effective_confidence: float) -> float:
        q_tokens = _tokenize(query)
        r_tokens = _tokenize(record.natural_text + " " + record.subject + " " + record.object)
        overlap = len(q_tokens & r_tokens) / max(len(q_tokens), 1)
        scope_match = 1.0 if any(scope in query for scope in ["客厅", "卧室", "书房", "厨房"]) and record.room_id else 0.6
        memory_worth = compute_memory_worth(record)
        return (
            0.30 * overlap
            + 0.20 * scope_match
            + 0.20 * effective_confidence
            + 0.10 * 1.0
            + 0.10 * record.importance
            + 0.10 * memory_worth
        )

