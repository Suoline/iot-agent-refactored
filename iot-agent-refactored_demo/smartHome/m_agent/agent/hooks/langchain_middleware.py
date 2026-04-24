from dataclasses import dataclass

from smartHome.m_agent.agent.utils.privacy_codex import transform_messages, encode_text, decode_text
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.agents.middleware import before_model, after_model, AgentState, before_agent, after_agent
from langgraph.runtime import Runtime
from typing import Any

@dataclass
class AgentContext:
    agent_name: str

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

        encoded_messages = transform_messages(messages, encode_text)

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