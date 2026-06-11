from __future__ import annotations

import uuid
from typing import Iterable

from .confidence import get_default_half_life, get_source_authority, now_utc
from .matcher import MemoryMatcher
from .schemas import CandidateResolution, ExtractedMemoryCandidate, MemoryRecord
from .sqlite_store import SqliteMemoryStore


class MemoryUpdatePolicy:
    def __init__(self, store: SqliteMemoryStore, matcher: MemoryMatcher) -> None:
        self.store = store
        self.matcher = matcher

    def apply_candidate(
        self,
        candidate: ExtractedMemoryCandidate,
        resolution: CandidateResolution,
    ) -> MemoryRecord:
        now = now_utc()
        matches = self.matcher.find_matches(candidate)
        equivalent = next((item for item in matches if self.matcher.is_equivalent(candidate, item)), None)
        if equivalent is not None:
            equivalent.evidence_refs.extend(candidate.evidence_refs)
            equivalent.evidence_refs = self._dedupe_evidence(equivalent.evidence_refs)
            equivalent.updated_at = now
            equivalent.update_count += 1
            if candidate.source == "user_correction":
                equivalent.status = "active"
                equivalent.confidence = max(equivalent.confidence, 0.95)
            self.store.upsert_record(equivalent)
            self.store.add_event("memory_refreshed", memory_id=equivalent.memory_id, payload={"source": candidate.source}, created_at=now)
            return equivalent

        if any(self.matcher.is_broad_narrow_pair(candidate, item) for item in matches):
            record = self._build_record(candidate, resolution, now=now)
            self.store.upsert_record(record)
            self.store.add_event("memory_parallel_added", memory_id=record.memory_id, payload={"reason": "broad_narrow"}, created_at=now)
            return record

        conflicts = self.matcher.find_conflicts(candidate, resolution.scope)
        if conflicts and candidate.source in {"user_explicit", "user_correction", "ha_registry", "execution_verification"}:
            record = self._build_record(candidate, resolution, now=now)
            for conflict in conflicts:
                conflict.status = "superseded" if candidate.source in {"user_explicit", "user_correction", "ha_registry"} else "conflicted"
                if record.memory_id not in conflict.conflicts_with:
                    conflict.conflicts_with.append(record.memory_id)
                conflict.superseded_by = record.memory_id if conflict.status == "superseded" else conflict.superseded_by
                conflict.updated_at = now
                conflict.update_count += 1
                self.store.upsert_record(conflict)
                if conflict.memory_id not in record.supersedes and conflict.status == "superseded":
                    record.supersedes.append(conflict.memory_id)
                if conflict.memory_id not in record.conflicts_with:
                    record.conflicts_with.append(conflict.memory_id)
            self.store.upsert_record(record)
            self.store.add_event("memory_revised", memory_id=record.memory_id, payload={"conflicts": [item.memory_id for item in conflicts]}, created_at=now)
            return record

        record = self._build_record(candidate, resolution, now=now)
        self.store.upsert_record(record)
        self.store.add_event("memory_created", memory_id=record.memory_id, payload={"source": candidate.source}, created_at=now)
        return record

    def _build_record(
        self,
        candidate: ExtractedMemoryCandidate,
        resolution: CandidateResolution,
        *,
        now,
    ) -> MemoryRecord:
        scope = resolution.scope
        status = "candidate"
        if resolution.resolution_state in {"bound", "downgraded"} and candidate.source != "llm_inference":
            status = "active"
        if candidate.memory_type == "habit" and candidate.source == "user_behavior":
            status = "candidate"
        if candidate.source == "llm_inference":
            status = "candidate"
        return MemoryRecord(
            memory_id=f"mem_{uuid.uuid4().hex[:24]}",
            scope=scope,
            device_id=resolution.device_id,
            entity_id=resolution.entity_id,
            room_id=resolution.room_id,
            user_id=resolution.user_id,
            memory_type=candidate.memory_type,
            subject=candidate.subject_text,
            predicate=candidate.predicate,
            object=candidate.object_text,
            condition=candidate.condition,
            action=candidate.action,
            natural_text=candidate.natural_text or self._build_natural_text(candidate),
            structured_payload={
                **candidate.structured_payload,
                "scope_hint": candidate.scope_hint,
                "raw_mentions": candidate.raw_mentions,
                "resolution_state": resolution.resolution_state,
                "candidate_device_ids": resolution.candidate_device_ids,
                "candidate_entity_ids": resolution.candidate_entity_ids,
            },
            source=candidate.source,
            evidence_refs=self._dedupe_evidence(candidate.evidence_refs),
            confidence=get_source_authority(candidate.source),
            importance=0.5,
            half_life_days=get_default_half_life(candidate.memory_type),
            created_at=now,
            updated_at=now,
            valid_from=now,
            status=status,
        )

    @staticmethod
    def _build_natural_text(candidate: ExtractedMemoryCandidate) -> str:
        parts = [candidate.subject_text, candidate.predicate, candidate.object_text]
        if candidate.condition:
            parts.append(f"条件:{candidate.condition}")
        if candidate.action:
            parts.append(f"动作:{candidate.action}")
        return " ".join(part for part in parts if part)

    @staticmethod
    def _dedupe_evidence(items: Iterable) -> list:
        seen: set[tuple[str, str]] = set()
        results = []
        for item in items:
            key = (item.ref_type, item.ref_id)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
        return results

