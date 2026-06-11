from __future__ import annotations

import importlib


memory_mod = importlib.import_module("try.memory")
schemas_mod = importlib.import_module("try.memory.schemas")

create_memory_service = memory_mod.create_memory_service
ExtractedMemoryCandidate = schemas_mod.ExtractedMemoryCandidate


class DummyFetcher:
    def get_all_devices(self):
        return [
            {
                "device_id": "device.study_lamp",
                "name": "书房台灯",
                "area_id": "room.study",
                "entities": ["light.study_lamp"],
            },
            {
                "device_id": "device.living_light",
                "name": "客厅灯",
                "area_id": "room.living_room",
                "entities": ["light.living_room_main"],
            },
        ]

    def get_all_entities(self):
        return [
            {
                "entity_id": "light.study_lamp",
                "name": "书房台灯实体",
                "device_id": "device.study_lamp",
                "domain": "light",
            },
            {
                "entity_id": "light.living_room_main",
                "name": "客厅灯实体",
                "device_id": "device.living_light",
                "domain": "light",
            },
        ]

    def get_all_services(self):
        return [{"domain": "light", "services": {"turn_on": {}, "turn_off": {}}}]


def test_sync_ha_facts_and_search(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")
    summary = service.sync_ha_facts(DummyFetcher())
    assert summary["device_records"] > 0

    result = service.memory_search("书房台灯", include_stale=True)
    assert any(item.device_id == "device.study_lamp" for item in result.candidate_devices)


def test_alias_turn_binds_to_task_candidate(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")
    service.sync_ha_facts(DummyFetcher())

    records = service.memory_ingest_turn(
        "task_alias",
        "以后我把书房台灯叫做小书灯",
        task_candidates=[{"device_id": "device.study_lamp", "device_name": "书房台灯", "device_reason": "位于书房"}],
    )

    assert records
    alias_record = records[0]
    assert alias_record.device_id == "device.study_lamp"
    result = service.memory_search("小书灯", include_stale=True)
    assert any(item.device_id == "device.study_lamp" for item in result.candidate_devices)


def test_location_revision_supersedes_old_record(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")

    first = service.ingest_candidate(
        ExtractedMemoryCandidate(
            memory_type="location",
            scope_hint="device",
            subject_text="台灯",
            predicate="located_in",
            object_text="书房",
            room_text="书房",
            source="user_explicit",
            operation_hint="add_active",
            natural_text="台灯位于书房",
        ),
        task_candidates=[{"device_id": "device.study_lamp", "device_name": "台灯", "device_reason": "位于书房"}],
    )
    second = service.ingest_candidate(
        ExtractedMemoryCandidate(
            memory_type="location",
            scope_hint="device",
            subject_text="台灯",
            predicate="located_in",
            object_text="客厅",
            room_text="客厅",
            source="user_explicit",
            operation_hint="add_active",
            natural_text="台灯位于客厅",
        ),
        task_candidates=[{"device_id": "device.study_lamp", "device_name": "台灯", "device_reason": "已经移到客厅"}],
    )

    refreshed_first = service.memory_get(first.memory_id)
    assert refreshed_first is not None
    assert refreshed_first.status == "superseded"
    assert refreshed_first.superseded_by == second.memory_id


def test_home_level_preference_downgrade_without_device_binding(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")
    records = service.memory_ingest_turn("task_sleep", "睡觉时，关闭所有灯")

    assert records
    assert records[0].scope == "home"
    assert records[0].status == "active"


def test_broad_narrow_records_do_not_merge(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")
    left = service.ingest_candidate(
        ExtractedMemoryCandidate(
            memory_type="routine",
            scope_hint="home",
            subject_text="灯光场景",
            predicate="contains",
            object_text="客厅灯",
            source="user_explicit",
            operation_hint="add_active",
            natural_text="灯光场景包含客厅灯",
        )
    )
    right = service.ingest_candidate(
        ExtractedMemoryCandidate(
            memory_type="routine",
            scope_hint="home",
            subject_text="灯光场景",
            predicate="contains",
            object_text="客厅顶灯",
            source="user_explicit",
            operation_hint="add_active",
            natural_text="灯光场景包含客厅顶灯",
        )
    )

    assert left.memory_id != right.memory_id
    assert service.memory_get(left.memory_id) is not None
    assert service.memory_get(right.memory_id) is not None


def test_finalize_task_failure_creates_reflection(tmp_path):
    service = create_memory_service(tmp_path / "memory.sqlite3")
    service.memory_finalize_task(
        "task_failure",
        "failure",
        {"used_memory_ids": [], "stage_by_memory": {}, "note": "选错了设备，导致执行失败"},
    )

    reflections = service.list_records(memory_types=["reflection"])
    assert any("选错了设备" in item.natural_text for item in reflections)

