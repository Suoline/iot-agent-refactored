import configparser
import os

from smartHome.m_agent.common.logger import get_logger, setup_dynamic_indent_logger


class Global_Config():
    def __init__(self):
        self.configparser=self.load_configparser()
        # 功能启用
        self.privacy_protection_enabled = False

        # llm相关配置
        # Experiments may select a configured provider for one isolated child
        # process without rewriting the repository-wide default configuration.
        self.provider = os.environ.get(
            "SMART_HOME_M_AGENT_PROVIDER",
            self.configparser.get("base", 'selected_llm_provider'),
        )
        if not self.configparser.has_section(self.provider):
            raise ValueError(f"unknown_smart_home_llm_provider:{self.provider}")
        self.model = self.configparser.get(self.provider, 'model')
        self.base_url = self.configparser.get(self.provider, 'base_url')
        self.api_key = self.configparser.get(self.provider, 'api_key')

        # 日志
        self.logger = get_logger("my_test","logs/my_test.log")
        # self.memory_logger= get_logger("memory_update","logs/memory_update.log")
        self.memory_update_logger= setup_dynamic_indent_logger(logger_name="memory_update", log_file_path="logs/memory_update.log")
        self.memory_init_logger= setup_dynamic_indent_logger(logger_name="memory_init", log_file_path="logs/memory_init.log")
        # self.memory_init_logger= get_logger("memory_init","logs/memory_init.log")
        self.agent_init_dialogue_logger= setup_dynamic_indent_logger(logger_name="agent_init_dialogue", log_file_path="logs/agent_init_dialogue.log")
        # 嵌套agent多层次日志打印
        # self.nested_level=-1
        self.nested_agent_map={}
        self.nested_logger=setup_dynamic_indent_logger(logger_name="dynamic_indent_logger",log_file_path="logs/nested_agent.log")

        # env取值：dev，test，prod
        self.env="test"

        # homeassitant 配置
        self.homeassitant_api_isopen=True
        self.homeassitant_token = self.configparser.get("homeassitant", 'homeassitant_token')
        self.homeassitant_server = self.configparser.get("homeassitant", 'homeassitant_server_ip_port')
    def load_configparser(self):
        # 获取当前文件(global_config.py)的绝对路径
        current_file_path = os.path.abspath(__file__)
        # 定位到配置文件所在目录（与global_config.py同目录）
        config_dir = os.path.dirname(current_file_path)
        # 拼接配置文件的绝对路径
        llm_config_file_path = os.path.join(config_dir, 'llm_config.ini')

        # 创建配置解析器对象
        llm_configparser = configparser.ConfigParser()
        # 读取INI文件
        llm_configparser.read(llm_config_file_path, encoding='utf-8')

        # 设置LangSmith跟踪开关和API密钥和标签
        os.environ["LANGSMITH_TRACING"] = llm_configparser.get('LangSmith', 'langsmith_tracing')
        os.environ["LANGSMITH_API_KEY"] = llm_configparser.get('LangSmith', 'langsmith_api_key')
        os.environ["LANGSMITH_PROJECT"] = llm_configparser.get('LangSmith', 'LANGSMITH_PROJECT')

        return llm_configparser

    def print_nested_log(self, message: str,level: int=-1):
        """
        封装嵌套日志打印函数，根据层级自动计算缩进
        :param level: 嵌套层级（0=无缩进，1=4个空格，2=8个空格...）
        :param message: 日志消息
        """
        if(level==-1):
            level=self.get_nested_level()
        indent_space = "    " * level  # 每级缩进4个空格，符合Python编码规范
        self.nested_logger.info(message, extra={"indent": indent_space})

    def add_agent_name(self, agent_name):
        """添加字符串，存在则计数+1，不存在则新增key（计数=1）"""
        if agent_name in self.nested_agent_map:
            self.nested_agent_map[agent_name] += 1
        else:
            self.nested_agent_map[agent_name] = 1

    def delete_agent_name(self, agent_name):
        """删除字符串，计数-1；计数为0则删除该key"""
        if agent_name not in self.nested_agent_map:
            return
        self.nested_agent_map[agent_name] -= 1
        if self.nested_agent_map[agent_name] == 0:
            del self.nested_agent_map[agent_name]

    def get_nested_level(self):
        """返回字典中当前的key数量-1"""
        return len(self.nested_agent_map)-1

GLOBALCONFIG=Global_Config()
