from typing import TypedDict, Literal

from langchain.agents import create_agent

from langgraph.types import Command
from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from typing_extensions import TypedDict, Annotated
import operator

from smartHome.m_agent.agent.hooks.langchain_middleware import log_before, log_before_agent, log_response, \
    log_after_agent, AgentContext
from smartHome.m_agent.agent.tools.query_tool_func import query_tool
from smartHome.m_agent.common.get_llm import get_llm

from pydantic import BaseModel, Field
from typing import List  # 推荐导入List，规范类型注解


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
    planning_result: str
    final_answer: str

    # Classification result
    classification: EmailClassification | None

    # Raw search/API results
    search_results: list[str] | None  # List of raw document chunks
    customer_history: dict | None  # Raw customer data from CRM

    # Generated content
    draft_response: str | None
    # messages: list[str] | None

    llm_calls: int


def node_router(state: SmartHomeAgentState) -> Command[Literal["deliver_node"]]:
    """
    路由节点
    :param state:
    :return:
    """

    # if:
    return Command(
        update={"messages": ["content"], },  # Store raw results or error
        goto="deliver_node"
    )


def node_deliver(state: SmartHomeAgentState) -> Command[Literal[END]]:
    """
    交付任务
    :param state:
    :return:
    """

    # content = [AIMessage(content="end")]
    return Command(
        update={
            # "messages": content,
            "final_answer": "content"
        },  # Store raw results or error
        goto=END
    )



def run_ourAgent(task: str):
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


def temp_test(task: str):
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
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
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

    result=run_ourAgent("开灯")
    print(result["command"])
    # run_ourAgent("不，就打开我当前位置的灯就行")
    # res=run_ourAgent("打开客厅灯")
    # run_ourAgent("关闭所有灯，我睡觉时不留灯开着。也不用音乐。")
    # run_ourAgent("打开卧室灯164c1a92b8ce9cda0e2a8c13440b4722")
    # res=run_ourAgent("你好")
    # print(temp_test("Is the living room very dark?"))
    # print(res)
