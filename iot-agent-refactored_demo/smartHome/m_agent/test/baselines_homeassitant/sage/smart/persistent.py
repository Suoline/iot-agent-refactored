import traceback
from typing import List, Callable, Any

from langchain.agents import create_agent
from langgraph.graph import MessagesState
from langchain.tools import tool
from smartHome.m_agent.agent.langchain_middleware import log_before, log_response, log_before_agent, log_after_agent, \
    AgentContext
from smartHome.m_agent.agent.persistent_tools import PythonInterpreterTool
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.memory.fake.tools_fake_request import tool_get_all_entities, tool_get_services_by_domain, \
    tool_execute_action_by_entity_id, tool_get_states_by_entity_id
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.smartThings import SmartThingsPlannerTool

@tool
def ConditionCheckerTool(text):
    """
    Use this tool to check whether a certain condition is satisfied. Accepts natural language commands. Returns the name of a function which checks the condition. Inputs should be phrased as questions. In addition to the question that needs to be checked, you should also provide any extra information that might contextualize the question.
    """
    question=f"""
    Condition to check: {text}
    Thought: I should generate a plan to help with checking this condition.
    """

    system_prompt = f"""
            【任务】{question}
            You are an agent that writes code that checks whether a condition is satisfied by querying some API.
I have encapsulated the API calls into functions and also added them to your tool list. You can directly call these functions—get_all_entity_id, get_services_by_domain, get_states_by_entity_id, and execute_domain_service_by_entity_id—in your code.
You should first plan the sequence of API calls you need to make. Then you should get detailed
documentation for each of the API calls. Finally, you should write python code to check the condition. Remember, the code:
- should be tested using a python interpreter
- should involve the checking of a single condition that involves checking the state of a single device that best fulfills the user request
- should follow the correct dictionary key structure
- the function must return True if the device state condition is satisfied and False otherwise
- the code should only include a single function definition that does not include any arguments.
- the last line of the programe should be an example of the function call

If the code doesn't work, you should try to figure out why and fix it. Your final answer should include the current status of the condition and the name of the function you wrote to check it. Remember, the function should return True or False.

Starting below, you should follow this format:

User query: the query a User wants help with related to the API

Thought: you should always think about what to do

Action: the action to take, should be one of the tools

Action Input: the input to the action

Observation: the result of the action

... (this Thought/Action/Action Input/Observation can repeat N times)

Thought: I am finished executing a plan and have the information the user asked for or the data the user asked to create

Final Answer: the final output from executing the plan. This must always include the name of the function
you wrote to check the condition.

Your must always output a thought, action, and action input.

Begin! 
            """
    agent = create_agent(model=get_llm(),
                         tools=[SmartThingsPlannerTool, tool_get_all_entities, tool_get_services_by_domain,
                                tool_execute_action_by_entity_id, tool_get_states_by_entity_id,PythonInterpreterTool,],
                         system_prompt=system_prompt,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="ConditionCheckerTool")
    )
    return result["messages"][-1].content

@tool
def NotifyOnConditionTool(function_name,notify_when,condition_description,action_description):
    """
    Use this tool to get notified when a condition occurs. It should be called keys:
    function_name (str): name of the function that checks the condition
    notify_when (bool): [true, false]
    condition_description (str)
    action_description (str): what to do when the condition is met
    """
    return f"You will be notified when the condition occurs."