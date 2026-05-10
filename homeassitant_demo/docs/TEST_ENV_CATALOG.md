# 测试环境总览（环境 / 设备 / 故障 / 空间）

本文档用于回答“有哪些测试环境、各自设备与空间信息、故障类型与规则”。  
如果你需要 API 调用方式，请看：[测试环境调用指南](TEST_ENVIRONMENTS.md)。

> 统计口径日期：`2026-04-20`  
> 说明：`base_env` 为动态环境，统计会随 `copied_data` 基线变化而变化。

## 1. 环境总表

| env_id | 来源 | 默认故障模式 | 可选故障模式 | 空间（area） | 设备数 | 实体数 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `te_ac_sensor_v1` | YAML | `normal` | `normal`, `one_shot_network_error`, `fake_success` | `room.living_room`, `room.bedroom` | 2 | 4 | 空调+温度传感器联动示例环境 |
| `te_normal_pair_a_v1` | YAML | `normal` | `normal` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `te_normal_pair_b_v1` | YAML | `normal` | `normal` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `te_one_shot_network_error_pair_a_v1` | YAML | `one_shot_network_error` | `one_shot_network_error` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `te_one_shot_network_error_pair_b_v1` | YAML | `one_shot_network_error` | `one_shot_network_error` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `te_fake_success_pair_a_v1` | YAML | `fake_success` | `fake_success` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `te_fake_success_pair_b_v1` | YAML | `fake_success` | `fake_success` | `room.living_room`, `room.bedroom` | 4 | 4 | six-pair 之一，固定单故障模式 |
| `base_env` | 动态（运行时注册） | `normal` | `normal` | `3108946409de_jia_ke_ting`（当前快照） | 14 | 71 | 由 `fake_homeassitant_try/copied_data` 生成，仅在 `legacy_root` 可解析时可用 |

## 2. 设备与空间信息

### 2.1 `te_ac_sensor_v1`

- 设备：2 个
  - `device.test_ac_01`（空调，`area_id=room.living_room`）
  - `device.test_temp_sensor_01`（温度传感器设备，`area_id=room.living_room`）
- 实体：4 个
  - `climate.test_ac_01`
  - `sensor.test_room_temperature_living_1`
  - `sensor.test_room_temperature_living_2`
  - `sensor.test_room_temperature_bedroom_1`
- 空间分布（按实体）：
  - `room.living_room`：空调 + 2 个温度传感器
  - `room.bedroom`：1 个温度传感器
- 结构特性：
  - 卧室温度传感器实体（`sensor.test_room_temperature_bedroom_1`）挂在同一温度传感器设备 `device.test_temp_sensor_01` 下。

### 2.2 六套 pair 环境（共用拓扑）

环境列表：

- `te_normal_pair_a_v1`
- `te_normal_pair_b_v1`
- `te_one_shot_network_error_pair_a_v1`
- `te_one_shot_network_error_pair_b_v1`
- `te_fake_success_pair_a_v1`
- `te_fake_success_pair_b_v1`

共用设备拓扑（4 设备 / 4 实体）：

- 客厅空调设备：`device.test_living_room_ac_main` -> `climate.test_living_room_ac_main`
- 客厅温度传感器设备：`device.test_living_room_temperature_sensor_main` -> `sensor.test_living_room_temperature_main`
- 卧室空调设备：`device.test_bedroom_ac_main` -> `climate.test_bedroom_ac_main`
- 卧室温度传感器设备：`device.test_bedroom_temperature_sensor_main` -> `sensor.test_bedroom_temperature_main`

空间分布：

- `room.living_room`：2 设备，2 实体
- `room.bedroom`：2 设备，2 实体

### 2.3 `base_env`（设备摘要 + 空间信息）

- 来源：`fake_homeassitant_try/copied_data`（运行时动态生成，非 YAML 文件）
- 当前快照摘要（`2026-04-20`）：
  - 设备数：14（来自 `device_registry.json`）
  - 实体数：71（`/api/mock/init_env` 初始化 `base_env` 后的 `entity_count`）
- 空间（area）信息：
  - 设备 `area_id` 当前集中在：`3108946409de_jia_ke_ting`（14 设备）
- 代表性设备厂家分布（摘要）：
  - 小米：4
  - Yeelight：3
  - Gosund：2
  - 小方：2
  - 飞利浦：2
  - Aqara：1

## 3. 故障类型与注入规则

### 3.1 故障类型定义

- `normal`
  - 不注入故障，按正常逻辑执行。
- `one_shot_network_error`
  - 命中规则的首次调用返回 `503`，且不写入状态；后续同类调用恢复正常。
- `fake_success`
  - 命中规则时返回 `200`，但不写入状态（`changed_states` 为空）。

### 3.2 环境到故障类型与规则映射

> 规则字段维度：`domain / service / entity_id / times`

| env_id | 支持故障模式 | 注入规则（命中维度） |
| --- | --- | --- |
| `te_ac_sensor_v1` | `normal`, `one_shot_network_error`, `fake_success` | `one_shot_network_error`: `climate / set_temperature / climate.test_ac_01 / 1`；`fake_success`: `climate / set_temperature / climate.test_ac_01 / -`；`normal`: 无规则 |
| `te_normal_pair_a_v1` | `normal` | 无规则（`fault_profiles={}`） |
| `te_normal_pair_b_v1` | `normal` | 无规则（`fault_profiles={}`） |
| `te_one_shot_network_error_pair_a_v1` | `one_shot_network_error` | `climate / set_temperature / climate.test_living_room_ac_main / 1`；`climate / set_temperature / climate.test_bedroom_ac_main / 1` |
| `te_one_shot_network_error_pair_b_v1` | `one_shot_network_error` | `climate / set_temperature / climate.test_living_room_ac_main / 1`；`climate / set_temperature / climate.test_bedroom_ac_main / 1` |
| `te_fake_success_pair_a_v1` | `fake_success` | `climate / set_temperature / climate.test_living_room_ac_main / -`；`climate / set_temperature / climate.test_bedroom_ac_main / -` |
| `te_fake_success_pair_b_v1` | `fake_success` | `climate / set_temperature / climate.test_living_room_ac_main / -`；`climate / set_temperature / climate.test_bedroom_ac_main / -` |
| `base_env` | `normal` | 无规则（动态环境，`fault_profiles={}`） |

## 4. A/B 环境差异

A/B 对照仅在初始温度（`climate` 设定温度）不同，其余设备拓扑、联动规则、故障注入规则保持一致。

| 组别 | env_a | env_b | 客厅初始设温 | 卧室初始设温 | 其他差异 |
| --- | --- | --- | --- | --- | --- |
| `normal` 组 | `te_normal_pair_a_v1` | `te_normal_pair_b_v1` | A: 24.0, B: 22.0 | A: 25.0, B: 23.0 | 无 |
| `one_shot_network_error` 组 | `te_one_shot_network_error_pair_a_v1` | `te_one_shot_network_error_pair_b_v1` | A: 24.0, B: 22.0 | A: 25.0, B: 23.0 | 无 |
| `fake_success` 组 | `te_fake_success_pair_a_v1` | `te_fake_success_pair_b_v1` | A: 24.0, B: 22.0 | A: 25.0, B: 23.0 | 无 |

## 5. 维护约定

- 数据来源优先级：
  - 静态环境：`src/fake_homeassistant_v2/data/test_envs/*.yaml`
  - 动态环境：运行时 `base_env`（由 `fake_homeassitant_try/copied_data` 构建）
- 同步更新时机：
  - 新增/删除/修改任一测试环境 YAML
  - `copied_data` 基线更新导致 `base_env` 设备、实体、空间统计变化
  - `runtime.py` 中测试环境注册逻辑发生变化（例如故障模式约束调整）
- 更新动作要求：
  - 同步刷新“环境总表”“故障映射表”“A/B 差异表”
  - 在文档顶部更新“统计口径日期”
- 约束说明：
  - `base_env` 为条件注册：仅当 `legacy_root` 可访问且可解析时存在；否则不可用。
