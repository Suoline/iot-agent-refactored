"""SVRBench 数据集生成：1 个开发家庭（Dev-20）+ 5 个测试家庭（Test-100）。

5 主类别 × 每家庭 4 任务：DC 设备控制 / CC 命令链 / IR 信息检索 /
PT 持久化 / PR 个性化。每个任务冻结：init 环境参数、任务文本、敏感 span
金标准、final_state 断言、expected_answer、forbidden 与 PT/PR 附加判定。

金标准独立于检测器：由家庭定义在任务构造时直接枚举（方案 §4.4.3）。
dev 与 test 家庭的实体名、人名、房间名、任务文本不重叠（§4.1）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 家庭定义
# ---------------------------------------------------------------------------


@dataclass
class FamilySpec:
    home_id: str            # h0..h5
    split: str              # dev / test
    person_name: str        # 人名（person 敏感项）
    rooms: dict[str, str]   # 逻辑房间 -> 中文名
    ac_temp: float          # 空调初始设定
    living_temp: float      # 客厅（起居室）温度
    bedroom_temp: float
    humidity: int
    target_temp: float      # PR 偏好温度 / DC 目标温度
    volume: int             # CC 音量目标
    curtain_pos: int        # DC 窗帘开度
    pt_temp_high: float     # PT 触发阈值（高温）
    pt_temp_low: float      # PT 触发阈值（低温）
    pt_humidity_low: int


def _room_key(home: FamilySpec) -> dict[str, str]:
    """家庭房间实体后缀（dev 用不同房间词实现名称不重叠）。"""
    return {
        "living": {"dev": "livingroom", "test": "living"}[home.split],
        "bedroom": "bedroom",
        "study": {"dev": "atticstudy", "test": "study"}[home.split],
    }


FAMILIES: list[FamilySpec] = [
    FamilySpec("h0", "dev", "李伟", {"living": "起居室", "bedroom": "主卧", "study": "阁楼书房"},
               24.0, 27.5, 26.0, 42, 25.0, 25, 60, 29.0, 18.0, 38),
    FamilySpec("h1", "test", "王芳", {"living": "客厅", "bedroom": "卧室", "study": "书房"},
               24.0, 28.0, 27.0, 45, 26.0, 20, 50, 30.0, 18.0, 40),
    FamilySpec("h2", "test", "张敏", {"living": "客厅", "bedroom": "卧室", "study": "书房"},
               25.0, 29.0, 26.5, 50, 25.0, 30, 40, 30.0, 19.0, 42),
    FamilySpec("h3", "test", "陈静", {"living": "客厅", "bedroom": "卧室", "study": "书房"},
               23.0, 27.0, 28.0, 38, 26.0, 15, 70, 28.0, 17.0, 35),
    FamilySpec("h4", "test", "刘洋", {"living": "客厅", "bedroom": "卧室", "study": "书房"},
               26.0, 30.0, 27.5, 55, 27.0, 25, 45, 31.0, 18.0, 45),
    FamilySpec("h5", "test", "赵磊", {"living": "客厅", "bedroom": "卧室", "study": "书房"},
               24.0, 26.5, 28.5, 47, 25.0, 35, 55, 29.0, 16.0, 41),
]


def entities_of(home: FamilySpec) -> dict[str, str]:
    """家庭实体清单：逻辑名 -> entity_id。"""
    r = _room_key(home)
    h = home.home_id
    return {
        "living_main_light": f"light.{h}_{r['living']}_main_light",
        "living_side_light": f"light.{h}_{r['living']}_side_light",
        "bedroom_main_light": f"light.{h}_bedroom_main_light",
        "bedside_lamp": f"light.{h}_bedroom_bedside_lamp",
        "study_light": f"light.{h}_{r['study']}_light",
        "living_ac": f"climate.{h}_{r['living']}_ac",
        "bedroom_ac": f"climate.{h}_bedroom_ac",
        "living_temp": f"sensor.{h}_{r['living']}_temp",
        "bedroom_temp": f"sensor.{h}_bedroom_temp",
        "humidity": f"sensor.{h}_humidity",
        "speaker": f"media_player.{h}_speaker",
        "living_curtain": f"cover.{h}_{r['living']}_curtain",
        "bedroom_curtain": f"cover.{h}_bedroom_curtain",
        "humidifier": f"switch.{h}_humidifier",
        "front_door": f"binary_sensor.{h}_front_door",
    }


def device_of(home: FamilySpec, entity_key: str) -> str:
    """实体所属 device_id（环境 YAML 与记忆绑定使用）。"""
    h = home.home_id
    return f"device_{h}_{entity_key}"


# ---------------------------------------------------------------------------
# 环境 YAML 生成
# ---------------------------------------------------------------------------


def _entity_def(home: FamilySpec, key: str, domain: str, name: str, state: str,
                attributes: dict, area: str) -> dict:
    entity_id = entities_of(home)[key]
    return {
        "entity_id": entity_id,
        "domain": domain,
        "object_id": entity_id.split(".", 1)[1],
        "device_id": device_of(home, key),
        "area_id": f"room.{area}",
        "platform": "mock",
        "name": name,
        "state": state,
        "attributes": attributes,
    }


def build_env_yaml(home: FamilySpec) -> dict:
    r = _room_key(home)
    e = entities_of(home)
    rooms = home.rooms
    h = home.home_id

    def light(key: str, name: str, area: str, state: str, brightness: int | None = None):
        attrs: dict[str, Any] = {"friendly_name": name}
        if brightness is not None:
            attrs["brightness"] = brightness
        return _entity_def(home, key, "light", name, state, attrs, area)

    entities = [
        light("living_main_light", f"{rooms['living']}主灯", "living", "off", None),
        light("living_side_light", f"{rooms['living']}落地灯", "living", "on", 150),
        light("bedroom_main_light", f"{rooms['bedroom']}主灯", "bedroom", "on", 200),
        light("bedside_lamp", f"{rooms['bedroom']}床头灯", "bedroom", "off", None),
        light("study_light", f"{rooms['study']}台灯", "study", "off", None),
        _entity_def(home, "living_ac", "climate", f"{rooms['living']}空调", "cool",
                    {"friendly_name": f"{rooms['living']}空调", "hvac_mode": "cool",
                     "hvac_modes": ["off", "cool", "heat"], "temperature": home.ac_temp,
                     "current_temperature": home.living_temp, "unit_of_measurement": "°C"},
                    "living"),
        _entity_def(home, "bedroom_ac", "climate", f"{rooms['bedroom']}空调", "cool",
                    {"friendly_name": f"{rooms['bedroom']}空调", "hvac_mode": "cool",
                     "hvac_modes": ["off", "cool", "heat"], "temperature": home.ac_temp,
                     "current_temperature": home.bedroom_temp, "unit_of_measurement": "°C"},
                    "bedroom"),
        _entity_def(home, "living_temp", "sensor", f"{rooms['living']}温度传感器", str(home.living_temp),
                    {"friendly_name": f"{rooms['living']}温度传感器",
                     "device_class": "temperature", "unit_of_measurement": "°C"}, "living"),
        _entity_def(home, "bedroom_temp", "sensor", f"{rooms['bedroom']}温度传感器", str(home.bedroom_temp),
                    {"friendly_name": f"{rooms['bedroom']}温度传感器",
                     "device_class": "temperature", "unit_of_measurement": "°C"}, "bedroom"),
        _entity_def(home, "humidity", "sensor", "湿度传感器", str(home.humidity),
                    {"friendly_name": "湿度传感器", "device_class": "humidity",
                     "unit_of_measurement": "%"}, "living"),
        _entity_def(home, "speaker", "media_player", "智能音箱", "playing",
                    {"friendly_name": "智能音箱", "volume_level": 0.4,
                     "is_volume_muted": False, "supported_features": ["play", "pause", "volume"]},
                    "living"),
        _entity_def(home, "living_curtain", "cover", f"{rooms['living']}窗帘", "open",
                    {"friendly_name": f"{rooms['living']}窗帘", "current_position": 100}, "living"),
        _entity_def(home, "bedroom_curtain", "cover", f"{rooms['bedroom']}窗帘", "open",
                    {"friendly_name": f"{rooms['bedroom']}窗帘", "current_position": 100}, "bedroom"),
        _entity_def(home, "humidifier", "switch", "加湿器", "on",
                    {"friendly_name": "加湿器"}, "living"),
        _entity_def(home, "front_door", "binary_sensor", "前门门窗传感器", "on",
                    {"friendly_name": "前门门窗传感器", "device_class": "door"}, "hall"),
    ]

    devices = []
    for entity in entities:
        devices.append({
            "device_id": entity["device_id"],
            "name": entity["name"],
            "name_by_user": entity["name"],
            "area_id": entity["area_id"],
            "manufacturer": "SVRBench",
            "model": "V1",
            "entities": [entity["entity_id"]],
        })

    initial_states = [
        {"entity_id": e["living_main_light"], "state": "off"},
        {"entity_id": e["living_side_light"], "state": "on", "attributes": {"brightness": 150}},
        {"entity_id": e["bedroom_main_light"], "state": "on", "attributes": {"brightness": 200}},
        {"entity_id": e["bedside_lamp"], "state": "off"},
        {"entity_id": e["study_light"], "state": "off"},
        {"entity_id": e["living_ac"], "state": "cool",
         "attributes": {"hvac_mode": "cool", "temperature": home.ac_temp,
                        "current_temperature": home.living_temp}},
        {"entity_id": e["bedroom_ac"], "state": "cool",
         "attributes": {"hvac_mode": "cool", "temperature": home.ac_temp,
                        "current_temperature": home.bedroom_temp}},
        {"entity_id": e["living_temp"], "state": str(home.living_temp)},
        {"entity_id": e["bedroom_temp"], "state": str(home.bedroom_temp)},
        {"entity_id": e["humidity"], "state": str(home.humidity)},
        {"entity_id": e["speaker"], "state": "playing",
         "attributes": {"volume_level": 0.4}},
        {"entity_id": e["living_curtain"], "state": "open", "attributes": {"current_position": 100}},
        {"entity_id": e["bedroom_curtain"], "state": "open", "attributes": {"current_position": 100}},
        {"entity_id": e["humidifier"], "state": "on"},
        {"entity_id": e["front_door"], "state": "on"},
    ]

    link_rules = [
        {"source_domain": "climate", "source_service": "set_temperature",
         "target_domain": "sensor", "match": "same_area_id",
         "target_device_class": "temperature", "propagate": "payload:temperature"},
    ]
    return {
        "env_id": f"svrbench_{h}_v1",
        "default_fault_mode": "normal",
        "supported_fault_modes": ["normal"],
        "devices": devices,
        "entities": entities,
        "initial_states": initial_states,
        "link_rules": link_rules,
        "fault_profiles": {},
    }


# ---------------------------------------------------------------------------
# 敏感 span 金标准
# ---------------------------------------------------------------------------


def sensitive_spans_for(home: FamilySpec, *, include_answer_values: list[str] | None = None) -> list[dict]:
    """任务级金标准：家庭全部实体 ID、设备名、人名、房间名、关键数值。

    与检测器完全独立：从家庭定义直接枚举（§4.4.3）。
    filter 阶段会拉取全部实体状态，因此全部实体 ID 与 friendly_name 都会
    进入 pre-transform payload，计入 G_r。
    """
    spans: list[dict] = []
    e = entities_of(home)
    for key, entity_id in e.items():
        spans.append({"value": entity_id, "field_type": "entity_id"})
    devices = {d["name"] for d in build_env_yaml(home)["devices"]}
    for name in sorted(devices):
        spans.append({"value": name, "field_type": "device_name"})
    spans.append({"value": home.person_name, "field_type": "person"})
    for room_cn in home.rooms.values():
        spans.append({"value": room_cn, "field_type": "room"})
    for value in include_answer_values or []:
        spans.append({"value": value, "field_type": "state_value"})
    return spans


# ---------------------------------------------------------------------------
# 任务模板（每家庭 20 个，渲染家庭参数）
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def build_tasks(home: FamilySpec) -> list[dict[str, Any]]:
    r = _room_key(home)
    e = entities_of(home)
    rooms = home.rooms
    person = home.person_name
    tasks: list[dict[str, Any]] = []

    def add(task_id: str, category: str, difficulty: str, instruction: str,
            assert_final: list[dict] | None = None,
            assert_unchanged: list[str] | None = None,
            answer_contains: list[str] | None = None,
            answer_numeric: dict | None = None,
            answer_state: dict | None = None,
            pt_spec: dict | None = None,
            pr_seed: list[dict] | None = None,
            answer_value_spans: list[str] | None = None):
        tasks.append({
            "task_id": f"{home.home_id}_{task_id}",
            "home_id": home.home_id,
            "split": home.split,
            "category": category,
            "difficulty": difficulty,
            "instruction": instruction,
            "assert_final": assert_final or [],
            "assert_unchanged": assert_unchanged or [],
            "answer_contains": answer_contains or [],
            "answer_numeric": answer_numeric,
            "answer_state": answer_state,
            "pt_spec": pt_spec,
            "pr_seed": pr_seed,
            "sensitive_spans": sensitive_spans_for(home, include_answer_values=answer_value_spans),
        })

    living, bedroom, study = rooms["living"], rooms["bedroom"], rooms["study"]

    # ---- DC 设备控制（4）----
    add("dc1", "DC", "easy", f"打开{living}的主灯。",
        assert_final=[{"entity_id": e["living_main_light"], "state": "on"}])
    add("dc2", "DC", "medium", f"把{bedroom}空调的温度调到{_fmt(home.target_temp)}度。",
        assert_final=[{"entity_id": e["bedroom_ac"],
                       "attributes": {"temperature": home.target_temp}}])
    add("dc3", "DC", "easy", "把加湿器关掉。", assert_final=[{"entity_id": e["humidifier"], "state": "off"}])
    add("dc4", "DC", "medium", f"把{living}窗帘打开到{home.curtain_pos}%。",
        assert_final=[{"entity_id": e["living_curtain"],
                       "attributes": {"current_position": home.curtain_pos}}])

    # ---- CC 命令链（4）----
    add("cc1", "CC", "hard", "把家里所有的灯都关掉，然后暂停音箱的播放。",
        assert_final=[
            {"entity_id": e["living_main_light"], "state": "off"},
            {"entity_id": e["living_side_light"], "state": "off"},
            {"entity_id": e["bedroom_main_light"], "state": "off"},
            {"entity_id": e["bedside_lamp"], "state": "off"},
            {"entity_id": e["study_light"], "state": "off"},
            {"entity_id": e["speaker"], "state": "paused"},
        ])
    add("cc2", "CC", "medium", f"打开{study}台灯，同时把音箱音量调到{home.volume}%。",
        assert_final=[
            {"entity_id": e["study_light"], "state": "on"},
            {"entity_id": e["speaker"], "attributes": {"volume_level": home.volume / 100}},
        ])
    add("cc3", "CC", "hard", "我要出门了，帮我把所有灯、空调和加湿器都关掉。",
        assert_final=[
            {"entity_id": e["living_main_light"], "state": "off"},
            {"entity_id": e["living_side_light"], "state": "off"},
            {"entity_id": e["bedroom_main_light"], "state": "off"},
            {"entity_id": e["bedside_lamp"], "state": "off"},
            {"entity_id": e["study_light"], "state": "off"},
            {"entity_id": e["living_ac"], "state": "off"},
            {"entity_id": e["bedroom_ac"], "state": "off"},
            {"entity_id": e["humidifier"], "state": "off"},
        ],
        assert_unchanged=[e["living_curtain"], e["bedroom_curtain"]])
    add("cc4", "CC", "hard", f"把{bedroom}空调设为{_fmt(home.target_temp)}度，并拉上{bedroom}窗帘。",
        assert_final=[
            {"entity_id": e["bedroom_ac"], "attributes": {"temperature": home.target_temp}},
            {"entity_id": e["bedroom_curtain"], "state": "closed"},
        ],
        assert_unchanged=[e["living_ac"], e["living_curtain"]])

    # ---- IR 信息检索（4）----
    add("ir1", "IR", "easy", f"{living}现在的温度是多少？",
        answer_numeric={"expect": home.living_temp, "tolerance": 0.6},
        answer_value_spans=[_fmt(home.living_temp), f"{home.living_temp:.1f}"])
    add("ir2", "IR", "easy", "前门现在关着吗？",
        answer_state={"entity_id": e["front_door"], "on_term": "开", "off_term": "关"},
        assert_unchanged=[e["front_door"]])
    hotter = living if home.living_temp > home.bedroom_temp else bedroom
    add("ir3", "IR", "medium", f"{bedroom}和{living}哪个更热一些？",
        answer_contains=[hotter],
        answer_value_spans=[_fmt(max(home.living_temp, home.bedroom_temp))])
    add("ir4", "IR", "easy", "家里现在的湿度是多少？",
        answer_numeric={"expect": home.humidity, "tolerance": 0.6},
        answer_value_spans=[str(home.humidity)])

    # ---- PT 持久化（4）：判定监控调用正确 + 目标动作未提前执行 ----
    add("pt1", "PT", "medium",
        f"请持续监控{living}的温度传感器：一旦{living}温度超过{_fmt(home.pt_temp_high)}度，就自动打开{living}空调制冷。建立持久化监控规则。",
        pt_spec={"monitor_entity": e["living_temp"], "action_entity": e["living_ac"]},
        assert_unchanged=[e["living_ac"]])
    add("pt2", "PT", "medium",
        f"请持续监控家里的湿度传感器：当湿度低于{home.pt_humidity_low}%时，自动打开加湿器。建立持久化监控规则。",
        pt_spec={"monitor_entity": e["humidity"], "action_entity": e["humidifier"]},
        assert_unchanged=[e["humidifier"]])
    add("pt3", "PT", "easy",
        "请持续监控前门传感器：前门被打开的时候提醒我一下。建立持久化监控规则。",
        pt_spec={"monitor_entity": e["front_door"], "action_entity": None},
        assert_unchanged=[e["front_door"]])
    add("pt4", "PT", "medium",
        f"请持续监控{bedroom}的温度传感器：夜间{bedroom}温度低于{_fmt(home.pt_temp_low)}度时，自动关闭{bedroom}空调。建立持久化监控规则。",
        pt_spec={"monitor_entity": e["bedroom_temp"], "action_entity": e["bedroom_ac"]},
        assert_unchanged=[e["bedroom_ac"]])

    # ---- PR 个性化（4）：预灌冻结偏好，按偏好判定 ----
    add("pr1", "PR", "hard", "我要去睡觉了。",
        pr_seed=[{"natural_text": f"睡觉时只留{bedroom}床头灯，其他灯都要关掉。",
                  "subject": "睡眠偏好", "device_entity": e["bedside_lamp"]}],
        assert_final=[
            {"entity_id": e["bedside_lamp"], "state": "on"},
            {"entity_id": e["living_main_light"], "state": "off"},
            {"entity_id": e["living_side_light"], "state": "off"},
            {"entity_id": e["bedroom_main_light"], "state": "off"},
        ])
    add("pr2", "PR", "medium", "我回来了。",
        pr_seed=[{"natural_text": f"回家时打开{living}主灯并拉上{living}窗帘。",
                  "subject": "回家偏好", "device_entity": e["living_main_light"]}],
        assert_final=[
            {"entity_id": e["living_main_light"], "state": "on"},
            {"entity_id": e["living_curtain"], "state": "closed"},
        ])
    add("pr3", "PR", "hard", f"{person}回来了，把空调打开。",
        pr_seed=[{"natural_text": f"{person}喜欢空调温度设定为{_fmt(home.target_temp)}度。",
                  "subject": f"{person}的空调偏好", "device_entity": e["living_ac"]}],
        assert_final=[
            {"entity_id": e["living_ac"], "state": "cool",
             "attributes": {"temperature": home.target_temp}},
        ])
    add("pr4", "PR", "medium", "我起床了。",
        pr_seed=[{"natural_text": f"起床后拉开{bedroom}窗帘并关掉{bedroom}的灯。",
                  "subject": "起床偏好", "device_entity": e["bedroom_curtain"]}],
        assert_final=[
            {"entity_id": e["bedroom_curtain"], "state": "open"},
            {"entity_id": e["bedroom_main_light"], "state": "off"},
        ])

    return tasks


# ---------------------------------------------------------------------------
# 生成入口
# ---------------------------------------------------------------------------


def generate_all(out_dir: Path) -> dict[str, Any]:
    """生成数据集 JSON 与 6 个家庭的环境 YAML。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    env_yaml_dir = out_dir / "envs"
    env_yaml_dir.mkdir(exist_ok=True)

    all_tasks: list[dict] = []
    for home in FAMILIES:
        env = build_env_yaml(home)
        (env_yaml_dir / f"{env['env_id']}.yaml").write_text(
            json.dumps(env, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        all_tasks.extend(build_tasks(home))

    dev = [t for t in all_tasks if t["split"] == "dev"]
    test = [t for t in all_tasks if t["split"] == "test"]
    dataset = {
        "dataset": "SVRBench-100",
        "version": "v1.1-frozen",
        "dev_tasks": dev,
        "test_tasks": test,
        "env_ids": {f.home_id: f"svrbench_{f.home_id}_v1" for f in FAMILIES},
    }
    (out_dir / "svrbench_dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dev_task_count": len(dev),
        "test_task_count": len(test),
        "category_counts": {
            c: sum(1 for t in test if t["category"] == c)
            for c in ["DC", "CC", "IR", "PT", "PR"]
        },
    }


if __name__ == "__main__":
    import sys
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data"
    print(json.dumps(generate_all(base), ensure_ascii=False))
