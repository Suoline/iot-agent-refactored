from dataclasses import dataclass

from smartHome.m_agent.agent.utils.privacy_codex import transform_messages, encode_messages, decode_text
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.agents.middleware import before_model, after_model, AgentState, before_agent, after_agent, wrap_model_call
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
    if(GLOBALCONFIG.privacy_protection_enabled):
        messages = state["messages"]

        encoded_messages = encode_messages(messages)

        print("进入模型前（编码后）======================")
        for m in encoded_messages:
            print(repr(m))
        print("======================")
        # 关键：返回更新后的 state
        return {"messages": encoded_messages}
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
