from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from smartHome.m_agent.agent.tools import query_tool_func
from smartHome.m_agent.memory.runtime_v1 import DemoMemoryRuntime


def test_query_tool_reads_seeded_memory(tmp_path, monkeypatch):
    runtime = DemoMemoryRuntime(str(tmp_path / "demo_memory.sqlite3"))
    monkeypatch.setattr(query_tool_func, "get_demo_memory_runtime", lambda: runtime)

    text = query_tool_func.query_tool.func("小书灯")

    assert "长期记忆" in text
    assert "书房台灯" in text or "小书灯" in text
