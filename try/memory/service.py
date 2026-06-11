from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .confidence import now_utc
from .extractor import HeuristicMemoryExtractor
from .fts_index import SQLiteFTSIndex
from .ha_sync import HAFetcher, HomeAssistantSync
from .maintenance import MemoryMaintenance
from .matcher import MemoryMatcher
from .resolver import DisambiguationResolver, MemoryResolver
from .retriever import MemoryRetriever
from .schemas import (
    CandidateResolution,
    ExtractedMemoryCandidate,
    MemoryRecord,
    MemoryUsageEvent,
    SearchResultPackage,
)
from .sqlite_store import SqliteMemoryStore
from .update_policy import MemoryUpdatePolicy
from .usage_tracker import MemoryUsageTracker


class MemoryService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        user_id: str = "user.primary",
        extractor=None,
        disambiguator: DisambiguationResolver | None = None,
    ) -> None:
        self.store = SqliteMemoryStore(db_path)
        self.fts_index = SQLiteFTSIndex(self.store)
        self.extractor = extractor or HeuristicMemoryExtractor()
        self.matcher = MemoryMatcher(self.store, self.fts_index)
        self.resolver = MemoryResolver(self.store, user_id=user_id, disambiguator=disambiguator)
        self.update_policy = MemoryUpdatePolicy(self.store, self.matcher)
        self.retriever = MemoryRetriever(self.store)
        self.usage_tracker = MemoryUsageTracker(self.store)
        self.maintenance = MemoryMaintenance(self.store)
        self.ha_sync = HomeAssistantSync(self.store)

    def sync_ha_facts(self, fetcher: HAFetcher) -> dict[str, int]:
        return self.ha_sync.sync(fetcher)

    def upsert_memory_record(self, record: MemoryRecord) -> MemoryRecord:
        self.store.upsert_record(record)
        return record

    def memory_get(self, memory_id: str) -> MemoryRecord | None:
        return self.store.get_record(memory_id)

    def memory_search(
        self,
        query: str,
        scope: str | None = None,
        memory_types: list[str] | None = None,
        min_confidence: float = 0.45,
        include_stale: bool = False,
    ) -> SearchResultPackage:
        return self.retriever.search(
            query,
            scope=scope,
            memory_types=memory_types,
            min_confidence=min_confidence,
            include_stale=include_stale,
        )

    def memory_ingest_turn(
        self,
        task_id: str,
        user_text: str,
        *,
        assistant_text: str = "",
        source: str = "user_explicit",
        task_candidates: list[dict[str, Any]] | None = None,
        turn_id: str | None = None,
    ) -> list[MemoryRecord]:
        turn_ref = turn_id or f"{task_id}:user"
        extracted = self.extractor.extract_from_turn(user_text, source=source, turn_id=turn_ref)
        results = []
        for candidate in extracted:
            record = self.ingest_candidate(candidate, task_candidates=task_candidates)
            results.append(record)
        return results

    def ingest_candidate(
        self,
        candidate: ExtractedMemoryCandidate,
        *,
        task_candidates: list[dict[str, Any]] | None = None,
    ) -> MemoryRecord:
        resolution = self.resolver.resolve(candidate, task_candidates=task_candidates)
        return self.update_policy.apply_candidate(candidate, resolution)

    def memory_mark_used(self, task_id: str, memory_id: str, stage: str) -> None:
        self.usage_tracker.mark_used(task_id, memory_id, stage)

    def memory_mark_outcome(
        self,
        task_id: str,
        memory_id: str,
        contribution: str,
        outcome: str,
        *,
        note: str = "",
        verification_delta: float | None = None,
        stage: str = "unknown",
    ) -> None:
        event = MemoryUsageEvent(
            task_id=task_id,
            memory_id=memory_id,
            used_stage=stage,  # type: ignore[arg-type]
            contribution=contribution,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            verification_delta=verification_delta,
            note=note,
        )
        self.usage_tracker.mark_outcome(event)

    def memory_finalize_task(
        self,
        task_id: str,
        final_outcome: str,
        verification_bundle: dict[str, Any],
        correction_bundle: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        updated: list[MemoryRecord] = []
        used_memory_ids = verification_bundle.get("used_memory_ids", [])
        stage_by_memory = verification_bundle.get("stage_by_memory", {})
        helpful_memory_ids = set(verification_bundle.get("helpful_memory_ids", used_memory_ids))
        misleading_memory_ids = set(verification_bundle.get("misleading_memory_ids", []))
        note = verification_bundle.get("note", "")
        for memory_id in used_memory_ids:
            stage = stage_by_memory.get(memory_id, "unknown")
            if memory_id in misleading_memory_ids:
                contribution = "misleading"
            elif memory_id in helpful_memory_ids:
                contribution = "helpful"
            else:
                contribution = "neutral"
            self.memory_mark_outcome(
                task_id,
                memory_id,
                contribution,
                final_outcome,
                note=note,
                stage=stage,
            )

        if final_outcome == "failure" and note:
            reflection = ExtractedMemoryCandidate(
                memory_type="reflection",
                scope_hint="home",
                subject_text="本次任务",
                predicate="verification_failure",
                object_text=note,
                source="execution_verification",
                operation_hint="add_active",
                natural_text=f"任务失败反思：{note}",
            )
            updated.append(self.ingest_candidate(reflection))

        if correction_bundle and correction_bundle.get("user_text"):
            updated.extend(
                self.memory_ingest_turn(
                    task_id,
                    correction_bundle["user_text"],
                    source="user_correction",
                    turn_id=correction_bundle.get("turn_id"),
                )
            )
        return updated

    def memory_maintenance(self) -> dict[str, int]:
        return self.maintenance.run()

    def list_records(self, **kwargs) -> list[MemoryRecord]:
        return self.store.list_records(**kwargs)
