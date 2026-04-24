import datetime
import json
import re
import traceback
from functools import wraps
from typing import Union, Dict, List
from smartHome.m_agent.memory.fake.fake_request import fake_get_states_by_entity_id
from smartHome.m_agent.test.test_code.test_judge_result import METHOD_CALL_STATUS

"""
饰器的执行顺序遵循 “就近原则”：
即离函数定义最近的装饰器先执行，然后依次向外层装饰器传递结果。
最终，函数会被多层装饰器 “包装”，每层装饰器的逻辑都会生效。
"""

domain_register={}
# 定义带参数的装饰器 @domain(name="xx")
def domain(name):
    def decorator(func):
        # 将函数注册到字典中：键为name，值为函数对象
        domain_register[name] = func
        # 返回原函数（不修改函数功能）
        return func
    return decorator

# todo 执行domain中的service时，如果body里的entity_id；其他参数错误会返回什么response?!
def exception_return(response):
    """装饰器：当函数执行出现异常时，返回指定的response"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                # 尝试执行原函数
                return func(*args, **kwargs)
            except  Exception as e:  # 关键：as e 绑定具体异常实例到变量e
                # 打印捕获到的具体异常实例（包含错误描述）
                print("发生异常：", e)
                traceback.print_exc()
                return response
        return wrapper
    return decorator


def update_service_time(func):
    """
    装饰器：更新函数返回的entity字典中三个时间字段为当前时间的ISO格式
    """

    @wraps(func)  # 保留原函数的元信息（如名称、文档字符串等）
    def wrapper(*args, **kwargs):
        # 调用被装饰的函数，获取其返回的entity
        entity = func(*args, **kwargs)

        # 检查返回值是否为字典（确保可以更新字段）
        if isinstance(entity, dict):
            # 获取当前时间的ISO格式字符串
            current_time = datetime.datetime.now().isoformat()

            # 更新三个时间字段
            entity["last_changed"] = current_time
            entity["last_reported"] = current_time
            entity["last_updated"] = current_time
        # 返回更新后的entity
        return entity

    return wrapper

bad_request="400: Bad Request"

@exception_return(response=bad_request)
def fake_execute_domain_service_by_entity_id(domain, service, body, ) -> Union[Dict, List]:
    """
    Calls a service within a specific domain. Will return when the service has been executed.
    """

    domain_func = domain_register[domain]
    result=domain_func(service,body)
    return result

@exception_return(response=bad_request)
@domain(name="switch")
def domain_switch(service:str,body:dict):
    """
        Switch域的服务入口，根据服务名称分发到对应处理函数
        :param service: 服务名称（turn_on/turn_off/toggle）
        :param body: 服务调用参数（包含目标实体等信息）
        :return: 处理结果
        """
    service_map = {
        "turn_on": service_switch_turn_on,
        "turn_off": service_switch_turn_off,
        "toggle": service_switch_toggle
    }
    body=json.loads(body)
    # 调用对应服务函数
    return service_map[service](body)

@update_service_time
def service_switch_turn_on(body: dict):
    """处理开关开启服务"""
    entity_id=body["entity_id"]
    entity=fake_get_states_by_entity_id(entity_id)
    entity['state']="on"
    friendly_name = entity["attributes"]["friendly_name"]
    return entity
@update_service_time
def service_switch_turn_off(body: dict):
    """处理开关关闭服务"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    entity['state'] = "off"
    return entity

@update_service_time
def service_switch_toggle(body: dict):
    """处理开关切换服务（反转当前状态）"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    if entity['state'] == "on":
        entity['state'] = "off"
    else:
        entity['state'] = "on"
    return entity

# todo 灯泡实体属性中的"hs_color"，"rgb_color"，"xy_color"会变化吗？如何变化。 目前未处理
# todo 台灯设置渐灭是会如何处理，entity如何改变
# Light域服务实现（针对Yeelink灯泡特性）
@exception_return(response=bad_request)
@domain(name="light")
def domain_light(service: str, body: str) -> Union[Dict, str]:
    """light域服务入口，处理Yeelink灯泡的开关、亮度和色温调节"""
    service_map = {
        "turn_on": service_light_turn_on,
        "turn_off": service_light_turn_off,
        "toggle": service_light_toggle
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

# 亮度调节工具函数（处理多种亮度参数）
def _adjust_brightness(entity: Dict, body: Dict):
    current_brightness = entity["attributes"]["brightness"]
    # 处理亮度绝对值（0-255）
    if "brightness" in body:
        brightness = max(0, min(255, int(body["brightness"])))
    # 处理亮度百分比（0-100%）
    elif "brightness_pct" in body:
        pct = max(0, min(100, int(body["brightness_pct"])))
        brightness = int(pct * 255 / 100)
    # 处理亮度步进百分比（-100%~100%）
    elif "brightness_step_pct" in body:
        step_pct = int(body["brightness_step_pct"])
        new_pct = (current_brightness / 255) * 100 + step_pct
        new_pct = max(0, min(100, new_pct))
        brightness = int(new_pct * 255 / 100)
    else:
        brightness = current_brightness  # 保持当前亮度
    entity["attributes"]["brightness"] = brightness

# 色温调节工具函数（处理K值和mired值）
def _adjust_color_temp(entity: Dict, body: Dict):
    # 色温范围：2700K~6500K（对应mired 153~370）
    if "color_temp_kelvin" in body:
        kelvin = max(2700, min(6500, int(body["color_temp_kelvin"])))
        entity["attributes"]["color_temp_kelvin"] = kelvin
        entity["attributes"]["color_temp"] = int(1000000 / kelvin)  # 转换为mired
    elif "color_temp" in body:
        mired = max(153, min(370, int(body["color_temp"])))
        entity["attributes"]["color_temp"] = mired
        entity["attributes"]["color_temp_kelvin"] = int(1000000 / mired)  # 转换为K值

def _set_effect_philips(entity: Dict, body: Dict):
    """处理特效切换逻辑"""
    # todo 并没有根据选择的mode实际改变灯的亮度
    if "effect" not in body:
        return  # 不指定则保持当前特效

    effect = body["effect"]
    valid_effects = entity["attributes"]["effect_list"]

    if effect not in valid_effects:
        raise ValueError(f"无效特效: {effect}，可选值为{valid_effects}")

    entity["attributes"]["effect"] = effect

# 灯服务处理函数
@update_service_time
def service_light_turn_on(body: Dict) -> Dict:
    """开启灯泡，支持同时调节亮度和色温"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    entity["state"] = "on"
    # 调节亮度（如有参数）
    _adjust_brightness(entity, body)
    friendly_name=entity["attributes"]["friendly_name"]
    if("灯泡" in friendly_name):
        # 调节色温（如有参数）
        _adjust_color_temp(entity, body)
    if ("台灯" in friendly_name):
        # 切换特效（若指定）
        _set_effect_philips(entity, body)
    return entity
@update_service_time
def service_light_turn_off(body: Dict) -> Dict:
    """关闭灯泡"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    entity["state"] = "off"
    return entity
@update_service_time
def service_light_toggle(body: Dict) -> Dict:
    """切换灯泡状态，切换为开启时可调节参数"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    if entity["state"] == "on":
        entity["state"] = "off"
    else:
        service_light_turn_on(body)
    return entity


@exception_return(response=bad_request)
@domain(name="text")
def domain_text(service: str, body: str) -> Union[Dict, str]:
    """text域服务入口，处理文本值的更新"""
    service_map = {
        "set_value": service_text_set_value  # 核心服务：设置文本值
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

def _text_gateway(body: Dict):
    """
        更新text实体的文本值（生效时间段）
        校验规则：
        1. 必须包含"value"参数
        2. 文本长度在min~max范围内
        3. 格式符合HH:MM-HH:MM（如23:00-07:30）
        """
    # 获取实体
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)

    new_value = body["value"]

    # 校验文本长度（基于实体的min和max属性）
    min_len = entity["attributes"]["min"]
    max_len = entity["attributes"]["max"]
    if not (min_len <= len(new_value) <= max_len):
        raise ValueError(f"文本长度需在{min_len}-{max_len}之间")

    # 校验时间段格式（HH:MM-HH:MM）
    time_pattern = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")
    if not time_pattern.match(new_value):
        raise ValueError("格式错误，需符合HH:MM-HH:MM（如21:00-09:00）")

    # 所有校验通过，更新实体状态
    entity["state"] = new_value
    return entity

def _text_speaker(body: Dict):
    """
        更新text实体的文本值（支持小米AI音箱勿扰模式时间段配置）
        校验规则：
        1. 必须包含"value"参数
        2. 文本长度在min~max范围内（0~255）
        3. 格式符合HH:MM:SS-HH:MM:SS（精确到秒的时间段）
        4. 时间值需合法（时0-23，分0-59，秒0-59）
        """
    # 获取实体
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)

    # 校验value参数存在性
    if "value" not in body:
        raise ValueError("缺少必填参数'value'")
    new_value = body["value"]

    # 校验文本长度（基于实体的min和max属性）
    min_len = entity["attributes"]["min"]
    max_len = entity["attributes"]["max"]
    if not (min_len <= len(new_value) <= max_len):
        raise ValueError(f"文本长度需在{min_len}-{max_len}之间")

    # 校验时间段格式（HH:MM:SS-HH:MM:SS）
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2}$")
    if not time_pattern.match(new_value):
        raise ValueError("格式错误，需符合HH:MM:SS-HH:MM:SS（如22:00:00-06:30:00）")

    # 拆分时间段并校验时间合法性
    start_time, end_time = new_value.split("-")
    for time_str in [start_time, end_time]:
        hh, mm, ss = map(int, time_str.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
            raise ValueError(f"无效时间值：{time_str}，时(0-23)、分(0-59)、秒(0-59)需合法")

    # 所有校验通过，更新实体状态
    entity["state"] = new_value
    return entity

@update_service_time
def service_text_set_value(body: Dict) -> Dict:
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    friendly_name = entity["attributes"]["friendly_name"]
    if ("网关" in friendly_name):
        result=_text_gateway(body)
    if ("音箱" in friendly_name):
        result=_text_speaker(body)
    return result

@exception_return(response=bad_request)
@domain(name="number")
def domain_number(service: str, body: str) -> Union[Dict, str]:
    """number域服务入口，处理数值型实体的更新"""
    service_map = {
        "set_value": service_number_set_value  # 仅支持set_value服务
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

def _number_gateway(body: Dict) -> Dict:
    """
        处理指示灯亮度设置服务
        校验规则：
        1. value必须为1-100之间的整数（包含边界）
        2. 必须包含entity_id参数
        """
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)

    # 获取并校验亮度值
    try:
        value = int(body["value"])
    except (ValueError, TypeError):
        raise ValueError("亮度值必须为整数")

    min_val = entity["attributes"]["min"]
    max_val = entity["attributes"]["max"]
    if not (min_val <= value <= max_val):
        raise ValueError(f"亮度值必须在{min_val}-{max_val}之间（包含边界）")

    # 更新实体状态
    entity["state"] = value
    return entity

def _number_desk_lamp(body: Dict) -> Dict:
    """
        处理卧室的米家智能台灯Lite延时关灯时间设置服务
        校验规则：
        1. value必须为0-21600之间的整数（包含边界）
        2. 必须包含entity_id参数
        """
    entity_id = body["entity_id"]
    # 验证实体ID是否匹配延时关灯时间配置实体
    if entity_id != "number.philips_cn_1061200910_lite_dvalue_p_3_1":
        raise ValueError(f"无效实体ID: {entity_id}，预期为延时关灯时间配置实体")

    entity = fake_get_states_by_entity_id(entity_id)

    # 获取并校验延时值
    try:
        value = int(body["value"])
    except (ValueError, TypeError):
        raise ValueError("延时值必须为整数")

    # 验证数值范围（0-21600秒）
    min_val = 0
    max_val = 21600
    if not (min_val <= value <= max_val):
        raise ValueError(f"延时值必须在{min_val}-{max_val}秒之间（包含边界）")

    # 更新实体状态（存储当前延时设置）
    entity["state"] = value
    return entity

@update_service_time
def service_number_set_value(body: Dict) -> Dict:
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    friendly_name = entity["attributes"]["friendly_name"]
    if ("网关" in friendly_name):
        result=_number_gateway(body)
    if ("台灯" in friendly_name):
        result=_number_desk_lamp(body)
    return result

@exception_return(response=bad_request)
@domain(name="select")
def domain_select(service: str, body: str) -> Union[Dict, str]:
    """select域服务入口，处理小米智能多模网关2的勿扰模式状态切换"""
    service_map = {
        "select_first": service_select_first,
        "select_last": service_select_last,
        "select_next": service_select_next,
        "select_option": service_select_option,
        "select_previous": service_select_previous
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

@update_service_time
def service_select_first(body: Dict) -> Dict:
    """选择选项列表中的第一个值(Close)"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    # 设置为选项列表中的第一个值
    entity["state"] = entity["attributes"]["options"][0]
    return entity
@update_service_time
def service_select_last(body: Dict) -> Dict:
    """选择选项列表中的最后一个值(Open)"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    # 设置为选项列表中的最后一个值
    entity["state"] = entity["attributes"]["options"][-1]
    return entity
@update_service_time
def service_select_next(body: Dict) -> Dict:
    """切换到下一个选项，支持循环"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    options = entity["attributes"]["options"]
    current_index = options.index(entity["state"])
    # 获取cycle参数，默认为True
    cycle = body.get("cycle", True)

    if current_index < len(options) - 1:
        # 不是最后一个选项，切换到下一个
        entity["state"] = options[current_index + 1]
    elif cycle:
        # 是最后一个选项且允许循环，切换到第一个
        entity["state"] = options[0]

    return entity
@update_service_time
def service_select_option(body: Dict) -> Dict:
    """直接指定选择某个选项"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    option = body["option"]

    # 验证选项是否有效
    if option not in entity["attributes"]["options"]:
        raise ValueError(f"无效选项: {option}，可选值为{entity['attributes']['options']}")

    entity["state"] = option
    return entity
@update_service_time
def service_select_previous(body: Dict) -> Dict:
    """切换到上一个选项，支持循环"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    options = entity["attributes"]["options"]
    current_index = options.index(entity["state"])
    # 获取cycle参数，默认为True
    cycle = body.get("cycle", True)

    if current_index > 0:
        # 不是第一个选项，切换到上一个
        entity["state"] = options[current_index - 1]
    elif cycle:
        # 是第一个选项且允许循环，切换到最后一个
        entity["state"] = options[-1]

    return entity


@exception_return(response=bad_request)
@domain(name="button")
def domain_button(service: str, body: str) -> Union[Dict, str]:
    """button域服务入口，处理米家智能台灯Lite控制按钮的服务调用"""
    # 仅支持press服务
    service_map = {
        "press": service_button_press
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

def _button_desk_lamp(body: Dict):
    entity_id = body["entity_id"]
    button_entity = fake_get_states_by_entity_id(entity_id)
    # 关联的台灯light实体ID
    light_entity_id = "light.philips_cn_1061200910_lite_s_2"
    light_entity = fake_get_states_by_entity_id(light_entity_id)

    # 更新按钮最后触发时间（状态即为触发时间）
    button_entity["state"] = datetime.datetime.now().isoformat()

    # 根据按钮类型执行不同操作
    if entity_id == "button.philips_cn_1061200910_lite_toggle_a_2_1":
        # 开关状态切换按钮：调用light的toggle服务
        fake_execute_domain_service_by_entity_id(
            domain="light",
            service="toggle",
            body=json.dumps({"entity_id": light_entity_id})
        )

    elif entity_id == "button.philips_cn_1061200910_lite_brightness_down_a_3_1":
        # 亮度降低按钮：每次递减25（最低0）
        if light_entity["state"] == "on":
            current_brightness = light_entity["attributes"].get("brightness", 0)
            new_brightness = max(0, current_brightness - 25)
            # 调用light的turn_on服务调节亮度（保持开启状态）
            fake_execute_domain_service_by_entity_id(
                domain="light",
                service="turn_on",
                body=json.dumps({
                    "entity_id": light_entity_id,
                    "brightness": new_brightness
                })
            )
            # 若亮度降至0，同步关闭灯光
            if new_brightness == 0:
                fake_execute_domain_service_by_entity_id(
                    domain="light",
                    service="turn_off",
                    body=json.dumps({"entity_id": light_entity_id})
                )

    elif entity_id == "button.philips_cn_1061200910_lite_brightness_up_a_3_2":
        # 亮度增加按钮：每次递增25（最高255）
        current_brightness = light_entity["attributes"].get("brightness", 0)
        new_brightness = min(255, current_brightness + 25)
        # 若当前灯是关闭状态，先开启
        if light_entity["state"] == "off":
            fake_execute_domain_service_by_entity_id(
                domain="light",
                service="turn_on",
                body=json.dumps({"entity_id": light_entity_id})
            )
        # 调节亮度
        fake_execute_domain_service_by_entity_id(
            domain="light",
            service="turn_on",
            body=json.dumps({
                "entity_id": light_entity_id,
                "brightness": new_brightness
            })
        )

    return button_entity

def _button_speaker(body: Dict):
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    entity["state"]=datetime.datetime.now().isoformat()
    friendly_name = entity["attributes"]["friendly_name"]
    if("播放音乐" in friendly_name):
        service_media_play(body)
    return entity
@update_service_time
def service_button_press(body: Dict) -> Dict:
    """处理按钮按压服务，根据不同按钮实体联动控制light实体"""
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    friendly_name = entity["attributes"]["friendly_name"]
    result=None
    if ("音箱" in friendly_name):
        result=_button_speaker(body)
    if ("台灯" in friendly_name):
        result=_button_desk_lamp(body)
    return result


# 媒体播放器领域服务实现
@exception_return(response=bad_request)
@domain(name="media_player")
def domain_media_player(service: str, body: str) -> Union[Dict, str]:
    """media_player域服务入口，处理小米AI音箱的媒体控制"""
    service_map = {
        # 音量控制类
        "volume_set": service_volume_set,
        "volume_up": service_volume_up,
        "volume_down": service_volume_down,
        "volume_mute": service_volume_mute,
        # 播放控制类
        "media_play": service_media_play,
        "media_pause": service_media_pause,
        "media_play_pause": service_media_play_pause,
        "media_stop": service_media_stop,
        # 曲目与列表类
        "media_previous_track": service_media_previous,
        "media_next_track": service_media_next,
        # "clear_playlist": service_clear_playlist
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)

# 音量控制服务实现
@update_service_time
def service_volume_set(body: Dict) -> Dict:
    """设置音量（0.0~1.0）"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    volume = float(body["volume_level"])
    if not (0.0 <= volume <= 1.0):
        raise ValueError("音量必须在0.0-1.0之间")
    entity["attributes"]["volume_level"] = volume
    return entity
@update_service_time
def service_volume_up(body: Dict) -> Dict:
    """音量步进增加（+0.1）"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    current = entity["attributes"]["volume_level"]
    entity["attributes"]["volume_level"] = min(1.0, current + 0.1)
    return entity
@update_service_time
def service_volume_down(body: Dict) -> Dict:
    """音量步进减少（-0.1）"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    current = entity["attributes"]["volume_level"]
    entity["attributes"]["volume_level"] = max(0.0, current - 0.1)
    return entity
@update_service_time
def service_volume_mute(body: Dict) -> Dict:
    """静音切换"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    entity["attributes"]["is_volume_muted"] = body["is_volume_muted"]
    return entity

# 播放控制服务实现
@update_service_time
def service_media_play(body: Dict) -> Dict:
    """开始播放"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    if entity["state"] != "playing":
        entity["state"] = "playing"
    return entity
@update_service_time
def service_media_pause(body: Dict) -> Dict:
    """暂停播放"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    if entity["state"] == "playing":
        entity["state"] = "paused"
    return entity
@update_service_time
def service_media_play_pause(body: Dict) -> Dict:
    """切换播放/暂停状态"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    if entity["state"] == "playing":
        entity["state"] = "paused"
    elif entity["state"] in ["paused", "idle"]:
        entity["state"] = "playing"
    return entity
@update_service_time
def service_media_stop(body: Dict) -> Dict:
    """停止播放"""
    entity = fake_get_states_by_entity_id(body["entity_id"])
    entity["state"] = "stopped"
    return entity

# 曲目与列表服务实现
@update_service_time
def service_media_previous(body: Dict) -> Dict:
    """上一曲"""
    METHOD_CALL_STATUS.set(key="media_previous_track",value=True)
    entity = fake_get_states_by_entity_id(body["entity_id"])
    return entity
@update_service_time
def service_media_next(body: Dict) -> Dict:
    """下一曲"""
    METHOD_CALL_STATUS.set(key="media_next_track", value=True)
    entity = fake_get_states_by_entity_id(body["entity_id"])
    return entity


@exception_return(response=bad_request)
@domain(name="notify")
def domain_notify(service: str, body: str) -> Union[Dict, str]:
    """notify域服务入口，处理小米AI音箱的智能交互型通知"""
    service_map = {
        "send_message": service_notify_send_message  # 仅支持send_message服务
    }
    body_dict = json.loads(body)
    return service_map[service](body_dict)
@update_service_time
def service_notify_send_message(body: Dict) -> Dict:
    """
    处理消息发送服务，根据不同notify实体执行对应交互功能
    """
    METHOD_CALL_STATUS.set(key="notify_message", value=body["message"])
    entity_id = body["entity_id"]
    entity = fake_get_states_by_entity_id(entity_id)
    friendly_name = entity["attributes"]["friendly_name"]

    # 更新最后触发时间（状态字段）
    entity["state"] = datetime.datetime.now().isoformat()

    return entity

"""
大模型提示词生成代码：

分析这个homeassitant中的实体及能力：

参照这个文件中的代码模拟实现这个实体的homeassitant操作效果
"""

if __name__ == "__main__":
    # {'domain': 'light', 'service': 'turn_off', 'body': {'entity_id': 'light.yeelink_cn_1162511951_mbulb3_s_2'}}
    domain='notify'
    service='send_message'
    body="""{"entity_id":"notify.xiaomi_cn_701074704_l15a_execute_text_directive_a_7_4","message":"播放 Shape of You - Ed Sheeran","data":{"silent":false}}"""
    body_dict = json.loads(body)
    result = fake_execute_domain_service_by_entity_id(domain, service, body)
    print(result)
    # from smartHome.m_agent.test.test_code.test_judge_result import judge_play_an_english_song, METHOD_CALL_STATUS

    # judge_play_an_english_song()