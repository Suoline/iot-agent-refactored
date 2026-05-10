import json
import os
from typing import TypedDict, Literal

from langchain.agents import create_agent

from langgraph.types import Command
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from typing_extensions import TypedDict, Annotated
import operator

from smartHome.m_agent.agent.hooks.langchain_middleware import log_before, log_before_agent, log_response, \
    log_after_agent, AgentContext
from smartHome.m_agent.agent.tools.executor_entity_agent import executor_planning
from smartHome.m_agent.agent.tools.query_tool_func import query_tool
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.tools import tool
from pydantic import BaseModel, Field
from typing import List  # 推荐导入List，规范类型注解

from smartHome.m_agent.memory.fake_api_tool.fake_api_func import tool_get_all_entities_states, \
    tool_get_states_by_entity_id


class EntityInfo(BaseModel):
    """单个智能家居实体的完整信息模型（包含ID、名称、选择理由）"""
    entity_id: str = Field(
        description="实体唯一标识ID",
        examples=["light.philips_cn_1061200910_lite_s_2"]
    )
    entity_name: str = Field(
        description="实体名称",
        examples=["客厅智能吸顶灯"]
    )
    entity_reason: str = Field(
        description="选择该实体的理由（50字以内）",
        examples=["亮度可调节，能匹配客厅日常照明和观影场景需求"]
    )

class EntityIdList(BaseModel):
    """多个智能家居实体的事实性信息列表模型"""
    entities: List[EntityInfo] = Field(
        default=[],
        description="所有候选实体的完整信息列表（ID、名称、选择理由）",
        examples=[
            [
                {
                    "entity_id": "light.philips_cn_1061200910_lite_s_2",
                    "entity_name": "客厅智能吸顶灯",
                    "entity_reason": "亮度可调节，能匹配客厅日常照明和观影场景需求"
                },
                {
                    "entity_id": "cover.demo_bedroom_curtain",
                    "entity_name": "卧室智能窗帘",
                    "entity_reason": "支持定时开合，能配合作息自动调节卧室采光"
                }
            ]
        ]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entities": [
                        {
                            "entity_id": "light.philips_cn_1061200910_lite_s_2",
                            "entity_name": "客厅智能吸顶灯",
                            "entity_reason": "亮度可调节，能匹配客厅日常照明和观影场景需求"
                        },
                        {
                            "entity_id": "cover.demo_bedroom_curtain",
                            "entity_name": "卧室智能窗帘",
                            "entity_reason": "支持定时开合，能配合作息自动调节卧室采光"
                        }
                    ]
                }
            ]
        }
    }


class EmailClassification(TypedDict):
    intent: Literal["question", "bug", "billing", "feature", "complex"]
    urgency: Literal["low", "medium", "high", "critical"]
    topic: str
    summary: str


class SmartHomeAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    command: str
    task: str

    filter_devices: str
    planning_result:str
    final_answer:str
    execution_completed_at: str

    classification: EmailClassification | None

    # Raw search/API results
    search_results: list[str] | None  # List of raw document chunks
    customer_history: dict | None  # Raw customer data from CRM

    # Generated content
    draft_response: str | None
    # messages: list[str] | None

    llm_calls: int

import os
import json

def update_json_file(key: str, value):
    """
    更新当前 py 文件目录下 note/base_agent_result.json 中的指定 key

    :param key: 要更新的键
    :param value: 要写入的值（dict / list / 基本类型都可以）
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    note_dir = os.path.join(current_dir, "note")
    os.makedirs(note_dir, exist_ok=True)

    file_path = os.path.join(note_dir, "base_agent_result.json")

    # 读取已有数据
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    # 更新 key
    data[key] = value

    # 写回文件
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def node_router(state:SmartHomeAgentState)-> Command[Literal["deliver_node"]]:
    """
    路由节点
    :param state:
    :return:
    """

    prompt = f"""
            【任务】{state["command"]}
            调用工具来完成任务，如果任务中没有指明实体ID，需要先通过@tool tool_filter得到实体ID列表。
            再得到实体ID列表后，可以将任务和实体列表传递给@tool tool_planner来完成任务。
            最后，汇报本次任务执行情况。
            - 除非某个工具出现问题，否则你应该直接传入原始【任务】，不应该做修改。

            其中：@tool tool_planner的调用参数示例如下：
            "tool_calls": [
                  {{
                    "args": {{
                      "entities": {{
                        "entities": [
                          {{
                            "entity_id": "binary_sensor.demo_smoke_sensor",
                            "entity_name": "烟雾传感器",
                            "entity_reason": "可以确认家中是否发生火灾"
                          }}
                        ]
                      }},
                      "task": "I'm home."
                    }},
                  }}
                ]

            tips: 
            - **如果任务不是调用或者可能涉及到调用智能家居的任务，直接结束。并简短说明原因即可。但需谨慎，因为有些看似不会涉及智能家居，但可能属于用户的场景设定，比如“天气很糟”，对应提醒我关窗**
            - 最好是通过@tool tool_filter尝试得到实体ID列表，如果为空，说明用户确实没有这类场景设定
            """
    agent = create_agent(model=get_llm(),
                         tools=[
                                # ask_human
                             tool_filter,
                             tool_planner
                                ],
                         # response_format=EntityIdList,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="home_路由节点")
    )

    content = [AIMessage(content=result["messages"][-1].content)]
    # if:
    return Command(
        update={"messages": content,},  # Store raw results or error
        goto="deliver_node"
    )

@tool
def tool_filter(task:str):
    """
    筛选出执行task所需的实体ID列表
    """
    prompt = f"""
        【任务】：{task}
        调用 tool_get_all_entities_states 获取当前全部实体状态，再筛选出完成该任务所需的实体IDs。
        当前实验环境不依赖设备注册表，也不要先找设备再拆实体，而是直接围绕实体来筛选。
        最后保留实体ID，和简单说明理由。如果没有任何实体满足约束条件，说明原因。
        """
    # prompt = f"""
    #     【任务】：{task}
    #     调用查询工具来获取task中所需的实体IDs
    #     最后保留实体ID，和简单说明理由。如果没有任何实体满足约束条件，说明原因。
    #
    #     比如：
    #     1）若任务为“查看音量”，那么应该向@tool query_tool提出“能查看音量的所有实体ID”
    #     2）若任务为“打开餐桌上的灯”，那么应该向@tool query_tool提出“餐桌上的灯的实体ID”
    #     """
    agent = create_agent(model=get_llm(),
                         tools=[
                                # query_tool,
                                # ask_human
                                tool_get_all_entities_states
                                ],
                         response_format=EntityIdList,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context = AgentContext(agent_name="home_过滤节点")
    )

    entity_info_list = result["structured_response"]
    # # 无缩进（紧凑格式，适合传输/存储）
    # json_str_compact = deviceInfoList.model_dump_json()
    # # 带缩进（美化格式，适合调试/查看）
    # json_str_pretty = deviceInfoList.model_dump_json(indent=4)
    # ans=result["messages"][-1].content
    # 转成 Python dict（而不是字符串）
    # 转成 dict
    entity_dict = entity_info_list.model_dump()

    # 调用封装函数
    update_json_file("filter_devices", entity_dict)

    return entity_info_list


def entity_id_list_to_ids(entity_id_list_obj: EntityIdList) -> List[str]:
    """
    将EntityIdList对象转换为纯实体ID列表

    Args:
        entity_id_list_obj: EntityIdList类型的Pydantic对象

    Returns:
        仅包含实体ID的字符串列表，若entities为空则返回空列表

    Example:
        >>> obj = EntityIdList(entities=[EntityInfo(entity_id="light.demo", entity_name="灯", entity_reason="理由")])
        >>> entity_id_list_to_ids(obj)
        ["light.demo"]
    """
    entity_ids = [entity_info.entity_id for entity_info in entity_id_list_obj.entities]
    return entity_ids
@tool
def tool_planner(task:str,entities:EntityIdList):
    """
    规划和执行
    :param state:
    :return:
    """
    # todo 补充可以根据实体ID获取实体能力、实体状态类型
    # entity_info=DEVICEBASECONST.get_entity_states_capabilities(entity_id_list_to_ids(entities))
    entity_info=""
    SMARTHOMEMEMORY=None
    prompt = f"""
    【任务】：{task}
    【候选实体集】：{entities.model_dump_json()}
    根据任务及实体能力和状态类型、使用习惯，制定计划调用实体或者回答用户问题
    - 计划表里的计划需要包含完整的实体ID
    - 除非任务明确包含持久化监控某个实体或许显示提到持久化、建立自动化规则，否则计划中不能出现持久化操作
    - 不要奢望通过和用户交互得到答案，用户无法直接回复你。所以不要问用户，自己做。
    - **不要向用户确认计划！！！制定计划就自己执行**
    - 对于用户没有指明要调整实体到什么状态，如果存在用户偏好（通用偏好或者实体特定偏好），那么应该按照其规划调用方案；否则依照常理来规划。
    - 如果需要知晓某个实体的使用偏好，请使用@tool query_tool查询对应实体ID的偏好
    - 如果需要知晓某个实体ID的实时状态来制定计划，请使用@tool tool_get_states_by_entity_id。例如类似调亮灯光这类任务，需要先知道当前亮度值，才能确定要调整后亮度值。
    - 当前环境中直接基于候选实体集规划，不要假设存在设备注册表，也不要再拆分“设备下有哪些实体”。
    """
    # prompt=f"""
    # 【任务】：{task}
    # 【候选实体集】：{entities.model_dump_json()}
    # 根据任务及实体能力和状态类型、使用习惯，制定计划调用实体或者回答用户问题
    # - 计划表里的计划需要包含完整的实体ID
    # - 除非任务明确包含持久化监控某个实体或许显示提到持久化、建立自动化规则，否则计划中不能出现持久化操作
    # - 不要奢望通过和用户交互得到答案，用户无法直接回复你。所以不要问用户，自己做。
    # - **不要向用户确认计划！！！制定计划就自己执行**
    # - 对于用户没有指明要调整实体到什么状态，如果存在用户偏好（通用偏好或者实体特定偏好），那么应该按照其规划调用方案；否则依照常理来规划。
    # - 如果需要知晓某个实体的使用偏好，请使用@tool query_tool查询对应实体ID的偏好
    # - 如果需要知晓某个实体ID的实时状态来制定计划，请使用@tool tool_get_states_by_entity_id。例如类似调亮灯光这类任务，需要先知道当前亮度值，才能确定要调整后亮度值。
    #
    # 下面是参考信息：
    # [用户的通用偏好（不会存储特定与某个实体的偏好）]：{SMARTHOMEMEMORY.general_preferences}
    # [实体能力和属性信息]：{entity_info}
    # """

    agent = create_agent(
        model=get_llm(),
        tools=[
            # get_devices_states,get_devices_capabilities,get_devices_usage_habits,
                query_tool,
                tool_get_states_by_entity_id,
                executor_planning],
        middleware=[log_before, log_response, log_before_agent, log_after_agent],
        context_schema=AgentContext
    )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context = AgentContext(agent_name="home_规划阶段")
    )

    # msg_content = "\n" + "\n".join(map(repr, result["messages"]))
    # GLOBALCONFIG.logger.info("================" + "规划阶段")
    # GLOBALCONFIG.logger.info(msg_content)
    # GLOBALCONFIG.logger.info("\n")

    ans=result["messages"][-1].content

    # 调用封装函数
    update_json_file("planning_result", ans)

    return ans

def node_deliver(state:SmartHomeAgentState)-> Command[Literal[END]]:
    """
    交付任务
    :param state:
    :return:
    """

    # content = [AIMessage(content="end")]
    return Command(
        update={
            # "messages": content,
            # "final_answer": content
        },  # Store raw results or error
        goto=END
    )

def run_ourAgent_for_full_result(task:str):
    agent_builder = StateGraph(SmartHomeAgentState)
    # Add nodes
    agent_builder.add_node("router_node", node_router)
    agent_builder.add_node("deliver_node", node_deliver)

    agent_builder.add_edge(START, "router_node")
    # agent_builder.add_edge("filter_node", "planner_and_executor_node")
    # agent_builder.add_edge("planner_and_executor_node", END)

    agent = agent_builder.compile()
    # GLOBALCONFIG.nested_logger=GLOBALCONFIG.agent_init_dialogue_logger
    # Test with an urgent billing issue
    # task="关闭卧室灯泡"
    # task="关闭灯164c1a92b8ce9cda0e2a8c13440b4722"
    initial_state = {
        "command": task,
        "messages": [HumanMessage(content=task)]
    }
    result = agent.invoke(initial_state)
    # for m in result["messages"]:
    #     m.pretty_print()

    return result
def run_easy_ourAgent(task:str):
    agent_builder = StateGraph(SmartHomeAgentState)
    # Add nodes
    agent_builder.add_node("router_node", node_router)
    agent_builder.add_node("deliver_node", node_deliver)

    agent_builder.add_edge(START, "router_node")
    # agent_builder.add_edge("filter_node", "planner_and_executor_node")
    # agent_builder.add_edge("planner_and_executor_node", END)

    agent = agent_builder.compile()
    # GLOBALCONFIG.nested_logger=GLOBALCONFIG.agent_init_dialogue_logger
    # Test with an urgent billing issue
    # task="关闭卧室灯泡"
    # task="关闭灯164c1a92b8ce9cda0e2a8c13440b4722"
    initial_state = {
        "command": task,
        "messages": [HumanMessage(content=task)]
    }
    result = agent.invoke(initial_state)
    # for m in result["messages"]:
    #     m.pretty_print()

    return result["messages"][-1].content


def temp_test(task:str):
    prompt = f"""
            【任务】：{task}
            调用查询工具来获取 task 中所需的实体IDs
            最后保留实体ID，和简单说明理由。如果没有任何实体满足约束条件，说明原因。

            比如：
            1）若任务为“查看音量”，那么应该向@tool query_tool提出“能查看音量的所有实体ID”
            2）若任务为“打开餐桌上的灯”，那么应该向@tool query_tool提出“餐桌上的灯的实体ID”
            """
    agent = create_agent(model=get_llm(),
                         tools=[
                             query_tool,
                             # ask_human
                         ],
                         response_format=EntityIdList,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="home_过滤节点")
    )

    entity_info_list = result["structured_response"]
    return entity_info_list
if __name__ == "__main__":
    # run_ourAgent("开灯")
    # run_ourAgent("不，就打开我当前位置的灯就行")
    # res=run_ourAgent("打开客厅灯")
    # run_ourAgent("关闭所有灯，我睡觉时不留灯开着。也不用音乐。")
    # run_ourAgent("打开卧室灯164c1a92b8ce9cda0e2a8c13440b4722")
    res=run_easy_ourAgent("关闭任意一盏灯")
    # print(temp_test("Is the living room very dark?"))
    print(res)
