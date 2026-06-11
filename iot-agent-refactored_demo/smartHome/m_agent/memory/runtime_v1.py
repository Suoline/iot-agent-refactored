from __future__ import annotations

import importlib
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try_memory = importlib.import_module("try.memory")
try_memory_confidence = importlib.import_module("try.memory.confidence")

from smartHome.m_agent.memory.fake_api_tool import fake_api_func

MemoryRecord = try_memory.MemoryRecord
MemoryService = try_memory.MemoryService
create_memory_service = try_memory.create_memory_service
SearchResultPackage = try_memory.SearchResultPackage
EvidenceRef = try_memory.EvidenceRef

get_default_half_life = try_memory_confidence.get_default_half_life
get_source_authority = try_memory_confidence.get_source_authority

MEMORY_DB_PATH = os.path.join(REPO_ROOT, "try", "memory", "runtime", "memory_v1.sqlite3")


def _call_tool(tool_func, *args, **kwargs):
    return tool_func.func(*args, **kwargs) if hasattr(tool_func, "func") else tool_func(*args, **kwargs)


class DemoHAFetcher:
    def get_all_devices(self):
        return _call_tool(fake_api_func.tool_get_all_devices)

    def get_all_entities(self):
        return _call_tool(fake_api_func.tool_get_all_entities)

    def get_all_services(self):
        return fake_api_func._request_json("GET", "/api/services")


@dataclass
class DemoTaskContext:
    task_id: str
    task: str
    stage: str = "unknown"
    used_memory_ids: set[str] = field(default_factory=set)
    stage_by_memory: dict[str, str] = field(default_factory=dict)


class DemoMemoryRuntime:
    def __init__(self, db_path: str | None = None) -> None:
        self.service: MemoryService = create_memory_service(db_path or MEMORY_DB_PATH)
        self.fetcher = DemoHAFetcher()
        self._seeded = False
        self.current_task: DemoTaskContext | None = None

    def ensure_ready(self) -> None:
        self._safe_sync_ha()
        self._seed_demo_memories()

    def start_task(self, task: str) -> str:
        self.ensure_ready()
        self.service.memory_maintenance()
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        self.current_task = DemoTaskContext(task_id=task_id, task=task)
        self.service.memory_ingest_turn(task_id, task, source="user_explicit")
        return task_id

    def finish_task(self, output: str, *, success: bool | None = None) -> None:
        if self.current_task is None:
            return
        task = self.current_task
        outcome = self._infer_outcome(output, success=success)
        self.service.memory_finalize_task(
            task.task_id,
            outcome,
            {
                "used_memory_ids": sorted(task.used_memory_ids),
                "stage_by_memory": task.stage_by_memory,
                "helpful_memory_ids": sorted(task.used_memory_ids),
                "note": output[:500],
            },
        )
        self.service.memory_maintenance()
        self.current_task = None

    def set_stage(self, stage: str) -> None:
        if self.current_task is not None:
            self.current_task.stage = stage

    def clear_stage(self) -> None:
        if self.current_task is not None:
            self.current_task.stage = "unknown"

    def search(self, query: str) -> SearchResultPackage:
        self.ensure_ready()
        result = self.service.memory_search(query, include_stale=True)
        if self.current_task is not None:
            for item in result.matched_memories:
                self.current_task.used_memory_ids.add(item.memory_id)
                self.current_task.stage_by_memory.setdefault(item.memory_id, self.current_task.stage)
                self.service.memory_mark_used(self.current_task.task_id, item.memory_id, self.current_task.stage)
        return result

    def format_search_result(self, query: str, result: SearchResultPackage) -> str:
        if not result.matched_memories:
            return f"未找到与“{query}”直接相关的长期记忆。"

        lines = [f"与“{query}”相关的长期记忆如下："]
        if result.candidate_devices:
            lines.append("候选设备线索：")
            for device in result.candidate_devices[:5]:
                lines.append(
                    f"- {device.name}（device_id: {device.device_id}，score={device.score:.2f}，confidence={device.confidence:.2f}）"
                )
                for memory in device.matched_memories[:2]:
                    lines.append(f"  依据：{memory.text}")
        if result.global_constraints:
            lines.append("全局/房间级记忆：")
            for item in result.global_constraints[:5]:
                lines.append(f"- [{item.memory_type}] {item.text}（confidence={item.confidence:.2f}）")
        if result.should_ask_user:
            lines.append(f"需要澄清：{result.ask_reason}")
        else:
            lines.append("当前没有高优先级澄清需求。")
        return "\n".join(lines)

    def _safe_sync_ha(self) -> None:
        try:
            self.service.sync_ha_facts(self.fetcher)
        except Exception:
            pass

    def _seed_demo_memories(self) -> None:
        if self._seeded:
            return
        existing = self.service.list_records(source="user_explicit")
        if existing:
            self._seeded = True
            return
        now = datetime.now(timezone.utc)
        for record in self._build_seed_records(now):
            self.service.upsert_memory_record(record)
        self._seeded = True

    def _build_seed_records(self, now: datetime) -> list[MemoryRecord]:
        def record(
            *,
            memory_id: str,
            scope: str,
            memory_type: str,
            subject: str,
            predicate: str,
            object_text: str,
            natural_text: str,
            device_id: str | None = None,
            room_id: str | None = None,
            condition: str | None = None,
            action: str | None = None,
            payload: dict[str, Any] | None = None,
        ) -> MemoryRecord:
            return MemoryRecord(
                memory_id=memory_id,
                scope=scope,  # type: ignore[arg-type]
                device_id=device_id,
                room_id=room_id,
                memory_type=memory_type,  # type: ignore[arg-type]
                subject=subject,
                predicate=predicate,
                object=object_text,
                condition=condition,
                action=action,
                natural_text=natural_text,
                structured_payload=payload or {},
                source="user_explicit",
                evidence_refs=[EvidenceRef(ref_type="doc", ref_id="demo_seed", timestamp=now)],
                confidence=get_source_authority("user_explicit"),
                importance=0.6,
                half_life_days=get_default_half_life(memory_type),  # type: ignore[arg-type]
                created_at=now,
                updated_at=now,
                valid_from=now,
                status="active",
            )

        return [
            record(
                memory_id="demo_alias_little_book_light",
                scope="device",
                device_id="e2bf03e9b274e88f9e7b6852d1e2c90d",
                room_id="room.study",
                memory_type="alias",
                subject="书房台灯",
                predicate="alias_of",
                object_text="小书灯",
                natural_text="用户把书房台灯称为小书灯，默认指代书房台灯设备。",
            ),
            record(
                memory_id="demo_location_bedroom_light",
                scope="device",
                device_id="164c1a92b8ce9cda0e2a8c13440b4722",
                room_id="room.bedroom",
                memory_type="location",
                subject="卧室灯",
                predicate="located_in",
                object_text="卧室",
                natural_text="卧室灯位于卧室，对应主灯设备。",
            ),
            record(
                memory_id="demo_location_study_light",
                scope="device",
                device_id="c86e3c14d0egbfc02g4cae35662d6944",
                room_id="room.study",
                memory_type="location",
                subject="书房灯",
                predicate="located_in",
                object_text="书房",
                natural_text="书房灯位于书房，对应书房主灯设备。",
            ),
            record(
                memory_id="demo_location_living_light",
                scope="device",
                device_id="b75d2b03c9dfaebf1f3b9d24551c5833",
                room_id="room.living_room",
                memory_type="location",
                subject="客厅灯",
                predicate="located_in",
                object_text="客厅",
                natural_text="客厅灯位于客厅，对应客厅主灯设备。",
            ),
            record(
                memory_id="demo_location_bedside_lamp",
                scope="device",
                device_id="31ae92d8a163d77f8d6a5741c0d1b89c",
                room_id="room.bedroom",
                memory_type="location",
                subject="卧室床边灯",
                predicate="located_in",
                object_text="卧室",
                natural_text="卧室床边灯位于卧室，对应床边台灯设备。",
            ),
            record(
                memory_id="demo_location_study_lamp",
                scope="device",
                device_id="e2bf03e9b274e88f9e7b6852d1e2c90d",
                room_id="room.study",
                memory_type="location",
                subject="书房台灯",
                predicate="located_in",
                object_text="书房",
                natural_text="书房台灯位于书房，是阅读时优先使用的台灯。",
            ),
            record(
                memory_id="demo_location_front_door_sensor",
                scope="device",
                device_id="cf03cb835279ea4876ab6ee202aa9832",
                room_id="room.entry",
                memory_type="location",
                subject="进家门门窗传感器",
                predicate="located_in",
                object_text="入户门",
                natural_text="c开头的门窗传感器安装在进家门上。",
            ),
            record(
                memory_id="demo_location_window_sensor",
                scope="device",
                device_id="d914ad946380fb5987bc7ff313bb0a45",
                room_id="room.living_room",
                memory_type="location",
                subject="客厅窗户传感器",
                predicate="located_in",
                object_text="客厅窗户",
                natural_text="d开头的门窗传感器安装在客厅窗户上。",
            ),
            record(
                memory_id="demo_music_preference",
                scope="home",
                memory_type="preference",
                subject="用户",
                predicate="prefers_music",
                object_text="周杰伦，默认音量10",
                natural_text="用户平时喜欢听周杰伦，默认音量通常调到10。",
            ),
            record(
                memory_id="demo_sleep_preference",
                scope="home",
                memory_type="preference",
                subject="用户",
                predicate="prefers",
                object_text="关闭所有灯",
                condition="睡觉时",
                natural_text="用户睡觉时不想要任何灯光，默认关闭所有灯。",
            ),
            record(
                memory_id="demo_call_preference",
                scope="home",
                memory_type="preference",
                subject="用户",
                predicate="prefers",
                object_text="音箱静音",
                condition="接电话时",
                natural_text="用户接电话时，希望音箱静音。",
            ),
            record(
                memory_id="demo_humidifier_constraint",
                scope="device",
                device_id="df406d66e297203b9cbccd7f7b2b0376",
                memory_type="constraint",
                subject="加湿器插座",
                predicate="must_not",
                object_text="关闭加湿器",
                condition="除非用户明确说关",
                natural_text="加湿器除非用户明确要求，否则不能关闭。",
            ),
            record(
                memory_id="demo_reading_preference",
                scope="room",
                room_id="room.study",
                memory_type="preference",
                subject="用户",
                predicate="prefers",
                object_text="不用开灯泡，台灯亮度中等",
                condition="看书时",
                natural_text="用户看书时偏好不开灯泡，而使用台灯并保持中等亮度。",
            ),
            record(
                memory_id="demo_color_temp_preference",
                scope="home",
                memory_type="preference",
                subject="用户",
                predicate="favorite_color_temperature",
                object_text="3000",
                natural_text="用户偏好的常用色温是3000。",
            ),
            record(
                memory_id="demo_atmosphere_group",
                scope="room",
                room_id="room.study",
                memory_type="routine",
                subject="氛围组设备",
                predicate="includes",
                object_text="书房灯泡和书房台灯",
                natural_text="氛围组设备默认指书房灯泡和书房台灯。",
                payload={"target_device_ids": ["c86e3c14d0egbfc02g4cae35662d6944", "e2bf03e9b274e88f9e7b6852d1e2c90d"]},
            ),
            record(
                memory_id="demo_living_nap_routine",
                scope="room",
                room_id="room.living_room",
                memory_type="routine",
                subject="客厅午睡",
                predicate="trigger_routine",
                object_text="打开风扇并播放音乐，音量较低",
                condition="客厅午睡",
                natural_text="用户说客厅午睡时，需要打开风扇并播放音乐，音量保持较低。",
                payload={"target_device_ids": ["e0517e77f3a8314caeddef8a8c3c1487", "21ab4b42c3e6a3fb27f93385082d4075"]},
            ),
            record(
                memory_id="demo_gateway_network",
                scope="device",
                device_id="ac5c6e84654cf19a5f91d3d36d0ff05b",
                memory_type="stable_state_fact",
                subject="网关",
                predicate="preferred_network",
                object_text="ccrv-tv",
                natural_text="用户确认网关应该连接的网络名称是ccrv-tv。",
            ),
            record(
                memory_id="demo_stars_decoration",
                scope="device",
                device_id="31ae92d8a163d77f8d6a5741c0d1b89c",
                room_id="room.bedroom",
                memory_type="stable_state_fact",
                subject="卧室床边灯",
                predicate="decoration",
                object_text="星星形状的装饰",
                natural_text="卧室床边灯挂有星星形状的装饰。",
            ),
        ]

    @staticmethod
    def _infer_outcome(output: str, *, success: bool | None) -> str:
        if success is False:
            return "failure"
        lowered = output.lower()
        if any(token in output for token in ["失败", "未能", "无法", "错误"]):
            return "partial_success"
        if any(token in lowered for token in ["need", "clarify"]) or "请告诉我" in output:
            return "partial_success"
        return "success"


_RUNTIME = DemoMemoryRuntime()


def get_demo_memory_runtime() -> DemoMemoryRuntime:
    return _RUNTIME
