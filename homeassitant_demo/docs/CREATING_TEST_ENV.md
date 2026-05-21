# 如何编写测试环境配置文件

本文档教你从零编写一个测试环境 YAML 文件。读完就能写出自己的设备配置。

---

## 第一步：创建文件

在 `src/fake_homeassistant_v2/data/test_envs/` 目录下新建任意 `.yaml` 文件，启动时会自动加载，无需重启进程即可通过 API 切换。

---

## 第二步：完整示例（灯光 + 光照传感器联动）

下面是一个完整可工作的环境配置。把这个例子看懂，就能写任何设备的配置了。

```yaml
# ─── 环境基本信息 ───────────────────────────────────────────────
# 唯一标识，之后通过这个 id 切换环境
env_id: te_my_light_env
# 默认故障模式（normal 表示不注入故障）
default_fault_mode: normal
# 这个环境支持哪些故障模式
supported_fault_modes:
  - normal

# ─── 设备（Device）──────────────────────────────────────────────
# 一台设备 = 一个物理硬件。一台设备可以包含多个实体。
devices:
  # ↓ 每项是一个设备，下面逐行解释 ——
  - device_id: device.my_living_light    # 设备唯一 ID（必须唯一）
    name: 客厅吸顶灯                      # 显示名称（给 LLM 看的）
    area_id: room.living_room            # 所属房间（联动匹配用，必填）
    manufacturer: Demo                   # 厂商（可选）
    model: Light-1                       # 型号（可选）
    entities:                            # 该设备下有哪些实体 ID
      - light.my_living_light

  - device_id: device.my_living_sensor
    name: 客厅光照传感器
    area_id: room.living_room
    entities:
      - sensor.my_living_illuminance

# ─── 实体（Entity）──────────────────────────────────────────────
# 一个实体 = 一个可操作或可读取的数据点（灯、传感器、开关等）。
# domain 决定这个实体支持哪些操作（turn_on、set_temperature 等）。
entities:
  # ↓ 灯光实体 ——
  - entity_id: light.my_living_light     # 实体唯一 ID，格式：domain.object_id
    domain: light                        # 必填，决定支持哪些 service
    object_id: my_living_light           # entity_id 中 "." 后面的部分
    device_id: device.my_living_light    # 归属哪个设备
    area_id: room.living_room            # 所属房间
    state: "off"                         # 初始状态
    attributes:                          # 初始属性（不同 domain 需要的属性不同）
      brightness: 0                      # 亮度 0-255，0=关

  # ↓ 传感器实体 ——
  - entity_id: sensor.my_living_illuminance
    domain: sensor                       # sensor 是只读的，不能调用 service
    object_id: my_living_illuminance
    device_id: device.my_living_sensor
    area_id: room.living_room
    device_class: illuminance            # 传感器分类，联动时用于精确筛选
    state: 0                             # 初始照度值
    attributes:
      unit_of_measurement: lx            # 单位

# ─── 初始状态覆盖（可选）─────────────────────────────────────────
# 和 entities 中的 state/attributes 作用相同，但可以覆盖实体定义中的默认值。
# 通常只需在 entities 里写 state/attributes，这里省略也可以。
initial_states: []

# ─── 联动规则（link_rules）───────────────────────────────────────
# 当某个实体的 service 被调用时，自动联动修改其他实体。
# 本例：开灯时，同房间的光照传感器值 = 灯光亮度。
link_rules:
  - source_domain: light                 # 触发联动的实体 domain
    source_service: turn_on              # 触发联动的服务名
    target_domain: sensor                # 被联动影响的实体 domain
    target_device_class: illuminance     # 进一步限制：只影响 illuminance 类传感器
    match: same_area_id                  # 匹配策略：同房间的传感器
    propagate: attr:brightness           # 传播值：灯光当前的 brightness 属性

# ─── 故障注入（可选）─────────────────────────────────────────────
fault_profiles: {}
```

---

## 第三步：验证是否可用

无需重启。直接调用 API 切换环境并测试：

```powershell
# 1. 切换到新环境
curl -X POST http://127.0.0.1:8123/api/mock/init_env `
  -H "Content-Type: application/json" `
  -d "{\"env_id\":\"te_my_light_env\"}"

# 2. 调用 service
curl -X POST http://127.0.0.1:8123/api/services/light/turn_on `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"light.my_living_light\",\"brightness_pct\":80}"

# 3. 检查联动结果
curl http://127.0.0.1:8123/api/states/sensor.my_living_illuminance
# → state 应该是 204（= 80% 亮度）
```

---

## 第四步：字段参考

### 实体 domain 与支持的 service

实体的 `domain` 决定它能调用哪些 service。当前内置支持的 domain：

| domain | 可调用的 service | 典型设备 |
|--------|-----------------|---------|
| `light` | `turn_on`, `turn_off`, `toggle` | 灯 |
| `switch` | `turn_on`, `turn_off`, `toggle` | 插座、风扇开关 |
| `climate` | `set_temperature` | 空调、温控器 |
| `media_player` | `volume_set`, `volume_up`, `volume_down`, `volume_mute`, `media_play`, `media_pause`, `media_play_pause`, `media_stop`, `media_previous_track`, `media_next_track` | 电视、音箱 |
| `number` | `set_value` | 数值设置（如网关亮度） |
| `text` | `set_value` | 文本设置（如勿扰时段） |
| `select` | `select_option`, `select_first`, `select_last`, `select_next`, `select_previous` | 下拉选项 |
| `button` | `press` | 按钮 |
| `notify` | `send_message` | 通知（如音箱播报文字） |
| `sensor` | （只读） | 温度、湿度、光照传感器 |
| `binary_sensor` | （只读） | 门磁、人体传感器 |
| `homeassistant` | `turn_on`, `turn_off`, `toggle`, `update_entity` | 通用，可批量操作 |

### link_rules 字段

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `source_domain` | 是 | — | 触发联动的实体 domain |
| `source_service` | 是 | — | 触发联动的服务名 |
| `target_domain` | 是 | — | 被联动影响的实体 domain |
| `match` | 否 | `same_area_id` | `same_area_id` / `same_device` / `source_entity` |
| `match_key` | 否 | — | `match=source_entity` 时指定精确的 entity_id |
| `target_device_class` | 否 | — | 进一步限制目标实体的 device_class |
| `propagate` | 否 | `state` | 传播什么值：`state` / `attr:字段名` / `payload:字段名` |
| `target_action` | 否 | — | `dry_run` 只记录不执行 |

### 各种设备配置片段

**电视（media_player）**

```yaml
- entity_id: media_player.tv_living
  domain: media_player
  object_id: tv_living
  device_id: device.tv_living
  area_id: room.living_room
  state: "off"
  attributes:
    volume_level: 0.3
    is_volume_muted: false
```

**冰箱（climate）**

```yaml
- entity_id: climate.fridge_kitchen
  domain: climate
  object_id: fridge_kitchen
  device_id: device.fridge_kitchen
  area_id: room.kitchen
  state: "cool"
  attributes:
    hvac_mode: "cool"
    hvac_modes: ["off", "cool"]
    temperature: 4.0
    current_temperature: 4.5
```

**风扇（switch）**

```yaml
- entity_id: switch.fan_bedroom
  domain: switch
  object_id: fan_bedroom
  device_id: device.fan_bedroom
  area_id: room.bedroom
  state: "off"
```

**人体传感器（binary_sensor）**

```yaml
- entity_id: binary_sensor.motion_living
  domain: binary_sensor
  object_id: motion_living
  device_id: device.motion_living
  area_id: room.living_room
  device_class: motion
  state: "off"
```

**温度传感器（sensor）**

```yaml
- entity_id: sensor.temp_living
  domain: sensor
  object_id: temp_living
  device_id: device.temp_living
  area_id: room.living_room
  device_class: temperature
  state: 24.0
  attributes:
    unit_of_measurement: "°C"
```

---

## 第五步：常见联动示例

**空调调温 → 同房间温度传感器更新**

```yaml
link_rules:
  - source_domain: climate
    source_service: set_temperature
    target_domain: sensor
    target_device_class: temperature
    match: same_area_id
    propagate: payload:temperature     # 取 set_temperature 请求中的 temperature 值
```

**开灯 → 光照传感器反映亮度**

```yaml
link_rules:
  - source_domain: light
    source_service: turn_on
    target_domain: sensor
    target_device_class: illuminance
    match: same_area_id
    propagate: attr:brightness         # 取灯光当前的 brightness 属性
```

**关灯 → 光照传感器归零**

```yaml
link_rules:
  - source_domain: light
    source_service: turn_off
    target_domain: sensor
    target_device_class: illuminance
    match: same_area_id
    propagate: state                   # state 是 "off"，传感器值变为 0（因为 light off 时 brightness=0 会被 set_state 覆盖）
                                      # 如果希望精确控制，可以用 target_action: dry_run 只记录不执行
```

> 每个 `link_rules` 条目独立运作。一个实体可以有多个联动目标，也可以被多个 source 联动。

---

## 参考

- 完整示例文件：`src/fake_homeassistant_v2/data/test_envs/te_light_sensor_v1.yaml`（灯光联动）
- 完整示例文件：`src/fake_homeassistant_v2/data/test_envs/te_ac_sensor_v1.yaml`（空调联动 + 故障注入）
- 所有环境清单：`docs/TEST_ENV_CATALOG.md`
