import json
import os
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from smartHome.m_agent.agent.base_home_agent import SmartHomeAgentState, run_ourAgent
from smartHome.m_agent.agent.base_home_easy_agent import run_easy_ourAgent
from typing import Literal
from langchain.tools import tool
from langchain.agents import create_agent

from langgraph.types import Command
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from smartHome.m_agent.agent.hooks.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.common.get_llm import get_llm


TaskExecutor = Callable[[str], str]


def get_execution_completed_at() -> str:
    """
    生成与 fake Home Assistant 实体时间一致的时间戳格式
    """
    timezone_name = os.getenv("FAKE_HA_TIMEZONE", "Asia/Shanghai")
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz=timezone).isoformat()

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


def make_run_base_node(executor: TaskExecutor):
    def node_run_base(state: SmartHomeAgentState) -> Command[Literal["verify_node"]]:
        executor(task=state["command"])
        execution_completed_at = get_execution_completed_at()
        return Command(
            update={
                "execution_completed_at": execution_completed_at,
                # "messages": content,
                # "final_answer": content
            },
            goto="verify_node"
        )

    return node_run_base


def make_run_check_tool(executor: TaskExecutor):
    @tool
    def run_check(task: str):
        """
        调用设备执行任务task
        """
        return executor(task=task)

    return run_check


def make_verify_node(executor: TaskExecutor):
    run_check = make_run_check_tool(executor)

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
        execution_completed_at = state.get("execution_completed_at", "")

        # 构建验证查询
        verify_prompt = f"""
        【原始任务】：{state["command"]}
        【规划执行结果】：{planning_result}
        【涉及的设备】：{filter_devices}
        【本轮任务执行完成时间】：{execution_completed_at}

        请重新调用agent验证上述设备是否成功执行了任务，并且严格基于“周边传感器当前状态 + 周边传感器最近状态变更时间 + 本轮任务执行完成时间”联合判断。
        验证必须优先通过设备所在位置的周边传感器来确认设备状态变化，不能把目标设备自身状态作为最终结论依据。目标设备自身状态最多只能作为辅助上下文，不能单独据此判定成功。

        你必须遵守以下验证流程：
        1. 先识别【原始任务】和【规划执行结果】中希望设备最终达到的目标状态。
        2. 从【涉及的设备】中找出目标设备周边可作为旁证的传感器。
        3. 读取这些周边传感器的当前状态，以及最近状态变更时间；时间字段优先使用 last_changed，其次使用 last_updated。
        4. 将每个传感器的最近状态变更时间与【本轮任务执行完成时间】进行比较。
        5. 基于“当前状态是否符合预期”以及“最近状态变更时间是否接近 execution_completed_at”来给出最终结论。

        你必须使用以下判定规则：
        - 如果周边传感器当前状态符合目标，并且最近状态变更时间与 execution_completed_at 足够接近，可以判定为“通过”。
        - 如果周边传感器当前状态符合目标，但最近状态变更时间明显早于 execution_completed_at，说明该状态很可能在本轮任务前就已存在，不能作为本次成功证据，应判定为“无法判断”。
        - 如果周边传感器当前状态不符合目标，应判定为“未通过”。
        - 如果缺少 last_changed、last_updated 或 execution_completed_at 这类关键时间证据，无法建立可靠时间关系，应判定为“无法判断”。
        - 不要因为当前状态看起来正确，就忽略时间关系直接判定成功。

        例如：
        - 对于卧室灯，可以通过卧室的人体传感器光照强度或相关环境光传感器来确认灯打开后的周边变化；只有当光照状态与开灯目标一致，且其变更时间接近 execution_completed_at，才能判定通过。
        - 对于空调，可以通过室温传感器确认环境趋势或状态变化；只有当传感器状态与调温目标一致，且其变更时间接近 execution_completed_at，才能作为成功证据。

        最终输出尽量稳定包含以下内容：
        - 使用了哪些周边传感器
        - 每个传感器的当前状态
        - 每个传感器的最近状态变更时间
        - execution_completed_at
        - 最终结论：通过 / 未通过 / 无法判断
        - 结论理由

        如果证据不足，请直接输出“无法判断”并说明缺失了什么证据，不要继续追问用户补充材料。
        请生成验证查询并调用验证流程。
        """

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

    return node_verify


def build_verification_agent(executor: TaskExecutor = run_ourAgent):
    """
    构建验证agent：
    router -> filter -> planner -> deliver -> verify -> END
    """
    agent_builder = StateGraph(SmartHomeAgentState)

    # 添加基础节点
    agent_builder.add_node("run_base_node", make_run_base_node(executor))

    # 添加验证节点
    agent_builder.add_node("verify_node", make_verify_node(executor))

    # 设置边
    agent_builder.add_edge(START, "run_base_node")
    # agent_builder.add_edge("deliver_node", "verify_node")
    # agent_builder.add_edge("verify_node", END)

    return agent_builder.compile()


def run_validate_Agent(task: str):
    """
    运行验证agent
    """
    agent = build_verification_agent(executor=run_ourAgent)
    initial_state = {
        "command": task,
        "messages": [HumanMessage(content=task)]
    }
    result = agent.invoke(initial_state)

    return result["messages"][-1].content


def run_easy_validate_Agent(task: str):
    """
    运行 easy 验证agent
    """
    agent = build_verification_agent(executor=run_easy_ourAgent)
    initial_state = {
        "command": task,
        "messages": [HumanMessage(content=task)]
    }
    result = agent.invoke(initial_state)

    return result["messages"][-1].content


if __name__ == "__main__":
    # run_ourAgent("打开客厅灯")
    run_validate_Agent("打开卧室灯")
