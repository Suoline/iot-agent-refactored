"""统一 egress 审计：patch middleware 的编码入口，记录每次云请求的
pre-transform payload 与实际 outbound payload（实验方案 §7.E1 流程 3）。

所有配置（C0-C3）都经由同一 before_model 钩子进入 encode_messages，因此
egress coverage 恒为 100%（E0.1 / §6.3）；审计对每个配置记录：
- pre_texts：变换前 payload 中的全部字符串（含 tool_calls 参数）
- outbound_texts：实际发送 payload 中的全部字符串
- sensitive_hits：按任务金标准在两侧的明文出现计数（G_r / L_r 来源）
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage


def collect_message_strings(messages: list[BaseMessage]) -> list[str]:
    strings: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, str):
            strings.append(obj)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            for value in obj.values():
                walk(value)

    for m in messages:
        walk(getattr(m, "content", ""))
        walk(getattr(m, "additional_kwargs", None))
        if isinstance(m, AIMessage):
            walk(getattr(m, "tool_calls", None))
            walk(getattr(m, "invalid_tool_calls", None))
    return strings


class EgressAuditRecorder:
    """进程内审计器：每次 encode_messages 调用记一条 request 记录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.requests: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []  # 解码后的模型工具调用（TSR/PT 判定用）

    # -- 敏感 span 计数 ----------------------------------------------------
    @staticmethod
    def _count_occurrences(texts: list[str], value: str) -> int:
        if not value:
            return 0
        if re.fullmatch(r"[0-9A-Za-z_.\-:@]+", value):
            # entity_id / 时间戳 / hex 等结构化值：词边界匹配，避免 126.7 命中 26.7
            pattern = re.compile(r"(?<![0-9A-Za-z_.])" + re.escape(value) + r"(?![0-9A-Za-z_])")
            return sum(len(pattern.findall(t)) for t in texts)
        # 中文人名 / 房间名等自由文本：直接子串计数
        return sum(t.count(value) for t in texts)

    def record_request(
        self,
        *,
        pre_messages: list[BaseMessage],
        outbound_messages: list[BaseMessage],
        sensitive_spans: list[dict[str, str]],
    ) -> None:
        pre_texts = collect_message_strings(pre_messages)
        outbound_texts = collect_message_strings(outbound_messages)
        hits: list[dict[str, Any]] = []
        for span in sensitive_spans:
            value = span["value"]
            g = self._count_occurrences(pre_texts, value)
            l = self._count_occurrences(outbound_texts, value)
            if g > 0:
                hits.append({"value": value, "field_type": span.get("field_type", "unknown"),
                             "G": g, "L": l})
        with self._lock:
            self.requests.append({
                "seq": len(self.requests),
                "pre_texts": pre_texts,
                "outbound_texts": outbound_texts,
                "sensitive_hits": hits,
            })

    def record_tool_call(self, name: str, args: dict[str, Any]) -> None:
        with self._lock:
            self.tool_calls.append({"name": name, "args": args})

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total_g = sum(h["G"] for r in self.requests for h in r["sensitive_hits"])
            total_l = sum(h["L"] for r in self.requests for h in r["sensitive_hits"])
            return {
                "cloud_request_count": len(self.requests),
                "total_sensitive_spans_pre": total_g,
                "total_sensitive_spans_leaked": total_l,
            }


def install(
    strategy: Any,
    sensitive_spans: list[dict[str, str]],
) -> EgressAuditRecorder:
    """patch langchain_middleware，使所有配置走同一审计 + 策略链。

    所有配置下都强制 privacy_protection_enabled=True，保证 C0-C3 走完全
    相同的代码路径（before_model → encode_messages / after_model → decode）。
    """
    from smartHome.m_agent.agent.hooks import langchain_middleware as mw
    from smartHome.m_agent.common.global_config import GLOBALCONFIG

    recorder = EgressAuditRecorder()
    sensitive_spans = list(sensitive_spans)

    def encode_wrapper(messages):
        outbound = strategy.encode_messages(messages)
        recorder.record_request(
            pre_messages=messages,
            outbound_messages=outbound,
            sensitive_spans=sensitive_spans,
        )
        # 记录本次模型侧发起的工具调用（消息里的 AIMessage.tool_calls 已是解码后的真值；
        # 对编码前 messages 里的历史 AIMessage 同样记录，供 PT 判定使用）
        for m in messages:
            if isinstance(m, AIMessage):
                for tc in getattr(m, "tool_calls", None) or []:
                    recorder.record_tool_call(tc.get("name", ""), tc.get("args", {}))
        return outbound

    mw.encode_messages = encode_wrapper
    mw.decode_text = strategy.decode_text
    GLOBALCONFIG.privacy_protection_enabled = True
    return recorder


def uninstall() -> None:
    """恢复 middleware 原始函数（子进程结束前调用，避免跨任务泄漏）。"""
    from smartHome.m_agent.agent.hooks import langchain_middleware as mw
    from smartHome.m_agent.agent.utils import privacy_codex
    from smartHome.m_agent.common.global_config import GLOBALCONFIG

    mw.encode_messages = privacy_codex.encode_messages
    mw.decode_text = privacy_codex.decode_text
    GLOBALCONFIG.privacy_protection_enabled = False


def build_strategy(config: str):
    """按配置名构造策略对象。C3 直接复用 privacy_codex 原实现。"""
    if config == "C0":
        from experiments.privacy_v3.privacy_strategies import IdentityStrategy
        return IdentityStrategy()
    if config == "C1":
        from experiments.privacy_v3.privacy_strategies import RedactionStrategy
        return RedactionStrategy()
    if config == "C2":
        from experiments.privacy_v3.privacy_strategies import OpaqueStrategy
        return OpaqueStrategy()
    if config == "C3":
        return _SVRStrategy()
    raise ValueError(f"unknown config: {config}")


class _SVRStrategy:
    """C3：直接复用 privacy_codex 的 encode/decode（@semantic@ 语义占位符）。"""

    name = "C3"

    def encode_messages(self, messages):
        from smartHome.m_agent.agent.utils.privacy_codex import encode_messages
        return encode_messages(messages)

    def decode_text(self, text):
        from smartHome.m_agent.agent.utils.privacy_codex import decode_text
        return decode_text(text)
