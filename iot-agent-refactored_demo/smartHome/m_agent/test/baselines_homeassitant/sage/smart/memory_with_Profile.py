import json
import os
import uuid

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from langchain.agents import create_agent
from langchain_core.tools import tool
from chromadb.api.models.Collection import Collection
from smartHome.m_agent.common.get_llm import get_llm
from smartHome.m_agent.agent.langchain_middleware import AgentContext, log_before, log_response, log_before_agent, \
    log_after_agent
from smartHome.m_agent.common.global_config import GLOBALCONFIG

preferences_map={
    "gpt-5-mini":"""
- 设备/房间映射
  - 卧室灯 = 设备1，卧室床边灯 = 设备4（床边灯有星星装饰）
  - 书房灯泡 = 设备2，书房台灯（已命名为“小书灯”）= 设备5（device_id: e2bf03e9b274e88f9e7b6852d1e2c90d）
  - 客厅灯 = 设备3
  - 插座6 连风扇，插座7 连加湿器（加湿器除非明确要求否则不得关）
  - 门窗/传感器：c开头 = 进门门上，d开头 = 客厅窗户；人体传感器：f开头在客厅，5开头在卧室，a开头在书房

- 灯光偏好
  - 常用色温约 3000K（偏暖）
  - 睡觉时不想要任何灯光——常设为睡眠时关闭所有灯
  - 睡前看新闻/睡前看书：关闭房间灯泡，床边灯维持较低亮度（或 10%）
  - 看书时不用顶灯/灯泡，使用台灯（小书灯）并设为中等亮度
  - 有时喜欢“极低亮度暖光”作为氛围灯

- 音频/音乐偏好
  - 常听周杰伦歌曲
  - 默认音量为 10
  - 午睡/安静场景常用较低音量（示例：5）
  - 来电话时将音箱音量调至最低，接电话时音箱静音
  - 有场景设置：说“客厅午睡”时（已设）会打开风扇并播放音乐

- 场景与自动化
  - 氛围组 = 书房灯泡 + 书房台灯（小书灯）
  - 网关夜间勿扰已设为 22:00–07:00
  - 对网关联网信息有偏好网络名 ccrv-tv

- 操作与约束
  - “把可以关掉的设备都关了”类指令会关闭灯、插座和音箱等可关设备
  - 空气加湿器除非用户明确说关，否则不要关闭
  - 已记录并会识别用户为设备所做的命名与装饰（如“小书灯”、卧室灯的星星装饰）
""",
    "gpt-5-nano":"""用户偏好要点简要整理：

- 设备与命名
  - 喜欢用日常、易记的昵称，并会让系统记住映射，例如把书房台灯叫“小书灯”（与具体设备ID绑定）。
  - 对设备有清晰的房间分组（卧室、书房、客厅等），能按区域单独控制。

- 灯光偏好
  - 常用暖色温，目标色温大约3000K。
  - 阅读/看书时偏好中等或较低亮度；睡前常将床边灯保持低亮或单独控制，睡眠时尽量关闭大部分灯。
  - 有时需要“关闭所有灯”作为睡眠入口，但也允许在特定情景下保留床边灯等低亮度灯。

- 场景与自动化
  - 设定场景触发，如“客厅午睡”自动开启风扇并播放音乐等。
  - 希望保存并使用场景指令，便于未来直接喊出“客厅午睡”等。
  - 关注节日/氛围场景（如万圣节轻音乐等）。
  - 对网关夜间勿扰有明确时间段设定（如22:00–07:00）。

- 音乐与音箱
  - 常用音箱播放音乐，愿意设定并调整音量（如10、5等）；
  - 接电话时音箱静音，来电后自动处理音量与状态。

- 安全与传感
  - 通过门/窗传感器和人体传感器判断房间状态（如客厅是否有人、窗户是否开着）。
  - 对门窗传感器有固定的命名前缀映射（如c开头为进门门，d开头为客厅窗户）。

- 网络与设备状态
  - 会核对网关连接的WiFi名称，偏好属于自己的网络。
  - 愿意查看并确认窗户/门的状态、以及房间内设备的当前状态。

- 额外偏好
  - 希望能记住灯饰、床头装饰等个性化信息（如卧室灯的星星形装饰），以便未来触发时同步设备。""",
}
preferences=preferences_map[GLOBALCONFIG.model]
def update_daily_user_preferences(history:str):
    """基于每日交互提取用户的每日偏好"""

    # 提示模板：基于提供的交互历史总结用户偏好
    system_prompt = f"""基于以下交互，请总结用户的偏好。历史内容：\n
            {history}
            用户的偏好是："""
    llm=get_llm()
    result = llm.invoke(
        [
            {"role": "system", "content": system_prompt},
        ])
    return result.content
def get_short_uuid_by_cut(length: int = 12) -> str:
    """
    截取uuid的hex值生成短字符串
    :param length: 要生成的短字符串长度（建议4-16位，过长失去缩短意义，过短易冲突）
    :return: 简短字符串（十六进制，0-9a-f）
    """
    if not (4 <= length <= 32):
        raise ValueError("长度建议在4-32之间")
    full_hex = uuid.uuid4().hex
    return full_hex[:length]  # 截取前length位（也可截取后length位：full_hex[-length:]）
class MemoryBank():
    def __init__(self):
        from smartHome.m_agent.common.global_config import GLOBALCONFIG
        # 初始化文本嵌入函数（保持不变）
        self.embedding_func = SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"  # 轻量高效，支持中英文
        )
        # 初始化Chroma向量数据库（保持不变，支持持久化）
        current_dir = os.path.dirname(os.path.abspath(__file__))
        db_dir = f"{GLOBALCONFIG.provider}_{GLOBALCONFIG.model}_chroma_text_db"
        file_path = os.path.join(current_dir, db_dir)
        self.client = chromadb.PersistentClient(path=file_path)
    def get_or_create_collection(self, collection_name: str="user") -> Collection:
        """
        获取或者创建以 user 的集合
        """
        return self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_func,
            metadata={
                "description": "存储用户交互记录",
            }
        )

    def add_text_to_vector_db(self,content: str, collection_name: str="user"):
        """将交互记录存入向量数据库"""
        collection=self.get_or_create_collection()

        # 入库操作
        collection.add(
            ids=[get_short_uuid_by_cut()],
            documents=[content],
        )
        print(f"✅ 文本「{content}」已成功存入向量数据库")

    def search(self, query: str, top_k: int, collection_name: str = "user") -> list[str]:
        """
        从数据库中检索出与query最相似的topk个记忆，仅返回文档内容列表
        Args:
            query: 检索查询语句
            top_k: 返回最相似的记录数量
            collection_name: 集合名称，默认"user"
        Returns:
            匹配到的文档内容列表（str列表），无匹配结果返回空列表
        """
        # 步骤1：获取目标集合
        collection = self.get_or_create_collection(collection_name)

        # 步骤2：参数合法性校验
        if not query.strip():
            raise ValueError("检索查询语句query不能为空，请输入有效内容")
        if not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k必须为正整数，请传入大于0的整数")

        # 步骤3：执行向量检索
        search_results = collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents"]  # 仅按需获取documents，提升效率
        )

        # 步骤4：提取并返回documents列表（核心修改：只保留文本内容列表）
        # 提取单个query的结果，直接返回扁平的documents列表
        documents_list = search_results["documents"][0]

        # 步骤5：打印检索提示（可选，方便调试）
        print(f"🔍 检索完成，找到与「{query[:30]}...」最相似的 {len(documents_list)} 条记录")

        # 步骤6：仅返回documents列表
        return documents_list


MEMORYBANK=MemoryBank()
@tool
def UserProfileTool(query:str):
    """
    Use this to learn about the user preferences and retrieve past interactions with the user.
    Use this tool before addressing subjective or ambiguous user commands that require personalized information.
    This tool is not capable of asking the user anything.
    The query should be clear, precise and formatted as a question.
    The query should specify which type of preferences you are looking for.
    """
    # 检索相关记忆和偏好
    memories = MEMORYBANK.search(query=query, top_k=5)

    system_prompt=f"""
    您是一个AI，应该能够（1）推断和理解用户偏好；（2）理解过去的交互并提取有关用户偏好的相关信息。
    尝试根据可用的偏好和命令历史做出最佳判断。您的任务是（1）首先检查用户偏好；（2）如果用户偏好中没有答案，您应该从命令历史中推断答案；
    
    如果请求没有相关信息，请说明。
    问题：{query}
    用户的偏好是：{preferences}
    最相关的命令历史：{memories}    
    """

    agent = create_agent(model=get_llm(),
                         tools=[],
                         system_prompt=system_prompt,
                         middleware=[log_before, log_response, log_before_agent, log_after_agent],
                         context_schema=AgentContext
                         )
    result = agent.invoke(
        input={"messages": [
            {"role": "system", "content": system_prompt},
        ]},
        context=AgentContext(agent_name="UserProfileTool")
    )
    return result["messages"][-1].content

def load_json_and_convert_dialogues():
    """
        加载JSON文件，将每段对话转换为一段格式化字符串
        :param json_file_path: JSON文件的路径（相对路径或绝对路径）
        :return: 包含每段对话字符串的列表，若执行失败返回空列表
        """
    json_file_path="./dialogue_records.json"
    # 初始化返回结果列表
    dialogue_str_list = []

    try:
        # 1. 打开并加载JSON文件（with语句自动关闭文件，更安全）
        with open(json_file_path, 'r', encoding='utf-8') as f:
            dialogue_json = json.load(f)

        # 2. 遍历JSON中的每一段对话（外层数组的每个子数组）
        for single_dialogue in dialogue_json:
            # 初始化单段对话的拼接字符串
            dialogue_content = ""

            # 3. 遍历单段对话中的每个角色消息（user/ai）
            for msg_obj in single_dialogue:
                # 提取user或ai的内容，避免KeyError
                if "user" in msg_obj:
                    dialogue_content += f"用户：{msg_obj['user']}\n"
                elif "ai" in msg_obj:
                    dialogue_content += f"AI：{msg_obj['ai']}\n"

            # 4. 去除末尾多余的换行符，添加到结果列表（可选，提升整洁度）
            dialogue_str_list.append(dialogue_content.rstrip('\n'))

        return dialogue_str_list

    except FileNotFoundError:
        print(f"错误：未找到指定的JSON文件 -> {json_file_path}")
        return []
    except json.JSONDecodeError:
        print("错误：JSON文件格式无效，无法解析")
        return []
    except Exception as e:
        print(f"错误：执行过程中出现未知异常 -> {str(e)}")
        return []

if __name__ == "__main__":
    # 初始偏好画像
    # dialogue_str_list=load_json_and_convert_dialogues()
    # result_str = '\n'.join(dialogue_str_list)
    # ans=update_daily_user_preferences(result_str)
    # print(ans)

    # 初始用户交互记录到向量库
    dialogue_str_list = load_json_and_convert_dialogues()
    for idx, dialogue_str in enumerate(dialogue_str_list, start=1):
        MEMORYBANK.add_text_to_vector_db(content=dialogue_str)

    # query="睡觉时的习惯"
    # query="网关勿扰偏好"
    # result=MEMORYBANK.search(query=query, top_k=5)
    # print(result)

    # UserProfileTool(query=query)
    """
    根据已有偏好和历史记录，整理出您“睡觉时”的习惯（可直接用于自动化）：

主要行为
- 关灯：睡觉时关闭所有灯（卧室顶灯、书房、客厅等）。
- 床边灯：床边灯（设备 4）除外，应保持最低亮度约 10%。
- 加湿器：插座 7（空气加湿器）按偏好默认保持开启，除非您明确说“关”。
- 插座/风扇：客厅暂眠时须关闭风扇（插座 6）。卧室常规睡觉未明确要求关闭风扇时可按场景处理。
- 音箱/音量：通常不需要大音量；若需要背景音，音量偏低（午睡示例为 5）；默认听歌音量为 10。接到来电时将音箱音量调最低并在通话期间静音。
- 网关勿扰：夜间网关勿扰时间为 22:00–07:00（不希望网关灯或打扰在夜间出现）。
- 色温：默认偏好 3000K；睡眠/氛围时可能偏暖、极低亮度。

其它便利偏好
- 喜欢一键关闭可关闭的所有设备（灯、插座、音箱）。
- “氛围组”与读书／临睡前场景已有偏好（可复用为睡眠场景的一部分）。

需要补充或确认的项目
- 睡眠时是否要明确关闭卧室插座（例如风扇/其他设备）？当前只在“暂眠客厅”明确要求关闭风扇。
- 是否希望我把上述设置保存为“睡觉模式”自动化并在触发时自动执行，还是每次执行前先提示您确认？

要我现在为您保存并启用“睡觉模式”自动化吗？
    """
    pass