import importlib
import sys
import types
import unittest
from unittest.mock import patch


def _install_langchain_stubs() -> None:
    langchain_module = types.ModuleType("langchain")
    langchain_chat_models_module = types.ModuleType("langchain.chat_models")
    langchain_core_module = types.ModuleType("langchain_core")
    langchain_core_callbacks_module = types.ModuleType("langchain_core.callbacks")
    langchain_core_messages_module = types.ModuleType("langchain_core.messages")

    class _BaseMessage:
        def __init__(self, content=None, additional_kwargs=None):
            self.content = content
            self.additional_kwargs = additional_kwargs or {}

    class _AIMessage(_BaseMessage):
        def __init__(self, content=None, additional_kwargs=None, tool_calls=None, invalid_tool_calls=None):
            super().__init__(content=content, additional_kwargs=additional_kwargs)
            self.tool_calls = tool_calls
            self.invalid_tool_calls = invalid_tool_calls

    def _init_chat_model(*args, **kwargs):
        raise AssertionError("测试中不应直接调用 init_chat_model")

    langchain_chat_models_module.init_chat_model = _init_chat_model
    langchain_core_callbacks_module.CallbackManager = object
    langchain_core_messages_module.BaseMessage = _BaseMessage
    langchain_core_messages_module.AIMessage = _AIMessage

    langchain_module.chat_models = langchain_chat_models_module
    langchain_core_module.callbacks = langchain_core_callbacks_module
    langchain_core_module.messages = langchain_core_messages_module

    sys.modules["langchain"] = langchain_module
    sys.modules["langchain.chat_models"] = langchain_chat_models_module
    sys.modules["langchain_core"] = langchain_core_module
    sys.modules["langchain_core.callbacks"] = langchain_core_callbacks_module
    sys.modules["langchain_core.messages"] = langchain_core_messages_module


try:
    privacy_codex = importlib.import_module("smartHome.m_agent.agent.utils.privacy_codex")
except ModuleNotFoundError as exc:
    if exc.name not in {"langchain", "langchain_core"}:
        raise
    _install_langchain_stubs()
    privacy_codex = importlib.import_module("smartHome.m_agent.agent.utils.privacy_codex")


class _StubLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.invocations: list[list[dict[str, str]]] = []

    def invoke(self, messages):
        self.invocations.append(messages)
        if not self._responses:
            raise AssertionError("Stub LLM 没有更多响应可用")
        return self._responses.pop(0)


class _Message:
    def __init__(self, content=None, additional_kwargs=None):
        self.content = content
        self.additional_kwargs = additional_kwargs or {}


class PrivacyCodexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        privacy_codex._PRIVACY_HANDLER = None
        privacy_codex._ENCODE_MAP.clear()
        privacy_codex._DECODE_MAP.clear()
        privacy_codex._TEXT_CACHE.clear()

    def test_encode_then_decode_round_trip(self) -> None:
        stub_llm = _StubLLM([])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            encoded = privacy_codex.encode_text("设备 climate.test_bedroom_ac_main 已开启")
            decoded = privacy_codex.decode_text(encoded)

        self.assertEqual(encoded, "设备 @entity_id@ 已开启")
        self.assertEqual(decoded, "设备 climate.test_bedroom_ac_main 已开启")
        self.assertEqual(stub_llm.invocations, [])

    def test_previous_tokens_can_still_be_decoded_after_multiple_encodes(self) -> None:
        stub_llm = _StubLLM([])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            first_encoded = privacy_codex.encode_text("climate.test_living_room_ac_main")
            second_encoded = privacy_codex.encode_text("sensor.test_living_room_temperature")

            self.assertEqual(
                privacy_codex.decode_text(first_encoded + " " + second_encoded),
                "climate.test_living_room_ac_main sensor.test_living_room_temperature",
            )

    def test_same_original_value_reuses_existing_token(self) -> None:
        stub_llm = _StubLLM([])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            first_encoded = privacy_codex.encode_text("climate.test_living_room_ac_main")
            second_encoded = privacy_codex.encode_text("再次处理 climate.test_living_room_ac_main")

        self.assertEqual(first_encoded, "@entity_id@")
        self.assertEqual(second_encoded, "再次处理 @entity_id@")

    def test_conflicting_semantic_name_is_renumbered(self) -> None:
        stub_llm = _StubLLM([
            '{"encoded_text": {"家庭状态A": "status_01"}}',
            '{"encoded_text": {"家庭状态B": "status_01"}}',
        ])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            first_encoded = privacy_codex.encode_text("家庭状态A")
            second_encoded = privacy_codex.encode_text("家庭状态B")

        self.assertEqual(first_encoded, "@status_01@")
        self.assertEqual(second_encoded, "@status_01_02@")
        self.assertEqual(privacy_codex.decode_text(first_encoded), "家庭状态A")
        self.assertEqual(privacy_codex.decode_text(second_encoded), "家庭状态B")

    def test_same_original_text_uses_cache_without_second_llm_call(self) -> None:
        stub_llm = _StubLLM([
            '{"encoded_text": {"家庭状态A": "status_01"}}',
        ])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            first_encoded = privacy_codex.encode_text("家庭状态A")
            second_encoded = privacy_codex.encode_text("家庭状态A")

        self.assertEqual(first_encoded, "@status_01@")
        self.assertEqual(second_encoded, "@status_01@")
        self.assertEqual(len(stub_llm.invocations), 1)

    def test_stable_values_are_encoded_locally_without_llm(self) -> None:
        stub_llm = _StubLLM([])
        context_id = "0f0fa9d171c74db1aa7b3b2ef7f0ad06"
        original = (
            "climate.test_living_room_ac_main "
            "2026-05-06T12:04:11.507253+08:00 "
            f"{context_id}"
        )

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            encoded = privacy_codex.encode_text(original)
            decoded = privacy_codex.decode_text(encoded)

        self.assertIn("@entity_id@", encoded)
        self.assertIn("@timestamp@", encoded)
        self.assertIn("@context_id@", encoded)
        self.assertEqual(decoded, original)
        self.assertEqual(stub_llm.invocations, [])

    def test_encode_messages_batches_llm_fallback_once(self) -> None:
        stub_llm = _StubLLM([
            '{"encoded_text": {"家庭状态A": "status_01", "家庭状态B": "status_01"}}',
        ])
        messages = [
            _Message(content="家庭状态A"),
            _Message(content={"nested": ["家庭状态B", "climate.test_bedroom_ac_main"]}),
        ]

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            encoded_messages = privacy_codex.encode_messages(messages)

        self.assertEqual(encoded_messages[0].content, "@status_01@")
        self.assertEqual(encoded_messages[1].content["nested"][0], "@status_01_02@")
        self.assertEqual(encoded_messages[1].content["nested"][1], "@entity_id@")
        self.assertEqual(len(stub_llm.invocations), 1)

    def test_empty_string_does_not_clear_history(self) -> None:
        stub_llm = _StubLLM([])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            encoded = privacy_codex.encode_text("sensor.test_bedroom_temperature")
            self.assertEqual(privacy_codex.encode_text(""), "")
            decoded = privacy_codex.decode_text(encoded)

        self.assertEqual(decoded, "sensor.test_bedroom_temperature")

    def test_decode_evaluates_arithmetic_expression(self) -> None:
        stub_llm = _StubLLM([
            '{"encoded_text": {"12": "value"}}',
        ])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            privacy_codex.encode_text("12")
            decoded = privacy_codex.decode_text("@value@*5-4")

        self.assertEqual(decoded, "56")

    def test_decode_preserves_date_like_text(self) -> None:
        stub_llm = _StubLLM([])

        with patch.object(privacy_codex, "create_custom_llm", return_value=stub_llm):
            privacy_codex.encode_text("2026-05-06")
            decoded = privacy_codex.decode_text("@date@")

        self.assertEqual(decoded, "2026-05-06")


if __name__ == "__main__":
    unittest.main()
