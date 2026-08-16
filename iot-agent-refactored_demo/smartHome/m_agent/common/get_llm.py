from langchain.chat_models import init_chat_model
from langchain_core.callbacks import CallbackManager

from smartHome.m_agent.common.global_config import GLOBALCONFIG


def get_llm():
    # max_tokens: int = 1000
    provider=GLOBALCONFIG.provider
    model = GLOBALCONFIG.model
    base_url = GLOBALCONFIG.base_url
    api_key = GLOBALCONFIG.api_key

    llm = init_chat_model(
        model=model,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_retries=0,
        # The product Agent performs multi-tool turns through an external proxy.
        # Keep retries explicit in middleware, but allow one request enough time
        # to receive a valid upstream response before classifying it as transport.
        timeout=90,
    )
    return llm

def create_custom_llm(model:str,base_url:str=GLOBALCONFIG.base_url,api_key:str=GLOBALCONFIG.api_key):
    # max_tokens: int = 1000
    provider=GLOBALCONFIG.provider
    # model = GLOBALCONFIG.model
    # base_url = GLOBALCONFIG.base_url
    # api_key = GLOBALCONFIG.api_key

    llm = init_chat_model(
        model=model,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_retries=0,
        timeout=90,
    )
    return llm

if __name__ == "__main__":
    llm=get_llm()
    print(llm.invoke("你好"))
