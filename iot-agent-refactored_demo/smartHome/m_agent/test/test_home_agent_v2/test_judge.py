"""judge 函数：通过 v2 HTTP API 检查实体状态 + LLM 评估 agent 输出。

每个 judge 函数接收 agent 的输出字符串，返回 bool。
- Pattern A: GET /api/states/{entity_id} 直接检查状态
- Pattern B: 原需要 METHOD_CALL_STATUS 标记 → 改为 LLM 评估
- Pattern C: LLM 评估 agent 输出文本
"""

from __future__ import annotations

import json
import os
import sys
from urllib.request import Request, urlopen

# 复用 test_code 中的 LLM 裁判
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "test_code"))
from utils_for_test import check_answer_matches_expected

BASE = "http://127.0.0.1:8123"


def _get_json(path: str) -> dict | list:
    url = f"{BASE}{path}"
    req = Request(url, headers={"Content-Type": "application/json"}, method="GET")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_state(entity_id: str) -> dict:
    return _get_json(f"/api/states/{entity_id}")


def _entity_is(entity_id: str, state: str | None = None, **attrs) -> bool:
    """检查实体当前状态/属性是否匹配。"""
    try:
        es = _get_state(entity_id)
    except Exception:
        return False
    if state is not None and es["state"] != state:
        return False
    for k, v in attrs.items():
        if es.get("attributes", {}).get(k) != v:
            return False
    return True


# ── 实体 ID ─────────────────────────────────────────────────────────────────────

BEDROOM_BULB = "light.yeelink_cn_1162511951_mbulb3_s_2"
LIVING_ROOM_BULB = "light.yeelink_cn_1162512052_mbulb3_s_2"
STUDY_BULB = "light.yeelink_cn_1162512153_mbulb3_s_2"
BEDROOM_LAMP = "light.philips_cn_1061200910_lite_s_2"
STUDY_LAMP = "light.philips_cn_1061201010_lite_s_2"

ALL_LIGHTS = [BEDROOM_BULB, LIVING_ROOM_BULB, STUDY_BULB, BEDROOM_LAMP, STUDY_LAMP]

BULB_ENTITIES = [BEDROOM_BULB, LIVING_ROOM_BULB, STUDY_BULB]
LAMP_ENTITIES = [BEDROOM_LAMP, STUDY_LAMP]

SPEAKER = "media_player.xiaomi_cn_701074704_l15a"
RADIO_BUTTON = "button.xiaomi_cn_701074704_l15a_play_radio_a_7_2"

HUMIDIFIER = "switch.cuco_cn_269067598_cp1_on_p_2_1"
FAN = "switch.cuco_cn_269067699_cp1_on_p_2_1"

# --- 其他灯组合 ---
OTHER_THAN_LIVING_ROOM = [BEDROOM_BULB, STUDY_BULB, BEDROOM_LAMP, STUDY_LAMP]
OTHER_THAN_STUDY = [BEDROOM_BULB, LIVING_ROOM_BULB, BEDROOM_LAMP]
OTHER_THAN_BEDSIDE = [BEDROOM_BULB, LIVING_ROOM_BULB, STUDY_BULB, STUDY_LAMP]
STUDY_LIGHTS = [STUDY_BULB, STUDY_LAMP]
BEDROOM_LIGHTS = [BEDROOM_BULB, BEDROOM_LAMP]
OTHER_THAN_STUDY_2 = [BEDROOM_BULB, LIVING_ROOM_BULB, BEDROOM_LAMP]


# ── 辅助 ────────────────────────────────────────────────────────────────────────

def _all_lights_off() -> bool:
    for eid in ALL_LIGHTS:
        try:
            if _get_state(eid)["state"] == "on":
                return False
        except Exception:
            pass
    return True


def _check_others_unchanged(
    unchanged: list[str],
    state_check: str | None = None,
    attr_check: tuple[str, object] | None = None,
) -> bool:
    for eid in unchanged:
        try:
            es = _get_state(eid)
        except Exception:
            return False
        if state_check is not None and es["state"] != state_check:
            return False
        if attr_check is not None:
            k, v = attr_check
            if es.get("attributes", {}).get(k) != v:
                return False
    return True


# ── 50 个 judge 函数 ────────────────────────────────────────────────────────────


# 1. 网络状况 (LLM)
def judge_network_status(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="可列出网关状态或通过网关分析网络状况，也可通过其他设备间接分析。",
    )


# 2. 所有灯都亮了吗？(LLM)
def judge_are_all_the_lights_on(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="所有灯均为开启状态。答案中需表明这一信息或与此相符。",
    )


# 3. 关闭所有灯光 (entity state)
def judge_turn_off_all_the_lights(output: str) -> bool:
    return _all_lights_off()


# 4. 人体传感器需要换电池了吗？(LLM)
def judge_does_the_human_body_sensor_need_battery_replacement(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="三个传感器电量100%，不需要换电池。",
    )


# 5. 关掉音乐 (entity state)
def judge_turn_off_the_music(output: str) -> bool:
    try:
        s = _get_state(SPEAKER)["state"]
        return s in ("stopped", "paused")
    except Exception:
        return False


# 6. 将整个房子变暗 (entity state)
def judge_dim_the_entire_house(output: str) -> bool:
    for eid in ALL_LIGHTS:
        try:
            es = _get_state(eid)
            if es["state"] == "on" and es.get("attributes", {}).get("brightness", 0) >= 80:
                return False
        except Exception:
            return False
    return True


# 7. 切换下一首歌 (原 METHOD_CALL_STATUS → LLM)
def judge_switch_to_the_next_song(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="执行了切换下一首的操作。",
    )


# 8. 音量下调2% (entity state)
def judge_lower_the_volume_by_2_percent(output: str) -> bool:
    try:
        vol = _get_state(SPEAKER)["attributes"]["volume_level"]
        return vol == 0.08
    except Exception:
        return False


# 9. 打开电台 (entity state)
def judge_turn_on_the_radio(output: str) -> bool:
    try:
        return _get_state(RADIO_BUTTON)["state"] != "unknown"
    except Exception:
        return False


# 10. 暂停播放 (entity state)
def judge_pause_the_playback(output: str) -> bool:
    try:
        return _get_state(SPEAKER)["state"] == "paused"
    except Exception:
        return False


# 11. 再听一遍 (原 METHOD_CALL_STATUS → LLM)
def judge_that_song_was_great_just_now_i_want_to_listen_to_it_again(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="执行了播放上一首或重新播放的操作。",
    )


# 12. 放一首英文歌 (原 METHOD_CALL_STATUS → LLM)
def judge_play_an_english_song(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="与播放英文歌相关的指令。",
    )


# 13. 播放晴天 + 关卧室灯 (LLM + entity)
def judge_play_sunny_day_and_turn_off_the_bedroom_light(output: str) -> bool:
    res1 = bool(output) and check_answer_matches_expected(
        answer=output,
        expected="与播放歌曲【晴天】或【Sunny Day】相关的指令。",
    )
    try:
        res2 = _get_state(BEDROOM_BULB)["state"] == "off"
    except Exception:
        res2 = False
    return res1 and res2


# 14. 调高音量 + 客厅灯调暗 (entity state)
def judge_increase_the_speaker_volume_and_dim_the_living_room_light(output: str) -> bool:
    try:
        vol = _get_state(SPEAKER)["attributes"]["volume_level"]
        res1 = vol > 0.1
    except Exception:
        return False
    # 其他灯亮度应保持 80 不变
    if not _check_others_unchanged(OTHER_THAN_LIVING_ROOM, attr_check=("brightness", 80)):
        return False
    try:
        res2 = _get_state(LIVING_ROOM_BULB)["attributes"]["brightness"] < 80
    except Exception:
        return False
    return res1 and res2


# 15. 关书房灯 + 开卧室灯 (entity state)
def judge_turn_off_all_study_lights_and_turn_on_all_bedroom_lights(output: str) -> bool:
    try:
        res1 = all(_get_state(eid)["state"] == "off" for eid in STUDY_LIGHTS)
        res2 = any(_get_state(eid)["state"] == "on" for eid in BEDROOM_LIGHTS)
    except Exception:
        return False
    return res1 and res2


# 16. 客厅很暗吗？(LLM)
def judge_is_the_living_room_very_dark(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="说明客厅很暗，或提到客厅传感器光照强度低。不能只罗列所有传感器。",
    )


# 17. 客厅窗户关了吗？(LLM)
def judge_is_the_living_room_window_closed(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="客厅窗户已关，因为门窗传感器接触状态显示关闭。",
    )


# 18. 客厅灯亮度50% (entity state)
def judge_set_the_living_room_light_brightness_to_50_percent(output: str) -> bool:
    if not _check_others_unchanged(OTHER_THAN_LIVING_ROOM, attr_check=("brightness", 80)):
        return False
    try:
        b = _get_state(LIVING_ROOM_BULB)["attributes"]["brightness"]
        return b in (127, 128)
    except Exception:
        return False


# 19. 客厅灯调暖 (entity state)
def judge_warm_up_the_living_room_light_a_bit(output: str) -> bool:
    unchanged = [BEDROOM_BULB, STUDY_BULB]
    if not _check_others_unchanged(unchanged, attr_check=("color_temp_kelvin", 4000)):
        return False
    try:
        return _get_state(LIVING_ROOM_BULB)["attributes"]["color_temp_kelvin"] < 4000
    except Exception:
        return False


# 20. 空气太干燥 (entity state)
def judge_the_air_is_too_dry(output: str) -> bool:
    try:
        return _get_state(HUMIDIFIER)["state"] == "on"
    except Exception:
        return False


# 21. 有点热了 (entity state)
def judge_its_a_bit_hot(output: str) -> bool:
    try:
        return _get_state(FAN)["state"] == "on"
    except Exception:
        return False


# 22. 床边灯1/3亮度 (entity state)
def judge_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness(output: str) -> bool:
    if not _check_others_unchanged(OTHER_THAN_BEDSIDE, attr_check=("brightness", 80)):
        return False
    try:
        return _get_state(BEDROOM_LAMP)["attributes"]["brightness"] == 1
    except Exception:
        return False


# 23. 门关了吗？(LLM)
def judge_did_i_close_the_door_after_i_got_home(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="提及门窗传感器接触状态显示关闭。",
    )


# 24. 开书房所有灯，灯泡暗 (entity state)
def judge_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim(output: str) -> bool:
    unchanged = [BEDROOM_BULB, LIVING_ROOM_BULB, BEDROOM_LAMP]
    if not _check_others_unchanged(unchanged, state_check="off"):
        return False
    try:
        return _get_state(STUDY_LAMP)["state"] == "on" and _get_state(STUDY_BULB)["state"] == "on"
    except Exception:
        return False


# 25. 关客厅灯，网关灯亮 (entity state)
def judge_turn_off_the_living_room_light_but_keep_the_gateway_light_on(output: str) -> bool:
    unchanged = [BEDROOM_BULB, STUDY_BULB, BEDROOM_LAMP, STUDY_LAMP]
    for eid in unchanged:
        try:
            if _get_state(eid)["state"] == "off":
                return False
        except Exception:
            return False
    try:
        res1 = _get_state(LIVING_ROOM_BULB)["state"] == "off"
        gw = _get_state("number.lumi_cn_551385025_mcn001_indicator_brightness_p_6_3")
        res2 = 1 <= int(gw["state"]) <= 100
    except Exception:
        return False
    return res1 and res2


# ── 26-34: 持久化用例（跳过） ────────────────────────────────────────────────────

def _persistent_skip(output: str) -> bool:
    return False  # is_tested=False，不会实际调用


judge_turn_on_the_living_room_light_when_someone_moves_around_in_the_living_room = _persistent_skip
judge_turn_on_the_light_when_someone_is_in_the_bedroom_and_it_is_dark = _persistent_skip
judge_remind_me_if_the_window_is_open_when_it_gets_dark = _persistent_skip
judge_notify_me_if_the_living_room_window_has_been_open_for_more_than_30_minutes = _persistent_skip
judge_turn_off_all_other_lights_when_the_bedside_light_is_turned_on = _persistent_skip
judge_turn_off_all_lights_if_no_human_movement_is_detected_for_5_minutes = _persistent_skip
judge_turn_off_music_when_study_desk_lamp_is_turned_on = _persistent_skip
judge_dim_and_warm_up_the_bedroom_light_if_the_bedside_light_brightness_is_below_10_percent = _persistent_skip
judge_turn_off_the_fan_when_the_speaker_is_muted = _persistent_skip


# ── 35. 太安静了放音乐 (LLM + entity) ────────────────────────────────────────────

def judge_its_too_quiet_play_some_music(output: str) -> bool:
    res1 = bool(output) and check_answer_matches_expected(
        answer=output,
        expected="与周杰伦歌曲相关的指令。",
    )
    try:
        res2 = _get_state(SPEAKER)["attributes"]["volume_level"] == 0.1
    except Exception:
        res2 = False
    return res1 and res2


# 36. 关闭小书灯 (entity state)
def judge_turn_off_the_little_book_light(output: str) -> bool:
    unchanged = [BEDROOM_BULB, LIVING_ROOM_BULB, STUDY_BULB, BEDROOM_LAMP]
    for eid in unchanged:
        try:
            if _get_state(eid)["state"] == "off":
                return False
        except Exception:
            return False
    try:
        return _get_state(STUDY_LAMP)["state"] == "off"
    except Exception:
        return False


# 37. 我要睡觉了 (entity state)
def judge_im_going_to_sleep(output: str) -> bool:
    return _all_lights_off()


# 38. 接电话调音量 (entity state)
def judge_im_on_a_call_now_adjust_the_speaker_volume(output: str) -> bool:
    try:
        attrs = _get_state(SPEAKER)["attributes"]
        return attrs.get("is_volume_muted") or attrs.get("volume_level") == 0
    except Exception:
        return False


# 39. 准备出门关不必要设备 (entity state)
def judge_preparing_to_go_out_turn_off_all_unnecessary_devices(output: str) -> bool:
    try:
        return _get_state(HUMIDIFIER)["state"] == "on"
    except Exception:
        return False


# 40. 看书模式 (entity state)
def judge_im_going_to_start_reading_adjust_the_light_to_a_suitable_mode(output: str) -> bool:
    try:
        return _get_state(STUDY_BULB)["state"] == "off"
    except Exception:
        return False


# 41. 客厅灯最喜欢色温 (entity state)
def judge_set_the_living_room_light_to_my_favorite_color_temperature(output: str) -> bool:
    try:
        attrs = _get_state(LIVING_ROOM_BULB)["attributes"]
        return attrs.get("color_temp_kelvin") == 3000 or attrs.get("color_temp") == 333
    except Exception:
        return False


# 42. 我回家了 (LLM)
def judge_im_home(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="无特定偏好，操作符合常理即可。",
    )


# 43. 网关异网吓人 (LLM)
def judge_if_the_gateway_is_not_connected_to_my_network_turn_off_all_lights_and_then_turn_them_on_again_to_scare_the_intruder(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="判定为网关不在你的网络或 Wi-Fi SSID 不符。",
    )


# 44. 万圣节 (LLM)
def judge_create_a_halloween_atmosphere_at_home(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="把灯打开，极低亮度暖光。",
    )


# 45. 关闭氛围组设备 (entity state)
def judge_turn_off_the_atmosphere_group_devices(output: str) -> bool:
    # 卧室灯、客厅灯、卧室台灯 应保持 on
    for eid in [BEDROOM_BULB, LIVING_ROOM_BULB, BEDROOM_LAMP]:
        try:
            if _get_state(eid)["state"] == "off":
                return False
        except Exception:
            return False
    # 书房灯泡 + 书房台灯 应 off
    try:
        return (_get_state(STUDY_BULB)["state"] == "off" and
                _get_state(STUDY_LAMP)["state"] == "off")
    except Exception:
        return False


# 46. 客厅沙发午睡 (LLM)
def judge_im_going_to_take_a_nap_on_the_living_room_sofa(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="打开风扇，同时播放音乐。",
    )


# 47. 睡前看资料 (LLM)
def judge_i_cant_fall_asleep_easily_i_plan_to_read_some_materials_before_going_to_bed(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="关闭房间灯泡，以10%亮度打开床边灯。",
    )


# 48. 关星星灯 (entity state)
def judge_turn_off_the_light_decorated_with_stars(output: str) -> bool:
    try:
        return _get_state(BEDROOM_BULB)["state"] == "off"
    except Exception:
        return False


# 49. 网关勿扰模式 (entity state)
def judge_help_me_configure_the_gateways_do_not_disturb_mode(output: str) -> bool:
    try:
        return _get_state("text.lumi_cn_551385025_mcn001_effective_time_p_6_2")["state"] == "22:00-07:00"
    except Exception:
        return False


# 50. 天气真好 (LLM)
def judge_oh_the_weather_is_so_nice_today(output: str) -> bool:
    return bool(output) and check_answer_matches_expected(
        answer=output,
        expected="需要检查门窗是否打开。",
    )
