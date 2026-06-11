from langchain.tools import tool
from smartHome.m_agent.memory import get_demo_memory_runtime

@tool
def query_tool(query:str):
    """
    查询返回记忆库中相关长期记忆摘要
    """
    runtime = get_demo_memory_runtime()
    result = runtime.search(query)
    return runtime.format_search_result(query, result)
