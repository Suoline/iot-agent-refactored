"""50 用例测试入口 — 基于 v2 HTTP 服务端 + base_home_agent。

用法：
    cd iot-agent-refactored_demo
    conda run -n mySmart_env python smartHome/m_agent/test/test_home_agent_v2/test_runner.py

前提：v2 服务端已在 127.0.0.1:18123 运行（base_env）。
      如需自动启动服务端，见本目录 test_integration.py 中的 fake_ha_server fixture。

结果保存在 results/ourAgent_v2/{model}/1_smart_home_test_results.json，
支持中断续跑（已跑完的用例 is_tested=True，下次跳过）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

# 确保 smartHome 在路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from smartHome.m_agent.agent.base_home_agent import run_ourAgent
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from smartHome.m_agent.common.logger import setup_dynamic_indent_logger

from test_env_setup import (
    init_env,
    init_env_dim_the_entire_house,
    init_env_playing_the_playback,
    init_env_turn_off_the_music,
    init_env_the_air_is_too_dry,
    init_env_its_a_bit_hot,
    init_env_is_the_living_room_very_dark,
    init_env_is_the_living_room_window_closed,
    init_env_set_the_living_room_light_brightness_to_50_percent,
    init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness,
    init_env_did_i_close_the_door_after_i_got_home,
    init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim,
    init_env_im_on_a_call_now_adjust_the_speaker_volume,
    init_env_judge_its_too_quiet_play_some_music,
    init_env_turn_off_the_little_book_light,
    init_env_preparing_to_go_out_turn_off_all_unnecessary_devices,
)

from test_judge import (
    judge_network_status,
    judge_are_all_the_lights_on,
    judge_turn_off_all_the_lights,
    judge_does_the_human_body_sensor_need_battery_replacement,
    judge_turn_off_the_music,
    judge_dim_the_entire_house,
    judge_switch_to_the_next_song,
    judge_lower_the_volume_by_2_percent,
    judge_turn_on_the_radio,
    judge_pause_the_playback,
    judge_that_song_was_great_just_now_i_want_to_listen_to_it_again,
    judge_play_an_english_song,
    judge_play_sunny_day_and_turn_off_the_bedroom_light,
    judge_increase_the_speaker_volume_and_dim_the_living_room_light,
    judge_turn_off_all_study_lights_and_turn_on_all_bedroom_lights,
    judge_is_the_living_room_very_dark,
    judge_is_the_living_room_window_closed,
    judge_set_the_living_room_light_brightness_to_50_percent,
    judge_warm_up_the_living_room_light_a_bit,
    judge_the_air_is_too_dry,
    judge_its_a_bit_hot,
    judge_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness,
    judge_did_i_close_the_door_after_i_got_home,
    judge_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim,
    judge_turn_off_the_living_room_light_but_keep_the_gateway_light_on,
    # 26-34 持久化（跳过）
    judge_turn_on_the_living_room_light_when_someone_moves_around_in_the_living_room,
    judge_turn_on_the_light_when_someone_is_in_the_bedroom_and_it_is_dark,
    judge_remind_me_if_the_window_is_open_when_it_gets_dark,
    judge_notify_me_if_the_living_room_window_has_been_open_for_more_than_30_minutes,
    judge_turn_off_all_other_lights_when_the_bedside_light_is_turned_on,
    judge_turn_off_all_lights_if_no_human_movement_is_detected_for_5_minutes,
    judge_turn_off_music_when_study_desk_lamp_is_turned_on,
    judge_dim_and_warm_up_the_bedroom_light_if_the_bedside_light_brightness_is_below_10_percent,
    judge_turn_off_the_fan_when_the_speaker_is_muted,
    # 35-50
    judge_its_too_quiet_play_some_music,
    judge_turn_off_the_little_book_light,
    judge_im_going_to_sleep,
    judge_im_on_a_call_now_adjust_the_speaker_volume,
    judge_preparing_to_go_out_turn_off_all_unnecessary_devices,
    judge_im_going_to_start_reading_adjust_the_light_to_a_suitable_mode,
    judge_set_the_living_room_light_to_my_favorite_color_temperature,
    judge_im_home,
    judge_if_the_gateway_is_not_connected_to_my_network_turn_off_all_lights_and_then_turn_them_on_again_to_scare_the_intruder,
    judge_create_a_halloween_atmosphere_at_home,
    judge_turn_off_the_atmosphere_group_devices,
    judge_im_going_to_take_a_nap_on_the_living_room_sofa,
    judge_i_cant_fall_asleep_easily_i_plan_to_read_some_materials_before_going_to_bed,
    judge_turn_off_the_light_decorated_with_stars,
    judge_help_me_configure_the_gateways_do_not_disturb_mode,
    judge_oh_the_weather_is_so_nice_today,
)


# ── 类型 ────────────────────────────────────────────────────────────────────────

TestCase = tuple[Callable[[], None], str, Callable[[str], bool], bool]

# ── 50 用例 ──────────────────────────────────────────────────────────────────────

SMART_HOME_TEST_DATASET: list[TestCase] = [
    (init_env, "Network status", judge_network_status, False),  # 1
    (init_env, "Are all the lights on?", judge_are_all_the_lights_on, False),  # 2
    (init_env, "Turn off all the lights.", judge_turn_off_all_the_lights, False),  # 3
    (init_env, "Does the human body sensor need battery replacement?", judge_does_the_human_body_sensor_need_battery_replacement, False),  # 4
    (init_env_turn_off_the_music, "Turn off the music.", judge_turn_off_the_music, False),  # 5
    (init_env_dim_the_entire_house, "Dim the entire house.", judge_dim_the_entire_house, False),  # 6
    (init_env, "Switch to the next song.", judge_switch_to_the_next_song, False),  # 7
    (init_env, "Lower the volume by 2%.", judge_lower_the_volume_by_2_percent, False),  # 8
    (init_env, "Turn on the radio.", judge_turn_on_the_radio, False),  # 9
    (init_env_playing_the_playback, "Pause the playback.", judge_pause_the_playback, False),  # 10
    (init_env_playing_the_playback, "That song was great just now; I want to listen to it again.", judge_that_song_was_great_just_now_i_want_to_listen_to_it_again, False),  # 11
    (init_env, "Play an English song.", judge_play_an_english_song, False),  # 12
    (init_env, "Play 'Sunny Day' and turn off the bedroom light.", judge_play_sunny_day_and_turn_off_the_bedroom_light, False),  # 13
    (init_env_playing_the_playback, "Increase the speaker volume and dim the living room light.", judge_increase_the_speaker_volume_and_dim_the_living_room_light, False),  # 14
    (init_env, "Turn off all study lights and turn on all bedroom lights.", judge_turn_off_all_study_lights_and_turn_on_all_bedroom_lights, False),  # 15
    (init_env_is_the_living_room_very_dark, "Is the living room very dark?", judge_is_the_living_room_very_dark, False),  # 16
    (init_env_is_the_living_room_window_closed, "Is the living room window closed?", judge_is_the_living_room_window_closed, False),  # 17
    (init_env_set_the_living_room_light_brightness_to_50_percent, "Set the living room light brightness to 50%.", judge_set_the_living_room_light_brightness_to_50_percent, False),  # 18
    (init_env, "Warm up the living room light a bit.", judge_warm_up_the_living_room_light_a_bit, False),  # 19
    (init_env_the_air_is_too_dry, "The air is too dry.", judge_the_air_is_too_dry, False),  # 20
    (init_env_its_a_bit_hot, "It's a bit hot.", judge_its_a_bit_hot, False),  # 21
    (init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness, "The bedside light is too bright; dim it to one third of the current brightness.", judge_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness, False),  # 22
    (init_env_did_i_close_the_door_after_i_got_home, "Did I close the door after I got home?", judge_did_i_close_the_door_after_i_got_home, False),  # 23
    (init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim, "Turn on all the lights in the study, but keep the bulbs dim.", judge_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim, False),  # 24
    (init_env, "Turn off the living room light, but keep the gateway light on.", judge_turn_off_the_living_room_light_but_keep_the_gateway_light_on, False),  # 25
    # ── 26-34: 持久化用例（当前跳过） ──
    (init_env, "Set up persistence: Turn on the living room light when someone moves around in the living room.", judge_turn_on_the_living_room_light_when_someone_moves_around_in_the_living_room, False),  # 26
    (init_env, "Set up persistence: Turn on the light when someone is in the bedroom and it is dark.", judge_turn_on_the_light_when_someone_is_in_the_bedroom_and_it_is_dark, False),  # 27
    (init_env, "Set up persistence: Remind me if the window is open when it gets dark.", judge_remind_me_if_the_window_is_open_when_it_gets_dark, False),  # 28
    (init_env, "Set up persistence: Notify me if the living room window has been open for more than 30 minutes.", judge_notify_me_if_the_living_room_window_has_been_open_for_more_than_30_minutes, False),  # 29
    (init_env, "Set up persistence: Turn off all other lights when the bedside light is turned on.", judge_turn_off_all_other_lights_when_the_bedside_light_is_turned_on, False),  # 30
    (init_env, "Set up persistence: Turn off all lights if no human movement is detected for 5 minutes.", judge_turn_off_all_lights_if_no_human_movement_is_detected_for_5_minutes, False),  # 31
    (init_env, "Set up persistence: Turn off the music When the desk lamp in the study is turned on", judge_turn_off_music_when_study_desk_lamp_is_turned_on, False),  # 32
    (init_env, "Set up persistence: Dim and warm up the bedroom light if the bedside light brightness is below 10%.", judge_dim_and_warm_up_the_bedroom_light_if_the_bedside_light_brightness_is_below_10_percent, False),  # 33
    (init_env, "Set up persistence: Turn off the fan when the speaker is muted.", judge_turn_off_the_fan_when_the_speaker_is_muted, False),  # 34
    # ── 35-50 ──
    (init_env_judge_its_too_quiet_play_some_music, "It's too quiet; play some music.", judge_its_too_quiet_play_some_music, False),  # 35
    (init_env_turn_off_the_little_book_light, "Turn off the Little Book Light.", judge_turn_off_the_little_book_light, False),  # 36
    (init_env, "I'm going to sleep.", judge_im_going_to_sleep, False),  # 37
    (init_env_im_on_a_call_now_adjust_the_speaker_volume, "I'm on a call now; adjust the speaker volume.", judge_im_on_a_call_now_adjust_the_speaker_volume, False),  # 38
    (init_env_preparing_to_go_out_turn_off_all_unnecessary_devices, "Preparing to go out; turn off all unnecessary devices.", judge_preparing_to_go_out_turn_off_all_unnecessary_devices, False),  # 39
    (init_env, "I'm going to start reading; adjust the light to a suitable mode.", judge_im_going_to_start_reading_adjust_the_light_to_a_suitable_mode, False),  # 40
    (init_env, "Set the living room light to my favorite color temperature.", judge_set_the_living_room_light_to_my_favorite_color_temperature, False),  # 41
    (init_env, "I'm home.", judge_im_home, False),  # 42
    (init_env, "If the gateway is not connected to my network, turn off all lights and then turn them on again to scare the intruder.", judge_if_the_gateway_is_not_connected_to_my_network_turn_off_all_lights_and_then_turn_them_on_again_to_scare_the_intruder, False),  # 43
    (init_env, "Create a Halloween atmosphere at home.", judge_create_a_halloween_atmosphere_at_home, False),  # 44
    (init_env, "Turn off the atmosphere group devices.", judge_turn_off_the_atmosphere_group_devices, False),  # 45
    (init_env, "I'm going to take a nap on the living room sofa.", judge_im_going_to_take_a_nap_on_the_living_room_sofa, False),  # 46
    (init_env, "I can't fall asleep easily; I plan to read some materials before going to bed.", judge_i_cant_fall_asleep_easily_i_plan_to_read_some_materials_before_going_to_bed, False),  # 47
    (init_env, "Turn off the light decorated with stars.", judge_turn_off_the_light_decorated_with_stars, False),  # 48
    (init_env, "Help me configure the gateway's do-not-disturb mode.", judge_help_me_configure_the_gateways_do_not_disturb_mode, False),  # 49
    (init_env, "Oh, the weather is so nice today.", judge_oh_the_weather_is_so_nice_today, False),  # 50
]


# ── 结果管理 ─────────────────────────────────────────────────────────────────────

RESULT_DIR = os.path.join(
    os.path.dirname(__file__), "results", "ourAgent_v2", GLOBALCONFIG.model
)
RESULT_PATH = os.path.join(RESULT_DIR, "1_smart_home_test_results.json")


def load_history_results(save_path: str) -> tuple[dict[int, dict], list[TestCase]]:
    history = {}
    dataset = SMART_HOME_TEST_DATASET.copy()
    if not os.path.exists(save_path):
        return history, dataset

    try:
        with open(save_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for res in data.get("详细结果", []):
                if not res:
                    continue
                cid = res.get("用例编号")
                if cid is None:
                    continue
                history[cid] = res
                if 1 <= cid <= len(dataset):
                    init_f, test_s, judge_f, _ = dataset[cid - 1]
                    dataset[cid - 1] = (init_f, test_s, judge_f, True)
        print(f"已加载 {len(history)} 个历史结果")
    except Exception as e:
        print(f"加载历史结果失败: {e}")
    return history, dataset


def save_test_results(results: list[dict], accuracy: float, save_path: str) -> None:
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    final = {
        "统计信息": {
            "总用例数": len(results),
            "通过数": sum(1 for r in results if r.get("是否正确", False)),
            "正确率": f"{accuracy:.2%}",
        },
        "详细结果": results,
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {save_path}")


# ── 主流程 ───────────────────────────────────────────────────────────────────────

def init_global_env(idx: int, question: str):
    cleaned = re.sub(r"[^\w一-龥]", "", question)
    filename = f"{idx}_{cleaned}.log"
    GLOBALCONFIG.nested_logger = setup_dynamic_indent_logger(
        logger_name=f"agent_test_v2_{idx}_{GLOBALCONFIG.model}",
        log_file_path=f"logs/test_results/ourAgent_v2/{GLOBALCONFIG.model}/1/{filename}",
    )
    os.environ["LANGSMITH_PROJECT"] = f"shuaSmartHomeTest_ourAgent_v2_{GLOBALCONFIG.model}"


def run_all():
    history, dataset = load_history_results(RESULT_PATH)
    total = len(dataset)
    results = [{} for _ in range(total)]
    for cid, res in history.items():
        if 1 <= cid <= total:
            results[cid - 1] = res

    pass_count = sum(1 for r in results if r.get("是否正确", False))

    print(f"\n{'='*60}")
    print(f"智能家居 v2 测试 — 50 用例")
    print(f"模型: {GLOBALCONFIG.model}")
    print(f"{'='*60}\n")

    for i, (init_func, test_str, judge_func, is_tested) in enumerate(dataset, 1):
        init_global_env(i, test_str)

        if is_tested:
            display = test_str[:60] + "..." if len(test_str) > 60 else test_str
            status = "✓" if results[i - 1].get("是否正确") else "✗"
            print(f"用例{i:02d}: {display} → 已测试 {status}")
            continue

        print(f"用例{i:02d}: {test_str[:80]}", end=" ", flush=True)

        try:
            init_func()
            output = run_ourAgent(test_str)
            is_correct = judge_func(output)
        except Exception as e:
            output = f"执行异常: {e}"
            is_correct = False
            import traceback
            traceback.print_exc()

        results[i - 1] = {
            "用例编号": i,
            "测试用例字符串": test_str,
            "模型输出": output,
            "是否正确": is_correct,
        }
        if is_correct:
            pass_count += 1

        print("✓" if is_correct else "✗")
        accuracy = pass_count / total
        save_test_results(results, accuracy, RESULT_PATH)

    print(f"\n{'='*60}")
    print(f"完成: {pass_count}/{total} 通过 ({pass_count / total:.1%})")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_all()
