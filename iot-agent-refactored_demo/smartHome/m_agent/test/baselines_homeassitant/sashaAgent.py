from pydantic import BaseModel, Field
from langchain.agents import create_agent

from smartHome.m_agent.agent.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.agent.persistent_tools import PythonInterpreterTool, NotifyOnConditionTool
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.memory.fake.tools_fake_request import tool_get_services_by_domain, tool_get_all_entities, \
    tool_get_states_by_entity_id, tool_execute_action_by_entity_id


class ClarificationResponse(BaseModel):
    response: str = Field(
        description="Answer by YES or NO if the task is possible or the query can be answered"
    )
    explanation: str = Field(
        description="Return an explanation of the response to the user"
    )


class FilteringResponse(BaseModel):
    devices: list[str] = Field(
        # description="A list that contains the devices that can potentially accomplish the task"
        description="A list of device IDs (string type) that can potentially accomplish the task"
    )


class PrePlanningResponse(BaseModel):
    response: str = Field(
        description="Respond, IN ONE WORD, whether the instruction is associated with sensor (the retrieval of information from a sensor), control (the control of a device), or persistent (complex goals that demand the creation of automation routines)."
    )


class PlanningResponse(BaseModel):
    output: str = Field(
        description=""
    )


class ReadingResponse(BaseModel):
    output: str = Field(
        description=""
    )


class PersistentResponse(BaseModel):
    output: str = Field(
        description=""
    )


def clarification(task: str):
    system_prompt = f"""
            你是一款控制智能家居的人工智能。你可以通过调用给定的工具来获取设备信息。
            请回答：当前设备是否能成功执行用户的指令。
            【任务】{task}
            """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_all_entities, tool_get_services_by_domain, ],
                         system_prompt=system_prompt,
                         response_format=ClarificationResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="clarification")
    )
    clarificationResponse = result["structured_response"]
    return clarificationResponse


def filtering(task: str):
    system_prompt = f"""
                你是一个控制智能家居的人工智能（AI）。 你可以通过调用给定的工具来获取设备信息。
                需提取出与执行用户指令相关的设备名称。  
              
                请通过返回部分设备（即相关设备子集）来回应。
                【任务】{task}
               """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_all_entities, tool_get_services_by_domain, ],
                         system_prompt=system_prompt,
                         response_format=FilteringResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="filtering")
    )
    filteringResponse = result["structured_response"]
    return filteringResponse


def pre_planning(devices: list[str], task: str):
    system_prompt = f"""
                    你是一款控制智能家居的人工智能（AI）。你将收到一组设备清单（清单上的设备即可完成用户指令）。
                    【任务】{task}
                    设备清单：{devices}
                   """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_states_by_entity_id],
                         system_prompt=system_prompt,
                         response_format=PrePlanningResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="pre_planning")
    )
    prePlanningResponse = result["structured_response"]
    return prePlanningResponse


def planning(devices: list[str], task: str):
    system_prompt = f"""
                        你是一款控制智能家居的人工智能（AI）。你将收到一组设备清单（清单上的设备即可完成用户指令）。
                        你的任务是通过调用工具使用这些设备以完成指令。  
                        
                        【任务】{task}
                        筛选后的设备：{devices}  
                       """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_states_by_entity_id, tool_get_services_by_domain,
                                tool_execute_action_by_entity_id],
                         system_prompt=system_prompt,
                         response_format=PlanningResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="planning")
    )
    planningResponse = result["structured_response"]
    return planningResponse


def reading(devices: list[str], task: str):
    system_prompt = f"""
                            你是一款控制智能家居的人工智能（AI）。你将收到一组设备清单（清单上的设备即可完成用户指令）。
                            你的任务是通过调用工具从设备状态中获取相关信息以完成任务。  

                            【任务】{task}
                            筛选后的设备：{devices}  
                           """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_states_by_entity_id],
                         system_prompt=system_prompt,
                         response_format=ReadingResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="reading")
    )
    readingResponse = result["structured_response"]
    return readingResponse


def persistent(devices: list[str], task: str):
    system_prompt = f"""
                            你是一款控制智能家居的人工智能（AI）。你将收到一组设备清单（清单上的设备即可完成用户指令）。
                            你接收用户指令，并以此为依据创建自动化流程。
        
                            对设备与传感器进行分析，提出一个传感器触发条件，以及基于该触发条件应如何调整设备（状态/设置）  

                            【任务】{task}
                            筛选后的设备：{devices}  
                           """
    agent = create_agent(model=get_llm(),
                         tools=[tool_get_states_by_entity_id, PythonInterpreterTool, NotifyOnConditionTool],
                         system_prompt=system_prompt,
                         response_format=PersistentResponse,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="persistent")
    )
    persistentResponse = result["structured_response"]
    return persistentResponse


def run_sashaAgent(command: str)->str:
    clarification_answer = clarification(command)

    if clarification_answer.response == "YES":
        # filtering step
        filtering_answer = filtering(command)
        devices = filtering_answer.devices

        # pre_planning
        pre_planning_answer = pre_planning(devices=devices, task=command)

        assert pre_planning_answer.response in [
            "control",
            "sensor",
            "persistent",
        ], "Pre-Planning response failed and didn't return Control or Sensor"

        if pre_planning_answer.response == "control":
            # planning step
            answer = planning(devices=devices, task=command)
            return answer.output

        elif pre_planning_answer.response == "sensor":
            # sensor reading prompt
            answer = reading(devices=devices, task=command)
            return answer.output

        elif pre_planning_answer.response == "persistent":
            answer = persistent(devices=devices, task=command)
            return answer.output
    else:
        return clarification_answer.explanation


if __name__ == "__main__":
    # run_sashaAgent("打开卧室灯泡")
    # run_sashaAgent("睡觉啦")
    # run_sashaAgent("开灯")
    # run_sashaAgent("不，就打开我当前位置的灯就行")
    print("=" * 50)
    res=run_sashaAgent("打开灯light.yeelink_cn_1162512052_mbulb3_s_2")
    print("="*50)
    print(res)
    # run_sashaAgent("打开灯泡light.yeelink_cn_1162511951_mbulb3_s_2")
