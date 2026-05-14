from __future__ import annotations
from langchain.tools import tool

def get_device_all_entities_capabilities():
    pass

def get_device_all_entities_states():
    pass


import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


# 模拟 Home Assistant API 的默认地址。
# 当前项目默认监听 http://127.0.0.1:8123，因此这里直接使用该地址。
FAKE_HA_BASE_URL = "http://127.0.0.1:8123"

# 当服务端启用了 Bearer Token 鉴权时，在这里填入 token；
# 留空表示不附带 Authorization 请求头，兼容当前仓库默认的无鉴权配置。
FAKE_HA_TOKEN = ""

# 统一的 HTTP 请求超时时间，单位为秒。
DEFAULT_TIMEOUT = 10

def _build_headers() -> dict[str, str]:
    """构建访问模拟 Home Assistant API 所需的请求头。"""
    headers = {"Content-Type": "application/json"}
    if FAKE_HA_TOKEN:
        headers["Authorization"] = f"Bearer {FAKE_HA_TOKEN}"
    return headers


def _request_json(method: str, path: str, payload: Any | None = None) -> Any | str | None:
    """发送 HTTP 请求，并在失败时降级为可读文本而不是抛出异常。

    Args:
        method: HTTP 方法，例如 GET、POST。
        path: API 路径，例如 /api/states。
        payload: 需要编码为 JSON 的请求体；GET 请求通常为 None。

    Returns:
        成功时返回解析后的 JSON 响应内容。
        失败时返回可直接给上层 Agent 使用的纯文本错误信息。
        若响应为空，则返回 None。
    """
    normalized_method = method.upper()
    url = f"{FAKE_HA_BASE_URL.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url=url, data=data, headers=_build_headers(), method=normalized_method)

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return (
            f"调用模拟 Home Assistant API 失败: {normalized_method} {path} "
            f"返回 HTTP {exc.code}，响应内容: {error_body}"
        )
    except URLError as exc:
        reason_text = str(exc.reason)
        return (
            f"调用模拟 Home Assistant API 失败: {normalized_method} {path} "
            f"网络不可达，原因: {reason_text}"
        )

    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return (
            f"模拟 Home Assistant API 返回的内容不是合法 JSON: {normalized_method} {path} -> {raw}"
        )


@tool
def tool_get_all_entities_states() -> list[dict[str, Any]] | str | None:
    """获取所有实体的当前状态。

    对应 HTTP 路由:
        GET /api/states

    Returns:
        成功时返回实体状态列表；远端错误或非 JSON 响应时返回文本说明。
    """
    return _request_json("GET", "/api/states")


@tool
def tool_get_states_by_entity_id(entity_id: str) -> dict[str, Any] | str | None:
    """按 entity_id 查询单个实体的当前状态。

    对应 HTTP 路由:
        GET /api/states/{entity_id}

    Args:
        entity_id: 实体 ID，例如 light.philips_cn_1061200910_lite_s_2。

    Returns:
        成功时返回单个实体的状态 JSON 对象；远端错误或非 JSON 响应时返回文本说明。
    """
    encoded_entity_id = quote(entity_id, safe="._-")
    return _request_json("GET", f"/api/states/{encoded_entity_id}")


@tool
def tool_get_services_by_domain(domain: str) -> dict[str, Any] | str:
    """获取指定 domain 下支持的服务定义。

    对应 HTTP 路由:
        GET /api/services

    注意:
        模拟 API 没有单独提供“按 domain 查询 service”的接口，
        这里会先请求全部 services，再在本地筛选指定 domain。

    Args:
        domain: 域名，例如 light、switch、media_player。

    Returns:
        匹配到的 domain 配置，格式通常为:
        {"domain": "...", "services": {...}}
        若未找到，则返回空字典 {}；远端错误或非 JSON 响应时返回文本说明。
    """
    all_services = _request_json("GET", "/api/services")
    if isinstance(all_services, str):
        return all_services
    if all_services is None:
        return {}
    for domain_entry in all_services:
        if domain_entry.get("domain") == domain:
            return domain_entry
    return {}


@tool
def tool_get_all_devices() -> list[dict[str, Any]] | str | None:
    """获取所有设备注册信息。

    对应 HTTP 路由:
        GET /api/devices

    Returns:
        成功时返回设备定义列表，每个设备包含 device_id、name、area_id、entities（实体 ID 列表）等字段。
    """
    return _request_json("GET", "/api/devices")


@tool
def tool_get_device_by_id(device_id: str) -> dict[str, Any] | str | None:
    """按 device_id 查询单个设备的注册信息及其所有实体的当前状态。

    对应 HTTP 路由:
        GET /api/devices/{device_id}

    Args:
        device_id: 设备 ID，例如 device.test_living_room_ac_main。

    Returns:
        成功时返回 {"device": {...}, "entity_states": [...]}。
    """
    encoded_device_id = quote(device_id, safe="._-")
    return _request_json("GET", f"/api/devices/{encoded_device_id}")


@tool
def tool_get_device_entities(device_id: str) -> list[str] | str | None:
    """按 device_id 查询该设备下的所有实体 ID 列表（不含状态）。

    对应 HTTP 路由:
        GET /api/devices/{device_id}

    Args:
        device_id: 设备 ID，例如 device.test_living_room_ac_main。

    Returns:
        成功时返回该设备下的实体 ID 字符串列表，例如:
        ["climate.test_ac_01", "sensor.test_room_temperature_living_1"]。
        如需查询实体的当前状态，请使用 tool_get_states_by_entity_id。
        如需查询实体定义，请使用 tool_get_entity_definition。
    """
    encoded_device_id = quote(device_id, safe="._-")
    result = _request_json("GET", f"/api/devices/{encoded_device_id}")
    if isinstance(result, dict) and "device" in result:
        return result["device"].get("entities", [])
    return result


@tool
def tool_get_all_entities() -> list[dict[str, Any]] | str | None:
    """获取所有实体定义（EntityDefinition），非状态快照。

    对应 HTTP 路由:
        GET /api/entities

    Returns:
        成功时返回实体定义列表，每个实体包含 entity_id、domain、device_id、name、device_class 等字段。
        如果需要查询实体的当前状态，请使用 tool_get_all_entities_states。
    """
    return _request_json("GET", "/api/entities")


@tool
def tool_get_entity_definition(entity_id: str) -> dict[str, Any] | str | None:
    """按 entity_id 查询单个实体的定义信息（EntityDefinition）。

    对应 HTTP 路由:
        GET /api/entities/{entity_id}

    Args:
        entity_id: 实体 ID，例如 light.philips_cn_1061200910_lite_s_2。

    Returns:
        成功时返回实体的完整定义，包含 device_id、domain、device_class、actions 等字段。
        如需查询实体当前状态，请使用 tool_get_states_by_entity_id。
    """
    encoded_entity_id = quote(entity_id, safe="._-")
    return _request_json("GET", f"/api/entities/{encoded_entity_id}")


@tool
def tool_execute_action_by_entity_id(domain: str, service: str, body: str) -> dict[str, Any] | str:
    """调用指定 domain/service，对实体执行操作。

    对应 HTTP 路由:
        POST /api/services/{domain}/{service}

    Args:
        domain: 服务所属 domain，例如 light、switch、media_player。
        service: 要执行的服务名，例如 turn_on、toggle、media_next_track。
        body: JSON 字符串格式的请求体，至少需要包含 entity_id 字段。
            示例:
            {"entity_id": "light.philips_cn_1061200910_lite_s_2", "brightness": 50}

    Returns:
        成功时返回结构化成功结果；远端 HA/网络错误或非 JSON 响应时返回文本说明。

    Raises:
        ValueError: body 不是合法 JSON，或缺少 entity_id 字段时抛出。
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"body 必须是合法的 JSON 字符串: {body}") from exc

    if not isinstance(payload, dict):
        raise ValueError("body 必须是 JSON 对象，例如 {'entity_id': 'light.demo'}。")
    if "entity_id" not in payload:
        raise ValueError("body 至少需要包含 entity_id 字段。")

    encoded_domain = quote(domain, safe="._-")
    encoded_service = quote(service, safe="._-")
    path = f"/api/services/{encoded_domain}/{encoded_service}"

    result = _request_json("POST", path, payload=payload)
    if isinstance(result, str):
        return result

    return {
        "ok": True,
        "retryable": False,
        "domain": domain,
        "service": service,
        "path": path,
        "result": result,
    }
