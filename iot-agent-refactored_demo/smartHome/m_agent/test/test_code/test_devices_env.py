from smartHome.m_agent.memory.fake.fake_request import fake_get_states_by_entity_id, HOMEASSITANT_DATA
from smartHome.m_agent.test.test_code.test_judge_result import device_info, get_lamp_light_entityId


def init_env():
    """测试环境初始化函数"""
    HOMEASSITANT_DATA.init_entities()

def init_env_turn_off_the_music():
    init_env_playing_the_playback()

# 39. 准备出门。关闭所有非必要的设备。
def init_env_preparing_to_go_out_turn_off_all_unnecessary_devices():
    HOMEASSITANT_DATA.init_entities()
    entity_id = device_info["加湿器插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"]="on"

def init_env_playing_the_playback():
    """
    将音箱状态改为播放
    :return:
    """
    HOMEASSITANT_DATA.init_entities()
    entity_id = "media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "playing"
    entity_state["attributes"]["is_volume_muted"] = False

    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["attributes"]["brightness"] = 80

def init_env_the_air_is_too_dry():
    HOMEASSITANT_DATA.init_entities()
    entity_id = device_info["加湿器插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "off"

def init_env_its_a_bit_hot():
    HOMEASSITANT_DATA.init_entities()
    entity_id = device_info["风扇插座"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "off"

def init_env_is_the_living_room_very_dark():
    """
    把客厅灯关闭，其他传感器置为光照强
    :return:
    """
    # HOMEASSITANT_DATA.init_entities()
    # 把灯的亮度值都设为80
    init_env_dim_the_entire_house()
    entity_id = device_info["客厅灯泡"]["entity_ids"][0]
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "off"
    # 人体传感器
    entity_ids=["sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_illumination_p_2_1005","sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_illumination_p_2_1005"]
    for entity_id in entity_ids:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["state"] = "100.0"
    # 门窗传感器
    entity_id="sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_illumination_p_2_1"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "强"
def init_env_is_the_living_room_window_closed():
    HOMEASSITANT_DATA.init_entities()

    # 家门上的门窗传感器
    entity_id = "binary_sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_contact_state_p_2_2"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "on"

def init_env_judge_its_too_quiet_play_some_music():
    HOMEASSITANT_DATA.init_entities()
    entity_id = "media_player.xiaomi_cn_701074704_l15a"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["attributes"]["volume_level"] = 0.2

def init_env_turn_off_the_little_book_light():
    HOMEASSITANT_DATA.init_entities()
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["state"] = "on"
def init_env_set_the_living_room_light_brightness_to_50_percent():
    HOMEASSITANT_DATA.init_entities()
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["attributes"]["brightness"] = 80
def init_env_dim_the_entire_house():
    HOMEASSITANT_DATA.init_entities()
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["attributes"]["brightness"]=80
def init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness():
    init_env_dim_the_entire_house()
    entity_id = get_lamp_light_entityId("卧室台灯")
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["attributes"]["brightness"] = 3
def init_env_did_i_close_the_door_after_i_got_home():
    """
    把窗户的门窗传感器设为on
    :return:
    """
    HOMEASSITANT_DATA.init_entities()
    entity_id="binary_sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_contact_state_p_2_2"
    entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
    entity_state["state"] = "on"

def init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim():
    HOMEASSITANT_DATA.init_entities()
    light_tag_list = ["卧室灯泡", "客厅灯泡", "书房灯泡"]
    lamp_tag_list = ["卧室台灯", "书房台灯"]
    entityId_list = []
    for light_tag in light_tag_list:
        entityId_list.append(device_info[light_tag]["entity_ids"][0])
    for lamp_tag in lamp_tag_list:
        entityId_list.append(get_lamp_light_entityId(lamp_tag))

    for entity_id in entityId_list:
        entity_state = fake_get_states_by_entity_id(entity_id=entity_id)
        entity_state["state"]= "off"
def init_env_im_on_a_call_now_adjust_the_speaker_volume():
    init_env_playing_the_playback()