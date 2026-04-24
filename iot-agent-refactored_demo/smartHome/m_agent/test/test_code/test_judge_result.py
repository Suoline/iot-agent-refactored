# 全局变量：存储当前用例的模型输出
import json
import os

from smartHome.m_agent.memory.fake.fake_request import fake_get_states_by_entity_id
from smartHome.m_agent.test.test_code.utils_for_test import check_answer_matches_expected


# 设备信息
device_info = {
    # ========== 灯泡（3个） ==========
    "卧室灯泡": {
        "device_id": "164c1a92b8ce9cda0e2a8c13440b4722",
        "entity_ids": [
            "light.yeelink_cn_1162511951_mbulb3_s_2"
        ]
    },  # 灯泡1：卧室灯
    "客厅灯泡": {
        "device_id": "b75d2b03c9dfaebf1f3b9d24551c5833",
        "entity_ids": [
            "light.yeelink_cn_1162512052_mbulb3_s_2"
        ]
    },  # 灯泡2：客厅灯
    "书房灯泡": {
        "device_id": "c86e3c14d0egbfc02g4cae35662d6944",
        "entity_ids": [
            "light.yeelink_cn_1162512153_mbulb3_s_2"
        ]
    },  # 灯泡3：书房灯（注：ID中包含字母g，确认是否为笔误）

    # ========== 台灯（2个） ==========
    "卧室台灯": {
        "device_id": "31ae92d8a163d77f8d6a5741c0d1b89c",
        "entity_ids": [
            # "button.philips_cn_1061200910_lite_toggle_a_2_1",
            # "button.philips_cn_1061200910_lite_brightness_down_a_3_1",
            # "button.philips_cn_1061200910_lite_brightness_up_a_3_2",
            # "event.philips_cn_1061200910_lite_notify_you_e_3_1",
            "light.philips_cn_1061200910_lite_s_2",
            "number.philips_cn_1061200910_lite_dvalue_p_3_1",
            "number.philips_cn_1061200910_lite_notify_time_p_3_3",
            "switch.philips_cn_1061200910_lite_notify_switch_p_3_2",
            "switch.philips_cn_1061200910_lite_night_light_en_p_3_4"
        ]
    },  # 台灯1：卧室、床边
    "书房台灯": {
        "device_id": "e2bf03e9b274e88f9e7b6852d1e2c90d",
        "entity_ids": [
            "button.philips_cn_1061201010_lite_toggle_a_2_1",
            "button.philips_cn_1061201010_lite_brightness_down_a_3_1",
            "button.philips_cn_1061201010_lite_brightness_up_a_3_2",
            "event.philips_cn_1061201010_lite_notify_you_e_3_1",
            "light.philips_cn_1061201010_lite_s_2",
            "number.philips_cn_1061201010_lite_dvalue_p_3_1",
            "number.philips_cn_1061201010_lite_notify_time_p_3_3",
            "switch.philips_cn_1061201010_lite_notify_switch_p_3_2",
            "switch.philips_cn_1061201010_lite_night_light_en_p_3_4"
        ]
    },  # 台灯2：书房

    # ========== 音箱（1个） ==========
    "音箱": {
        "device_id": "21ab4b42c3e6a3fb27f93385082d4075",
        "entity_ids": [
            "button.xiaomi_cn_701074704_l15a_stop_alarm_a_6_1",
            "button.xiaomi_cn_701074704_l15a_wake_up_a_7_1",
            "button.xiaomi_cn_701074704_l15a_play_radio_a_7_2",
            "button.xiaomi_cn_701074704_l15a_play_music_a_7_5",
            "button.xiaomi_cn_701074704_l15a_tv_switchon_a_8_1",
            "media_player.xiaomi_cn_701074704_l15a",
            "notify.xiaomi_cn_701074704_l15a_seek_a_3_1",
            "notify.xiaomi_cn_701074704_l15a_play_text_a_7_3",
            "notify.xiaomi_cn_701074704_l15a_execute_text_directive_a_7_4",
            "switch.xiaomi_cn_701074704_l15a_mute_p_4_1"
        ]
    },  # 音箱：客厅

    # ========== 人体传感器（3个） ==========
    "卧室人体传感器": {
        "device_id": "53bea65f446cb0a8150250354cb28a40",
        "entity_ids": [
            "event.xiaomi_cn_blt_3_1ftnm7360c800_pir1_device_be_reset_e_2_1028",
            "event.xiaomi_cn_blt_3_1ftnm7360c800_pir1_motion_detected_e_2_1008",
            "sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_no_motion_duration_p_2_1024",
            "sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_illumination_p_2_1005",
            "sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_custom_no_motion_time_p_2_1053",
            "sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_battery_level_p_3_1003"
        ]
    },  # 人体传感器1：卧室
    "客厅人体传感器": {
        "device_id": "f4cfb76e557dc1b961361465dc39b51d",
        "entity_ids": [
            "event.xiaomi_cn_blt_3_2gunh8471d911_pir1_device_be_reset_e_2_1028",
            "event.xiaomi_cn_blt_3_2gunh8471d911_pir1_motion_detected_e_2_1008",
            "sensor.xiaomi_cn_blt_3_2gunh8471d911_pir1_no_motion_duration_p_2_1024",
            "sensor.xiaomi_cn_blt_3_2gunh8471d911_pir1_illumination_p_2_1005",
            "sensor.xiaomi_cn_blt_3_2gunh8471d911_pir1_custom_no_motion_time_p_2_1053",
            "sensor.xiaomi_cn_blt_3_2gunh8471d911_pir1_battery_level_p_3_1003"
        ]
    },  # 人体传感器2：客厅
    "书房人体传感器": {
        "device_id": "a5dfc87f668ed2c072472576ed40c62e",
        "entity_ids": [
            "event.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_device_be_reset_e_2_1028",
            "event.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_motion_detected_e_2_1008",
            "sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_no_motion_duration_p_2_1024",
            "sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_illumination_p_2_1005",
            "sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_custom_no_motion_time_p_2_1053",
            "sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_battery_level_p_3_1003"
        ]
    },  # 人体传感器3：书房

    # ========== 门窗传感器（2个） ==========
    "进家门门窗传感器": {
        "device_id": "cf03cb835279ea4876ab6ee202aa9832",
        "entity_ids": [
            "sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_illumination_p_2_1",
            "sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_battery_level_p_3_1",
            "binary_sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_contact_state_p_2_2"
        ]
    },  # 门窗传感器1：进家的门上
    "客厅窗户门窗传感器": {
        "device_id": "d914ad946380fb5987bc7ff313bb0a45",
        "entity_ids": [
            "sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_illumination_p_2_1",
            "sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_battery_level_p_3_1",
            "binary_sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_contact_state_p_2_2"
        ]
    },  # 门窗传感器2：客厅窗户

    # ========== 网关（1个） ==========
    "网关": {
        "device_id": "ac5c6e84654cf19a5f91d3d36d0ff05b",
        "entity_ids": [
            "button.lumi_cn_551385025_mcn001_identify_a_20_1",
            "event.lumi_cn_551385025_mcn001_network_changed_e_2_1",
            "event.lumi_cn_551385025_mcn001_click_e_4_1",
            "event.lumi_cn_551385025_mcn001_double_click_e_4_2",
            "event.lumi_cn_551385025_mcn001_long_press_e_4_3",
            "event.lumi_cn_551385025_mcn001_event_unbind_e_7_1",
            "number.lumi_cn_551385025_mcn001_indicator_brightness_p_6_3",
            "select.lumi_cn_551385025_mcn001_status_p_6_1",
            "select.lumi_cn_551385025_mcn001_status_p_7_1",
            "sensor.lumi_cn_551385025_mcn001_access_mode_p_2_1",
            "sensor.lumi_cn_551385025_mcn001_ip_address_p_2_2",
            "sensor.lumi_cn_551385025_mcn001_wifi_ssid_p_2_3",
            "sensor.lumi_cn_551385025_mcn001_access_mode_p_2_5",
            "text.lumi_cn_551385025_mcn001_effective_time_p_6_2"
        ]
    },  # 网关：客厅

    # ========== 插座（2个） ==========
    "加湿器插座": {
        "device_id": "df406d66e297203b9cbccd7f7b2b0376",
        "entity_ids": [
            "switch.cuco_cn_269067598_cp1_on_p_2_1"
        ]
    },  # 插座1：连着加湿器
    "风扇插座": {
        "device_id": "e0517e77f3a8314caeddef8a8c3c1487",
        "entity_ids": [
            "switch.cuco_cn_269067699_cp1_on_p_2_1"
        ]
    }  # 插座2：连着风扇
}

class MethodCallStatus:
    # 获取当前py文件所在的绝对目录
    _current_file_dir = os.path.dirname(os.path.abspath(__file__))
    # 拼接JSON文件完整路径（固定在当前py文件同级目录）
    JSON_FILE = os.path.join(_current_file_dir, "method_call_status.json")
    # 默认初始数据
    DEFAULT_STATUS = {
        "media_next_track": False,
        "media_previous_track": False,
        "notify_message": None,
        "persistent_code": None,
        "current_model_output": None
    }

    def __init__(self):
        """初始化：文件不存在则自动创建"""
        self.init_file()

    def init_file(self):
        """初始化 JSON 文件"""
        if not os.path.exists(self.JSON_FILE):
            self.reset()

    def reset(self):
        """重置为默认状态（每次测试用例执行完调用）"""
        with open(self.JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(self.DEFAULT_STATUS, f, ensure_ascii=False, indent=4)

    def set(self, key: str, value):
        """设置指定键的值（工具/主程序通用）"""
        # 读取当前数据
        with open(self.JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 修改数据
        data[key] = value
        # 保存回文件
        with open(self.JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def get(self, key: str = None):
        """获取数据：不传 key 则返回全部字典"""
        with open(self.JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(key) if key else data

# 创建全局唯一实例（全项目共用这一个对象）
METHOD_CALL_STATUS = MethodCallStatus()
# 存放方法调用状态 / 中间数据的字典.用来判断结果是否正确。
# METHOD_CALL_STATUS={
#     "media_next_track": False,
#     "media_previous_track": False,
#     "notify_message": None,
#     "persistent_code": None,
#     "current_model_output" : None
# }


def get_lamp_light_entityId(deviceTag:str):
    """
    :return: 台灯的灯实体ID
    """
    entity_id_list=device_info[deviceTag]["entity_ids"]
    for entity_id in entity_id_list:
        if entity_id.startswith("light."):
            return entity_id
    # 否则抛出异常，说明deviceTag没有light实体
    raise ValueError(f"设备标签「{deviceTag}」对应的实体ID列表中，未找到light开头的实体ID！\n"
                     f"该设备的所有entity_id：{entity_id_list}")
# 1. 网络状况
def judge_network_status():
    """
    """
    answer=METHOD_CALL_STATUS.get("current_model_output")
    expected="""
    答案中应可以列出网关状态、或者通过网关分析网络状况，当然也可以通过其他设备间接分析。
    """
    return answer is not None and check_answer_matches_expected(answer=answer,expected=expected)

# 2. 所有的灯都亮了吗？
def judge_are_all_the_lights_on():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
       所有的灯均为开启状态。答案中需表明这一信息或与此相符。
       """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 3. 关闭所有灯光。
def judge_turn_off_all_the_lights():
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state=fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "on":
            return False
    return True

# 4. 人体传感器需要换电池了吗？
def judge_does_the_human_body_sensor_need_battery_replacement():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
           所有的三个人体传感器的电量都是100%，不需要换电池。
           答案中需表明这一信息或与此相符。
    """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 5. 关掉音乐。
def judge_turn_off_the_music():
    entity_id="media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]=="stopped" or entity_state["state"]=="paused"

# 6. 将整个房子变暗
def judge_dim_the_entire_house():
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "on" and entity_state["attributes"]["brightness"]>=80:
            return False
    return True

# 7. 切换下一首歌
def judge_switch_to_the_next_song():
    # METHOD_CALL_STATUS["media_next_track"] = True
    # return METHOD_CALL_STATUS["media_next_track"]
    return METHOD_CALL_STATUS.get("media_next_track")

# 8. 音量下调2%
def judge_lower_the_volume_by_2_percent():
    entity_id="media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["volume_level"]==0.08

# 9. 打开电台
def judge_turn_on_the_radio():
    entity_id="button.xiaomi_cn_701074704_l15a_play_radio_a_7_2"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]!="unknown"

# 10. 暂停播放
def judge_pause_the_playback():
    entity_id = "media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]== "paused"

# 11. 刚刚那首歌听着不错，我想再听一遍
def judge_that_song_was_great_just_now_i_want_to_listen_to_it_again():
    # return METHOD_CALL_STATUS["media_previous_track"]
    return METHOD_CALL_STATUS.get("media_previous_track")

# 12. 放一首英文歌
def judge_play_an_english_song():
    # answer = METHOD_CALL_STATUS["notify_message"]
    answer = METHOD_CALL_STATUS.get("notify_message")
    expected = """
                   答案应该是和英文歌有关的指令内容，可以是英文歌，也可以是具体的英文歌名。
                   答案中需表明这一信息或与此相符。
            """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 13. 播放晴天，关闭卧室灯。
def judge_play_sunny_day_and_turn_off_the_bedroom_light():
    # answer = METHOD_CALL_STATUS["notify_message"]
    answer = METHOD_CALL_STATUS.get("notify_message")
    expected = """
                       答案应该是和播放歌曲【晴天】或【Sunny Day】有关的指令内容。
                       答案中需表明这一信息或与此相符。
                """
    res1 = answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

    entity_id = device_info["卧室灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    # entity_id2=get_lamp_light_entityId("卧室台灯")
    # entity_state2 = fake_get_states_by_entity_id(entity_id=entity_id2)
    res2 = entity_state["state"]=="off"
    return res1 and res2

# 14. 调高音箱音量，并把客厅灯调暗。
def judge_increase_the_speaker_volume_and_dim_the_living_room_light():
    entity_id = "media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    res1 = entity_state["attributes"]["volume_level"] > 0.1

    light_tag_list = ["卧室灯泡",  "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["attributes"]["brightness"] != 80:
            return False
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    res2 = entity_state["attributes"]["brightness"] <80
    return res1 and res2

# 15. 把书房灯都关掉，打开卧室灯。
def judge_turn_off_all_study_lights_and_turn_on_all_bedroom_lights():
    entityIds = [device_info["书房灯泡"]["entity_ids"][0],get_lamp_light_entityId("书房台灯")]
    entity_state1 = fake_get_states_by_entity_id(entity_id=entityIds[0])
    entity_state2 = fake_get_states_by_entity_id(entity_id=entityIds[1])
    res1 = entity_state1["state"]=="off" and entity_state2["state"]=="off"

    entityIds = [device_info["卧室灯泡"]["entity_ids"][0], get_lamp_light_entityId("卧室台灯")]
    entity_state1 = fake_get_states_by_entity_id(entity_id=entityIds[0])
    entity_state2 = fake_get_states_by_entity_id(entity_id=entityIds[1])
    res2 = entity_state1["state"] == "on" or entity_state2["state"] == "on"
    return res1 and res2

# 16. 客厅很暗吗？
def judge_is_the_living_room_very_dark():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
               回答可以说明客厅很暗，或者提到客厅的传感器光照强度低。
               答案中需表明这一信息或与此相符。
               - 如果只是罗列出所有的传感器，而没有提及客厅传感器，那错误
        """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 17. 客厅窗户关了吗？
def judge_is_the_living_room_window_closed():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                   客厅窗户已经关了。因为门窗传感器的接触状态显示关闭
                   答案中需表明这一信息或与此相符。
            """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 18. 把客厅灯亮度调到50%。
def judge_set_the_living_room_light_brightness_to_50_percent():
    light_tag_list = ["卧室灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["attributes"]["brightness"] != 80:
            return False
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["brightness"] == 127 or entity_state["attributes"]["brightness"] == 128

# 19. 把客厅灯调暖一点。
def judge_warm_up_the_living_room_light_a_bit():
    light_tag_list = ["卧室灯泡", "书房灯泡"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["attributes"]["color_temp_kelvin"]!= 4000:
            return False
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["color_temp_kelvin"] <4000

# 20. 空气太干燥了。
def judge_the_air_is_too_dry():
    entity_id = device_info["加湿器插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]=="on"

# 21. 有点热了。
def judge_its_a_bit_hot():
    entity_id = device_info["风扇插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"] == "on"

# 22. 床边灯太亮了，调暗到当前值的1/3。
def judge_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness():
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["attributes"]["brightness"] != 80:
            return False
    entity_id = get_lamp_light_entityId("卧室台灯")
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["brightness"] == 1

# 23. 我回家后，把门关了吗？
def judge_did_i_close_the_door_after_i_got_home():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                       提及把门关了或者门窗传感器的接触状态显示关闭
                       答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 24. 打开书房所有灯，但灯泡要暗一点。
def judge_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim():
    light_tag_list = ["卧室灯泡", "客厅灯泡"]
    lamp_tag_list = ["卧室台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "on":
            return False
    entity_id1 = get_lamp_light_entityId("书房台灯")
    entity_state1 = fake_get_states_by_entity_id(entity_id=entity_id1)
    entity_id2 = device_info["书房灯泡"]["entity_ids"][0]
    entity_state2 = fake_get_states_by_entity_id(entity_id=entity_id2)
    return entity_state1["state"] == "on" and entity_state2["state"] == "on"

# 25. 关闭客厅灯，但保持网关灯亮着。
def judge_turn_off_the_living_room_light_but_keep_the_gateway_light_on():
    light_tag_list = ["卧室灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "off":
            return False
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    res1 = entity_state["state"]== "off"
    entity_id2 = "number.lumi_cn_551385025_mcn001_indicator_brightness_p_6_3"
    entity_state2 = fake_get_states_by_entity_id(entity_id=entity_id2)
    # res2 = entity_state2["state"] <= "100" and entity_state2["state"] >= "1"
    res2 = 1 <= int(entity_state2["state"]) <= 100
    return res1 and res2

# 26. 有人在客厅走动时打开客厅灯光
def judge_turn_on_the_living_room_light_when_someone_moves_around_in_the_living_room():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                编写的代码应该检查实体(id=event.xiaomi_cn_blt_3_2gunh8471d911_pir1_motion_detected_e_2_1008)的上一次更新时间来确定是否有人在客厅走动。
                答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)


# 27. 当有人在卧室并且很暗时，打开灯。
def judge_turn_on_the_light_when_someone_is_in_the_bedroom_and_it_is_dark():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
            编写的代码应该依据实体(id=event.xiaomi_cn_blt_3_1ftnm7360c800_pir1_motion_detected_e_2_1008)的上一次移动时间和
            实体(id=sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_illumination_p_2_1005)的光照度来验证【有人在卧室并且很暗】。
            答案中需表明这一信息或与此相符。
    """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 28. 天黑时，如果窗户没关，告诉我。
def judge_remind_me_if_the_window_is_open_when_it_gets_dark():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                编写的代码应该依据实体(id=sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_illumination_p_2_1)的光照度和
                实体(id=binary_sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_contact_state_p_2_2)的接触状态来验证条件【天黑时，窗户没关】。
                答案中需表明这一信息或与此相符。
        """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 29. 如果客厅窗户打开超过 30 分钟，通知我。
def judge_notify_me_if_the_living_room_window_has_been_open_for_more_than_30_minutes():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                    编写的代码应该依据实体(id=binary_sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_contact_state_p_2_2)的接触状态和上次更新时间来验证条件【窗户打开超过 30 分钟】。
                    答案中需表明这一信息或与此相符。
            """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 30. 当床边灯打开时，关闭其他所有灯。
def judge_turn_off_all_other_lights_when_the_bedside_light_is_turned_on():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                编写的代码应该依据实体(id=light.philips_cn_1061200910_lite_s_2)的开关状态来验证条件【床边灯打开】。
                答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 31. 如果5分钟没有检测到有人走动，就关闭所有灯。
def judge_turn_off_all_lights_if_no_human_movement_is_detected_for_5_minutes():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                编写的代码应该依据实体(id=sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_no_motion_duration_p_2_1024,
                id=sensor.xiaomi_cn_blt_3_2gunh8471d911_pir1_no_motion_duration_p_2_1024,
                id=sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_no_motion_duration_p_2_1024)的无移动状态持续时间来验证条件【5分钟没有检测到有人走动】。
                答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 32. 当书房台灯打开时，关闭音乐。
def judge_turn_off_music_when_study_desk_lamp_is_turned_on():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                    编写的代码应该依据实体(id=light.philips_cn_1061201010_lite_s_2)的状态来验证条件【灯打开】。
                    答案中需表明这一信息或与此相符。
                    """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 33. 当床边灯亮度低于10%，降低卧室灯亮度，并且调暖
def judge_dim_and_warm_up_the_bedroom_light_if_the_bedside_light_brightness_is_below_10_percent():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                   编写的代码应该依据实体(id=light.philips_cn_1061200910_lite_s_2)的亮度值来验证条件【灯亮度低于10%】。
                   可以检查亮度值<26或者检查亮度百分比小于10%
                   答案中需表明这一信息或与此相符。
                   """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 34. 当音箱静音时，关闭风扇
def judge_turn_off_the_fan_when_the_speaker_is_muted():
    answer = METHOD_CALL_STATUS.get("persistent_code")
    expected = """
                       编写的代码应该依据实体(id=media_player.xiaomi_cn_701074704_l15a)的是否静音来验证条件【当音箱静音时】。
                       答案中需表明这一信息或与此相符。
                       """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 35. 太安静了，放点音乐。
def judge_its_too_quiet_play_some_music():
    # answer = METHOD_CALL_STATUS["notify_message"]
    answer = METHOD_CALL_STATUS.get("notify_message")
    expected = """
           答案应该是和周杰伦歌曲有关的指令内容。
           答案中需表明这一信息或与此相符。
    """
    res1 = answer is not None and check_answer_matches_expected(answer=answer, expected=expected)
    entity_id2 = "media_player.xiaomi_cn_701074704_l15a"
    entity_state2 = fake_get_states_by_entity_id(entity_id=entity_id2)
    res2 = entity_state2["attributes"]["volume_level"] == 0.1
    return res1 and res2

# 36. 关闭小书灯
def judge_turn_off_the_little_book_light():
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "off":
            return False
    entity_id=get_lamp_light_entityId("书房台灯")
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"] == "off"

# 37. 我要睡觉了。
def judge_im_going_to_sleep():
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "on" :
            return False
    return True

# 38. 我正在接电话，调一下音箱的音量。
def judge_im_on_a_call_now_adjust_the_speaker_volume():
    entity_id="media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["is_volume_muted"] or entity_state["attributes"]["volume_level"]==0

# 39. 准备出门。关闭所有非必要的设备。
def judge_preparing_to_go_out_turn_off_all_unnecessary_devices():
    entity_id=device_info["加湿器插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"] == "on"

# 40. 我要开始看书了，把灯调到合适模式。
def judge_im_going_to_start_reading_adjust_the_light_to_a_suitable_mode():
    entity_id = device_info["书房灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]=="off"

# 41. 将客厅灯调至我最喜欢的色温。
def judge_set_the_living_room_light_to_my_favorite_color_temperature():
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["attributes"]["color_temp_kelvin"]== 3000 or entity_state["attributes"]["color_temp"]== 333

# 42. 我回家了。
def judge_im_home():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
           用户在【我回家了】这一场景暂无偏好，只要操作符合常理即可视为正确。
    """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)


# 43. 网关如果连的不是我的网络，把所有灯关掉，然后再打开，吓吓他。
def judge_if_the_gateway_is_not_connected_to_my_network_turn_off_all_lights_and_then_turn_them_on_again_to_scare_the_intruder():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
               当前连接的 Wi‑Fi SSID 与预期不符或者判定为“网关不在你的网络"
               答案中需表明这一信息或与此相符。
        """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 44. 为家里营造万圣节气氛。
def judge_create_a_halloween_atmosphere_at_home():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                       用户在万圣节这一场景下的设备偏好是：把灯打开，极低亮度暖光。
                       答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 45. 关闭氛围组设备。
def judge_turn_off_the_atmosphere_group_devices():
    light_tag_list = ["卧室灯泡", "客厅灯泡"]
    lamp_tag_list = ["卧室台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        if entity_state["state"] == "off":
            return False

    entityIds = [device_info["书房灯泡"]["entity_ids"][0], get_lamp_light_entityId("书房台灯")]
    entity_state1 = fake_get_states_by_entity_id(entity_id=entityIds[0])
    entity_state2 = fake_get_states_by_entity_id(entity_id=entityIds[1])
    return entity_state1["state"] == "off" and entity_state2["state"] == "off"

# 46. 我要在客厅沙发上午睡一会。
def judge_im_going_to_take_a_nap_on_the_living_room_sofa():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                       用户在【客厅沙发上午睡一会】这一场景的偏好是：打开风扇，同时播放音乐。
                       答案中需表明这一信息或与此相符。
                """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 47. 有点睡不着，我打算睡前看点资料。
def judge_i_cant_fall_asleep_easily_i_plan_to_read_some_materials_before_going_to_bed():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                           用户在【睡前看点资料】这一场景的偏好是：关闭房间灯泡，并以10%亮度打开床边灯。
                           答案中需表明这一信息或与此相符。
                    """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

# 48. 关闭挂着星星装饰的灯。
def judge_turn_off_the_light_decorated_with_stars():
    entity_id = device_info["卧室灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"] == "off"

# 49. 帮我配置下网关的勿扰模式。
def judge_help_me_configure_the_gateways_do_not_disturb_mode():
    entity_id="text.lumi_cn_551385025_mcn001_effective_time_p_6_2"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    return entity_state["state"]=="22:00-07:00"
# 50. 哦，今天天气真好。
def judge_oh_the_weather_is_so_nice_today():
    answer = METHOD_CALL_STATUS.get("current_model_output")
    expected = """
                               用户在【今天天气真好】这一场景的偏好是：需要检查门窗是否打开。
                               答案中需表明这一信息或与此相符。
                        """
    return answer is not None and check_answer_matches_expected(answer=answer, expected=expected)

if __name__ == "__main__":
    # print(METHOD_CALL_STATUS)
    from smartHome.m_agent.test.test_code.test_devices_env import init_env_dim_the_entire_house
    init_env_dim_the_entire_house()
    print(judge_dim_the_entire_house())