"""structured_output_fallback 单元测试。"""
import unittest
from unittest import mock

from langchain_core.messages import AIMessage, HumanMessage

from smartHome.m_agent.agent.utils.structured_output_fallback import (
    decode_value,
    extract_device_ids_from_messages,
    extract_entity_ids_from_messages,
)


class TestExtractEntityIds(unittest.TestCase):
    def test_extracts_ha_entity_ids_from_plain_text(self):
        messages = [AIMessage(content=(
            "根据获取到的实体状态信息：\n"
            "1. 实体ID：climate.test_living_room_ac_main\n"
            "2. 实体ID：climate.test_bedroom_ac_main\n"
            "这两个空调实体都可以调整温度。"
        ))]
        self.assertEqual(
            extract_entity_ids_from_messages(messages),
            ["climate.test_living_room_ac_main", "climate.test_bedroom_ac_main"],
        )

    def test_ignores_unknown_domains_and_keeps_order_unique(self):
        messages = [AIMessage(content=(
            "tool_filter.get 返回了 light.ceiling_main 和 light.ceiling_main，"
            "注意 media_player.sonos_living_room 也在列表中"
        ))]
        self.assertEqual(
            extract_entity_ids_from_messages(messages),
            ["light.ceiling_main", "media_player.sonos_living_room"],
        )

    def test_skips_non_string_content_and_empty_messages(self):
        self.assertEqual(extract_entity_ids_from_messages([]), [])
        self.assertEqual(extract_entity_ids_from_messages(None), [])
        messages = [HumanMessage(content=[{"type": "text", "text": "light.a_b"}])]
        self.assertEqual(extract_entity_ids_from_messages(messages), [])


class TestExtractDeviceIds(unittest.TestCase):
    def test_extracts_32hex_device_ids(self):
        messages = [AIMessage(content=(
            "选中设备 31ae92d8a163d77f8d6a5741c0d1b89c 和 "
            "31ae92d8a163d77f8d6a54856d1b89cd；前者是灯。"
        ))]
        self.assertEqual(
            extract_device_ids_from_messages(messages),
            ["31ae92d8a163d77f8d6a5741c0d1b89c", "31ae92d8a163d77f8d6a54856d1b89cd"],
        )

    def test_no_match_on_short_hex(self):
        messages = [AIMessage(content="abc123def4")]
        self.assertEqual(extract_device_ids_from_messages(messages), [])


class TestDecodeValue(unittest.TestCase):
    def test_passthrough_without_placeholder(self):
        self.assertEqual(decode_value("climate.test_ac_main"), "climate.test_ac_main")
        self.assertEqual(decode_value(""), "")

    def test_decode_placeholder_when_mapping_exists(self):
        with mock.patch(
            "smartHome.m_agent.agent.utils.privacy_codex.decode_text",
            return_value="climate.real_id",
        ):
            self.assertEqual(decode_value("@entity_id@"), "climate.real_id")

    def test_fallback_to_original_when_decode_raises(self):
        with mock.patch(
            "smartHome.m_agent.agent.utils.privacy_codex.decode_text",
            side_effect=ValueError("decode_map 为空"),
        ):
            self.assertEqual(decode_value("@entity_id@"), "@entity_id@")


if __name__ == "__main__":
    unittest.main()
