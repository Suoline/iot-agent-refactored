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

from langchain.tools import tool


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


def _request_json(method: str, path: str, payload: Any | None = None) -> Any:
    """发送 HTTP 请求并把 JSON 响应解析为 Python 对象。

    Args:
        method: HTTP 方法，例如 GET、POST。
        path: API 路径，例如 /api/states。
        payload: 需要编码为 JSON 的请求体；GET 请求通常为 None。

    Returns:
        解析后的 JSON 响应内容。

    Raises:
        RuntimeError: 模拟 API 返回 HTTP 错误或网络不可达时抛出可读异常。
        ValueError: 服务端返回的内容不是合法 JSON 时抛出。
    """
    url = f"{FAKE_HA_BASE_URL.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url=url, data=data, headers=_build_headers(), method=method.upper())

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"调用模拟 Home Assistant API 失败: {method.upper()} {path} "
            f"返回 HTTP {exc.code}，响应内容: {error_body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"调用模拟 Home Assistant API 失败: {method.upper()} {path} 网络不可达，原因: {exc.reason}"
        ) from exc

    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"模拟 Home Assistant API 返回的内容不是合法 JSON: {method.upper()} {path} -> {raw}"
        ) from exc


@tool
def tool_get_all_entities_states() -> list[dict[str, Any]]:
    """获取所有实体的当前状态。

    对应 HTTP 路由:
        GET /api/states

    Returns:
        包含所有实体状态的列表。每一项通常包含 entity_id、state、attributes 等字段。
    """
    return _request_json("GET", "/api/states")


@tool
def tool_get_states_by_entity_id(entity_id: str) -> dict[str, Any]:
    """按 entity_id 查询单个实体的当前状态。

    对应 HTTP 路由:
        GET /api/states/{entity_id}

    Args:
        entity_id: 实体 ID，例如 light.philips_cn_1061200910_lite_s_2。

    Returns:
        单个实体的状态 JSON 对象。
    """
    encoded_entity_id = quote(entity_id, safe="._-")
    return _request_json("GET", f"/api/states/{encoded_entity_id}")


@tool
def tool_get_services_by_domain(domain: str) -> dict[str, Any]:
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
        若未找到，则返回空字典 {}。
    """
    all_services = _request_json("GET", "/api/services")
    for domain_entry in all_services:
        if domain_entry.get("domain") == domain:
            return domain_entry
    return {}


@tool
def tool_execute_action_by_entity_id(domain: str, service: str, body: str) -> Any:
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
        模拟 API 的 JSON 响应。默认是状态变更列表，不附带 return_response=true。

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
    return _request_json("POST", f"/api/services/{encoded_domain}/{encoded_service}", payload=payload)
