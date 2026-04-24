from langchain.tools import tool

@tool
def query_tool(query:str):
    """
    查询工具
    """
    return "目前没有相关记忆"