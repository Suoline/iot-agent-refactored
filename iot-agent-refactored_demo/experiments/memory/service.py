from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from .canonical_store import CanonicalStore
from .confidence import TASK_THRESHOLDS, effective_confidence, memory_worth, source_confidence
from .expiration import is_usable_stale, refresh_status, stale_runtime_status
from .schemas import (
    CandidateDevice,
    MatchedMemory,
    MemoryEdge,
    MemoryRecord,
    SearchResultPackage,
    UsageEvent,
)
from .vector_index import VectorIndex
from experiments.evaluator.lifecycle import evaluator_status_for_record


class MemoryService:
    def __init__(self, db_path: str | Path, *, config: dict[str, Any] | None = None):
        self.store = CanonicalStore(db_path)
        self.index = VectorIndex()
        self.config = config or {}
        self.activation_log: list[dict[str, Any]] = []

    def _activation(self, mechanism: str, *, event: str, enabled: bool | None = None, **context: Any):
        self.activation_log.append(
            {
                "mechanism": mechanism,
                "event": event,
                "enabled": self.config.get(f"use_{mechanism}", True) if enabled is None else enabled,
                **context,
            }
        )

    def upsert(self, record: MemoryRecord, *, event_type: str = "upsert"):
        self.store.upsert(record, event_type=event_type)

    def get(self, memory_id: str):
        return self.store.get(memory_id)

    def apply_memory_op(self, op: dict[str, Any], now: datetime):
        kind = op["op"]
        if kind in {"add_active", "add_candidate"}:
            if kind == "add_candidate":
                self._activation("candidate_gate", event="candidate_write", status="candidate")
            existing = op.get("memory_id") and self.get(op["memory_id"])
            if existing and kind == "add_candidate":
                existing.positive_hits += 1
                existing.observed_at = now
                timestamps = list(existing.structured_payload.get("observation_timestamps", []))
                missing = max(0, existing.positive_hits - len(timestamps) - 1)
                if missing:
                    timestamps = [existing.created_at.isoformat()] * missing + timestamps
                timestamps.append(now.isoformat())
                existing.structured_payload = {
                    **existing.structured_payload,
                    "observation_timestamps": sorted(timestamps),
                }
                existing.updated_at = now
                self.upsert(existing, event_type="add_candidate_update")
                return existing
            payload = dict(op)
            if kind == "add_candidate":
                payload.setdefault("observed_at", now)
                if payload.get("source") == "user_behavior":
                    payload.setdefault("positive_hits", 1)
                    timestamps = list(payload.get("structured_payload", {}).get("observation_timestamps", []))
                    timestamps.append(now.isoformat())
                    payload["structured_payload"] = {
                        **payload.get("structured_payload", {}),
                        "observation_timestamps": timestamps,
                    }
            record = self._record_from_op(payload, now, active=kind == "add_active")
            self.upsert(record, event_type=kind)
            return record
        if kind == "delete":
            record = self.get(op["memory_id"])
            if record:
                record.status = "deleted"
                record.layer = "archived"
                record.updated_at = now
                self.upsert(record, event_type="delete")
                self.store.delete_index_record(record.memory_id)
            return record
        if kind == "mark_outcome":
            event = UsageEvent(
                task_id=op.get("task_id", op["memory_id"]),
                memory_id=op["memory_id"],
                used_stage=op["used_stage"],
                contribution=op["contribution"],
                outcome=op["outcome"],
                note=op.get("note", ""),
                timestamp=now,
            )
            self.mark_outcome(event)
            return self.get(op["memory_id"])
        if kind in {"revise", "invalidate"}:
            self._activation("conflict_handling", event=kind, memory_id=op.get("old_memory_id"))
            old = self.get(op["old_memory_id"])
            if old:
                if self.config.get("use_conflict_handling", True):
                    old.status = "superseded" if kind == "revise" else "expired"
                old.layer = "archived"
                old.updated_at = now
                self.upsert(old, event_type=kind)
            if kind == "revise":
                new = self._record_from_op(op["new_record"], now, active=True)
                self.upsert(new, event_type="revise_new")
                return new
            return old
        if kind == "merge":
            self._activation("feature_absorption", event="merge", source_ids=list(op.get("source_ids", [])))
            source_ids = op["source_ids"]
            records = [self.get(item) for item in source_ids]
            records = [item for item in records if item is not None]
            if self.config.get("use_feature_absorption", True):
                if any(record.object != records[0].object for record in records[1:]):
                    raise ValueError("merge rejected by feature absorption guard")
            merged = self._record_from_op(op["merged_record"], now, active=True)
            merged.merged_from = [item.memory_id for item in records]
            merged.coverage_proof = self._normalize_coverage_proof(op.get("coverage_proof"), source_ids)
            if merged.coverage_proof is None:
                merged.needs_review = True
            merged.evidence_refs = self._union_evidence_refs(records, merged.evidence_refs)
            merged.related_memory_ids = sorted({
                memory_id
                for record in records
                for memory_id in record.related_memory_ids
                if memory_id not in merged.merged_from
            })
            self.upsert(merged, event_type="merge")
            for record in records:
                record.status = "superseded"
                record.superseded_by = merged.memory_id
                record.layer = "archived"
                record.updated_at = now
                self.upsert(record, event_type="merge_source")
            return merged
        if kind == "split":
            self._activation("split", event="split", source_id=op.get("old_memory_id"))
            if not self.config.get("use_split", True):
                return []
            original = self.get(op["old_memory_id"])
            child_ids = sorted(child["memory_id"] for child in op["new_records"])
            if original:
                original.status = "superseded"
                original.layer = "archived"
                original.supersedes = child_ids
                original.updated_at = now
                self.upsert(original, event_type="split_source")
            created = []
            for child in op["new_records"]:
                child_payload = {
                    **child,
                    "derived_from_memory_ids": sorted(set(child.get("derived_from_memory_ids", []) + [op["old_memory_id"]])),
                    "related_memory_ids": sorted(
                        set(child.get("related_memory_ids", []) + [item for item in child_ids if item != child["memory_id"]])
                    ),
                }
                record = self._record_from_op(child_payload, now, active=True)
                self.upsert(record, event_type="split_child")
                if original:
                    self._create_edge(record.memory_id, "specializes", original.memory_id, now, source_memory_id=record.memory_id)
                    self._create_edge(original.memory_id, "generalizes", record.memory_id, now, source_memory_id=original.memory_id)
                created.append(record)
            return created
        if kind == "patch":
            record = self.get(op["memory_id"])
            if record:
                for key, value in op.get("updates", {}).items():
                    setattr(record, key, value)
                record.updated_at = now
                self.upsert(record, event_type="patch")
            return record
        raise ValueError(f"Unsupported memory operation: {kind}")

    def search(
        self,
        query: str,
        *,
        task_type: str = "control",
        now: datetime | None = None,
        top_k: int = 10,
    ) -> SearchResultPackage:
        now = now or datetime.now(timezone.utc)
        self._activation("lifecycle", event="search_lifecycle", enabled=self.config.get("use_lifecycle", True))
        self._activation("dynamic_confidence", event="search_confidence", enabled=self.config.get("use_dynamic_confidence", True))
        self._activation("candidate_gate", event="search_candidate_gate", enabled=self.config.get("use_candidate_gate", True))
        threshold = TASK_THRESHOLDS.get(task_type, 0.70)
        if (
            not self.config.get("use_memory", True)
            and self.config.get("score_mode") != "large_context"
        ):
            return SearchResultPackage(
                query=query,
                matched_memories=[],
                candidate_devices=[],
                should_ask_user=True,
                ask_reason="当前系统配置不使用长期记忆",
                threshold_used=threshold,
                task_type=task_type,
                retrieval_metadata={"top_k": top_k},
            )
        records = []
        for record in self.store.list():
            if self.config.get("use_lifecycle", True):
                refresh_status(record, now)
            if record.status == "deleted":
                continue
            if record.status in {"conflicted", "superseded", "expired", "archived"}:
                continue
            runtime_status = (
                stale_runtime_status(record, task_type=task_type, now=now)
                if self.config.get("use_lifecycle", True)
                else None
            )
            if record.status == "stale" and runtime_status is None:
                continue
            if record.status == "candidate" and self.config.get("use_candidate_gate", True):
                continue
            record.last_accessed_at = now
            record.access_count += 1
            records.append(record)

        matches = []
        memory_entity_map: dict[str, str] = {}
        grouped_candidates: dict[str, list[MatchedMemory]] = {}
        effective_top_k = len(records) if self.config.get("score_mode") == "large_context" else top_k
        for lexical_score, record in self.index.search(query, records, top_k=effective_top_k):
            eff = (
                effective_confidence(record, now)
                if self.config.get("use_dynamic_confidence", True)
                else record.confidence
            )
            worth = memory_worth(record)
            runtime_status = (
                stale_runtime_status(record, task_type=task_type, now=now)
                if self.config.get("use_lifecycle", True)
                else None
            ) or record.status
            usable = record.status == "active" or is_usable_stale(record, task_type=task_type, now=now)
            if record.status == "candidate" and not self.config.get("use_candidate_gate", True):
                usable = eff >= threshold
            passes_threshold = eff >= threshold or (task_type == "safety" and worth > 0.8)
            score = self._retrieval_score(
                record=record,
                lexical_score=lexical_score,
                effective=eff,
                worth=worth,
                now=now,
                query=query,
            )
            matched = MatchedMemory(
                memory_id=record.memory_id,
                memory_type=record.memory_type,
                text=record.natural_text,
                score=score,
                raw_confidence=record.confidence,
                effective_confidence=eff,
                memory_worth=worth,
                system_status=record.status,
                # This is an evaluator label derived from independent facts;
                # system_status remains the runtime prediction.
                true_status=evaluator_status_for_record(
                    record.model_dump(mode="json"), now
                ),
                runtime_status=runtime_status,
                layer=record.layer,
                in_usable_set=usable and passes_threshold,
            )
            matches.append(matched)
            entity_id = record.entity_id or (
                record.object if isinstance(record.object, str) and ("." in record.object or record.object.startswith("routine.")) else None
            )
            if entity_id:
                memory_entity_map[matched.memory_id] = entity_id
                grouped_candidates.setdefault(entity_id, []).append(matched)

        global_constraints = sorted(
            [item for item in matches if item.memory_type in {"preference", "routine", "reflection"}],
            key=lambda item: (-item.score, item.memory_id),
        )
        executable_matches = [item for item in matches if item.in_usable_set]
        clarification_only_matches = [
            item for item in matches
            if item.runtime_status == "usable-stale" and not item.in_usable_set
        ]
        candidate_devices = []
        for entity_id, related in grouped_candidates.items():
            best = max(related, key=lambda item: item.score)
            if not best.in_usable_set:
                continue
            candidate_devices.append(
                CandidateDevice(
                    entity_id=entity_id,
                    name=entity_id,
                    score=best.score,
                    confidence=best.effective_confidence,
                    matched_memories=sorted(related, key=lambda item: -item.score),
                )
            )
        candidate_devices.sort(key=lambda item: (-item.score, item.entity_id))
        top_passes_threshold = bool(executable_matches) and (
            executable_matches[0].effective_confidence >= threshold
            or (task_type == "safety" and executable_matches[0].memory_worth > 0.8)
        )
        should_ask = not top_passes_threshold
        if len(candidate_devices) > 1 and candidate_devices[0].score - candidate_devices[1].score < 0.10:
            should_ask = True
        ask_reason = None
        if should_ask:
            ask_reason = "没有足够置信度或候选差距不足"
        if clarification_only_matches and not candidate_devices:
            should_ask = True
            ask_reason = "仅有陈旧记忆可用于澄清"
        return SearchResultPackage(
            query=query,
            candidate_devices=candidate_devices,
            global_constraints=global_constraints,
            matched_memories=matches,
            should_ask_user=should_ask,
            ask_reason=ask_reason,
            threshold_used=threshold,
            task_type=task_type,
            retrieval_metadata={
                "top_k": top_k,
                "memory_entity_map": memory_entity_map,
                "usable_stale_memory_ids": [
                    item.memory_id for item in matches if item.runtime_status == "usable-stale"
                ],
                "clarification_only_memory_ids": [
                    item.memory_id for item in clarification_only_matches
                ],
            },
        )

    def list_records(self, *, include_deleted: bool = False):
        return self.store.list(include_deleted=include_deleted)

    def list_edges(self):
        return self.store.list_edges()

    def _memory_graph(self) -> tuple[dict[str, MemoryRecord], dict[str, set[str]]]:
        records = self.store.list(include_deleted=True)
        by_id = {record.memory_id: record for record in records if record.status != "deleted"}
        graph: dict[str, set[str]] = {memory_id: set() for memory_id in by_id}

        def connect(left: str | None, right: str | None):
            if not left or not right or left == right:
                return
            if left not in by_id or right not in by_id:
                return
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

        for record in by_id.values():
            for memory_id in (
                list(record.related_memory_ids)
                + list(record.depends_on_memory_ids)
                + list(record.derived_from_memory_ids)
                + list(record.supersedes)
                + list(record.conflicts_with)
            ):
                connect(record.memory_id, memory_id)
            connect(record.memory_id, record.superseded_by)
        return by_id, graph

    def _create_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        now: datetime,
        *,
        source_memory_id: str | None = None,
    ):
        edge = MemoryEdge(
            edge_id=f"edge_{uuid.uuid4().hex}",
            source_id=source_id,
            relation=relation,
            target_id=target_id,
            confidence=1.0,
            valid_from=now,
            source_memory_id=source_memory_id or source_id,
        )
        self.store.upsert_edge(edge)
        return edge

    def _union_evidence_refs(self, records: list[MemoryRecord], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in [*current, *[ref for record in records for ref in record.evidence_refs]]:
            key = str(payload)
            if key in seen:
                continue
            seen.add(key)
            rows.append(payload)
        return rows

    def _normalize_coverage_proof(self, proof: Any, source_ids: list[str]) -> dict[str, Any] | None:
        if not isinstance(proof, dict):
            return None
        if proof.get("status") != "provided":
            return None
        proof_sources = list(proof.get("sources") or source_ids)
        if sorted(proof_sources) != sorted(source_ids):
            return None
        return {**proof, "sources": list(source_ids)}

    def _candidate_observation_times(self, record: MemoryRecord) -> list[datetime]:
        raw_timestamps = list(record.structured_payload.get("observation_timestamps", []))
        timestamps: list[datetime] = []
        for raw in raw_timestamps:
            if isinstance(raw, str):
                timestamps.append(datetime.fromisoformat(raw))
            elif isinstance(raw, datetime):
                timestamps.append(raw)
        if timestamps:
            return sorted(timestamps)
        base = record.observed_at or record.created_at
        return [base for _ in range(max(0, record.positive_hits))]

    def _record_candidate_observation(self, record: MemoryRecord, now: datetime):
        timestamps = [dt.isoformat() for dt in self._candidate_observation_times(record)]
        timestamps.append(now.isoformat())
        record.structured_payload = {
            **record.structured_payload,
            "observation_timestamps": sorted(timestamps),
        }

    def _has_recent_support(self, record: MemoryRecord, *, min_hits: int, window_days: int) -> bool:
        timestamps = self._candidate_observation_times(record)
        if len(timestamps) < min_hits:
            return False
        recent = timestamps[-min_hits:]
        return (recent[-1] - recent[0]).total_seconds() <= window_days * 86400

    def _propagate_ripple(self, root_id: str, now: datetime):
        self._activation("ripple", event="feedback_ripple", root_id=root_id)
        if not self.config.get("use_ripple", True):
            return []
        by_id, graph = self._memory_graph()
        if root_id not in by_id:
            return []
        alpha_neg = float(self.config.get("alpha_neg", 0.20))
        affected: list[str] = []
        visited = {root_id}
        queue = deque([(root_id, 0)])
        while queue:
            current_id, distance = queue.popleft()
            if distance >= 2:
                continue
            for neighbor_id in sorted(graph.get(current_id, set())):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                next_distance = distance + 1
                if next_distance > 2:
                    continue
                penalty = 0.3 ** next_distance
                neighbor = by_id.get(neighbor_id)
                if not neighbor:
                    continue
                neighbor.ripple_penalty = round(neighbor.ripple_penalty + penalty, 6)
                neighbor.negative_hits += 1
                neighbor.confidence = max(0.01, neighbor.confidence - alpha_neg * penalty)
                if neighbor.status == "active" and neighbor.confidence < 0.45:
                    neighbor.status = "stale"
                neighbor.updated_at = now
                self.upsert(neighbor, event_type=f"ripple_{next_distance}")
                affected.append(neighbor.memory_id)
                queue.append((neighbor_id, next_distance))
        return affected

    def _retrieval_score(
        self,
        *,
        record: MemoryRecord,
        lexical_score: float,
        effective: float,
        worth: float,
        now: datetime,
        query: str,
    ) -> float:
        score_mode = self.config.get("score_mode", "ours")
        recency = 2 ** (
            -(
                (now - max(filter(None, [record.updated_at, record.observed_at, record.created_at]))).total_seconds()
                / 86400.0
            )
            / max(1, record.half_life_days)
        )
        importance = record.importance
        subject_match = 1.0 if record.subject and record.subject in query else 0.0
        if score_mode == "rag_only":
            return min(1.0, lexical_score + 0.2 * subject_match)
        if score_mode in {"ga_analog", "ga_inspired_heuristic"}:
            return min(1.0, (lexical_score + recency + importance + subject_match) / 4)
        if score_mode == "large_context":
            return min(1.0, lexical_score + 0.2 * subject_match)
        if score_mode == "source_prior":
            return min(1.0, 0.4 * lexical_score + 0.4 * record.confidence + 0.2 * subject_match)
        if score_mode == "structured_static":
            return min(1.0, 0.4 * lexical_score + 0.2 * record.confidence + 0.2 * importance + 0.2 * subject_match)
        return min(1.0, (
            0.30 * lexical_score
            + 0.20 * (1.0 if record.entity_id else 0.5)
            + 0.20 * effective
            + 0.10 * recency
            + 0.10 * importance
            + 0.10 * worth
            + 0.10 * subject_match
        ))

    def mark_outcome(self, event: UsageEvent):
        self._activation(
            "asym_feedback",
            event="mark_outcome",
            enabled=float(self.config.get("alpha_pos", 0.04)) != float(self.config.get("alpha_neg", 0.20)),
            contribution=event.contribution,
            outcome=event.outcome,
        )
        record = self.get(event.memory_id)
        if not record:
            return
        if event.contribution == "helpful" and event.outcome == "success":
            record.positive_hits += 1
            alpha_pos = float(self.config.get("alpha_pos", 0.04))
            record.confidence = min(0.99, record.confidence + alpha_pos * (1 - record.confidence))
            if record.status == "stale" and event.used_stage in {"planning", "execution", "verification"}:
                record.status = "active"
                record.layer = "active"
        elif event.contribution == "misleading" and event.outcome == "failure":
            record.negative_hits += 1
            alpha_neg = float(self.config.get("alpha_neg", 0.20))
            record.confidence = max(0.01, record.confidence - alpha_neg)
            record.ripple_penalty = round(record.ripple_penalty + 1.0, 6)
            if record.status == "active":
                record.status = "stale"
        record.updated_at = event.timestamp
        self.upsert(record, event_type="usage_outcome")
        if event.contribution == "misleading" and event.outcome == "failure":
            self._propagate_ripple(record.memory_id, event.timestamp)

    def maintenance(self, now: datetime):
        self._activation("governance", event="maintenance_governance", enabled=self.config.get("use_governance", True))
        self._activation("lifecycle", event="maintenance_lifecycle", enabled=self.config.get("use_lifecycle", True))
        changed = []
        expired: list[str] = []
        stale: list[str] = []
        archived: list[str] = []
        resampled: list[str] = []
        rollback_merge_ids: list[str] = []
        needs_review_ids: list[str] = []
        deleted_by_capacity_ids: list[str] = []
        if not self.config.get("use_lifecycle", True):
            return {
                "changed_memory_ids": changed,
                "expired_memory_ids": expired,
                "stale_memory_ids": stale,
                "archived_memory_ids": archived,
                "resampled_memory_ids": resampled,
                "rollback_merge_ids": rollback_merge_ids,
                "needs_review_ids": needs_review_ids,
                "deleted_by_capacity_ids": deleted_by_capacity_ids,
            }
        for record in self.store.list():
            before_payload = record.model_dump(mode="json")
            before_status = record.status
            refresh_status(record, now)
            self._promote_candidates(record)
            self._apply_dead_memory_policy(record, now)
            if record.sensitive and record.status == "archived":
                record.needs_review = True
                needs_review_ids.append(record.memory_id)
            if record.merged_from and record.coverage_proof is None:
                rollback_merge_ids.append(record.memory_id)
                for source_id in record.merged_from:
                    source = self.get(source_id)
                    if source:
                        source.status = "active"
                        source.layer = "active"
                        source.superseded_by = None
                        source.updated_at = now
                        self.upsert(source, event_type="merge_rollback_source")
                record.status = "deleted"
                record.layer = "archived"
                record.updated_at = now
            if not self.config.get("use_governance", True) and record.status in {"archived"}:
                record.status = before_status
            after = record.model_dump(mode="json")
            if after != before_payload:
                record.updated_at = now
                self.upsert(record, event_type="maintenance_status")
                changed.append(record.memory_id)
                if record.status == "expired":
                    expired.append(record.memory_id)
                if record.status == "stale":
                    stale.append(record.memory_id)
                if record.status == "archived":
                    archived.append(record.memory_id)
                if record.resampled:
                    resampled.append(record.memory_id)
        self._apply_capacity_governance(now, deleted_by_capacity_ids)
        return {
            "changed_memory_ids": changed,
            "expired_memory_ids": expired,
            "stale_memory_ids": stale,
            "archived_memory_ids": archived,
            "resampled_memory_ids": resampled,
            "rollback_merge_ids": rollback_merge_ids,
            "needs_review_ids": needs_review_ids,
            "deleted_by_capacity_ids": deleted_by_capacity_ids,
        }

    def _promote_candidates(self, record: MemoryRecord):
        if record.status != "candidate":
            return
        if record.memory_type in {"alias", "routine"} and record.source in {"user_explicit", "user_correction"}:
            record.status = "active"
            record.layer = "active"
            record.confidence = max(record.confidence, source_confidence(record.source))
            return
        if record.memory_type == "reflection" and record.source == "execution_verification":
            record.status = "active"
            record.layer = "active"
            record.confidence = max(record.confidence, 0.70)
            return
        if record.memory_type == "habit":
            if record.negative_hits == 0 and self._has_recent_support(record, min_hits=3, window_days=7):
                record.status = "active"
                record.layer = "active"
                record.confidence = max(record.confidence, 0.80)
            return
        if record.memory_type == "preference":
            confirmed = record.source in {"user_explicit", "user_correction"} or bool(
                record.structured_payload.get("confirmed_by_user", False)
            )
            if confirmed or (record.negative_hits == 0 and self._has_recent_support(record, min_hits=3, window_days=7)):
                record.status = "active"
                record.layer = "active"
                record.confidence = max(record.confidence, 0.80 if not confirmed else source_confidence(record.source))

    def _apply_dead_memory_policy(self, record: MemoryRecord, now: datetime):
        if not self.config.get("use_governance", True):
            return
        if record.status not in {"active", "stale"}:
            return
        age = (now - max(filter(None, [record.updated_at, record.observed_at, record.created_at]))).total_seconds() / 86400.0
        is_dead = (
            (record.access_count == 0 and age > record.half_life_days)
            or (record.last_accessed_at is None and age > 2 * record.half_life_days)
        )
        if not is_dead:
            return
        text = record.natural_text.strip()
        fuzzy = any(token in text for token in ["那个", "这个", "那盏", "这盏"]) or len(text) <= 6
        if fuzzy and self.config.get("use_resampling", True):
            original_text = record.natural_text
            resampled_text = (
                record.structured_payload.get("resampled_text")
                or f"{record.subject} {record.predicate} {record.object}"
            )
            record.natural_text = resampled_text
            record.structured_payload = {
                **record.structured_payload,
                "resampled_from": original_text,
                "resampled_at": now.isoformat(),
                "resampled_text": resampled_text,
            }
            record.resampled = True
            record.last_accessed_at = now
            record.updated_at = now
            record.status = "active"
            record.layer = "active"
        else:
            if not self.config.get("use_content_aging", True):
                return
            if record.status == "active":
                record.status = "stale"
            archive_threshold = record.half_life_days
            if record.memory_type in {"habit", "preference", "routine", "reflection"}:
                archive_threshold = record.half_life_days * 2 + 1
            if record.access_count == 0 and age > archive_threshold:
                record.status = "archived"
                record.layer = "archived"
            elif record.memory_type not in {"habit", "preference", "routine", "reflection"} and (
                memory_worth(record) < 0.2 or effective_confidence(record, now) < 0.3
            ):
                record.status = "archived"
                record.layer = "archived"
            else:
                record.layer = "dormant"

    def _apply_capacity_governance(self, now: datetime, deleted_by_capacity_ids: list[str]):
        records = self.store.list()
        active = [record for record in records if record.layer == "active" and record.status == "active"]
        dormant = [record for record in records if record.layer == "dormant"]
        archived = [record for record in records if record.layer == "archived" and record.status == "archived"]

        def prune(records_to_prune: list[MemoryRecord], limit: int):
            if len(records_to_prune) <= limit:
                return
            ranked = sorted(
                records_to_prune,
                key=lambda record: (
                    memory_worth(record),
                    effective_confidence(record, now),
                    record.updated_at,
                ),
            )
            for record in ranked[: len(records_to_prune) - limit]:
                if record.sensitive:
                    record.needs_review = True
                    self.upsert(record, event_type="capacity_needs_review")
                else:
                    record.status = "deleted"
                    record.layer = "archived"
                    record.updated_at = now
                    self.upsert(record, event_type="capacity_delete")
                    self.store.delete_index_record(record.memory_id)
                    deleted_by_capacity_ids.append(record.memory_id)

        prune(active, int(self.config.get("active_target_limit", 500)))
        prune(dormant, int(self.config.get("dormant_target_limit", 300)))
        prune(archived, int(self.config.get("archived_target_limit", 200)))

    def _record_from_op(self, op: dict[str, Any], now: datetime, *, active: bool):
        payload = dict(op)
        memory_type = payload["memory_type"]
        valid_from = payload.get("valid_from", now)
        valid_until = payload.get("valid_until")
        expires_at = payload.get("expires_at")
        created_at = payload.get("created_at", now)
        updated_at = payload.get("updated_at", now)

        for key, value in {
            "valid_from": valid_from,
            "valid_until": valid_until,
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": updated_at,
        }.items():
            if isinstance(value, str):
                parsed = datetime.fromisoformat(value)
                if key == "valid_from":
                    valid_from = parsed
                elif key == "valid_until":
                    valid_until = parsed
                elif key == "expires_at":
                    expires_at = parsed
                elif key == "created_at":
                    created_at = parsed
                elif key == "updated_at":
                    updated_at = parsed
        return MemoryRecord(
            memory_id=payload.get("memory_id", f"mem_{int(now.timestamp() * 1_000_000_000)}"),
            scope=payload.get("scope", "entity"),
            device_id=payload.get("device_id"),
            entity_id=payload.get("entity_id", payload.get("object")),
            room_id=payload.get("room_id"),
            user_id=payload.get("user_id"),
            memory_type=memory_type,
            subject=payload["subject"],
            predicate=payload.get("predicate", "refers_to"),
            object=payload["object"],
            condition=payload.get("condition"),
            action=payload.get("action"),
            natural_text=payload.get(
                "natural_text",
                f"{payload['subject']} {payload.get('predicate', 'refers_to')} {payload['object']}",
            ),
            structured_payload=payload.get("structured_payload", {}),
            source=payload.get("source", "user_explicit"),
            evidence_refs=payload.get("evidence_refs", []),
            source_turn_id=payload.get("source_turn_id"),
            source_trace_id=payload.get("source_trace_id"),
            confidence=payload.get("confidence", source_confidence(payload.get("source", "user_explicit"))),
            source_authority=payload.get("source_authority", source_confidence(payload.get("source", "user_explicit"))),
            importance=payload.get("importance", 0.5),
            positive_hits=payload.get("positive_hits", 0),
            negative_hits=payload.get("negative_hits", 0),
            ripple_penalty=payload.get("ripple_penalty", 0.0),
            created_at=created_at,
            updated_at=updated_at,
            last_accessed_at=payload.get("last_accessed_at"),
            observed_at=payload.get("observed_at"),
            valid_from=valid_from,
            valid_until=valid_until,
            expires_at=expires_at,
            half_life_days=payload.get("half_life_days", 180),
            status="active" if active else "candidate",
            layer=payload.get("layer", "active" if active else "active"),
            supersedes=payload.get("supersedes", []),
            superseded_by=payload.get("superseded_by"),
            conflicts_with=payload.get("conflicts_with", []),
            merged_from=payload.get("merged_from", []),
            coverage_proof=payload.get("coverage_proof"),
            related_memory_ids=payload.get("related_memory_ids", []),
            depends_on_memory_ids=payload.get("depends_on_memory_ids", []),
            derived_from_memory_ids=payload.get("derived_from_memory_ids", []),
            needs_review=payload.get("needs_review", False),
            access_count=payload.get("access_count", 0),
            update_count=payload.get("update_count", 0),
            last_used_task_id=payload.get("last_used_task_id"),
            resampled=payload.get("resampled", False),
            sensitive=payload.get("sensitive", False),
        )
