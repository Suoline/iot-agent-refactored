from __future__ import annotations

from datetime import datetime
from typing import Optional

from .confidence import effective_confidence


def refresh_status(record, now: datetime):
    if record.status == "deleted":
        return record
    if record.valid_until and now >= record.valid_until:
        record.status = "expired"
        record.layer = "archived"
        return record
    if record.expires_at and now >= record.expires_at:
        record.status = "expired"
        record.layer = "archived"
        return record
    if record.status == "active" and effective_confidence(record, now) < 0.45:
        record.status = "stale"
    if record.status == "stale" and effective_confidence(record, now) < 0.20:
        record.layer = "dormant"
    return record


def is_usable_stale(record, *, task_type: str, now: datetime) -> bool:
    return (
        stale_runtime_status(record, task_type=task_type, now=now) == "usable-stale"
        and task_type == "query"
    )


def stale_runtime_status(record, *, task_type: str, now: datetime) -> Optional[str]:
    if record.status != "stale":
        return None
    if effective_confidence(record, now) < 0.45:
        return None
    if task_type in {"query", "control", "safety", "automation"}:
        return "usable-stale"
    return None
