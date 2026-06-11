from __future__ import annotations

from .confidence import compute_effective_confidence, now_utc
from .sqlite_store import SqliteMemoryStore


class MemoryMaintenance:
    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def run(self) -> dict[str, int]:
        now = now_utc()
        stale_count = 0
        archived_count = 0
        expired_count = 0
        conflict_count = 0
        records = self.store.list_records()
        for record in records:
            if record.status in {"deleted", "superseded"}:
                continue
            if record.valid_until and record.valid_until <= now and record.status != "expired":
                record.status = "expired"
                record.updated_at = now
                self.store.upsert_record(record)
                expired_count += 1
                continue
            effective = compute_effective_confidence(record, now=now)
            if record.status == "active" and effective < 0.45:
                record.status = "stale"
                record.updated_at = now
                self.store.upsert_record(record)
                stale_count += 1
            elif record.status == "stale" and effective >= 0.55:
                record.status = "active"
                record.updated_at = now
                self.store.upsert_record(record)
            if record.status == "candidate":
                age_days = max((now - record.updated_at).total_seconds(), 0.0) / 86400.0
                if age_days > 30:
                    record.status = "archived"
                    record.updated_at = now
                    self.store.upsert_record(record)
                    archived_count += 1
            if record.status == "stale":
                age_days = max((now - (record.last_accessed_at or record.updated_at)).total_seconds(), 0.0) / 86400.0
                if age_days > 2 * max(record.half_life_days, 1):
                    record.status = "archived"
                    record.updated_at = now
                    self.store.upsert_record(record)
                    archived_count += 1

        signatures: dict[tuple[str, str, str], list] = {}
        for record in self.store.list_records(statuses=["active"]):
            signatures.setdefault((record.scope, record.subject, record.predicate), []).append(record)
        for items in signatures.values():
            if len(items) < 2:
                continue
            normalized_objects = {item.object for item in items}
            if len(normalized_objects) <= 1:
                continue
            for item in items:
                if item.status != "conflicted":
                    item.status = "conflicted"
                    item.updated_at = now
                    item.conflicts_with = sorted({*item.conflicts_with, *(other.memory_id for other in items if other.memory_id != item.memory_id)})
                    self.store.upsert_record(item)
                    conflict_count += 1

        return {
            "stale": stale_count,
            "archived": archived_count,
            "expired": expired_count,
            "conflicted": conflict_count,
        }

