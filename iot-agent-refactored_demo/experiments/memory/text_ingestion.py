from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from experiments.memory.schemas import MemoryRecord
from experiments.memory.service import MemoryService


_TEMPERATURE = re.compile(r"(?:喜欢|改成|调整为|设为).*?(\d{2})\s*度")
_ROOM = re.compile(r"(卧室|客厅|书房).*?空调")


def ingest_user_text(service: MemoryService, *, text: str, now: datetime, turn_id: str) -> dict[str, Any]:
    """Ingest a raw user utterance without scenario-owned memory operations."""
    if any(marker in text for marker in ("不喜欢", "不要", "别再")):
        return {"accepted": False, "reason": "negated_preference_requires_clarification", "text": text}
    temperature = _TEMPERATURE.search(text)
    room = _ROOM.search(text)
    if not temperature or not room:
        return {"accepted": False, "reason": "unsupported_text_pattern", "text": text}
    room_name = room.group(1)
    entity_id = {"卧室": "climate.bedroom_ac", "客厅": "climate.living_room_ac"}.get(room_name)
    if entity_id is None:
        return {"accepted": False, "reason": "unmapped_room", "text": text}
    value = temperature.group(1)
    previous = next(
        (
            record
            for record in service.list_records(include_deleted=True)
            if record.entity_id == entity_id
            and record.predicate == "preferred_temperature"
            and record.status not in {"superseded", "deleted"}
        ),
        None,
    )
    record = MemoryRecord(
        memory_id=f"ingested_{entity_id.replace('.', '_')}_{turn_id}",
        scope="entity", entity_id=entity_id, memory_type="preference",
        subject=f"{room_name}空调喜欢的温度", predicate="preferred_temperature", object=value,
        natural_text=text, source="user_correction" if previous else "user_explicit",
        source_turn_id=turn_id, confidence=0.95 if previous else 0.9,
        source_authority=0.95 if previous else 0.9, half_life_days=180,
        created_at=now, updated_at=now, observed_at=now, status="active", layer="active",
        structured_payload={"ingestion": "raw_user_text", "raw_text": text},
    )
    if previous:
        previous.status = "superseded"
        previous.layer = "archived"
        previous.superseded_by = record.memory_id
        previous.updated_at = now
        service.upsert(previous, event_type="text_ingestion_supersede")
    service.upsert(record, event_type="text_ingestion")
    return {
        "accepted": True, "memory_id": record.memory_id, "replaced_memory_id": previous.memory_id if previous else None,
        "entity_id": entity_id, "value": value, "source_text": text,
    }
