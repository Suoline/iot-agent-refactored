from typing import Annotated, List, Callable
from langchain.agents import create_agent
from langchain_core.tools import tool
from smartHome.m_agent.agent.langchain_middleware import log_before, log_response, log_before_agent, log_after_agent, \
    AgentContext
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.memory.fake.tools_fake_request import tool_get_states_by_entity_id, \
    tool_execute_action_by_entity_id, tool_get_services_by_domain, tool_get_all_entities
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.device_doc import Device_info_doc


@tool
def SmartThingsPlannerTool(task) -> str:
    """
    Used to generate a plan of api calls to make to execute a command. The input to this tool is the user command in natural language. Always share the original user command to this tool to provide the overall context.
    """
    one_liners_string = Device_info_doc.get_one_liners_string()
    device_capability_string = Device_info_doc.get_device_capability_string()
    system_prompt = f"""You are a planner that helps users interact with their smart devices.
    You are given a list of high level summaries of entity capabilities ("all capabilities:").
    You are also given a list of available entities ("entities you can use") which will tell you the name and entity_id of the entity, as well as listing which capabilities the entity has.
    Your job is to figure out the sequence of which entities and capabilities to use in order to execute the user's command.

    Follow these instructions:
    - Include entity_ids (guid strings), capability ids, and explanations of what needs to be done in your plan.
    - The capability information you receive is not detailed. Often there will be multiple capabilities that sound like they might work. You should list all of the ones that might work to be safe.
    - Don't always assume the devices are already on.

    all capabilities:
    {one_liners_string}

    devices you can use:
    {device_capability_string}

    Use the following format:
    Device Ids: list of relevant devices IDs and names
    Capabilities: list of relevant capabilities
    Plan: steps to execute the command
    Explanation: Any further explanations and notes
    <FINISHED>
    """
    system_message = {
        "role": "system",
        "content": system_prompt,
    }
    user_message = {
        "role": "user",
        "content": task,
    }

    agent = create_agent(model=get_llm(),
                         tools=[SmartThingsPlannerTool, tool_get_all_entities, tool_get_services_by_domain,
                                tool_execute_action_by_entity_id, tool_get_states_by_entity_id],
                         system_prompt=system_prompt,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [system_message] +[user_message]},
        context=AgentContext(agent_name="SmartThingsPlannerTool")
    )
    return result["messages"][-1].content


@tool
def SmartThingsTool(task) -> str:
    """
    Use this to interact with smartthings. Accepts natural language commands. Do not omit any details from the original command. Use this tool to determine which device can accomplish the query.
    """
    system_prompt = f"""
        【任务】{task}
        You are an agent that assists with queries against some API.

        Instructions:
        - Include a description of what you've done in the final answer, include device IDs
        - If you encounter an error, try to think what is the best action to solve the error instead of trial and error.
        
        Starting below, you should follow this format:
        
        User query: the query a User wants help with related to the API
        Thought: you should always think about what to do
        Action: the action to take, should be one of the tools
        Action Input: the input to the action
        Observation: the result of the action
        ... (this Thought/Action/Action Input/Observation can repeat N times)
        Thought: I am finished executing a plan and have the information the user asked for or the data the user asked to create
        Final Answer: the final output from executing the plan. Add <FINISHED> after your final answer.
        
        Your must always output a thought, action, and action input.
        Do not forget to say I'm finished when the user's command is executed.
        Begin!   
        """
    agent = create_agent(model=get_llm(),
                         tools=[SmartThingsPlannerTool,tool_get_all_entities,tool_get_services_by_domain,tool_execute_action_by_entity_id,tool_get_states_by_entity_id],
                         system_prompt=system_prompt,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="SmartThingsTool")
    )
    return result["messages"][-1].content


if __name__ == "__main__":
    # print(SmartThingsAgent().run_agent("台灯现在可用吗？"))
    pass