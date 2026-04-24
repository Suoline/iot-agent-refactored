from langchain.agents import create_agent

from smartHome.m_agent.common.get_llm import create_custom_llm
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from smartHome.m_agent.agent.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from pydantic import BaseModel, Field

class JudgeResult(BaseModel):
    """
       答案评判结果模型
       用于封装“答案是否符合预期”的评判结果及理由
       """
    # 规范命名+正确的bool类型示例+补充默认值
    judge_result: bool = Field(
        default=False,  # 补充合理默认值
        description="评判结果，布尔类型，表示答案是否符合预期",
        examples=[True, False]  # 修正为布尔值，而非字符串
    )

    # 补充长度校验+有意义的示例+默认空字符串
    reason: str = Field(
        default="",
        description="评判结果的理由（50字以内）",
        examples=["答案计算逻辑错误，正确结果应为10，实际输出为8", "答案完全符合预期，逻辑和结果均正确"]
    )

def check_answer_matches_expected(answer:str,expected:str):
    prompt = f"""
    你是答案评判助手，需完成以下任务：
    1. 对比内容：
       【实际答案】：{answer}
       【预期答案】：{expected}
    2. 输出要求：
       - judge_result：布尔值（True/False），表示是否符合预期
       - reason：50字以内的评判理由，说明匹配/不匹配的核心原因
    """
    llm=create_custom_llm(model="gpt-5-mini",base_url=GLOBALCONFIG.configparser.get("uniapi", 'base_url'),api_key=GLOBALCONFIG.configparser.get("uniapi", 'api_key'))
    agent = create_agent(model=llm,
                         tools=[
                         ],
                         response_format=JudgeResult,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": prompt},
        ]},
        context=AgentContext(agent_name="结果评判")
    )

    judgeResult = result["structured_response"]
    # # 无缩进（紧凑格式，适合传输/存储）
    # json_str_compact = deviceInfoList.model_dump_json()
    # # 带缩进（美化格式，适合调试/查看）
    # json_str_pretty = deviceInfoList.model_dump_json(indent=4)
    # ans=result["messages"][-1].content

    return judgeResult.judge_result

if __name__ == "__main__":
    answer="""
def bedside_brightness_below_26():
    \"\"\"Return True if the bedside lamp brightness is below 26.\"\"\"
    # tool_get_states_by_entity_id is available in the runtime and returns the entity state JSON
    state = tool_get_states_by_entity_id(entity_id="light.philips_cn_1061200910_lite_s_2")
    if not state:
        return False
    attrs = state.get("attributes", {})
    brightness = attrs.get("brightness")
    try:
        return bool(brightness is not None and int(brightness) < 26)
    except Exception:
        return False

# execution entry
bedside_brightness_below_26()
    """
    expected="""
编写的代码应该依据实体(id=light.philips_cn_1061200910_lite_s_2)的亮度值来验证条件【灯亮度低于10%】。
                   可以检查亮度值<26或者检查亮度百分比小于10%
                   答案中需表明这一信息或与此相符。
    """
    print(check_answer_matches_expected(answer=answer, expected=expected))