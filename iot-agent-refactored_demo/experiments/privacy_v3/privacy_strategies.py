"""C1/C2 隐私替换策略：与 C3（privacy_codex）共用同一检测层，只改变替换形式。

- C1 Redaction：所有检出敏感 span 统一替换为 [REDACTED]，不可逆。
- C2 Opaque Tokenization：每个原始值映射为固定长度随机令牌（[ID_A7F3] /
  [VAL_B219]），session 内可逆、不携带设备类型语义。

检测层与 privacy_codex 保持同源：同一组本地正则（_LOCAL_PATTERN_SPECS）、
同一 LLM 兜底触发条件（_should_use_llm）、同一批量识别 prompt 与同一隐私
LLM（默认本地 ollama），保证 C1/C2/C3 的差异只来自替换形式（实验方案 §5）。

映射表生命周期与 privacy_codex 一致：进程级（一个实验 unit 一个子进程），
进程退出即销毁，等价于 session 结束销毁。
"""
from __future__ import annotations

import json
import random
import re
import string
import threading
from typing import Any

from langchain_core.messages import BaseMessage, AIMessage

# ---------------------------------------------------------------------------
# 检测层（与 privacy_codex 同源）
# ---------------------------------------------------------------------------

_LOCAL_PATTERN_SPECS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "ip_address"),
    (re.compile(
        r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
    ), "timestamp"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "date"),
    (re.compile(r"\b[0-9a-fA-F]{32}\b"), "context_id"),
    (re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z0-9_]+(?:[._][a-zA-Z0-9_]+)*\b"), "entity_id"),
)

_FALLBACK_KEYWORD_PATTERN = re.compile(
    r"(wifi|ssid|token|api[_-]?key|secret|password|context|user[_-]?id|隐私|敏感|地址|账号|密码)",
    re.IGNORECASE,
)

# 检测层识别的语义类别 → C2 令牌前缀（标识符 vs 值）
_ID_SEMANTICS = {"entity_id", "context_id", "ip_address"}
_VAL_SEMANTICS = {"timestamp", "date", "status", "value", "temperature", "humidity",
                  "volume", "brightness", "person", "room", "user", "name"}

# 与 privacy_codex._normalize_token_name 一致语义的 @token@ 形态（检测层跳过）
_PRIVACY_TOKEN_PATTERN = re.compile(r"@[A-Za-z0-9_]+@")

# 家庭定义注入的扩展敏感词典（人名/房间名/别名）：由 runner 按任务注册
_EXTRA_SENSITIVE: dict[str, str] = {}  # value -> semantic_name
_EXTRA_LOCK = threading.Lock()


def register_extra_sensitive(mapping: dict[str, str]) -> None:
    """注册任务金标准中的额外敏感值（人名、房间名、设备别名等）。"""
    with _EXTRA_LOCK:
        _EXTRA_SENSITIVE.update(mapping)


def reset_extra_sensitive() -> None:
    with _EXTRA_LOCK:
        _EXTRA_SENSITIVE.clear()


def _build_local_detection(text: str) -> dict[str, str]:
    """本地检测：正则 + 扩展词典。返回 {原始值: 语义类别}。"""
    mapping: dict[str, str] = {}
    for pattern, semantic in _LOCAL_PATTERN_SPECS:
        for match in pattern.finditer(text):
            original = match.group(0)
            if original and not _PRIVACY_TOKEN_PATTERN.fullmatch(original):
                mapping.setdefault(original, semantic)
    with _EXTRA_LOCK:
        for value, semantic in _EXTRA_SENSITIVE.items():
            if value and value in text:
                mapping.setdefault(value, semantic)
    return mapping


def _should_use_llm(original_text: str, detected: dict[str, str]) -> bool:
    """与 privacy_codex._should_use_llm 同构：无本地命中必走 LLM；有命中则按关键词。"""
    if not original_text.strip():
        return False
    if not detected:
        return True
    return bool(_FALLBACK_KEYWORD_PATTERN.search(original_text))


def _build_batch_encode_prompt(texts: list[str]) -> list[dict[str, str]]:
    system_prompt = """
你是隐私信息处理助手。你的任务是从一组文本中识别仍未被占位符替换的敏感信息，并为每个敏感值生成可逆的语义化占位符。

输出规则：
1. 只返回 JSON，不要添加解释、Markdown、代码块或其他文字。
2. JSON 格式必须严格为 {"encoded_text": {"原始值": "semantic_name"}}。
3. semantic_name 只能包含字母、数字、下划线，优先使用语义化命名。
4. 如果同类型出现多个值，请追加编号，如 status_01、status_02。
5. 不要改写原文，不要翻译，不要总结，只返回映射表。
6. 已经形如 @token@ 的内容不要重复处理。

优先识别但不限于以下隐私类型：
- WiFi SSID
- 唯一标识符或上下文 ID
- 敏感状态值
- 其他上下文明确要求隐藏的个人或家庭信息

以下内容默认不视为隐私，除非上下文明确要求：
- friendly_name
- 普通描述性文本
""".strip()
    payload = {"texts": [{"id": f"text_{i}", "content": t} for i, t in enumerate(texts)]}
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class _DetectorLLM:
    """隐私检测 LLM 单例（与 privacy_codex 共用 provider 解析与模型）。"""
    _lock = threading.Lock()
    _llm = None

    @classmethod
    def get(cls):
        if cls._llm is None:
            with cls._lock:
                if cls._llm is None:
                    from smartHome.m_agent.common.get_llm import create_custom_llm
                    from smartHome.m_agent.common.global_config import GLOBALCONFIG
                    from smartHome.m_agent.agent.utils.privacy_codex import (
                        _resolve_privacy_provider,
                    )
                    provider = _resolve_privacy_provider()
                    cls._llm = create_custom_llm(
                        model=GLOBALCONFIG.configparser.get(provider, "model"),
                        base_url=GLOBALCONFIG.configparser.get(provider, "base_url"),
                        api_key=GLOBALCONFIG.configparser.get(provider, "api_key"),
                    )
        return cls._llm


def _llm_detect(texts: list[str]) -> dict[str, str]:
    """LLM 兜底检测：返回 {原始值: semantic_name}。失败时返回空（本地结果仍有效）。"""
    if not texts:
        return {}
    try:
        response = _DetectorLLM.get().invoke(_build_batch_encode_prompt(texts))
        content = getattr(response, "content", response)
        data = json.loads(str(content))
        raw = data.get("encoded_text", {}) if isinstance(data, dict) else {}
        return {
            str(k): re.sub(r"[^0-9A-Za-z_]+", "_", str(v)).strip("_") or "value"
            for k, v in raw.items() if isinstance(k, str) and k
        }
    except Exception:
        return {}


def detect_sensitive(texts: list[str]) -> dict[str, str]:
    """统一检测入口：本地正则/词典 + 与 C3 相同触发条件的 LLM 兜底。

    返回 {原始值: 语义类别}。这是 C1/C2 的唯一检测层，与 C3 共用
    相同的本地规则、相同 prompt、相同隐私 LLM。占位符形态的值
    （@token@ / [REDACTED] / [ID_xxxx]）不视为敏感明文。

    进程级文本缓存：agent 每轮会把全部历史消息重新送编码，重复文本的
    本地命中与 LLM 检出结果直接复用（检测是纯函数，缓存无损）。
    """
    merged: dict[str, str] = {}
    llm_candidates: list[str] = []
    for text in texts:
        local = _DETECT_CACHE.get(text)
        if local is None:
            local = _build_local_detection(text)
            _DETECT_CACHE[text] = local
        for value, semantic in local.items():
            merged.setdefault(value, semantic)
        if text not in _LLM_CACHE and _should_use_llm(text, local):
            llm_candidates.append(text)
    if llm_candidates:
        for text, mapping in zip(llm_candidates, _llm_detect_per_text(llm_candidates)):
            _LLM_CACHE[text] = mapping
    for text in texts:
        for value, semantic in _LLM_CACHE.get(text, {}).items():
            if not _is_placeholder_form(value):
                merged.setdefault(value, semantic)
    return {v: s for v, s in merged.items() if not _is_placeholder_form(v)}


_DETECT_CACHE: dict[str, dict[str, str]] = {}
_LLM_CACHE: dict[str, dict[str, str]] = {}


def _llm_detect_per_text(texts: list[str]) -> list[dict[str, str]]:
    """按文本粒度的 LLM 检测（批量 prompt 一次调用，结果按序拆分）。

    批量 prompt 的返回是全量映射，无法区分来源文本；为保证缓存 key
    正确，这里对每个候选单独调用（本地 ollama 延迟可控）。
    """
    return [_llm_detect([t]) for t in texts]


def _is_placeholder_form(value: str) -> bool:
    if not value:
        return True
    if _PRIVACY_TOKEN_PATTERN.fullmatch(value):
        return True
    if value == _REDACTED or _TOKEN_PATTERN_C2.fullmatch(value):
        return True
    return False


# ---------------------------------------------------------------------------
# 替换层
# ---------------------------------------------------------------------------

_REDACTED = "[REDACTED]"
_ESCAPED_REDACTED = "\\[REDACTED\\]"
_TOKEN_PATTERN_C2 = re.compile(r"\[(ID|VAL)_[0-9A-F]{4}\]")
_ESCAPED_TOKEN_C2 = re.compile(r"\\\[(ID|VAL)_[0-9A-F]{4}\\\]")


class OpaqueTokenizer:
    """C2 的可逆映射表：原始值 <-> 固定长度随机令牌。"""

    def __init__(self) -> None:
        self._encode: dict[str, str] = {}
        self._decode: dict[str, str] = {}
        self._rng = random.Random(20260925)
        self._lock = threading.RLock()

    def _new_token(self, semantic: str) -> str:
        prefix = "ID" if semantic in _ID_SEMANTICS else "VAL"
        while True:
            suffix = "".join(self._rng.choice(string.hexdigits.upper()[:16]) for _ in range(4))
            token = f"[{prefix}_{suffix}]"
            if token not in self._decode:
                return token

    def encode_mapping(self, detection: dict[str, str]) -> dict[str, str]:
        """对检测结果建立/复用映射，返回 {原始值: 令牌}。"""
        out: dict[str, str] = {}
        with self._lock:
            for original, semantic in detection.items():
                token = self._encode.get(original)
                if token is None:
                    token = self._new_token(semantic)
                    self._encode[original] = token
                    self._decode[token] = original
                out[original] = token
        return out

    def decode_text(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        with self._lock:
            for token in sorted(self._decode.keys(), key=len, reverse=True):
                if token in text:
                    text = text.replace(token, self._decode[token])
        return text

    def decode_unknown_kept(self, text: str) -> str:
        """未知令牌不映射、原样保留（E0.5：不能绑定到任何设备）。"""
        return self.decode_text(text)


# ---------------------------------------------------------------------------
# 消息级 encode/decode（接口与 privacy_codex.encode_messages 对齐）
# ---------------------------------------------------------------------------

def _collect_strings(obj: Any, output: list[str]) -> None:
    if isinstance(obj, str):
        output.append(obj)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_strings(item, output)
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_strings(value, output)


def _replace_text(text: str, mapping: dict[str, str]) -> str:
    for original in sorted(mapping.keys(), key=len, reverse=True):
        text = text.replace(original, mapping[original])
    return text


def _map_message_strings(messages: list[BaseMessage], fn) -> list[BaseMessage]:
    """对消息中所有字符串应用 fn，保持消息结构（content/additional_kwargs/tool_calls）。"""
    from copy import deepcopy

    def walk(obj: Any):
        if isinstance(obj, str):
            return fn(obj)
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, tuple):
            return tuple(walk(x) for x in obj)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    out: list[BaseMessage] = []
    for message in messages:
        msg = deepcopy(message)
        if hasattr(msg, "content"):
            msg.content = walk(msg.content)
        if getattr(msg, "additional_kwargs", None) is not None:
            msg.additional_kwargs = walk(msg.additional_kwargs)
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None) is not None:
            msg.tool_calls = walk(msg.tool_calls)
        if isinstance(msg, AIMessage) and getattr(msg, "invalid_tool_calls", None) is not None:
            msg.invalid_tool_calls = walk(msg.invalid_tool_calls)
        out.append(msg)
    return out


class RedactionStrategy:
    """C1：全部检出项 → [REDACTED]，不可逆；解码为恒等。"""

    name = "C1"

    def __init__(self) -> None:
        self.detections: set[str] = set()  # 累计检出值（E2 检测器评估用）

    def encode_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        strings: list[str] = []
        for m in messages:
            _collect_strings(getattr(m, "content", ""), strings)
            _collect_strings(getattr(m, "additional_kwargs", None), strings)
            if isinstance(m, AIMessage):
                _collect_strings(getattr(m, "tool_calls", None), strings)
                _collect_strings(getattr(m, "invalid_tool_calls", None), strings)
        unique = list(dict.fromkeys(s for s in strings if s))
        detection = detect_sensitive(unique)
        self.detections.update(detection.keys())
        mapping = {value: _REDACTED for value in detection}
        return _map_message_strings(messages, lambda t: _replace_text(t, mapping))

    def decode_text(self, text: str) -> str:
        return text


class OpaqueStrategy:
    """C2：每个检出项 → 固定长度随机令牌，session 内可逆。"""

    name = "C2"

    def __init__(self) -> None:
        self.tokenizer = OpaqueTokenizer()
        self.detections: set[str] = set()

    def encode_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        strings: list[str] = []
        for m in messages:
            _collect_strings(getattr(m, "content", ""), strings)
            _collect_strings(getattr(m, "additional_kwargs", None), strings)
            if isinstance(m, AIMessage):
                _collect_strings(getattr(m, "tool_calls", None), strings)
                _collect_strings(getattr(m, "invalid_tool_calls", None), strings)
        unique = list(dict.fromkeys(s for s in strings if s))
        detection = detect_sensitive(unique)
        self.detections.update(detection.keys())
        mapping = self.tokenizer.encode_mapping(detection)
        return _map_message_strings(messages, lambda t: _replace_text(t, mapping))

    def decode_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        return self.tokenizer.decode_unknown_kept(text)

    def decode_unknown_kept(self, text: str) -> str:
        return self.decode_text(text)


class IdentityStrategy:
    """C0：不替换（审计仍记录 pre/outbound，两者一致）。"""

    name = "C0"

    def encode_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        return messages

    def decode_text(self, text: str) -> str:
        return text
