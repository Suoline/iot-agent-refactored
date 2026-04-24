from langchain.tools import tool

@tool
def ask_human(quetion: str) -> str:
    """向人类提问，以获取缺失的信息。"""
    ans = input(f"请告诉我 {quetion}的答案: ")
    return ans