"""进程级 LLM token 用量统计器。

由 langchain_middleware.log_response 钩子喂数据：产品运行时的所有子 agent
（router / filter / planner / executor / 校验等）每次模型响应都会被计数，
不依赖 DemoMemoryRuntime 的任务上下文，因此任何入口（单次 run_ourAgent、
test_runner、隐私/可靠性 runner）运行结束时都能拿到全量统计。

进程退出时自动打印中文摘要；需要落盘时调用 dump_json()。
"""
from __future__ import annotations

import atexit
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_SUMMARY_WIDTH = 52


def _extract_usage(message: Any) -> tuple[dict[str, int], str]:
    """从 LangChain AIMessage 提取 usage 与模型名，兼容不同 provider 的字段位置。

    LangChain 新版统一放 usage_metadata（input/output/total_tokens）；
    OpenAI 兼容端点的历史字段在 response_metadata.token_usage
    （prompt/completion/total_tokens）。两者都缺失时（本地/异常响应）记 0。
    """
    usage = getattr(message, "usage_metadata", None) or {}
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    token_usage = metadata.get("token_usage", {})
    if not isinstance(token_usage, dict):
        token_usage = {}

    prompt = int(usage.get("input_tokens", token_usage.get("prompt_tokens", 0)) or 0)
    completion = int(usage.get("output_tokens", token_usage.get("completion_tokens", 0)) or 0)
    total = int(usage.get("total_tokens", token_usage.get("total_tokens", prompt + completion)) or 0)
    model = str(metadata.get("model_name") or "unknown")
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}, model


class TokenUsageTracker:
    """累计一次进程内所有 LLM 调用的 token 用量，按模型与 agent 节点分组。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._by_model: dict[str, dict[str, int]] = {}
        self._by_agent: dict[str, dict[str, int]] = {}
        self._auto_print_registered = False

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def record(self, message: Any, agent_name: str = "unknown") -> None:
        """记录一次模型响应的用量。message 为 LangChain AIMessage。"""
        usage, model = _extract_usage(message)
        with self._lock:
            self._calls += 1
            self._prompt_tokens += usage["prompt_tokens"]
            self._completion_tokens += usage["completion_tokens"]
            self._total_tokens += usage["total_tokens"]
            for bucket, key in ((self._by_model, model), (self._by_agent, agent_name)):
                entry = bucket.setdefault(
                    key,
                    {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                )
                entry["calls"] += 1
                entry["prompt_tokens"] += usage["prompt_tokens"]
                entry["completion_tokens"] += usage["completion_tokens"]
                entry["total_tokens"] += usage["total_tokens"]

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """返回结构化统计（总览 + 按模型 + 按 agent 节点）。"""
        with self._lock:
            return {
                "calls": self._calls,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "by_model": {k: dict(v) for k, v in self._by_model.items()},
                "by_agent": {k: dict(v) for k, v in self._by_agent.items()},
            }

    def print_summary(self) -> None:
        """打印中文摘要；无任何调用时不输出（避免干扰不走 LLM 的本地流程）。"""
        data = self.summary()
        if data["calls"] == 0:
            return
        lines = [
            "",
            "=" * _SUMMARY_WIDTH,
            "Token 用量统计（本次运行）",
            "=" * _SUMMARY_WIDTH,
            f"调用次数          : {data['calls']}",
            f"prompt tokens     : {data['prompt_tokens']:,}",
            f"completion tokens : {data['completion_tokens']:,}",
            f"total tokens      : {data['total_tokens']:,}",
        ]
        if data["by_model"]:
            lines.append("-" * _SUMMARY_WIDTH)
            lines.append("按模型:")
            for model, entry in sorted(data["by_model"].items()):
                lines.append(
                    f"  {model}: {entry['calls']} 次 | "
                    f"prompt {entry['prompt_tokens']:,} | "
                    f"completion {entry['completion_tokens']:,} | "
                    f"total {entry['total_tokens']:,}"
                )
        if data["by_agent"]:
            lines.append("-" * _SUMMARY_WIDTH)
            lines.append("按节点:")
            for agent, entry in sorted(data["by_agent"].items()):
                lines.append(
                    f"  {agent}: {entry['calls']} 次 | total {entry['total_tokens']:,}"
                )
        lines.append("=" * _SUMMARY_WIDTH)
        print("\n".join(lines))

    def dump_json(self, path: str | Path) -> Path:
        """把统计写入 JSON 文件（父目录不存在时自动创建），返回文件路径。"""
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            **self.summary(),
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    # ------------------------------------------------------------------
    # 进程退出自动摘要
    # ------------------------------------------------------------------
    def enable_auto_print_on_exit(self) -> None:
        """注册 atexit 钩子：进程退出时自动打印摘要（幂等）。"""
        if self._auto_print_registered:
            return
        self._auto_print_registered = True
        atexit.register(self.print_summary)


_TOKEN_TRACKER: TokenUsageTracker | None = None
_TRACKER_LOCK = threading.RLock()


def get_token_tracker() -> TokenUsageTracker:
    """模块级单例；首次获取时自动启用退出打印。"""
    global _TOKEN_TRACKER
    if _TOKEN_TRACKER is None:
        with _TRACKER_LOCK:
            if _TOKEN_TRACKER is None:
                _TOKEN_TRACKER = TokenUsageTracker()
                _TOKEN_TRACKER.enable_auto_print_on_exit()
    return _TOKEN_TRACKER
