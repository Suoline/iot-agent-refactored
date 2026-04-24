import json

import requests
from agent_project.agentcore.config.global_config import HOMEASSITANT_AUTHORIZATION_TOKEN, HOMEASSITANT_SERVER, \
    ACTIVE_PROJECT_ENV, PRIVACYHANDLER

token = HOMEASSITANT_AUTHORIZATION_TOKEN
server = HOMEASSITANT_SERVER
active_project_env = ACTIVE_PROJECT_ENV
privacyHandler = PRIVACYHANDLER

def get_all_entity_id():
    """
    Returns an array of state objects.
    Each state has the following attributes: entity_id, state, last_changed and attributes.
    """
    result = None
    if active_project_env == "dev":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        url = f"http://{server}/api/states"

        # 发送GET请求
        response = requests.get(url, headers=headers)
        # 检查请求是否成功
        response.raise_for_status()
        # 返回JSON响应内容
        result = response.json()

    return result

def get_services_by_domain(domain):
    """
    return all services included in the domain.
    """
    if active_project_env == "dev":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        url = f"http://{server}/api/services"

        # 发送GET请求
        response = requests.get(url, headers=headers)
        # 检查请求是否成功
        response.raise_for_status()
        # 返回JSON响应内容
        all_domain_and_services = response.json()
        for domain_entry in all_domain_and_services:
            # 匹配目标 domain
            if domain_entry.get("domain") == domain:
                # 返回该 domain 对应的 services 字典
                return domain_entry
            # 若未找到目标 domain，返回空字典
        return {}

def get_states_by_entity_id(entity_id) :
    """
    Returns a state object for specified entity_id.
    Returns 404 if not found.
    """

    result = None
    if active_project_env == "dev":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        url = f"http://{server}/api/states/{entity_id}"

        # 发送GET请求
        response = requests.get(url, headers=headers)
        # 检查请求是否成功
        response.raise_for_status()
        # 返回JSON响应内容
        result = response.json()

    return result

def execute_domain_service_by_entity_id(
        domain,
        service,
        body,
):
    """
    Calls a service within a specific domain. Will return when the service has been executed.

    由于智能家居的数据已经进行加密处理(加密后的数据形如：@xxx@)，如果你需要对传入body中的某些加密数据进行算术运算。你可以用在其前后加入算术运算，例如：
    {"entity_id": "@nB/MRO8IqOyD9Kj8t9A3kw==:5sWFd4t1UNtxvhX2LYYaqOZ6aVIKfXw7LiBwXmE/d38n30HHZColHIGWTZPpQlo6@", "brightness_pct": @n+4XiEGjo3K4qp1+WdooLw==:E034U68+xYq6U47e5i/isA==@*5-4}

    """
    import agent_project.agentcore.config.global_config as global_config
    logger = global_config.GLOBAL_AGENT_DETAILED_LOGGER
    if logger != None:
        logger.info("\n请求的body:\n" + body)
    result = None
    if active_project_env == "dev":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        url = f"http://{server}/api/services/{domain}/{service}"
        # 设置请求体数据
        # payload = {
        #     "entity_id": entity_id
        # }
        payload = json.loads(body)

        # 发送POST请求
        response = requests.post(
            url=url,
            json=payload,  # 自动将字典转换为JSON并设置正确的Content-Type
            headers=headers
        )
        # 检查请求是否成功
        response.raise_for_status()
        # 返回JSON响应
        result = response.json()

    return result

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
    from agent_project.agentcore.smart_home_agent.test_with_baselines.baselines_homeassitant.sage.smart.homeAssitant_api_func import \
    get_all_entity_id,get_services_by_domain,get_states_by_entity_id,execute_domain_service_by_entity_id
    %s

    return %s
""" % (
        code_define,
        code_run,
    )
    exec(wrapper_fn)
    return eval("wrapper()")

if __name__ == "__main__":
    print(run_code("a=get_all_entity_id()", "a"))
    pass