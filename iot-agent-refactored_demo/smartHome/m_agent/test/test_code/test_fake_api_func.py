import io
import importlib
import sys
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


class _StubTool:
    def __init__(self, func):
        self.func = func


def _install_langchain_stub() -> None:
    langchain_module = types.ModuleType("langchain")
    langchain_tools_module = types.ModuleType("langchain.tools")

    def tool(func):
        return _StubTool(func)

    langchain_tools_module.tool = tool
    langchain_module.tools = langchain_tools_module
    sys.modules["langchain"] = langchain_module
    sys.modules["langchain.tools"] = langchain_tools_module


try:
    fake_api_func = importlib.import_module("smartHome.m_agent.memory.fake_api_tool.fake_api_func")
except ModuleNotFoundError as exc:
    if exc.name != "langchain":
        raise
    _install_langchain_stub()
    fake_api_func = importlib.import_module("smartHome.m_agent.memory.fake_api_tool.fake_api_func")


class _MockResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeApiFuncTestCase(unittest.TestCase):
    def test_get_states_by_entity_id_returns_text_on_http_404(self) -> None:
        http_error = HTTPError(
            url="http://127.0.0.1:8123/api/states/sensor.missing",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"Unknown entity state: sensor.missing"}'),
        )
        with patch.object(fake_api_func, "urlopen", side_effect=http_error):
            result = fake_api_func.tool_get_states_by_entity_id.func("sensor.missing")

        self.assertIsInstance(result, str)
        self.assertIn("HTTP 404", result)
        self.assertIn("Unknown entity state: sensor.missing", result)

    def test_execute_action_returns_text_on_http_503(self) -> None:
        http_error = HTTPError(
            url="http://127.0.0.1:8123/api/services/climate/set_temperature",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"Simulated network error"}'),
        )
        body = '{"entity_id": "climate.test_bedroom_ac_main", "temperature": 25}'
        with patch.object(fake_api_func, "urlopen", side_effect=http_error):
            result = fake_api_func.tool_execute_action_by_entity_id.func(
                "climate",
                "set_temperature",
                body,
            )

        self.assertIsInstance(result, str)
        self.assertIn("HTTP 503", result)
        self.assertIn("Simulated network error", result)

    def test_get_states_by_entity_id_returns_text_on_url_error(self) -> None:
        with patch.object(fake_api_func, "urlopen", side_effect=URLError("connection refused")):
            result = fake_api_func.tool_get_states_by_entity_id.func("sensor.missing")

        self.assertEqual(
            result,
            "调用模拟 Home Assistant API 失败: GET /api/states/sensor.missing 网络不可达，原因: connection refused",
        )

    def test_request_json_returns_text_when_response_is_not_json(self) -> None:
        with patch.object(fake_api_func, "urlopen", return_value=_MockResponse("plain text response")):
            result = fake_api_func._request_json("GET", "/api/states")

        self.assertEqual(
            result,
            "模拟 Home Assistant API 返回的内容不是合法 JSON: GET /api/states -> plain text response",
        )

    def test_get_services_by_domain_propagates_error_text(self) -> None:
        with patch.object(fake_api_func, "_request_json", return_value="HTTP 503 error text"):
            result = fake_api_func.tool_get_services_by_domain.func("light")

        self.assertEqual(result, "HTTP 503 error text")

    def test_execute_action_keeps_success_shape(self) -> None:
        payload = [{"entity_id": "light.demo", "state": "on"}]
        body = '{"entity_id": "light.demo"}'
        with patch.object(fake_api_func, "_request_json", return_value=payload):
            result = fake_api_func.tool_execute_action_by_entity_id.func("light", "turn_on", body)

        self.assertEqual(
            result,
            {
                "ok": True,
                "retryable": False,
                "domain": "light",
                "service": "turn_on",
                "path": "/api/services/light/turn_on",
                "result": payload,
            },
        )


if __name__ == "__main__":
    unittest.main()
