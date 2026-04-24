import traceback

from langchain.tools import tool




def run_code(code_define: str, code_run: str):
    """
    Run some code in a string and return the result

    Args:
        code_define (str): the code that does imports, function definitions, etc
        code_run (str): the one line whose result you want to output.
    """
    # adding the wrapper function is needed to get the imports to work
    # add indentation
    code_define = "\n    ".join(code_define.split("\n"))
    code_run = code_run.strip("\n")

    wrapper_fn = """
def wrapper():
    from smartHome.m_agent.memory.fake.fake_request import fake_get_states_by_entity_id as tool_get_states_by_entity_id
    %s

    return %s
""" % (
        code_define,
        code_run,
    )
    exec(wrapper_fn)
    return eval("wrapper()")

@tool
def PythonInterpreterTool(command)->str:
    """
    Use this tool to run python code to automate tasks when necessary.
    Use like python_interpreter(<python code string here>). Make sure the last line is the entrypoint to your code.
    """
    command = command.replace("```", "").replace("python", "")
    try:
        # 将代码分割为“定义部分”和“执行部分”：按最后一个换行符拆分
        # 假设最后一行为执行入口（如函数调用），其余为定义（如函数、变量定义）
        code_define, code_run = command.strip("\n").rsplit("\n", 1)

        # 处理打印语句：若执行部分是 print(...)，则提取括号内的内容作为实际执行代码
        # 例如将 print(my_function()) 转换为 my_function()
        if code_run[:5] == "print":
            code_run = code_run[6:-1]

        # 提取执行部分的函数名：从执行代码（如 my_function(123)）中解析出函数名（my_function）
        # 用于后续将代码信息注册到 code_registry 中
        fn_name = (
            code_run.split("(")[0]  # 按左括号分割，取左侧部分（函数名+可能的空白符）
            .replace("\n", "")  # 移除换行符
            .replace(" ", "")  # 移除空格
            .replace("\t", "")  # 移除制表符
        )

        # 执行代码：先执行定义部分（如函数定义），再执行执行部分（如函数调用），获取结果
        result = run_code(code_define, code_run)

        # 返回代码执行结果
        return result
    except Exception:
        # 若执行过程中出错，返回错误堆栈信息（便于调试）
        return traceback.format_exc()

@tool
def NotifyOnConditionTool(function_name,notify_when,condition_description,action_description):
    """
    Use this tool to get notified when a condition occurs. It should be called keys:
    function_name (str): name of the function that checks the condition
    notify_when (bool): [true, false]，即当调用function_name对应的函数得到的布尔返回值==notify_when时，执行action_description描述的动作。
    condition_description (str)
    action_description (str): what to do when the condition is met
    """
    return f"You will be notified when the condition occurs."

if __name__ == "__main__":
    from smartHome.m_agent.memory.fake.fake_request import fake_get_states_by_entity_id as tool_get_states_by_entity_id


    def check_door_not_closed():
        # 获取实体状态
        data = tool_get_states_by_entity_id(
            {"entity_id": "binary_sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_contact_state_p_2_2"})
        state = data.get("state") if isinstance(data, dict) else None
        # 门未关闭时（open）Home Assistant 的二进制传感器通常为 'on'
        return state == "on"


    # 运行一次检查并返回结果

    check_door_not_closed()