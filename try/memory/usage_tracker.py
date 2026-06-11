from __future__ import annotations

from .confidence import apply_usage_feedback, now_utc
from .schemas import MemoryUsageEvent
from .sqlite_store import SqliteMemoryStore


class MemoryUsageTracker:
    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def mark_used(self, task_id: str, memory_id: str, stage: str) -> None:
        now = now_utc()
        self.store.touch_access(memory_id, task_id, now)
        self.store.add_event("memory_used", memory_id=memory_id, task_id=task_id, payload={"stage": stage}, created_at=now)

    def mark_outcome(self, event: MemoryUsageEvent) -> None:
        record = self.store.get_record(event.memory_id)
        if record is None:
            return
        now = now_utc()
        apply_usage_feedback(record, event.contribution, event.outcome, now=now)
        self.store.upsert_record(record)
        self.store.add_usage_event(event, created_at=now)
        self.store.add_event(
            "memory_outcome",
            memory_id=event.memory_id,
            task_id=event.task_id,
            payload=event.model_dump(mode="json"),
            created_at=now,
        )

