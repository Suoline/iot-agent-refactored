"""TokenUsageTracker 单元测试：累计、分组、字段回退与落盘。"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from langchain_core.messages import AIMessage

from smartHome.m_agent.common.token_tracker import TokenUsageTracker


def _message(usage_metadata=None, token_usage=None, model_name="test-model"):
    return AIMessage(
        content="ok",
        usage_metadata=usage_metadata,
        response_metadata={
            "model_name": model_name,
            **({"token_usage": token_usage} if token_usage else {}),
        },
    )


class TokenUsageTrackerTest(unittest.TestCase):
    def test_accumulates_and_groups_by_model_and_agent(self):
        tracker = TokenUsageTracker()
        tracker.record(_message({"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}), "router")
        tracker.record(_message({"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}), "planner")

        summary = tracker.summary()
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["prompt_tokens"], 150)
        self.assertEqual(summary["completion_tokens"], 15)
        self.assertEqual(summary["total_tokens"], 165)
        self.assertEqual(summary["by_model"]["test-model"]["total_tokens"], 165)
        self.assertEqual(summary["by_agent"]["router"]["total_tokens"], 110)
        self.assertEqual(summary["by_agent"]["planner"]["total_tokens"], 55)

    def test_falls_back_to_response_metadata_token_usage(self):
        tracker = TokenUsageTracker()
        tracker.record(_message(usage_metadata=None, token_usage={"prompt_tokens": 30, "completion_tokens": 3, "total_tokens": 33}), "router")

        summary = tracker.summary()
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["prompt_tokens"], 30)
        self.assertEqual(summary["total_tokens"], 33)

    def test_missing_usage_counts_zero_tokens(self):
        tracker = TokenUsageTracker()
        tracker.record(_message(usage_metadata=None, token_usage=None), "router")

        summary = tracker.summary()
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["total_tokens"], 0)

    def test_print_summary_silent_without_calls(self):
        tracker = TokenUsageTracker()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            tracker.print_summary()
        self.assertEqual(buffer.getvalue(), "")

    def test_print_summary_outputs_groups(self):
        tracker = TokenUsageTracker()
        tracker.record(_message({"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}), "home_路由节点")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            tracker.print_summary()
        text = buffer.getvalue()
        self.assertIn("Token 用量统计", text)
        self.assertIn("test-model", text)
        self.assertIn("home_路由节点", text)

    def test_dump_json_writes_payload(self):
        tracker = TokenUsageTracker()
        tracker.record(_message({"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}), "router")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "usage.json"
            written = tracker.dump_json(path)
            self.assertTrue(written.exists())
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(payload["total_tokens"], 11)
            self.assertIn("generated_at", payload)
            self.assertIn("by_model", payload)


if __name__ == "__main__":
    unittest.main()
