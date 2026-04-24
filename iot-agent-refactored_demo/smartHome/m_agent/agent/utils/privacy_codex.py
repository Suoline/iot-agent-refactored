from __future__ import annotations

from typing import Any
from copy import deepcopy

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
    ToolMessage,
)

# =========================
# 1) 你的编码 / 解码规则
# =========================

def encode_text(text: str) -> str:
    # todo 需要重写，这里只是示例：把“广东”编码成“@def5”
    return text.replace("广东", "@def5")

def decode_text(text: str) -> str:
    # todo 需要重写，这里只是示例：把“广东”编码成“@def5”
    return text.replace("@def5", "广东")


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
