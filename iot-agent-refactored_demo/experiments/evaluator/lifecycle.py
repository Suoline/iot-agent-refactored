from __future__ import annotations

from datetime import datetime
from typing import Any


INACTIVE_STATUSES = {"deleted", "expired", "superseded", "conflicted", "archived"}


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def evaluator_status_for_record(record: dict[str, Any], now: datetime) -> str:
    """Resolve lifecycle truth from frozen record facts, never runtime status.

    Scenario fixtures may declare evaluator_status/evaluator_superseded_by in
    structured_payload. They are hidden evaluator labels, not system output.
    """
    payload = record.get("structured_payload") or {}
    explicit = payload.get("evaluator_status")
    if explicit:
        return str(explicit)
    if payload.get("evaluator_superseded_by"):
        return "superseded"
    valid_until = _parse_time(record.get("valid_until"))
    expires_at = _parse_time(record.get("expires_at"))
    if valid_until is not None and now > valid_until:
        return "expired"
    if expires_at is not None and now > expires_at:
        return "expired"
    # Candidate is an observable prediction of the system. It is not used as
    # truth because a baseline may not implement candidate storage at all.
    return "active"
