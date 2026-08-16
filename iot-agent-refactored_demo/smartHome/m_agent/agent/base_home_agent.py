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
    log_after_agent, AgentContext, retry_upstream_unavailable
from smartHome.m_agent.agent.tools.executor_agent import get_device_current_status, executor_planning
from smartHome.m_agent.agent.tools.query_tool_func import query_tool
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from langchain.tools import tool
from pydantic import BaseModel, Field
from typing import List  # 推荐导入List，规范类型注解

from smartHome.m_agent.memory import get_demo_memory_runtime
from smartHome.m_agent.memory.fake_api_tool.fake_api_func import tool_get_all_entities_states, \
    tool_get_states_by_entity_id, tool_get_all_devices


class DeviceInfo(BaseModel):
    """单个智能家居设备的完整信息模型（包含ID、名称、选择理由）"""
    device_id: str = Field(
        description="设备唯一标识ID",
        examples=["31ae92d8a163d77f8d6a5741c0d1b89c"]
    )
    device_name: str = Field(
        description="设备名称",
        examples=["客厅智能吸顶灯"]
    )
    device_reason: str = Field(
        description="选择该设备的理由（50字以内）",
        examples=["亮度可调节，能匹配客厅日常照明和观影场景需求"]
    )

class DeviceIdList(BaseModel):
    """多个智能家居设备的事实性信息列表模型"""
    devices: List[DeviceInfo] = Field(
        default=[],
        description="所有候选设备的完整信息列表（ID、名称、选择理由）",
        examples=[
            [
                {
                    "device_id": "31ae92d8a163d77f8d6a5741c0d1b89c",
                    "device_name": "客厅智能吸顶灯",
                    "device_reason": "亮度可调节，能匹配客厅日常照明和观影场景需求"
                },
                {
                    "device_id": "31ae92d8a163d77f8d6a54856d1b89c",
                    "device_name": "卧室智能窗帘",
                    "device_reason": "支持定时开合，能配合作息自动调节卧室采光"
                }
            ]
        ]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "devices": [
                        {
                            "device_id": "31ae92d8a163d77f8d6a5741c0d1b89c",
                            "device_name": "客厅智能吸顶灯",
                            "device_reason": "亮度可调节，能匹配客厅日常照明和观影场景需求"
                        },
                        {
                            "device_id": "31ae92d8a163d77f8d6a54856d1b89c",
                            "device_name": "卧室智能窗帘",
                            "device_reason": "支持定时开合，能配合作息自动调节卧室采光"
                        }
                    ]
                }
            ]
        }
    }

# Define the structure for email classification
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

    # Classification result
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
            调用工具来完成任务，如果任务中没有指明设备ID，需要先通过@tool tool_filter得到设备ID列表。
            再得到设备ID列表后，可以将任务和设备IDs传递给@tool tool_planner来完成任务。
            最后，汇报本次任务执行情况。
            - 除非某个工具出现问题，否则你应该直接传入原始【任务】，不应该做修改。

            其中：@tool tool_planner的调用参数示例如下：
            "tool_calls": [
                  {{
                    "args": {{
                      "devices": {{
                        "devices": [
                          {{
                            "device_id": "cf03cb835279ea4876ab6ee202aa9832",
                            "device_name": "烟雾传感器",
                            "device_reason": "可以确认家中是否发生火灾"
                          }}
                        ]
                      }},
                      "task": "I'm home."
                    }},
                  }}
                ]

            tips: 
            - **如果任务不是调用或者可能涉及到调用智能家居的任务，直接结束。并简短说明原因即可。但需谨慎，因为有些看似不会涉及智能家居，但可能属于用户的场景设定，比如“天气很糟”，对应提醒我关窗**
            - 最好是通过@tool tool_filter尝试得到设备ID列表，如果为空，说明用户确实没有这类场景设定
            """
    agent = create_agent(model=get_llm(),
                         tools=[
                                # ask_human
                             tool_filter,
                             tool_planner
                                ],
                         # response_format=DeviceIdList,
                         middleware=[log_before, retry_upstream_unavailable, log_response, log_before_agent, log_after_agent],
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
    筛选出执行task所需的设备ID列表
    """
    prompt = f"""
        【任务】：{task}
        调用 @tool tool_get_all_devices 获取所有可用设备列表，
        调用 @tool query_tool 查询用户记忆中记录的各设备归属哪个房间。

        **重要**：HomeAssistant 中记录的 area_id 和设备名称不可靠，不能作为确定设备所在房间的依据。
        设备实际属于哪个房间，必须以 query_tool 返回的用户对话记忆为准。

        最后保留设备ID和简单说明理由（理由需注明依据 query_tool 还是 HA 数据）。
        如果没有任何设备满足约束条件，说明原因。
        """
    # prompt = f"""
    #     【任务】：{task}
    #     调用查询工具来获取task中所需的设备IDs
    #     最后保留设备ID，和简单说明理由。如果没有任何设备满足约束条件，说明原因。
    #
    #     比如：
    #     1）若任务为“查看音量”，那么应该向@tool query_tool提出“能查看音量的所有设备ID”
    #     2）若任务为“打开餐桌上的灯”，那么应该向@tool query_tool提出“餐桌上的灯的设备ID”
    #     """
    runtime = get_demo_memory_runtime()
    runtime.set_stage("device_filter")
    try:
        agent = create_agent(model=get_llm(),
                             tools=[
                                    query_tool,
                                    tool_get_all_devices
                                    ],
                             response_format=DeviceIdList,
                             middleware=[log_before, retry_upstream_unavailable, log_response, log_before_agent, log_after_agent],
                             context_schema=AgentContext
                             )
        result = agent.invoke(
            input={"messages": [
                {"role": "system", "content": prompt},
            ]},
            context = AgentContext(agent_name="home_过滤节点")
        )
    finally:
        runtime.clear_stage()

    deviceInfoList = result["structured_response"]
    # # 无缩进（紧凑格式，适合传输/存储）
    # json_str_compact = deviceInfoList.model_dump_json()
    # # 带缩进（美化格式，适合调试/查看）
    # json_str_pretty = deviceInfoList.model_dump_json(indent=4)
    # ans=result["messages"][-1].content
    # 转成 Python dict（而不是字符串）
    # 转成 dict
    device_dict = deviceInfoList.model_dump()

    # 调用封装函数
    update_json_file("filter_devices", device_dict)

    return deviceInfoList


def device_id_list_to_ids(device_id_list_obj: DeviceIdList) -> List[str]:
    """
    将DeviceIdList对象转换为纯设备ID列表

    Args:
        device_id_list_obj: DeviceIdList类型的Pydantic对象

    Returns:
        仅包含设备ID的字符串列表，若devices为空则返回空列表

    Example:
        >>> obj = DeviceIdList(devices=[DeviceInfo(device_id="123", device_name="灯", device_reason="理由")])
        >>> device_id_list_to_ids(obj)
        ["123"]
    """
    # 遍历devices列表，提取每个DeviceInfo的device_id字段
    device_ids = [device_info.device_id for device_info in device_id_list_obj.devices]
    return device_ids
@tool
def tool_planner(task:str,devices:DeviceIdList):
    """
    规划和执行
    :param state:
    :return:
    """
    # todo 补充可以根据设备ID获取设备能力、设备状态类型
    # device_info=DEVICEBASECONST.get_device_states_capabilities(device_id_list_to_ids(devices))
    device_info=""
    SMARTHOMEMEMORY=None
    prompt = f"""
    【任务】：{task}
    【候选设备集】：{devices.model_dump_json()}
    根据任务及设备能力和状态类型、使用习惯，制定计划调用设备或者回答用户问题
    - 计划表里的计划需要包含完整的设备ID
    - 除非任务明确包含持久化监控某个设备或许显示提到持久化、建立自动化规则，否则计划中不能出现持久化操作
    - 不要奢望通过和用户交互得到答案，用户无法直接回复你。所以不要问用户，自己做。
    - **不要向用户确认计划！！！制定计划就自己执行**
    - 对于涉及用户偏好、设备使用习惯、场景指令的任务，请使用@tool query_tool查询对应设备或场景的记忆
    - 对于需要感知设备当前状态来制定计划的（例如"调亮某灯"需先知道当前亮度），请使用@tool get_device_current_status
    - HomeAssistant 中记录的设备名称和 area_id 仅供参考，设备实际的归属和位置应以 query_tool 返回的用户记忆为准
    """
    # prompt=f"""
    # 【任务】：{task}
    # 【候选设备集】：{devices.model_dump_json()}
    # 根据任务及设备能力和状态类型、使用习惯，制定计划调用设备或者回答用户问题
    # - 计划表里的计划需要包含完整的设备ID
    # - 除非任务明确包含持久化监控某个设备或许显示提到持久化、建立自动化规则，否则计划中不能出现持久化操作
    # - 不要奢望通过和用户交互得到答案，用户无法直接回复你。所以不要问用户，自己做。
    # - **不要向用户确认计划！！！制定计划就自己执行**
    # - 对于用户没有指明要调整设备到什么状态，如果存在用户偏好（通用偏好或者设备特定偏好），那么应该按照其规划调用方案；否则依照常理来规划。
    # - 如果需要知晓某个设备的使用偏好，请使用@tool query_tool查询对应设备ID的偏好
    # - 如果需要知晓某个设备ID的实时状态来制定计划，请使用@tool get_device_current_status。例如类似调亮灯光这类任务，需要先知道当前亮度值，才能确定要调整后亮度值。
    #
    # 下面是参考信息：
    # [用户的通用偏好（不会存储特定与某个设备的偏好）]：{SMARTHOMEMEMORY.general_preferences}
    # [设备能力和属性信息]：{device_info}
    # """

    runtime = get_demo_memory_runtime()
    runtime.set_stage("planning")
    try:
        agent = create_agent(
            model=get_llm(),
            tools=[
                query_tool,
                executor_planning],
            middleware=[log_before, retry_upstream_unavailable, log_response, log_before_agent, log_after_agent],
            context_schema=AgentContext
        )
        result = agent.invoke(
            input={"messages": [
                {"role": "system", "content": prompt},
            ]},
            context = AgentContext(agent_name="home_规划阶段")
        )
    finally:
        runtime.clear_stage()

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

def run_ourAgent(task:str):
    runtime = get_demo_memory_runtime()
    runtime.start_task(task)
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
    try:
        result = agent.invoke(initial_state)
        output = result["messages"][-1].content
        runtime.finish_task(output)
        return output
    except Exception as exc:
        runtime.finish_task(str(exc), success=False)
        raise


def temp_test(task:str):
    prompt = f"""
            【任务】：{task}
            调用查询工具来获取task中所需的设备IDs
            最后保留设备ID，和简单说明理由。如果没有任何设备满足约束条件，说明原因。

            比如：
            1）若任务为“查看音量”，那么应该向@tool query_tool提出“能查看音量的所有设备ID”
            2）若任务为“打开餐桌上的灯”，那么应该向@tool query_tool提出“餐桌上的灯的设备ID”
            """
    agent = create_agent(model=get_llm(),
                         tools=[
                             query_tool,
                             # ask_human
                         ],
                         response_format=DeviceIdList,
                         middleware=[log_before, retry_upstream_unavailable, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="home_过滤节点")
    )

    deviceInfoList = result["structured_response"]
    return deviceInfoList
if __name__ == "__main__":
    # run_ourAgent("开灯")
    # run_ourAgent("不，就打开我当前位置的灯就行")
    # res=run_ourAgent("打开客厅灯")
    # run_ourAgent("关闭所有灯，我睡觉时不留灯开着。也不用音乐。")
    # run_ourAgent("打开卧室灯164c1a92b8ce9cda0e2a8c13440b4722")
    res=run_ourAgent("关闭任意一盏灯")
    # print(temp_test("Is the living room very dark?"))
    print(res)
