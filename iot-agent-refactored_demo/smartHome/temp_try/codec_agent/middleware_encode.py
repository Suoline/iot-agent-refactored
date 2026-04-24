from langchain.agents import create_agent

from smartHome.m_agent.common.get_llm import get_llm
from dataclasses import dataclass
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.agents.middleware import before_model, after_model, AgentState, before_agent, after_agent
from langgraph.runtime import Runtime
from typing import Any
from langchain.tools import tool

from smartHome.temp_try.codec_agent.test_encoder import transform_messages, encode_text, decode_text


@dataclass
class AgentContext:
    agent_name: str

@before_agent
def test_before_agent(state: AgentState, runtime: Runtime) -> None:
    # GLOBALCONFIG.add_agent_name(runtime.context.agent_name)
    print("进入 "+runtime.context.agent_name+" ======================================")

@after_agent
def test_after_agent(state: AgentState, runtime: Runtime) -> None:
    print("end__agent"+"======================")

@before_model
def test_before(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    messages = state["messages"]

    encoded_messages = transform_messages(messages, encode_text)

    print("进入模型前（编码后）======================")
    for m in encoded_messages:
        print(repr(m))

    # 关键：返回更新后的 state
    return {"messages": encoded_messages}


@after_model
def test_response(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    messages = state["messages"]

    decoded_messages = transform_messages(messages, decode_text)

    print("模型输出后（解码后）======================")
    for m in decoded_messages:
        print(repr(m))

    return {"messages": decoded_messages}


@tool
def query_weather(location:str):
    """
    查询location的天气情况
    """
    return "多云"

def run_llm():
    prompt = f"""
        查询用户给定地点的天气，数据可能已经被加密成@xx形式，你可以直接引用加密数据，后续会解密。
        """
    # 如果需要知晓某个设备ID的实时状态来制定计划，请使用@tool get_device_current_status。例如类似调亮灯光这类任务，需要先知道当前亮度值，才能确定要调整后亮度值。
    agent = create_agent(
        model=get_llm(),
        tools=[
                query_weather,
            ],
        middleware=[test_before,test_response, test_before_agent, test_after_agent],
        context_schema=AgentContext
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "广东"},
        ]},
        context=AgentContext(agent_name="编码测试")
    )

    # msg_content = "\n" + "\n".join(map(repr, result["messages"]))
    # GLOBALCONFIG.logger.info("================" + "规划阶段")
    # GLOBALCONFIG.logger.info(msg_content)
    # GLOBALCONFIG.logger.info("\n")

    ans = result["messages"][-1].content

if __name__ == "__main__":
    run_llm()
    """
    进入 编码测试 ======================================
进入模型前（编码后）======================
SystemMessage(content='\n        查询用户给定地点的天气，数据可能已经被加密成@xx形式，你可以直接引用加密数据，后续会解密。\n        ', additional_kwargs={}, response_metadata={}, id='953a76dc-93db-4d3e-849a-0eb6f32f575e')
HumanMessage(content='@def5', additional_kwargs={}, response_metadata={}, id='35f87e91-c47b-415b-a087-256d5f5995a8')
模型输出后（解码后）======================
SystemMessage(content='\n        查询用户给定地点的天气，数据可能已经被加密成@xx形式，你可以直接引用加密数据，后续会解密。\n        ', additional_kwargs={}, response_metadata={}, id='953a76dc-93db-4d3e-849a-0eb6f32f575e')
HumanMessage(content='广东', additional_kwargs={}, response_metadata={}, id='35f87e91-c47b-415b-a087-256d5f5995a8')
AIMessage(content='', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 153, 'prompt_tokens': 164, 'total_tokens': 317, 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': None, 'reasoning_tokens': 128, 'rejected_prediction_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': None}}, 'model_provider': 'openai', 'model_name': 'gpt-5-mini-2025-08-07', 'system_fingerprint': None, 'id': 'chatcmpl-DUskcxMRJUpCDBAeFWqjeMPmqbcVO', 'finish_reason': 'tool_calls', 'logprobs': None}, id='lc_run--019d90e8-c64f-7542-9ac0-c6c5bff696c5-0', tool_calls=[{'name': 'query_weather', 'args': {'location': '广东'}, 'id': 'call_0ZJgxQAkHx3MXWAfDXVaYmdD', 'type': 'tool_call'}], usage_metadata={'input_tokens': 164, 'output_tokens': 153, 'total_tokens': 317, 'input_token_details': {}, 'output_token_details': {'reasoning': 128}})
进入模型前（编码后）======================
SystemMessage(content='\n        查询用户给定地点的天气，数据可能已经被加密成@xx形式，你可以直接引用加密数据，后续会解密。\n        ', additional_kwargs={}, response_metadata={}, id='953a76dc-93db-4d3e-849a-0eb6f32f575e')
HumanMessage(content='@def5', additional_kwargs={}, response_metadata={}, id='35f87e91-c47b-415b-a087-256d5f5995a8')
AIMessage(content='', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 153, 'prompt_tokens': 164, 'total_tokens': 317, 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': None, 'reasoning_tokens': 128, 'rejected_prediction_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': None}}, 'model_provider': 'openai', 'model_name': 'gpt-5-mini-2025-08-07', 'system_fingerprint': None, 'id': 'chatcmpl-DUskcxMRJUpCDBAeFWqjeMPmqbcVO', 'finish_reason': 'tool_calls', 'logprobs': None}, id='lc_run--019d90e8-c64f-7542-9ac0-c6c5bff696c5-0', tool_calls=[{'name': 'query_weather', 'args': {'location': '@def5'}, 'id': 'call_0ZJgxQAkHx3MXWAfDXVaYmdD', 'type': 'tool_call'}], usage_metadata={'input_tokens': 164, 'output_tokens': 153, 'total_tokens': 317, 'input_token_details': {}, 'output_token_details': {'reasoning': 128}})
ToolMessage(content='多云', name='query_weather', id='b6d6274c-47cc-43f6-b7fa-be60a2211b9c', tool_call_id='call_0ZJgxQAkHx3MXWAfDXVaYmdD')
模型输出后（解码后）======================
SystemMessage(content='\n        查询用户给定地点的天气，数据可能已经被加密成@xx形式，你可以直接引用加密数据，后续会解密。\n        ', additional_kwargs={}, response_metadata={}, id='953a76dc-93db-4d3e-849a-0eb6f32f575e')
HumanMessage(content='广东', additional_kwargs={}, response_metadata={}, id='35f87e91-c47b-415b-a087-256d5f5995a8')
AIMessage(content='', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 153, 'prompt_tokens': 164, 'total_tokens': 317, 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': None, 'reasoning_tokens': 128, 'rejected_prediction_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': None}}, 'model_provider': 'openai', 'model_name': 'gpt-5-mini-2025-08-07', 'system_fingerprint': None, 'id': 'chatcmpl-DUskcxMRJUpCDBAeFWqjeMPmqbcVO', 'finish_reason': 'tool_calls', 'logprobs': None}, id='lc_run--019d90e8-c64f-7542-9ac0-c6c5bff696c5-0', tool_calls=[{'name': 'query_weather', 'args': {'location': '广东'}, 'id': 'call_0ZJgxQAkHx3MXWAfDXVaYmdD', 'type': 'tool_call'}], usage_metadata={'input_tokens': 164, 'output_tokens': 153, 'total_tokens': 317, 'input_token_details': {}, 'output_token_details': {'reasoning': 128}})
ToolMessage(content='多云', name='query_weather', id='b6d6274c-47cc-43f6-b7fa-be60a2211b9c', tool_call_id='call_0ZJgxQAkHx3MXWAfDXVaYmdD')
AIMessage(content='地点 广东 的天气：多云', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 12, 'prompt_tokens': 196, 'total_tokens': 208, 'completion_tokens_details': {'accepted_prediction_tokens': 0, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': None}}, 'model_provider': 'openai', 'model_name': 'gpt-5-mini-2025-08-07', 'system_fingerprint': None, 'id': 'chatcmpl-DUsmzaP0PeWkv6EIUexWZWcF4uFxe', 'finish_reason': 'stop', 'logprobs': None}, id='lc_run--019d90eb-0957-7423-8197-3c60e6e0a31d-0', usage_metadata={'input_tokens': 196, 'output_tokens': 12, 'total_tokens': 208, 'input_token_details': {}, 'output_token_details': {'reasoning': 0}})
end__agent======================
    """