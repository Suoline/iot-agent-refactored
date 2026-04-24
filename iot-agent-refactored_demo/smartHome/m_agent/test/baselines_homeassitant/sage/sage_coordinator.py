from langchain.agents import create_agent

from smartHome.m_agent.agent.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.agent.persistent_tools import NotifyOnConditionTool
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.human_interaction import HumanInteractionTool
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.memory_with_Profile import UserProfileTool
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.persistent import ConditionCheckerTool
from smartHome.m_agent.test.baselines_homeassitant.sage.smart.smartThings import SmartThingsTool


def run_sageAgent(command: str):
    system_prompt = f"""
    【任务】{command}
    You are an agent who controls smart homes. You always try to perform actions on their smart devices in response to user input.

    Instructions:
    - Try to personalize your actions when necessary.
    - Plan several steps ahead in your thoughts
    - The user's commands are not always clear, sometimes you will need to apply critical thinking
    - Tools work best when you give them as much information as possible
    - Only perform the task requested by the user, don't schedule additional tasks
    - You cannot interact with the user and ask questions.
    - You can assume that all the devices are smart.
    
    涉及持久化的操作请直接使用ConditionCheckerTool和NotifyOnConditionTool
    不要无端臆想没有提供的设备能力
    不要无谓地重复调用工具执行已完成的任务或者执行不了的任务，请合理地根据工具执行结果再决定是否需要继续调用或就此结束
    不要试图自己规划设备调用任务    
    """
    agent = create_agent(model=get_llm(),
                         tools=[SmartThingsTool,UserProfileTool,
                                ConditionCheckerTool,
                                NotifyOnConditionTool,
                                # HumanInteractionTool
                                ],
                         system_prompt=system_prompt,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="sage_coordinator")
    )
    return result["messages"][-1].content


if __name__ == "__main__":
#     run_sageAgent("""
# 关闭所有灯，我睡觉时不留灯开着。
#     """)
#     run_sageAgent("开灯")
#     run_sageAgent("不，就打开我当前位置的灯就行")
    run_sageAgent("打开灯light.yeelink_cn_1162512052_mbulb3_s_2")
    # run_sageAgent("打开灯light.yeelink_cn_1162511951_mbulb3_s_2")