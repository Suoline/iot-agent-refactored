from __future__ import annotations

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

# 这里维护模块级单例和累计映射，原因是中间件会对整批消息里的多个字符串逐个调用
# encode_text / decode_text，如果每次都新建 handler 或清空映射，就无法保证整批消息可逆。
_HANDLER_LOCK = RLock()
_PRIVACY_HANDLER: LLMPrivacyHandler | None = None
_ENCODE_MAP: dict[str, str] = {}
_DECODE_MAP: dict[str, str] = {}


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


def _make_unique_token(token: str) -> str:
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


def encode_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text:
        # 空串直接返回，但不能清空历史映射，否则同一批消息前面生成的 token 会失去可逆性。
        return text

    handler = _get_privacy_handler()
    with _HANDLER_LOCK:
        # 这里不直接调用 handler.encode_text，是因为原始实现会把映射限定在“单次文本”范围内；
        # 当前模块需要把每次编码得到的局部映射累计起来，适配 transform_messages 的逐字符串调用方式。
        messages = handler._build_encode_prompt(text)
        response = handler.llm.invoke(messages)
        response_text = getattr(response, "content", response)
        new_mapping = handler._parse_llm_mapping(str(response_text))
        effective_mapping = _merge_mapping(new_mapping)
        _sync_handler_maps(handler)
        return handler._replace_from_map(text, effective_mapping)

def decode_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text:
        return text

    handler = _get_privacy_handler()
    with _HANDLER_LOCK:
        if handler._TOKEN_PATTERN.search(text) and not _DECODE_MAP:
            raise ValueError("decode_map 为空；请先在同一个实例上调用 encode_text")

        _sync_handler_maps(handler)
        decoded_text = handler._replace_from_map(text, _DECODE_MAP)
        # 反向替换完成后，继续复用参考实现里的安全算术求值能力，
        # 支持类似 @token@*5-4 的表达式。
        return handler._evaluate_arithmetic_expressions(decoded_text)


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
