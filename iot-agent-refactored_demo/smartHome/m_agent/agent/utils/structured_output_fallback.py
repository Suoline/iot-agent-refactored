"""过滤节点结构化输出缺失时的文本回退解析。

免费档模型（glm-4-flash 等）在 response_format 下经常直接输出文本总结
而不调用结构化输出工具；原实现回退为空候选集，导致后续 planner 无实体
可用、任务必然失败。实际上模型的文本总结里通常已明确列出选中的实体 /
设备 ID，这里提供从消息文本中回退提取 ID 的能力，保证链路可以继续。

同时提供对结构化输出 / 文本中占位符（@token@）的统一解码：隐私编码开启
时 structured_response 不经过 after_model 解码链路，若模型把占位符原样
写入 entity_id / device_id，需要在返回给 planner / executor 前恢复真实值。
"""
from __future__ import annotations

import re

# Home Assistant 常见 domain 白名单：entity_id 形如 domain.object_id，
# 只认可信 domain，避免把 "tool_filter.get" 之类普通文本误判为实体 ID。
_KNOWN_DOMAINS: frozenset[str] = frozenset({
    "air_quality", "alarm_control_panel", "automation", "binary_sensor",
    "button", "calendar", "camera", "climate", "cover", "device_tracker",
    "fan", "humidifier", "input_boolean", "input_number", "input_select",
    "light", "lock", "media_player", "number", "scene", "script", "select",
    "sensor", "siren", "switch", "vacuum", "valve", "water_heater", "weather",
})

_ENTITY_ID_PATTERN = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z0-9_]+)\b")
_DEVICE_ID_PATTERN = re.compile(r"\b[0-9a-f]{32}\b")

# 匹配 @token@ 形式的隐私占位符（与 LLMPrivacyHandler._TOKEN_PATTERN 保持一致语义）
_PLACEHOLDER_PATTERN = re.compile(r"@[A-Za-z0-9_]+@")


def decode_value(value: str) -> str:
    """把可能含 @token@ 占位符的值恢复为真实值；无法恢复时原样返回。"""
    if not isinstance(value, str) or not _PLACEHOLDER_PATTERN.search(value):
        return value
    try:
        from smartHome.m_agent.agent.utils.privacy_codex import decode_text

        return decode_text(value)
    except Exception:
        # 映射缺失等异常时不能让过滤链路崩溃，退回原值由后续校验兜底。
        return value


def extract_entity_ids_from_messages(messages) -> list[str]:
    """从 agent 消息文本中回退提取实体 ID（按出现顺序去重）。"""
    found: list[str] = []
    seen: set[str] = set()
    for message in messages or []:
        text = getattr(message, "content", "")
        if not isinstance(text, str):
            continue
        for match in _ENTITY_ID_PATTERN.finditer(text):
            domain, object_id = match.group(1), match.group(2)
            if domain not in _KNOWN_DOMAINS:
                continue
            entity_id = f"{domain}.{object_id}"
            if entity_id not in seen:
                seen.add(entity_id)
                found.append(decode_value(entity_id))
    return found


def extract_device_ids_from_messages(messages) -> list[str]:
    """从 agent 消息文本中回退提取设备 ID（32 位 hex，按出现顺序去重）。"""
    found: list[str] = []
    seen: set[str] = set()
    for message in messages or []:
        text = getattr(message, "content", "")
        if not isinstance(text, str):
            continue
        for match in _DEVICE_ID_PATTERN.finditer(text):
            device_id = match.group(0)
            if device_id not in seen:
                seen.add(device_id)
                found.append(decode_value(device_id))
    return found
