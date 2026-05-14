"""init 函数：通过 v2 HTTP API 设置测试场景。

每个 init 函数的职责是：在 base_env 基础上，把特定实体的状态设为场景需要的样子。
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8123"


def _post_json(path: str, data: dict) -> dict:
    url = f"{BASE}{path}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _set_state(entity_id: str, state: str, **attrs):
    """通过 POST /api/states/{entity_id} 写入实体状态。"""
    return _post_json(f"/api/states/{entity_id}", {"state": state, "attributes": attrs})


def _init_env():
    """切换到 base_env。"""
    _post_json("/api/mock/init_env", {"env_id": "base_env"})


# ── 实体 ID 常量 ────────────────────────────────────────────────────────────────

BEDROOM_BULB = "light.yeelink_cn_1162511951_mbulb3_s_2"
LIVING_ROOM_BULB = "light.yeelink_cn_1162512052_mbulb3_s_2"
STUDY_BULB = "light.yeelink_cn_1162512153_mbulb3_s_2"
BEDROOM_LAMP = "light.philips_cn_1061200910_lite_s_2"
STUDY_LAMP = "light.philips_cn_1061201010_lite_s_2"

ALL_LIGHT_ENTITIES = [BEDROOM_BULB, LIVING_ROOM_BULB, STUDY_BULB, BEDROOM_LAMP, STUDY_LAMP]

SPEAKER = "media_player.xiaomi_cn_701074704_l15a"

HUMIDIFIER_SOCKET = "switch.cuco_cn_269067598_cp1_on_p_2_1"
FAN_SOCKET = "switch.cuco_cn_269067699_cp1_on_p_2_1"

BEDROOM_ILLUM = "sensor.xiaomi_cn_blt_3_1ftnm7360c800_pir1_illumination_p_2_1005"
STUDY_ILLUM = "sensor.xiaomi_cn_blt_3_3hvoj9582ea22_pir1_illumination_p_2_1005"
WINDOW_ILLUM = "sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_illumination_p_2_1"

FRONT_DOOR_CONTACT = "binary_sensor.isa_cn_blt_3_1md0u6qht0k00_dw2hl_contact_state_p_2_2"
WINDOW_CONTACT = "binary_sensor.isa_cn_blt_3_2ne1v7riu1l11_dw2hl_contact_state_p_2_2"


# ── init 函数 ───────────────────────────────────────────────────────────────────

def init_env():
    """重置到 base_env 默认状态。"""
    _init_env()


def init_env_dim_the_entire_house():
    """把所有灯亮度设为 80。"""
    _init_env()
    for eid in ALL_LIGHT_ENTITIES:
        _set_state(eid, "on", brightness=80)


def init_env_playing_the_playback():
    """音箱状态改为播放，灯亮度 80。"""
    _init_env()
    _set_state(SPEAKER, "playing", is_volume_muted=False)
    for eid in ALL_LIGHT_ENTITIES:
        _set_state(eid, "on", brightness=80)


init_env_turn_off_the_music = init_env_playing_the_playback


def init_env_the_air_is_too_dry():
    """加湿器插座设为 off。"""
    _init_env()
    _set_state(HUMIDIFIER_SOCKET, "off")


def init_env_its_a_bit_hot():
    """风扇插座设为 off。"""
    _init_env()
    _set_state(FAN_SOCKET, "off")


def init_env_is_the_living_room_very_dark():
    """关客厅灯，传感器光照度高。"""
    init_env_dim_the_entire_house()
    _set_state(LIVING_ROOM_BULB, "off")
    for eid in [BEDROOM_ILLUM, STUDY_ILLUM]:
        _set_state(eid, "100.0")
    _set_state(WINDOW_ILLUM, "强")


def init_env_is_the_living_room_window_closed():
    """进家门门窗传感器接触状态 = on（已关闭）。"""
    _init_env()
    _set_state(FRONT_DOOR_CONTACT, "on")


def init_env_did_i_close_the_door_after_i_got_home():
    """客厅窗户接触状态 = on。"""
    _init_env()
    _set_state(WINDOW_CONTACT, "on")


def init_env_set_the_living_room_light_brightness_to_50_percent():
    """所有灯亮度 80。"""
    init_env_dim_the_entire_house()


def init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness():
    """所有灯亮度 80，但卧室台灯亮度 3。"""
    init_env_dim_the_entire_house()
    _set_state(BEDROOM_LAMP, "on", brightness=3)


def init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim():
    """所有灯状态 off。"""
    _init_env()
    for eid in ALL_LIGHT_ENTITIES:
        _set_state(eid, "off")


def init_env_judge_its_too_quiet_play_some_music():
    """音箱音量 0.2。"""
    _init_env()
    _set_state(SPEAKER, "idle", volume_level=0.2)


def init_env_turn_off_the_little_book_light():
    """所有灯 on。"""
    _init_env()
    for eid in ALL_LIGHT_ENTITIES:
        _set_state(eid, "on")


def init_env_preparing_to_go_out_turn_off_all_unnecessary_devices():
    """加湿器插座 on。"""
    _init_env()
    _set_state(HUMIDIFIER_SOCKET, "on")


def init_env_im_on_a_call_now_adjust_the_speaker_volume():
    """= init_env_playing_the_playback。"""
    init_env_playing_the_playback()
