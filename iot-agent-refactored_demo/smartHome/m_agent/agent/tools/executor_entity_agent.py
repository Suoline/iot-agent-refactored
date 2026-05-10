from smartHome.m_agent.agent.hooks.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.agent.tools.persistent_tools import PythonInterpreterTool, NotifyOnConditionTool
from smartHome.m_agent.common.get_llm import get_llm
from langchain.agents import create_agent
from langchain.tools import tool

from smartHome.m_agent.memory.fake_api_tool.fake_api_func import tool_get_states_by_entity_id, tool_get_services_by_domain, \
    tool_execute_action_by_entity_id


@tool
def executor_planning(planning: str):
    """
    按照给定的计划表执行计划，返回计划执行结果
    :param planning:
    :return:
    """
    prompt = f"""
        【计划表】:{planning}
        根据计划表，调用不同的工具来完成计划表中的每一个任务，你不需要修正计划表，只需要如实记录各任务执行情况。
        - 如果任务失败，需要简练记录失败原因
        """
    agent = create_agent(
        model=get_llm(),
        tools=[
            get_entity_current_status,
            execute_entity_action,
            start_entity_persistent_monitoring,
        ],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext,
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="执行计划阶段"),
    )

    return result["messages"][-1].content


@tool
def get_entity_current_status(entity_id: str, what_status: str):
    """
    获取实体的实时状态
    :param entity_id:
    :param what_status:
    :return:
    """
    prompt = f"""
            【实体ID】：{entity_id}
            【任务】：{what_status}
            直接围绕该实体查询当前状态，并根据任务提炼出关键信息。
            """
    agent = create_agent(
        model=get_llm(),
        tools=[tool_get_states_by_entity_id],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext,
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="executor_读实体阶段"),
    )
    return result["messages"][-1].content


@tool
def execute_entity_action(entity_id: str, action: str):
    """
    让实体执行某些操作
    :param entity_id:
    :param action:
    :return:
    """
    prompt = f"""
                【实体ID】：{entity_id}
                【任务】：{action}
                调用工具对该实体执行动作。
                - 注意有些实体的状态可以采用不同的单位，比如百分比、具体数值等等。应该严格遵循任务中提出的单位来调度实体。
                - 不要向用户提问，只需依照任务执行动作即可，任务中未提及的操作参数不应设置（比如任务只说开灯，那么亮度值和色温值就不必设置，采用原来的默认值）
                """
    agent = create_agent(
        model=get_llm(),
        tools=[
            tool_get_states_by_entity_id,
            tool_get_services_by_domain,
            tool_execute_action_by_entity_id,
        ],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext,
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="executor_操作实体阶段"),
    )
    return result["messages"][-1].content


@tool
def start_entity_persistent_monitoring(entity_id: str, when_true: str, then_do: str):
    """
    持续监控：当某个实体的状态满足条件时，执行某些操作
    :param entity_id:
    :param when_true: 自然语言描述，当某个实体的状态处于某些条件时
    :param then_do: 自然语言描述。当when_true满足时，会执行then_do描述的行为。
    :return:
    """
    prompt = f"""
            【实体ID】：{entity_id}
            【持久化任务】：when_true-{when_true} then_do{then_do}
            1. 先调用工具获取该实体的 json 数据，观察其结构组成，分析应该使用哪些字段来作为条件判断依据。
            2. 编写代码，在代码中可以直接调用函数 tool_get_states_by_entity_id()，系统会确保其在运行时存在。
            3. 调用 @tool PythonInterpreterTool 运行一次代码，确保编写执行无误。
            4. 调用工具持久化监控。

            关于编写的代码-注意事项：
            - 编写的代码应分为“定义部分”和“执行部分”
            - 应该包含一个函数，函数的返回值为布尔类型
            - 最后一行为执行入口（如函数调用），其余为定义（如函数、变量定义）
            - 函数的名字用来传递给 @tool NotifyOnConditionTool，其返回值用于持续监控的条件判断
            """
    agent = create_agent(
        model=get_llm(),
        tools=[tool_get_states_by_entity_id, PythonInterpreterTool, NotifyOnConditionTool],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext,
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="executor_实体持久监控阶段"),
    )
    return result["messages"][-1].content


if __name__ == "__main__":
    entity_id = "binary_sensor.demo_window"
    when_true = "当实体状态显示门窗未关闭时"
    then_do = "通知我"
    prompt = f"""
                【实体ID】：{entity_id}
                【持久化任务】：when_true-{when_true} then_do{then_do}
                1. 先调用工具获取该实体的 json 数据，观察其结构组成。
                2. 编写代码，在代码中可以直接调用函数 tool_get_states_by_entity_id()，我会确保其在运行时存在。
                3. 调用 @tool PythonInterpreterTool 运行一次代码，确保编写执行无误。
                4. 调用工具持久化监控。
                """
    agent = create_agent(
        model=get_llm(),
        tools=[tool_get_states_by_entity_id, PythonInterpreterTool, NotifyOnConditionTool],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext,
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="executor_实体持久监控阶段"),
    )
    ans = result["messages"][-1].content
    print(ans)
