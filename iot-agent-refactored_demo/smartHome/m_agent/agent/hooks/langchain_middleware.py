from dataclasses import dataclass

from smartHome.m_agent.agent.utils.privacy_codex import transform_messages, encode_messages, decode_text
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.agents.middleware import before_model, after_model, AgentState, before_agent, after_agent, wrap_model_call
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from typing import Any
import time

@dataclass
class AgentContext:
    agent_name: str


def _is_retryable_upstream_unavailable(exc: Exception) -> bool:
    text = str(exc).lower()
    # Only transport failures observed from the configured OpenAI-compatible
    # proxy are repaired. Model/tool/validation failures remain single-shot.
    return any(
        marker in text
        for marker in (
            "upstream_unavailable",
            "上游服务暂时不可用",
            "request timed out",
            "apitimeouterror",
            "get_channel_failed",
            "可用渠道不存在",
        )
    )


def _record_transport_attempt(attempt: int, outcome: str, error: str | None = None) -> None:
    try:
        from smartHome.m_agent.memory import get_demo_memory_runtime

        get_demo_memory_runtime().record_transport_attempt(attempt, outcome, error)
    except Exception:
        pass


@wrap_model_call
def retry_upstream_unavailable(request, handler):
    """Repair one transient upstream failure without replaying Agent tools."""
    try:
        return handler(request)
    except Exception as exc:
        if not _is_retryable_upstream_unavailable(exc):
            raise
        _record_transport_attempt(1, "retryable_failure", f"{type(exc).__name__}:{str(exc)[:300]}")
        time.sleep(0.75)
        try:
            response = handler(request)
        except Exception as retry_exc:
            _record_transport_attempt(2, "failure", f"{type(retry_exc).__name__}:{str(retry_exc)[:300]}")
            raise
        _record_transport_attempt(2, "success")
        return response

@before_agent
def log_before_agent(state: AgentState, runtime: Runtime) -> None:
    GLOBALCONFIG.add_agent_name(runtime.context.agent_name)
    GLOBALCONFIG.print_nested_log("进入 "+runtime.context.agent_name+" ======================================")

@after_agent
def log_after_agent(state: AgentState, runtime: Runtime) -> None:
    GLOBALCONFIG.delete_agent_name(runtime.context.agent_name)

@before_model
def log_before(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    message = state['messages'][-1]
    s = repr(message)
    GLOBALCONFIG.print_nested_log(s)

    messages = state["messages"]
    # 智谱 GLM 等严格校验 messages 的 provider 不接受“仅 system 消息”的请求；
    # 本仓库各子 agent 均以 system-only 输入启动，这里统一补一条最小 user
    # 消息，保证消息序列对所有 provider 合法（对宽容 provider 无行为影响）。
    needs_user_padding = bool(messages) and not any(isinstance(m, HumanMessage) for m in messages)
    if needs_user_padding:
        messages = list(messages) + [HumanMessage(content="请根据上述系统指令开始执行任务。")]
        GLOBALCONFIG.print_nested_log("已补充 user 消息以满足 provider 的 messages 校验")

    if(GLOBALCONFIG.privacy_protection_enabled):
        encoded_messages = encode_messages(messages)

        print("进入模型前（编码后）======================")
        for m in encoded_messages:
            print(repr(m))
        print("======================")
        # 关键：返回更新后的 state
        return {"messages": encoded_messages}
    if needs_user_padding:
        return {"messages": messages}
    return None

@after_model
def log_response(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    message=state['messages'][-1]
    # The product runtime owns task-scoped audit state; importing lazily avoids
    # a module cycle while preserving the unmodified Agent execution path.
    try:
        from smartHome.m_agent.memory import get_demo_memory_runtime

        get_demo_memory_runtime().record_llm_response(message)
    except Exception:
        # Telemetry must not alter the behavior of a product Agent request.
        pass
    # 进程级 token 统计：不依赖任务上下文，任何入口的每次模型响应都计数，
    # 进程退出时自动打印摘要（同样不得影响 Agent 行为）。
    try:
        from smartHome.m_agent.common.token_tracker import get_token_tracker

        get_token_tracker().record(message, getattr(runtime.context, "agent_name", "unknown"))
    except Exception:
        pass
    s=repr(message)
    GLOBALCONFIG.print_nested_log(s)

    if (GLOBALCONFIG.privacy_protection_enabled):
        messages = state["messages"]

        decoded_messages = transform_messages(messages, decode_text)

        print("模型输出后（解码后）======================")
        for m in decoded_messages:
            print(repr(m))
        print("======================")
        return {"messages": decoded_messages}
    return None
