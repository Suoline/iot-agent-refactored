import json
import os
import re
from typing import Callable, List, Dict, Any, Tuple

from smartHome.m_agent.agent.base_home_agent import run_ourAgent
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from smartHome.m_agent.common.logger import setup_dynamic_indent_logger
from smartHome.m_agent.test.baselines_homeassitant.sage.sage_coordinator import run_sageAgent
from smartHome.m_agent.test.baselines_homeassitant.sashaAgent import run_sashaAgent
from smartHome.m_agent.test.test_code.test_devices_env import \
    init_env_preparing_to_go_out_turn_off_all_unnecessary_devices, init_env, \
    init_env_playing_the_playback, init_env_the_air_is_too_dry, init_env_its_a_bit_hot, \
    init_env_is_the_living_room_very_dark, init_env_judge_its_too_quiet_play_some_music, \
    init_env_turn_off_the_little_book_light, init_env_dim_the_entire_house, \
    init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness, \
    init_env_did_i_close_the_door_after_i_got_home, init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim, \
    init_env_im_on_a_call_now_adjust_the_speaker_volume, init_env_turn_off_the_music, \
    init_env_is_the_living_room_window_closed, init_env_set_the_living_room_light_brightness_to_50_percent
from test_judge_result import *  # 导入所有函数和全局变量

# ====================== 测试参数 ======================
AGENT_NAME:str=None # "smart_home_test_results.json"
TSET_NO_FOR_MODEL:int=None # 某个模型的测试次数序号，比如1,2,3
RESULT_SAVE_PATH:str=None
# ====================== 核心类型定义（调整judge函数类型） ======================
# 测试用例结构：(初始化函数, 测试用例字符串, 无参结果评判函数, 是否已测试)
TestCase = tuple[Callable[[], None], str, Callable[[], bool], bool]
TestResult = dict[str, Any]

# ====================== 50个测试用例列表（无修改，仅judge为无参） ======================
SMART_HOME_TEST_DATASET: List[TestCase] = [
    (init_env, "Network status", judge_network_status, False),  # 1. 网络状况
    (init_env, "Are all the lights on?", judge_are_all_the_lights_on, False),  # 2. 所有的灯都亮了吗？
    (init_env, "Turn off all the lights.", judge_turn_off_all_the_lights, False),  # 3. 关闭所有灯光。
    (init_env, "Does the human body sensor need battery replacement?", judge_does_the_human_body_sensor_need_battery_replacement, False),  # 4. 人体传感器需要换电池了吗？
    (init_env_turn_off_the_music, "Turn off the music.", judge_turn_off_the_music, False),  # 5. 关掉音乐。
    (init_env_dim_the_entire_house, "Dim the entire house.", judge_dim_the_entire_house, False),  # 6. 将整个房子变暗
    (init_env, "Switch to the next song.", judge_switch_to_the_next_song, False),  # 7. 切换下一首歌
    (init_env, "Lower the volume by 2%.", judge_lower_the_volume_by_2_percent, False),  # 8. 音量下调2%
    (init_env, "Turn on the radio.", judge_turn_on_the_radio, False),  # 9. 打开电台
    (init_env_playing_the_playback, "Pause the playback.", judge_pause_the_playback, False),  # 10. 暂停播放
    (init_env_playing_the_playback, "That song was great just now; I want to listen to it again.", judge_that_song_was_great_just_now_i_want_to_listen_to_it_again, False),  # 11. 刚刚那首歌听着不错
    (init_env, "Play an English song.", judge_play_an_english_song, False),  # 12. 放一首英文歌
    (init_env, "Play 'Sunny Day' and turn off the bedroom light.", judge_play_sunny_day_and_turn_off_the_bedroom_light, False),  # 13. 播放晴天+关卧室灯
    (init_env_playing_the_playback, "Increase the speaker volume and dim the living room light.", judge_increase_the_speaker_volume_and_dim_the_living_room_light, False),  # 14. 调音量+调暗客厅灯
    (init_env, "Turn off all study lights and turn on all bedroom lights.", judge_turn_off_all_study_lights_and_turn_on_all_bedroom_lights, False),  # 15. 关书房灯+开卧室灯
    (init_env_is_the_living_room_very_dark, "Is the living room very dark?", judge_is_the_living_room_very_dark, False),  # 16. 客厅很暗吗？
    (init_env_is_the_living_room_window_closed, "Is the living room window closed?", judge_is_the_living_room_window_closed, False),  # 17. 客厅窗户关了吗？
    (init_env_set_the_living_room_light_brightness_to_50_percent, "Set the living room light brightness to 50%.", judge_set_the_living_room_light_brightness_to_50_percent, False),  # 18. 客厅灯调50%亮度
    (init_env, "Warm up the living room light a bit.", judge_warm_up_the_living_room_light_a_bit, False),  # 19. 客厅灯调暖
    (init_env_the_air_is_too_dry, "The air is too dry.", judge_the_air_is_too_dry, False),  # 20. 空气太干燥
    (init_env_its_a_bit_hot, "It's a bit hot.", judge_its_a_bit_hot, False),  # 21. 有点热了
    (init_env_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness, "The bedside light is too bright; dim it to one third of the current brightness.", judge_the_bedside_light_is_too_bright_dim_it_to_one_third_of_the_current_brightness, False),  # 22. 床边灯调1/3亮度
    (init_env_did_i_close_the_door_after_i_got_home, "Did I close the door after I got home?", judge_did_i_close_the_door_after_i_got_home, False),  # 23. 我回家后，把门关了吗？
    (init_env_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim, "Turn on all the lights in the study, but keep the bulbs dim.", judge_turn_on_all_the_lights_in_the_study_but_keep_the_bulbs_dim, False),  # 24. 开书房灯但调暗
    (init_env, "Turn off the living room light, but keep the gateway light on.", judge_turn_off_the_living_room_light_but_keep_the_gateway_light_on, False),  # 25. 关客厅灯留网关灯
    (init_env, "Set up persistence: Turn on the living room light when someone moves around in the living room.", judge_turn_on_the_living_room_light_when_someone_moves_around_in_the_living_room, False),  # 26. 有人在客厅走动时打开客厅灯光
    (init_env, "Set up persistence: Turn on the light when someone is in the bedroom and it is dark.", judge_turn_on_the_light_when_someone_is_in_the_bedroom_and_it_is_dark, False),  # 27. 当有人在卧室并且很暗时，打开灯。
    (init_env, "Set up persistence: Remind me if the window is open when it gets dark.", judge_remind_me_if_the_window_is_open_when_it_gets_dark, False),  # 28. 天黑时，如果窗户没关，告诉我。
    (init_env, "Set up persistence: Notify me if the living room window has been open for more than 30 minutes.", judge_notify_me_if_the_living_room_window_has_been_open_for_more_than_30_minutes, False),  # 29. 窗开超30分钟通知
    (init_env, "Set up persistence: Turn off all other lights when the bedside light is turned on.", judge_turn_off_all_other_lights_when_the_bedside_light_is_turned_on, False),  # 30. 床边灯开则关其他灯
    (init_env, "Set up persistence: Turn off all lights if no human movement is detected for 5 minutes.", judge_turn_off_all_lights_if_no_human_movement_is_detected_for_5_minutes, False),  # 31. 5分钟无人动关灯
    (init_env, "Set up persistence: Turn off the music When the desk lamp in the study is turned on", judge_turn_off_music_when_study_desk_lamp_is_turned_on, False),  # 32. 当书房台灯打开时，关闭音乐。
    (init_env, "Set up persistence: Dim and warm up the bedroom light if the bedside light brightness is below 10%.", judge_dim_and_warm_up_the_bedroom_light_if_the_bedside_light_brightness_is_below_10_percent, False),  # 33. 床边灯<10%调卧室灯
    (init_env, "Set up persistence: Turn off the fan when the speaker is muted.", judge_turn_off_the_fan_when_the_speaker_is_muted, False),  # 34. 音箱静音关风扇
    (init_env_judge_its_too_quiet_play_some_music, "It's too quiet; play some music.", judge_its_too_quiet_play_some_music, False),  # 35. 太安静了，放点音乐。
    (init_env_turn_off_the_little_book_light, "Turn off the Little Book Light.", judge_turn_off_the_little_book_light, False),  # 36. 关闭小书灯。
    (init_env, "I'm going to sleep.", judge_im_going_to_sleep, False),  # 37. 我要睡觉了。
    (init_env_im_on_a_call_now_adjust_the_speaker_volume, "I'm on a call now; adjust the speaker volume.", judge_im_on_a_call_now_adjust_the_speaker_volume, False),  # 38. 我正在接电话，调一下音箱的音量。
    (init_env_preparing_to_go_out_turn_off_all_unnecessary_devices, "Preparing to go out; turn off all unnecessary devices.", judge_preparing_to_go_out_turn_off_all_unnecessary_devices, False),  # 39. 准备出门。关闭所有非必要的设备。
    (init_env, "I'm going to start reading; adjust the light to a suitable mode.", judge_im_going_to_start_reading_adjust_the_light_to_a_suitable_mode, False),  # 40. 我要开始看书了，把灯调到合适模式。
    (init_env, "Set the living room light to my favorite color temperature.", judge_set_the_living_room_light_to_my_favorite_color_temperature, False),  # 41. 将客厅灯调至我最喜欢的色温。
    (init_env, "I'm home.", judge_im_home, False),  # 42. 我回家了。
    (init_env, "If the gateway is not connected to my network, turn off all lights and then turn them on again to scare the intruder.", judge_if_the_gateway_is_not_connected_to_my_network_turn_off_all_lights_and_then_turn_them_on_again_to_scare_the_intruder, False),  # 43. 网关异网开关灯
    (init_env, "Create a Halloween atmosphere at home.", judge_create_a_halloween_atmosphere_at_home, False),  # 44. 为家里营造万圣节气氛。
    (init_env, "Turn off the atmosphere group devices.", judge_turn_off_the_atmosphere_group_devices, False),  # 45. 关闭氛围组设备。
    (init_env, "I'm going to take a nap on the living room sofa.", judge_im_going_to_take_a_nap_on_the_living_room_sofa, False),  # 46. 我要在客厅沙发上午睡一会。
    (init_env, "I can't fall asleep easily; I plan to read some materials before going to bed.", judge_i_cant_fall_asleep_easily_i_plan_to_read_some_materials_before_going_to_bed, False),  # 47. 有点睡不着，我打算睡前看点资料。
    (init_env, "Turn off the light decorated with stars.", judge_turn_off_the_light_decorated_with_stars, False),  # 48. 关闭挂着星星装饰的灯。
    (init_env, "Help me configure the gateway's do-not-disturb mode.", judge_help_me_configure_the_gateways_do_not_disturb_mode, False),  # 49. 帮我配置下网关的勿扰模式。
    (init_env, "Oh, the weather is so nice today.", judge_oh_the_weather_is_so_nice_today, False)  # 50. 哦，今天天气真好。
]

# ====================== 3. 加载历史测试结果（移除测试时间相关） ======================
def load_history_results(save_path: str) -> Tuple[Dict[int, TestResult], List[TestCase]]:
    """加载历史测试结果，更新测试用例的is_tested状态"""
    history_results = {}
    updated_dataset = SMART_HOME_TEST_DATASET.copy()

    if not os.path.exists(save_path):
        return history_results, updated_dataset

    try:
        with open(save_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for res in data["详细结果"]:
                if not res:
                    continue
                # get(键, 默认值)：键不存在时返回 None，不报错
                case_id = res.get("用例编号")
                if case_id is None:  # 跳过没有用例编号的无效数据
                    continue
                # case_id = res["用例编号"]
                history_results[case_id] = res

                if 1 <= case_id <= len(updated_dataset):
                    init_func, test_str, judge_func, _ = updated_dataset[case_id - 1]
                    updated_dataset[case_id - 1] = (init_func, test_str, judge_func, True)

        print(f"✅ 成功加载历史测试结果，共{len(history_results)}个已测试用例")
    except Exception as e:
        print(f"⚠️ 加载历史结果失败：{e}，将重新执行所有用例")

    return history_results, updated_dataset


# ====================== 4. 自动化测试核心程序（关键调整） ======================
def init_global_env(index:str,question:str):
    # 处理文件名：移除特殊字符，确保文件名合法 ; 保留中文、字母、数字和下划线，其他字符替换为下划线
    cleaned_name = re.sub(r'[^\w\u4e00-\u9fa5]', '', question)
    # 生成文件名：序号_清洗后的内容（如"0_将整个房子变暗"、"1_网络状况"）
    filename = f"{index}_{cleaned_name}.log"
    # 日志配置
    GLOBALCONFIG.nested_logger = setup_dynamic_indent_logger(logger_name=f"agent_test_{index}_logs_{AGENT_NAME}_{GLOBALCONFIG.model}_{TSET_NO_FOR_MODEL}",
                                                             log_file_path=f"logs/test_results/{AGENT_NAME}/{GLOBALCONFIG.model}/{TSET_NO_FOR_MODEL}/{filename}")

    # todo 真正测试前开启
    os.environ["LANGSMITH_PROJECT"] = f"shuaSmartHomeTest_{AGENT_NAME}_{GLOBALCONFIG.model}"


def run_automated_test(
        func: Callable[[str], Any],
        test_dataset: List[TestCase],
        history_results: Dict[int, TestResult],
        save_path: str  # 函数已包含save_path，直接用
) -> Tuple[float, List[TestResult]]:
    """执行自动化测试（跳过已测试用例，无测试时间，无参judge）【每用例实时保存】"""
    test_results = [{} for _ in range(len(test_dataset))]
    for case_id, res in history_results.items():
        if 1 <= case_id <= len(test_results):
            test_results[case_id - 1] = res

    pass_count = 0
    total_count = len(test_dataset)
    executed_count = 0
    print("\n===== 开始执行50个智能家居测试用例 =====")
    for idx, (init_func, test_str, judge_func, is_tested) in enumerate(test_dataset, 1):
        print("===== 初始化全局测试环境 =====")
        init_global_env(index=idx,question=test_str)
        METHOD_CALL_STATUS.reset()

        try:
            if is_tested:
                res = history_results.get(idx, {})
                if res and res.get("是否正确"):
                    pass_count += 1
                display_str = test_str[:60] + "..." if len(test_str) > 60 else test_str
                print(f"用例{idx}: {display_str} → 已测试，跳过执行")
                # --------------------------
                # 跳过用例：实时保存结果
                # --------------------------
                current_total_pass = sum(1 for r in test_results if r.get("是否正确", False))
                current_accuracy = current_total_pass / total_count if total_count > 0 else 0.0
                save_test_results(test_results, current_accuracy, save_path)
                continue

            executed_count += 1
            init_func()

            # 调用模型并将输出存入全局变量
            current_model_output = func(test_str)
            METHOD_CALL_STATUS.set(key="current_model_output",value=current_model_output)
            is_correct = judge_func()

            # 记录结果
            current_result = {
                "用例编号": idx,
                "测试用例字符串": test_str,
                "模型输出": current_model_output,
                "是否正确": is_correct
            }
            test_results[idx - 1] = current_result

            if is_correct:
                pass_count += 1

            display_str = test_str[:60] + "..." if len(test_str) > 60 else test_str
            print(f"用例{idx}: {display_str} → {'正确' if is_correct else '错误'}")

        except Exception as e:
            # 异常结果记录
            current_result = {
                "用例编号": idx,
                "测试用例字符串": test_str,
                "模型输出": f"执行异常: {str(e)}",
                "是否正确": False
            }
            test_results[idx - 1] = current_result
            print(f"用例{idx}: 执行异常 → {str(e)}")

        # --------------------------
        # ✅ 核心修改：每运行完1个用例，立即保存
        # --------------------------
        current_total_pass = sum(1 for r in test_results if r.get("是否正确", False))
        current_accuracy = current_total_pass / total_count if total_count > 0 else 0.0
        save_test_results(test_results, current_accuracy, save_path)

    # 过滤空结果，计算最终结果
    test_results = [res for res in test_results if res]
    total_pass = sum(1 for res in test_results if res.get("是否正确", False))
    accuracy = total_pass / total_count if total_count > 0 else 0.0

    print(f"\n===== 测试完成 =====")
    print(f"总用例数: {total_count} | 已测试数: {len(test_results)} | 本次新增执行: {executed_count}")
    print(f"通过数: {total_pass} | 正确率: {accuracy:.2%}")

    return accuracy, test_results


# ====================== 5. 结果存储函数（移除最后测试时间） ======================
def save_test_results(results: List[TestResult], accuracy: float, save_path: str) -> None:
    """存储测试结果（无测试时间字段）"""
    final_data = {
        "统计信息": {
            "总用例数": len(results),
            "通过数": sum(1 for res in results if res.get("是否正确", False)),
            "正确率": f"{accuracy:.2%}"
            # 移除「最后测试时间」字段
        },
        "详细结果": results
    }

    # ===================== 新增核心代码：自动创建目录 =====================
    # 获取文件所在的文件夹路径
    dir_path = os.path.dirname(save_path)
    # 如果文件夹不存在，则创建（exist_ok=True 表示目录已存在时不报错）
    os.makedirs(dir_path, exist_ok=True)
    # ====================================================================

    # 现在目录已存在，可以正常写入文件
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)

    print(f"\n💾 测试结果已保存至: {save_path}")


# ====================== 6. 模拟模型函数 ======================
def smart_home_model(agent_name: str):
    """
    根据智能体名称（agent_name），返回对应的智能体入口函数对象
    返回:
        function - 对应智能体的入口函数对象
    异常:
        ValueError - 当传入的agent_name未匹配到任何智能体时抛出
    """
    # 定义智能体名称与「函数对象」的映射关系
    agent_mapping = {
        "ourAgent": run_ourAgent,
        "sage": run_sageAgent,
        "sasha": run_sashaAgent,
    }

    # 查找匹配的函数，未匹配则抛出异常（核心修改）
    target_agent = agent_mapping.get(agent_name)
    if target_agent is None:
        raise ValueError(f"未识别的智能体名称：{agent_name}，可选值：{list(agent_mapping.keys())}")
    return target_agent


# ====================== 主程序入口 ======================
if __name__ == "__main__":
    # 修改同步到langsmith上的project名，名字应为{baseline}_{model}
    # todo 测试前 修改参数 / ！！模型名也记得确定
    AGENT_NAME: str = "ourAgent"  # "smart_home_test_results.json"
    TSET_NO_FOR_MODEL: int = 2  # 某个模型的测试次数序号，比如1,2,3
    RESULT_SAVE_PATH: str = f"results/{AGENT_NAME}/{GLOBALCONFIG.model}/{TSET_NO_FOR_MODEL}_smart_home_test_results.json"

    history_results, updated_dataset = load_history_results(save_path=RESULT_SAVE_PATH)
    accuracy, test_results = run_automated_test(
        smart_home_model(agent_name=AGENT_NAME),
        updated_dataset,
        history_results,
        save_path=RESULT_SAVE_PATH
    )
    save_test_results(test_results, accuracy,save_path=RESULT_SAVE_PATH)
    # todo 可以把全局信息放到查询-思考部分。
    # √ 对持久化监控的测试用例前加入字符串，持久监控/自动化规则：
    # √ 把日志的index+1去掉，不过得等这次测试完了，懒得改了。