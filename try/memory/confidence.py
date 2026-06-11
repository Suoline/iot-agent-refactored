from __future__ import annotations

from datetime import datetime, timezone

from .schemas import MemoryRecord, MemorySource, MemoryType, TaskOutcome, UsageContribution


SOURCE_AUTHORITY: dict[MemorySource, float] = {
    "ha_registry": 0.95,
    "ha_state_observation": 0.80,
    "user_explicit": 0.90,
    "user_correction": 0.95,
    "user_behavior": 0.65,
    "execution_verification": 0.85,
    "llm_inference": 0.45,
    "imported_doc": 0.70,
}

DEFAULT_HALF_LIFE_DAYS: dict[MemoryType, int] = {
    "capability": 180,
    "alias": 365,
    "location": 365,
    "preference": 180,
    "habit": 90,
    "constraint": 180,
    "routine": 180,
    "episode": 30,
    "reflection": 90,
    "layout_relation": 365,
    "safety_rule": 365,
    "stable_state_fact": 180,
}

TASK_THRESHOLDS: dict[str, float] = {
    "query": 0.45,
    "control": 0.70,
    "safety": 0.85,
    "automation": 0.85,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_source_authority(source: MemorySource) -> float:
    return SOURCE_AUTHORITY[source]


def get_default_half_life(memory_type: MemoryType) -> int:
    return DEFAULT_HALF_LIFE_DAYS[memory_type]


def compute_memory_worth(record: MemoryRecord) -> float:
    return (record.positive_hits + 1) / (record.positive_hits + record.negative_hits + 2)


def compute_effective_confidence(record: MemoryRecord, now: datetime | None = None) -> float:
    if now is None:
        now = now_utc()
    anchor = record.updated_at or record.observed_at or record.created_at
    age_seconds = max((now - anchor).total_seconds(), 0.0)
    age_days = age_seconds / 86400.0
    decay = 2 ** (-age_days / max(record.half_life_days, 1))
    hit_score = compute_memory_worth(record)
    return max(0.0, min(1.0, record.confidence * decay * (0.7 + 0.3 * hit_score)))


def apply_usage_feedback(
    record: MemoryRecord,
    contribution: UsageContribution,
    outcome: TaskOutcome,
    now: datetime | None = None,
) -> MemoryRecord:
    if now is None:
        now = now_utc()
    if contribution == "helpful" and outcome in {"success", "partial_success"}:
        record.positive_hits += 1
        record.confidence = min(0.99, record.confidence + 0.04 * (1 - record.confidence))
    elif contribution == "misleading" or outcome == "failure":
        record.negative_hits += 1
        record.confidence = max(0.01, record.confidence - 0.20)
        if record.status == "active":
            record.status = "stale"
    record.updated_at = now
    record.update_count += 1
    return record

