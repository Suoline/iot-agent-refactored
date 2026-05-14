"""base_home_agent.py 集成测试：启动 v2 后端，调用 Agent 端到端完成任务。

分层：
- 工具级测试：直接调用 fake_api_func tool 函数，验证 HTTP 返回格式。不涉及 LLM，秒级。
- Agent 集成测试：调用 run_ourAgent() 跑真实任务，需 LLM API。耗时较长。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import types
from typing import Generator

import pytest


# ── 导入 fake_api_func ──────────────────────────────────────────────────────────

def _install_langchain_stub() -> None:
    langchain_module = types.ModuleType("langchain")
    langchain_tools_module = types.ModuleType("langchain.tools")

    def tool(func):
        return func

    langchain_tools_module.tool = tool
    langchain_module.tools = langchain_tools_module
    sys.modules["langchain"] = langchain_module
    sys.modules["langchain.tools"] = langchain_tools_module


try:
    fake_api_func = importlib.import_module(
        "smartHome.m_agent.memory.fake_api_tool.fake_api_func"
    )
    _HAS_REAL_LANGCHAIN = True
except ModuleNotFoundError as exc:
    if exc.name != "langchain":
        raise
    _install_langchain_stub()
    fake_api_func = importlib.import_module(
        "smartHome.m_agent.memory.fake_api_tool.fake_api_func"
    )
    _HAS_REAL_LANGCHAIN = False


def _call_tool(tool_func, *args, **kwargs):
    """调用 tool 函数，兼容 @tool 装饰器（StructuredTool）和原始函数两种情况。"""
    if _HAS_REAL_LANGCHAIN:
        # langchain 的 @tool 返回 StructuredTool，实际函数在 .func
        return tool_func.func(*args, **kwargs)
    return tool_func(*args, **kwargs)


# ── 服务端 fixture ─────────────────────────────────────────────────────────────

SERVER_PORT = 18123
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


def _server_ready() -> bool:
    try:
        result = fake_api_func._request_json("GET", "/api/")
        return isinstance(result, dict) and result.get("message") == "API running."
    except Exception:
        return False


@pytest.fixture(scope="module")
def fake_ha_server() -> Generator[None, None, None]:
    """启动 v2 模拟服务端（base_env），测试结束后关闭。"""
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
    )
    demo_dir = os.path.join(project_root, "homeassitant_demo")

    # 清理旧数据，确保从 legacy 导入
    runtime_dir = os.path.join(demo_dir, ".fake_homeassistant")
    if os.path.exists(runtime_dir):
        shutil.rmtree(runtime_dir)

    server_process = subprocess.Popen(
        [
            "/opt/miniconda3/bin/conda", "run", "-n", "fake-homeassitant-env",
            "python", "-m", "uvicorn",
            "fake_homeassistant_v2.app:create_app",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(SERVER_PORT),
        ],
        cwd=demo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    original_base = fake_api_func.FAKE_HA_BASE_URL
    fake_api_func.FAKE_HA_BASE_URL = SERVER_URL

    deadline = time.time() + 15
    while time.time() < deadline:
        if _server_ready():
            break
        time.sleep(0.5)
    else:
        server_process.kill()
        server_process.wait()
        fake_api_func.FAKE_HA_BASE_URL = original_base
        pytest.fail("v2 服务端启动超时")

    yield

    fake_api_func.FAKE_HA_BASE_URL = original_base
    server_process.terminate()
    try:
        server_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.kill()
        server_process.wait()


# ── 工具级测试 ──────────────────────────────────────────────────────────────────


class TestDeviceTools:
    """设备注册表 tool 函数。"""

    def test_get_all_devices(self, fake_ha_server: None) -> None:
        result = _call_tool(fake_api_func.tool_get_all_devices)
        assert isinstance(result, list)
        assert len(result) == 14
        for device in result:
            assert "device_id" in device
            assert "name" in device
            assert "entities" in device
            assert isinstance(device["entities"], list)

    def test_get_device_by_id(self, fake_ha_server: None) -> None:
        all_devices = _call_tool(fake_api_func.tool_get_all_devices)
        device_id = all_devices[0]["device_id"]
        result = _call_tool(fake_api_func.tool_get_device_by_id, device_id)
        assert isinstance(result, dict)
        assert "device" in result
        assert "entity_states" in result
        assert result["device"]["device_id"] == device_id
        assert isinstance(result["entity_states"], list)

    def test_get_device_entities(self, fake_ha_server: None) -> None:
        all_devices = _call_tool(fake_api_func.tool_get_all_devices)
        device_id = all_devices[0]["device_id"]
        result = _call_tool(fake_api_func.tool_get_device_entities, device_id)
        assert isinstance(result, list)
        assert len(result) > 0
        for entity_id in result:
            assert isinstance(entity_id, str)
            assert "." in entity_id

    def test_get_device_not_found(self, fake_ha_server: None) -> None:
        result = _call_tool(fake_api_func.tool_get_device_by_id, "device.not_exist")
        assert isinstance(result, str)
        assert "404" in result or "Unknown device_id" in result


class TestEntityTools:
    """实体注册表 tool 函数。"""

    def test_get_all_entities(self, fake_ha_server: None) -> None:
        result = _call_tool(fake_api_func.tool_get_all_entities)
        assert isinstance(result, list)
        assert len(result) == 71
        for entity in result:
            assert "entity_id" in entity
            assert "domain" in entity

    def test_get_entity_definition(self, fake_ha_server: None) -> None:
        all_entities = _call_tool(fake_api_func.tool_get_all_entities)
        entity_id = all_entities[0]["entity_id"]
        result = _call_tool(fake_api_func.tool_get_entity_definition, entity_id)
        assert isinstance(result, dict)
        assert result["entity_id"] == entity_id
        assert "device_id" in result

    def test_get_entity_not_found(self, fake_ha_server: None) -> None:
        result = _call_tool(fake_api_func.tool_get_entity_definition, "light.not_exist")
        assert isinstance(result, str)
        assert "404" in result or "Unknown entity_id" in result


class TestStateTools:
    """实体状态 tool 函数（回归）。"""

    def test_get_all_states(self, fake_ha_server: None) -> None:
        result = _call_tool(fake_api_func.tool_get_all_entities_states)
        assert isinstance(result, list)
        assert len(result) == 71

    def test_get_state_by_id(self, fake_ha_server: None) -> None:
        all_states = _call_tool(fake_api_func.tool_get_all_entities_states)
        entity_id = all_states[0]["entity_id"]
        result = _call_tool(fake_api_func.tool_get_states_by_entity_id, entity_id)
        assert isinstance(result, dict)
        assert result["entity_id"] == entity_id


class TestServiceCall:
    """服务调用 tool 函数。"""

    def test_toggle_light(self, fake_ha_server: None) -> None:
        entity_id = "light.philips_cn_1061200910_lite_s_2"
        before = _call_tool(fake_api_func.tool_get_states_by_entity_id, entity_id)
        before_state = before["state"]

        result = _call_tool(fake_api_func.tool_execute_action_by_entity_id,
            "light", "toggle", json.dumps({"entity_id": entity_id})
        )
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert result["domain"] == "light"

        after = _call_tool(fake_api_func.tool_get_states_by_entity_id, entity_id)
        assert after["state"] != before_state


# ── Agent 集成测试 ──────────────────────────────────────────────────────────────


class TestAgentIntegration:
    """端到端测试：启动 v2 server → run_ourAgent → 验证结果。

    需要 LLM API（langchain + 模型配置）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, fake_ha_server: None) -> None:
        pass

    def test_agent_finds_devices_in_living_room(self) -> None:
        """Agent 通过 device registry 发现客厅设备。"""
        from smartHome.m_agent.agent.base_home_agent import run_ourAgent

        output = run_ourAgent("客厅有哪些设备")
        assert isinstance(output, str) and len(output) > 0, f"输出为空: {output}"
        assert any(kw in output for kw in ["灯泡", "台灯", "音箱", "客厅"]), \
            f"应包含客厅设备名，实际: {output[:300]}"

    def test_agent_turn_off_desk_lamp(self) -> None:
        """Agent 执行"关闭书桌台灯"。"""
        from smartHome.m_agent.agent.base_home_agent import run_ourAgent

        entity_id = "light.philips_cn_1061200910_lite_s_2"
        # 先确保灯开着
        _call_tool(fake_api_func.tool_execute_action_by_entity_id,
            "light", "turn_on", json.dumps({"entity_id": entity_id})
        )

        output = run_ourAgent("关闭书桌上的台灯")
        assert isinstance(output, str) and len(output) > 0, f"输出为空: {output}"

        state = _call_tool(fake_api_func.tool_get_states_by_entity_id, entity_id)
        if isinstance(state, dict):
            assert state["state"] == "off", \
                f"台灯应为 off，实际: {state['state']}。Agent 输出: {output[:300]}"

    def test_agent_list_all_devices(self) -> None:
        """Agent 通过 device registry 列出家中所有设备。"""
        from smartHome.m_agent.agent.base_home_agent import run_ourAgent

        output = run_ourAgent("我家有几个设备，列出所有设备名称")
        assert isinstance(output, str) and len(output) > 0, f"输出为空: {output}"
        assert any(kw in output for kw in ["灯泡", "台灯", "传感器", "插座", "网关"]), \
            f"应包含多种设备，实际: {output[:300]}"
