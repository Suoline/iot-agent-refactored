import json
import os

from smartHome.m_agent.agent.base_home_agent import SmartHomeAgentState, run_ourAgent
from typing import TypedDict, Literal
from langchain.tools import tool
from langchain.agents import create_agent

from langgraph.types import Command
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from smartHome.m_agent.agent.hooks.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.common.global_config import GLOBALCONFIG

def read_json_file(key: str, default=None):
    """
    读取 note/base_agent_result.json 中指定 key 的值
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "note", "base_agent_result.json")

    if not os.path.exists(file_path):
        return default

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return default

    return data.get(key, default)
def node_run_base(state: SmartHomeAgentState) -> Command[Literal["verify_node"]]:
    result=run_ourAgent(task=state["command"])
    return Command(
        update={
            # "messages": content,
            # "final_answer": content
        },
        goto="verify_node"
    )
@tool
def run_check(task:str):
    """
    调用设备执行任务task
    """
    result = run_ourAgent(task=task)
    return result

def node_verify(state: SmartHomeAgentState) -> Command[Literal[END]]:
    """
    验证节点 - 在主流程完成后，调用验证agent检查设备是否执行成功
    验证通过设备周边传感器确认执行结果
    :param state:
    :return:
    """
    # # 从state中获取planning_result和filter_devices
    # planning_result = state.get("planning_result", "")
    # filter_devices = state.get("filter_devices", "[]")
    planning_result = read_json_file("planning_result", "")
    filter_devices = read_json_file("filter_devices", [])

    # 构建验证查询
    verify_prompt = f"""
    【原始任务】：{state["command"]}
    【规划执行结果】：{planning_result}
    【涉及的设备】：{filter_devices}

    请重新调用agent验证上述设备是否成功执行了任务。
    验证必须通过设备所在位置的周边传感器来确认设备状态变化，而不能直接查看设备自身状态。

    例如：
    - 对于卧室灯，可以通过卧室的人体传感器光照强度来确认灯是否已打开
    - 对于空调，可以通过室温传感器确认空调是否在制冷/制热

    请生成一个验证查询并调用验证流程。
    """

    # 创建验证agent
    verify_agent = create_agent(
        model=get_llm(),
        tools=[
            run_check,
        ],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext
    )

    result = verify_agent.invoke(
        input={"messages": [
            {"role": "system", "content": verify_prompt},
        ]},
        context=AgentContext(agent_name="验证节点")
    )

    verify_result = result["messages"][-1].content

    content = [AIMessage(content=verify_result)]
    return Command(
        update={
            "messages": content,
            "final_answer": content
        },
        goto=END
    )

def build_verification_agent():
    """
    构建验证agent：
    router -> filter -> planner -> deliver -> verify -> END
    """
    agent_builder = StateGraph(SmartHomeAgentState)

    # 添加基础节点
    agent_builder.add_node("run_base_node", node_run_base)

    # 添加验证节点
    agent_builder.add_node("verify_node", node_verify)

    # 设置边
    agent_builder.add_edge(START, "node_run_base")
    # agent_builder.add_edge("deliver_node", "verify_node")
    # agent_builder.add_edge("verify_node", END)

    return agent_builder.compile()


def run_validate_Agent(task: str):
    """
    运行验证agent
    """
    agent = build_verification_agent()
    initial_state = {
        "command": task,
        "messages": [HumanMessage(content=task)]
    }
    result = agent.invoke(initial_state)

    return result["messages"][-1].content


if __name__ == "__main__":
    # run_ourAgent("打开客厅灯")
    run_validate_Agent("打开卧室灯")
