from __future__ import annotations

import json
import re
from copy import deepcopy
from threading import RLock
from typing import Any

from langchain_core.messages import (
    BaseMessage,
    AIMessage,
)

from smartHome.m_agent.agent.utils.llm_privacy_handler import LLMPrivacyHandler
from smartHome.m_agent.common.get_llm import create_custom_llm
from smartHome.m_agent.common.global_config import GLOBALCONFIG

# =========================
# 1) 你的编码 / 解码规则
# =========================

# 这里维护模块级单例和累计映射，原因是中间件会跨多轮 agent 复用 token。
# 所有映射（正则、LLM、批量）都必须先进入 _merge_mapping，确保 token 全局唯一。
_HANDLER_LOCK = RLock()
_PRIVACY_HANDLER: LLMPrivacyHandler | None = None
_ENCODE_MAP: dict[str, str] = {}
_DECODE_MAP: dict[str, str] = {}
_TEXT_CACHE: dict[str, str] = {}

_LOCAL_PATTERN_SPECS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "ip_address",
    ),
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}"
            r"[Tt ]\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?"
            r"(?:Z|[+-]\d{2}:\d{2})?\b"
        ),
        "timestamp",
    ),
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        "date",
    ),
    (
        re.compile(r"\b[0-9a-fA-F]{32}\b"),
        "context_id",
    ),
    (
        re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z0-9_]+(?:[._][a-zA-Z0-9_]+)*\b"),
        "entity_id",
    ),
)
_FALLBACK_KEYWORD_PATTERN = re.compile(
    r"(wifi|ssid|token|api[_-]?key|secret|password|context|user[_-]?id|隐私|敏感|地址|账号|密码)",
    re.IGNORECASE,
)


def _get_privacy_handler() -> LLMPrivacyHandler:
    global _PRIVACY_HANDLER
    if _PRIVACY_HANDLER is None:
        with _HANDLER_LOCK:
            if _PRIVACY_HANDLER is None:
                # todo 需要改成本地小模型，也就是ollama
                #  目前隐私编码也走项目当前配置的模型、base_url 和 api_key，避免引入额外配置分叉。
                provider = "deepseek"
                llm = create_custom_llm(
                    # model=GLOBALCONFIG.model,
                    # base_url=GLOBALCONFIG.base_url,
                    # api_key=GLOBALCONFIG.api_key,
                    model=GLOBALCONFIG.configparser.get(provider, 'model'),
                    base_url=GLOBALCONFIG.configparser.get(provider, 'base_url'),
                    api_key=GLOBALCONFIG.configparser.get(provider, 'api_key'),
                )
                _PRIVACY_HANDLER = LLMPrivacyHandler(llm)
    return _PRIVACY_HANDLER


def _sync_handler_maps(handler: LLMPrivacyHandler) -> None:
    # handler 内部也有映射表；这里保持模块级状态与 handler 状态同步，
    # 以便复用其替换与算术求值逻辑。
    handler.encode_map = dict(_ENCODE_MAP)
    handler.decode_map = dict(_DECODE_MAP)


def _normalize_token_name(semantic_name: str) -> str:
    semantic = str(semantic_name).strip().strip("@")
    semantic = re.sub(r"[^0-9A-Za-z_]+", "_", semantic)
    semantic = re.sub(r"_+", "_", semantic).strip("_")
    if not semantic:
        semantic = "value"
    if semantic[0].isdigit():
        semantic = f"value_{semantic}"
    return f"@{semantic}@"


def _make_unique_token(token: str) -> str:
    token = _normalize_token_name(token)
    if token not in _DECODE_MAP:
        return token

    semantic = token.strip("@")
    suffix = 2
    while True:
        candidate = f"@{semantic}_{suffix:02d}@"
        if candidate not in _DECODE_MAP:
            return candidate
        suffix += 1


def _merge_mapping(new_mapping: dict[str, str]) -> dict[str, str]:
    # LLM 每次只为当前文本返回局部映射，这里把它合并进全局映射：
    # 1. 已出现过的原始值直接复用旧 token，保证跨消息一致。
    # 2. 新值如果 token 撞名，则自动补序号，保证 token 全局唯一。
    merged_mapping: dict[str, str] = {}

    for original, proposed_token in new_mapping.items():
        if original in _ENCODE_MAP:
            merged_mapping[original] = _ENCODE_MAP[original]
            continue

        token = _make_unique_token(proposed_token)
        _ENCODE_MAP[original] = token
        _DECODE_MAP[token] = original
        merged_mapping[original] = token

    return merged_mapping


def _replace_from_map(text: str, replace_map: dict[str, str]) -> str:
    replaced_text = text
    for original_value in sorted(replace_map.keys(), key=len, reverse=True):
        replaced_text = replaced_text.replace(original_value, replace_map[original_value])
    return replaced_text


def _build_local_mapping(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pattern, semantic_name in _LOCAL_PATTERN_SPECS:
        for match in pattern.finditer(text):
            original = match.group(0)
            if original and not LLMPrivacyHandler._TOKEN_PATTERN.fullmatch(original):
                mapping[original] = _normalize_token_name(semantic_name)
    return mapping


def _apply_local_rules(text: str) -> tuple[str, bool]:
    local_mapping = _build_local_mapping(text)
    if not local_mapping:
        return text, False

    effective_mapping = _merge_mapping(local_mapping)
    return _replace_from_map(text, effective_mapping), True


def _should_use_llm(original_text: str, locally_encoded_text: str, local_changed: bool) -> bool:
    stripped = locally_encoded_text.strip()
    if not stripped:
        return False
    if LLMPrivacyHandler._TOKEN_PATTERN.fullmatch(stripped):
        return False
    if local_changed:
        return bool(_FALLBACK_KEYWORD_PATTERN.search(original_text))
    return True


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
    user_payload = {
        "texts": [
            {"id": f"text_{index}", "content": text}
            for index, text in enumerate(texts)
        ]
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _llm_mapping_for_texts(texts: list[str]) -> dict[str, str]:
    if not texts:
        return {}

    handler = _get_privacy_handler()
    messages = _build_batch_encode_prompt(texts)
    response = handler.llm.invoke(messages)
    response_text = getattr(response, "content", response)
    new_mapping = handler._parse_llm_mapping(str(response_text))
    effective_mapping = _merge_mapping(new_mapping)
    _sync_handler_maps(handler)
    return effective_mapping


def _encode_texts(texts: list[str]) -> list[str]:
    encoded_results: list[str] = []
    llm_candidate_indexes: list[int] = []
    llm_candidate_texts: list[str] = []

    for text in texts:
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")
        if not text:
            encoded_results.append(text)
            continue
        if text in _TEXT_CACHE:
            encoded_results.append(_TEXT_CACHE[text])
            continue

        locally_encoded_text, local_changed = _apply_local_rules(text)
        encoded_results.append(locally_encoded_text)
        if _should_use_llm(text, locally_encoded_text, local_changed):
            llm_candidate_indexes.append(len(encoded_results) - 1)
            llm_candidate_texts.append(locally_encoded_text)

    if llm_candidate_texts:
        effective_mapping = _llm_mapping_for_texts(llm_candidate_texts)
        for index in llm_candidate_indexes:
            encoded_results[index] = _replace_from_map(encoded_results[index], effective_mapping)

    for original, encoded in zip(texts, encoded_results):
        if original:
            _TEXT_CACHE[original] = encoded

    return encoded_results


def encode_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text:
        # 空串直接返回，但不能清空历史映射，否则同一批消息前面生成的 token 会失去可逆性。
        return text

    with _HANDLER_LOCK:
        return _encode_texts([text])[0]

def decode_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text:
        return text

    with _HANDLER_LOCK:
        if LLMPrivacyHandler._TOKEN_PATTERN.search(text) and not _DECODE_MAP:
            raise ValueError("decode_map 为空；请先在同一个实例上调用 encode_text")

        decoded_text = _replace_from_map(text, _DECODE_MAP)
        if _PRIVACY_HANDLER is None:
            return decoded_text

        _sync_handler_maps(_PRIVACY_HANDLER)
        # 反向替换完成后，继续复用参考实现里的安全算术求值能力，支持类似 @token@*5-4 的表达式。
        return _PRIVACY_HANDLER._evaluate_arithmetic_expressions(decoded_text)


# =========================
# 2) 递归处理任意 Python 结构
# =========================

def transform_obj(obj: Any, text_fn) -> Any:
    """
    递归处理任意对象中可能出现的字符串。
    只转换 str / list / dict。
    其他类型原样返回。
    """
    if isinstance(obj, str):
        return text_fn(obj)

    if isinstance(obj, list):
        return [transform_obj(x, text_fn) for x in obj]

    if isinstance(obj, tuple):
        return tuple(transform_obj(x, text_fn) for x in obj)

    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            # 一般不建议改 key，只改 value
            new_obj[k] = transform_obj(v, text_fn)
        return new_obj

    return obj


# =========================
# 3) 处理 Message 对象
# =========================

def transform_message(message: BaseMessage, text_fn) -> BaseMessage:
    """
    对单条消息做转换，尽量兼容不同消息类型。
    """
    msg = deepcopy(message)

    # 1. content 可能是 str，也可能是 list[dict] 等多模态结构
    if hasattr(msg, "content"):
        msg.content = transform_obj(msg.content, text_fn)

    # 2. additional_kwargs 里也可能藏文本或工具参数
    if hasattr(msg, "additional_kwargs") and msg.additional_kwargs is not None:
        msg.additional_kwargs = transform_obj(msg.additional_kwargs, text_fn)

    # 3. response_metadata 一般不建议改
    # 如果你确实想全量编码，也可以打开
    # if hasattr(msg, "response_metadata") and msg.response_metadata is not None:
    #     msg.response_metadata = transform_obj(msg.response_metadata, text_fn)

    # 4. AIMessage 特有：tool_calls
    if isinstance(msg, AIMessage):
        if getattr(msg, "tool_calls", None) is not None:
            msg.tool_calls = transform_obj(msg.tool_calls, text_fn)

        if hasattr(msg, "invalid_tool_calls") and msg.invalid_tool_calls is not None:
            msg.invalid_tool_calls = transform_obj(msg.invalid_tool_calls, text_fn)

    # 5. ToolMessage 特有：name / tool_call_id 一般不建议改
    # content 已经处理过了，name 和 tool_call_id 最好保持原样

    return msg


def transform_messages(messages: list[BaseMessage], text_fn) -> list[BaseMessage]:
    return [transform_message(m, text_fn) for m in messages]


def _collect_strings(obj: Any, output: list[str]) -> None:
    if isinstance(obj, str):
        output.append(obj)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_strings(item, output)
        return
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_strings(value, output)


def _replace_strings(obj: Any, encoded_by_original: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return encoded_by_original.get(obj, obj)
    if isinstance(obj, list):
        return [_replace_strings(item, encoded_by_original) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_replace_strings(item, encoded_by_original) for item in obj)
    if isinstance(obj, dict):
        return {
            key: _replace_strings(value, encoded_by_original)
            for key, value in obj.items()
        }
    return obj


def encode_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    strings: list[str] = []
    for message in messages:
        if hasattr(message, "content"):
            _collect_strings(message.content, strings)
        if hasattr(message, "additional_kwargs") and message.additional_kwargs is not None:
            _collect_strings(message.additional_kwargs, strings)
        if isinstance(message, AIMessage):
            if getattr(message, "tool_calls", None) is not None:
                _collect_strings(message.tool_calls, strings)
            if hasattr(message, "invalid_tool_calls") and message.invalid_tool_calls is not None:
                _collect_strings(message.invalid_tool_calls, strings)

    unique_strings = list(dict.fromkeys(strings))
    with _HANDLER_LOCK:
        encoded_strings = _encode_texts(unique_strings)
    encoded_by_original = dict(zip(unique_strings, encoded_strings))

    encoded_messages: list[BaseMessage] = []
    for message in messages:
        msg = deepcopy(message)
        if hasattr(msg, "content"):
            msg.content = _replace_strings(msg.content, encoded_by_original)
        if hasattr(msg, "additional_kwargs") and msg.additional_kwargs is not None:
            msg.additional_kwargs = _replace_strings(msg.additional_kwargs, encoded_by_original)
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None) is not None:
                msg.tool_calls = _replace_strings(msg.tool_calls, encoded_by_original)
            if hasattr(msg, "invalid_tool_calls") and msg.invalid_tool_calls is not None:
                msg.invalid_tool_calls = _replace_strings(msg.invalid_tool_calls, encoded_by_original)
        encoded_messages.append(msg)

    return encoded_messages
