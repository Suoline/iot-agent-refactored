# 测试环境调用指南

本文档说明如何通过 API 切换到测试环境、配置故障模式，以及恢复到原始环境。
测试环境清单、设备与空间信息、故障规则总览请看：[测试环境总览](TEST_ENV_CATALOG.md)。

## 1. 本次已实现功能

- 新增 `POST /api/mock/init_env`
  - 必填 `env_id`
  - 可选 `fault_mode`
- 新增 `POST /api/mock/original_env`
  - 恢复到进入测试环境前的快照
- 已支持故障模式：
  - `normal`
  - `one_shot_network_error`
  - `fake_success`
- 已内置测试环境：`te_ac_sensor_v1`
  - 设备：空调 + 温度传感器
  - 规则：空调设温后，仅联动同 `area_id` 的温度传感器；同房间多个传感器全部更新
- 已新增 6 套故障环境（共用同一设备拓扑，A/B 仅初始温度不同）：
  - `te_normal_pair_a_v1`
  - `te_normal_pair_b_v1`
  - `te_one_shot_network_error_pair_a_v1`
  - `te_one_shot_network_error_pair_b_v1`
  - `te_fake_success_pair_a_v1`
  - `te_fake_success_pair_b_v1`

## 2. 测试环境定义位置

- 目录：`src/fake_homeassistant_v2/data/test_envs/`
- 每个测试环境一个 YAML 文件
- 当前示例文件：
  - `src/fake_homeassistant_v2/data/test_envs/te_ac_sensor_v1.yaml`

核心字段：

- `env_id`
- `default_fault_mode`
- `supported_fault_modes`
- `devices`
- `entities`
- `initial_states`
- `link_rules`
- `fault_profiles`

## 3. API 调用方式

### 3.1 切换到测试环境

```http
POST /api/mock/init_env
Content-Type: application/json
```

请求体示例：

```json
{
  "env_id": "te_ac_sensor_v1",
  "fault_mode": "one_shot_network_error"
}
```

`fault_mode` 可省略。省略时使用该测试环境 YAML 中的 `default_fault_mode`。

成功响应示例：

```json
{
  "status": "initialized",
  "env_id": "te_ac_sensor_v1",
  "active_fault_mode": "one_shot_network_error",
  "saved_original_snapshot": true,
  "entity_count": 4
}
```

### 3.2 恢复原始环境

```http
POST /api/mock/original_env
```

成功响应示例：

```json
{
  "status": "restored",
  "restored": true,
  "entity_count": 71
}
```

如果当前进程尚未调用过 `init_env`（没有快照），会返回 `400`。

## 4. 故障模式说明

### `normal`

- 不注入故障
- 服务调用按正常逻辑执行

### `one_shot_network_error`

- 命中规则的第一次调用返回 `503`
- 不会修改任何状态
- 后续同类调用恢复正常

### `fake_success`

- 命中规则时接口返回成功（`200`）
- 但不写入目标状态（`changed_states` 为空）

### 六套新环境与故障模式映射

- `te_normal_pair_a_v1` / `te_normal_pair_b_v1`
  - 固定模式：`normal`
- `te_one_shot_network_error_pair_a_v1` / `te_one_shot_network_error_pair_b_v1`
  - 固定模式：`one_shot_network_error`
- `te_fake_success_pair_a_v1` / `te_fake_success_pair_b_v1`
  - 固定模式：`fake_success`

说明：

- 上述 6 套环境都将 `supported_fault_modes` 固定为单一模式，`default_fault_mode` 与其一致。
- 对这 6 套环境传入其他 `fault_mode` 会返回 `400`。

## 5. 联动规则说明（空调与温度传感器）

`te_ac_sensor_v1` 中已配置：

- 源：`climate.set_temperature`
- 目标：`sensor` 且 `device_class=temperature`
- 匹配方式：`same_area_id`

行为：

- 仅在当前激活测试环境时生效
- 仅更新与空调实体 `area_id` 相同的温度传感器
- 不同房间（不同 `area_id`）的传感器不会被更新

## 6. 推荐调用顺序

1. `POST /api/mock/init_env` 进入目标测试环境
2. `POST /api/services/...` 执行业务调用
3. `GET /api/states/...` 校验状态和联动结果
4. `POST /api/mock/original_env` 恢复原始环境

## 7. 动态内置环境 `base_env`

- `base_env` 基于 `fake_homeassitant_try/copied_data` 动态构建，不对应单独 YAML 文件。
- 创建方式复用现有接口：
  - `POST /api/mock/init_env`
  - 请求体：`{"env_id":"base_env"}`
- 可用前提：`legacy_root` 可访问（默认即 `fake_homeassitant_try/copied_data`）。
- 故障模式：仅支持 `normal`。
  - 传入其他 `fault_mode` 会返回 `400`。

示例：

```http
POST /api/mock/init_env
Content-Type: application/json
```

```json
{
  "env_id": "base_env"
}
```
