from __future__ import annotations

from datetime import datetime, timezone

SOURCE_CONFIDENCE = {
    "ha_registry": 0.95,
    "user_correction": 0.95,
    "user_explicit": 0.90,
    "execution_verification": 0.85,
    "ha_state_observation": 0.80,
    "user_behavior": 0.65,
    "llm_inference": 0.45,
    "imported_doc": 0.70,
}

HALF_LIFE_DAYS = {
    "capability": 180,
    "state": 180,
    "alias": 365,
    "location": 365,
    "layout_relation": 365,
    "preference": 180,
    "habit": 90,
    "routine": 180,
    "episode": 30,
    "reflection": 90,
}

TASK_THRESHOLDS = {
    "query": 0.45,
    "control": 0.70,
    "safety": 0.85,
    "automation": 0.85,
}


def source_confidence(source: str) -> float:
    return SOURCE_CONFIDENCE.get(source, 0.45)


def default_half_life(memory_type: str) -> int:
    return HALF_LIFE_DAYS.get(memory_type, 180)


def age_days(record, now: datetime) -> float:
    timestamps = [record.updated_at, record.observed_at, record.created_at]
    valid = [value for value in timestamps if value is not None]
    base = max(valid)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - base).total_seconds() / 86400.0)


def memory_worth(record) -> float:
    return (record.positive_hits + 1) / (
        record.positive_hits + record.negative_hits + 2
    )


def effective_confidence(record, now: datetime) -> float:
    age = age_days(record, now)
    half_life = max(1, record.half_life_days)
    decay = 2 ** (-age / half_life)
    hit_score = memory_worth(record)
    return record.confidence * decay * (0.7 + 0.3 * hit_score)


def update_after_outcome(record, *, helpful: bool, misleading: bool):
    if helpful:
        record.positive_hits += 1
        record.confidence = min(
            0.99, record.confidence + 0.04 * (1 - record.confidence)
        )
    elif misleading:
        record.negative_hits += 1
        record.confidence = max(0.01, record.confidence - 0.20)
        if record.status == "active":
            record.status = "stale"
    return record

