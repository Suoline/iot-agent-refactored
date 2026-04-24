from smartHome.m_agent.agent.base_home_agent import run_ourAgent
from smartHome.m_agent.common.global_config import GLOBALCONFIG


def run_codec_Agent(task:str):
    GLOBALCONFIG.privacy_protection_enabled=True
    return run_ourAgent(task)