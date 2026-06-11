from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Protocol

from .confidence import get_default_half_life, get_source_authority, now_utc
from .schemas import EvidenceRef, MemoryRecord
from .sqlite_store import SqliteMemoryStore


class HAFetcher(Protocol):
    def get_all_devices(self) -> list[dict[str, Any]] | str | None:
        ...

    def get_all_entities(self) -> list[dict[str, Any]] | str | None:
        ...

    def get_all_services(self) -> list[dict[str, Any]] | str | None:
        ...


class HomeAssistantSync:
    def __init__(self, store: SqliteMemoryStore) -> None:
        self.store = store

    def sync(self, fetcher: HAFetcher) -> dict[str, int]:
        started_at = now_utc()
        devices = fetcher.get_all_devices()
        entities = fetcher.get_all_entities()
        services = fetcher.get_all_services()
        if not isinstance(devices, list) or not isinstance(entities, list) or not isinstance(services, list):
            return {"device_records": 0, "entity_records": 0, "service_records": 0}

        active_ids: set[str] = set()
        device_count = 0
        entity_count = 0
        service_count = 0
        for device in devices:
            records = self._build_device_records(device, started_at)
            for record in records:
                active_ids.add(record.memory_id)
                self.store.upsert_record(record)
                device_count += 1

        for entity in entities:
            records = self._build_entity_records(entity, started_at)
            for record in records:
                active_ids.add(record.memory_id)
                self.store.upsert_record(record)
                entity_count += 1

        for service in services:
            records = self._build_service_records(service, started_at)
            for record in records:
                active_ids.add(record.memory_id)
                self.store.upsert_record(record)
                service_count += 1

        for record in self.store.list_records(source="ha_registry"):
            if record.memory_id not in active_ids and record.status != "expired":
                record.status = "expired"
                record.updated_at = started_at
                self.store.upsert_record(record)

        finished_at = now_utc()
        summary = {"device_records": device_count, "entity_records": entity_count, "service_records": service_count}
        self.store.add_sync_run(started_at, finished_at, summary)
        return summary

    def _build_device_records(self, device: dict[str, Any], now: datetime) -> list[MemoryRecord]:
        device_id = device["device_id"]
        device_name = device.get("name") or device_id
        area_id = device.get("area_id")
        entities = device.get("entities", [])
        return [
            self._ha_record(
                fact_key=f"device_registry:{device_id}",
                scope="device",
                device_id=device_id,
                memory_type="capability",
                subject=device_name,
                predicate="device_registry_id",
                object_text=device_id,
                natural_text=f"设备{device_name}的device_id是{device_id}",
                payload={"kind": "device_registry", "device": device},
                now=now,
            ),
            self._ha_record(
                fact_key=f"device_entities:{device_id}",
                scope="device",
                device_id=device_id,
                memory_type="stable_state_fact",
                subject=device_name,
                predicate="has_entities",
                object_text=", ".join(entities),
                natural_text=f"设备{device_name}包含实体{', '.join(entities)}",
                payload={"kind": "device_entities", "device": device},
                now=now,
            ),
            self._ha_record(
                fact_key=f"device_area:{device_id}",
                scope="device",
                device_id=device_id,
                memory_type="location",
                subject=device_name,
                predicate="located_in",
                object_text=area_id or "unknown",
                natural_text=f"设备{device_name}的HA area_id为{area_id or 'unknown'}",
                payload={"kind": "device_area", "device": device},
                now=now,
            ),
        ]

    def _build_entity_records(self, entity: dict[str, Any], now: datetime) -> list[MemoryRecord]:
        entity_id = entity["entity_id"]
        entity_name = entity.get("name") or entity_id
        domain = entity.get("domain") or entity_id.split(".", 1)[0]
        device_id = entity.get("device_id")
        return [
            self._ha_record(
                fact_key=f"entity_domain:{entity_id}",
                scope="entity",
                device_id=device_id,
                entity_id=entity_id,
                memory_type="capability",
                subject=entity_name,
                predicate="entity_domain",
                object_text=domain,
                natural_text=f"实体{entity_name}属于{domain}域",
                payload={"kind": "entity_domain", "entity": entity},
                now=now,
            ),
            self._ha_record(
                fact_key=f"entity_binding:{entity_id}",
                scope="entity",
                device_id=device_id,
                entity_id=entity_id,
                memory_type="stable_state_fact",
                subject=entity_name,
                predicate="bound_to_device",
                object_text=device_id or "unknown",
                natural_text=f"实体{entity_name}绑定到设备{device_id or 'unknown'}",
                payload={"kind": "entity_binding", "entity": entity},
                now=now,
            ),
        ]

    def _build_service_records(self, service: dict[str, Any], now: datetime) -> list[MemoryRecord]:
        domain = service.get("domain") or "unknown"
        items = service.get("services", {})
        records: list[MemoryRecord] = []
        for service_name in items:
            records.append(
                self._ha_record(
                    fact_key=f"service:{domain}.{service_name}",
                    scope="home",
                    memory_type="capability",
                    subject=domain,
                    predicate="supports_service",
                    object_text=service_name,
                    natural_text=f"{domain}域支持服务{service_name}",
                    payload={"kind": "service", "service": service},
                    now=now,
                )
            )
        return records

    def _ha_record(
        self,
        *,
        fact_key: str,
        scope: str,
        memory_type: str,
        subject: str,
        predicate: str,
        object_text: str,
        natural_text: str,
        payload: dict[str, Any],
        now: datetime,
        device_id: str | None = None,
        entity_id: str | None = None,
    ) -> MemoryRecord:
        memory_id = "ha_" + hashlib.sha1(fact_key.encode("utf-8")).hexdigest()[:20]
        return MemoryRecord(
            memory_id=memory_id,
            scope=scope,  # type: ignore[arg-type]
            device_id=device_id,
            entity_id=entity_id,
            memory_type=memory_type,  # type: ignore[arg-type]
            subject=subject,
            predicate=predicate,
            object=object_text,
            natural_text=natural_text,
            structured_payload={"ha_fact_key": fact_key, **payload},
            source="ha_registry",
            evidence_refs=[EvidenceRef(ref_type="event", ref_id=fact_key, timestamp=now)],
            confidence=get_source_authority("ha_registry"),
            importance=0.5,
            half_life_days=get_default_half_life(memory_type),  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
            valid_from=now,
            status="active",
        )

