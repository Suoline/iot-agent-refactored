from __future__ import annotations

import re

from .fts_index import SQLiteFTSIndex
from .schemas import ExtractedMemoryCandidate, MemoryRecord
from .sqlite_store import SqliteMemoryStore


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).strip().lower()


class MemoryMatcher:
    def __init__(self, store: SqliteMemoryStore, fts_index: SQLiteFTSIndex) -> None:
        self.store = store
        self.fts_index = fts_index

    def find_matches(self, candidate: ExtractedMemoryCandidate, limit: int = 10) -> list[MemoryRecord]:
        matched: dict[str, MemoryRecord] = {}
        direct = self.store.find_records(
            subject=candidate.subject_text,
            predicate=candidate.predicate,
            memory_type=candidate.memory_type,
            statuses=["active", "candidate", "stale", "conflicted"],
        )
        for record in direct:
            matched[record.memory_id] = record

        query_terms = " ".join(
            part
            for part in [
                candidate.subject_text,
                candidate.object_text,
                candidate.condition or "",
                candidate.room_text or "",
                candidate.alias_text or "",
            ]
            if part
        )
        for memory_id in self.fts_index.search(query_terms, limit=limit):
            record = self.store.get_record(memory_id)
            if record is not None:
                matched[record.memory_id] = record
        return list(matched.values())

    def find_conflicts(self, candidate: ExtractedMemoryCandidate, scope: str) -> list[MemoryRecord]:
        records = self.store.find_records(
            subject=candidate.subject_text,
            predicate=candidate.predicate,
            scope=scope,
            statuses=["active", "stale", "conflicted"],
        )
        return [record for record in records if normalize_text(record.object) != normalize_text(candidate.object_text)]

    def is_equivalent(self, candidate: ExtractedMemoryCandidate, record: MemoryRecord) -> bool:
        return (
            normalize_text(candidate.subject_text) == normalize_text(record.subject)
            and normalize_text(candidate.predicate) == normalize_text(record.predicate)
            and normalize_text(candidate.object_text) == normalize_text(record.object)
            and normalize_text(candidate.condition) == normalize_text(record.condition)
            and normalize_text(candidate.action) == normalize_text(record.action)
        )

    def is_broad_narrow_pair(self, candidate: ExtractedMemoryCandidate, record: MemoryRecord) -> bool:
        same_signature = (
            normalize_text(candidate.subject_text) == normalize_text(record.subject)
            and normalize_text(candidate.predicate) == normalize_text(record.predicate)
        )
        if not same_signature:
            return False
        left = normalize_text(candidate.object_text)
        right = normalize_text(record.object)
        return bool(left and right and left != right and (left in right or right in left))

