# Fake Home Assistant v2 扩展指南

本文档说明如何为当前模拟器新增设备、实体、service、actions 和自定义 handler。

## 1. 扩展前先判断选哪条路

优先顺序：

1. 声明式设备/实体
2. 实体 actions
3. Python handler

简单判断：

- 只是新增一个普通设备或实体，直接写定义
- 只是按钮联动、调用其他 service、发事件，优先写 actions
- 存在复杂校验、复杂状态计算、复杂副作用，才写 Python handler

详细原则见：

- [DESIGN_RULES.md](DESIGN_RULES.md)

## 2. 扩展入口一览

### 方式 A：通过 API 动态新增

适合：

- 调试
- 小规模新增
- 运行时快速验证

接口：

- `PUT /api/mock/devices/{device_id}`
- `PUT /api/mock/entities/{entity_id}`

注意：

- 这是运行时定义管理接口
- 写入后会持久化到 `.fake_homeassistant`

### 方式 B：直接写运行时定义文件

适合：

- 本地准备一批静态数据
- 希望服务启动后自动加载

目录：

- `.fake_homeassistant/devices/`
- `.fake_homeassistant/entities/`
- `.fake_homeassistant/services/`

写入后可以调用：

- `POST /api/mock/reload`

### 方式 C：修改仓库默认实现

适合：

- 新增通用内置行为
- 完善默认 service 定义
- 增加新的内置 handler

涉及位置：

- [base.yaml](../src/fake_homeassistant_v2/data/services/base.yaml)
- [runtime.py](../src/fake_homeassistant_v2/runtime.py)
- [tests/test_api.py](../tests/test_api.py)

## 3. 新增实体的最小步骤

如果目标只是增加一个能被查询和调用的实体，最少需要：

1. 确定 `entity_id`
2. 确定 `domain`
3. 定义初始 `state`
4. 定义必要的 `attributes`
5. 如果该 domain 已有可复用的 service，就不需要再改 handler

最小实体示例：

```json
{
  "entity": {
    "entity_id": "switch.demo_lamp_power",
    "domain": "switch",
    "object_id": "demo_lamp_power",
    "state": "off",
    "attributes": {
      "friendly_name": "Demo Lamp Power"
    }
  }
}
```

调用方式：

```powershell
curl -X PUT http://127.0.0.1:8123/api/mock/entities/switch.demo_lamp_power `
  -H "Content-Type: application/json" `
  -d "@entity.json"
```

## 4. 新增设备的最小步骤

如果一个设备包含多个实体，建议一次性用设备接口注册。

最小设备示例：

```json
{
  "device": {
    "device_id": "device.demo_lamp",
    "name": "Demo Lamp",
    "manufacturer": "Demo",
    "model": "Lamp",
    "entities": []
  },
  "entities": [
    {
      "entity_id": "switch.demo_lamp_power",
      "domain": "switch",
      "object_id": "demo_lamp_power",
      "device_id": "device.demo_lamp",
      "state": "off",
      "attributes": {
        "friendly_name": "Demo Lamp Power"
      }
    }
  ]
}
```

## 5. 新增 service 的最小步骤

如果现有 domain 没有对应 service，需要新增 `ServiceDefinition`。

service 定义最少包含：

- `domain`
- `service`
- `name`
- `fields`
- `handler`

最小示例：

```yaml
domain: fan
service: set_percentage
name: Set percentage
description: Set a fan percentage.
fields:
  entity_id:
    required: true
  percentage:
    required: true
target:
  entity:
    - {}
handler: tests.custom_handlers:fan_set_percentage
supports_response: true
```

如果 handler 指向的是自定义 Python 实现，运行时会按 `module:function` 方式动态加载。

## 6. 什么时候用 actions

actions 适合表达：

- 按按钮后调用另一个 service
- 修改另一个实体状态
- 发送一个事件

支持的 action 类型：

- `call_service`
- `set_state`
- `fire_event`

按钮联动灯的示例：

```json
{
  "actions": {
    "on_press": [
      {
        "type": "call_service",
        "domain": "light",
        "service": "toggle",
        "data": {
          "entity_id": "${links.light}"
        }
      }
    ]
  },
  "links": {
    "light": "light.demo_lamp"
  }
}
```

占位符支持读取：

- `${entity_id}`
- `${links.xxx}`
- `${payload.xxx}`
- `${attributes.xxx}`

## 7. 什么时候写 Python handler

出现下面情况时，直接写 handler：

- 参数校验明显复杂
- 需要根据多个属性计算状态
- 需要跨多个实体做复杂联动
- actions 表达起来已经不清晰
- 返回值需要明确的 `service_response`

自定义 handler 示例可以参考：

- [tests/custom_handlers.py](../tests/custom_handlers.py)

## 8. 推荐扩展流程

新增一个扩展时，按这个顺序做：

1. 先确认目标是否能复用现有 domain 和内置 service
2. 能复用就只写设备/实体定义
3. 如果需要简单联动，先尝试 actions
4. 如果 actions 不够，再增加 Python handler
5. 如果引入了新的 service，就补 `ServiceDefinition`
6. 最后补测试和文档

## 9. 每次扩展至少验证什么

最少验证：

1. 新实体能在 `/api/states` 中看到状态
2. 新实体能在 `/api/entities` 中看到定义（含 `device_id`）
3. 如果通过设备注册，能在 `/api/devices/{device_id}` 中看到设备-实体关联
4. 对应 service 能调用成功
5. 状态变化后会持久化
6. 重启后状态或定义符合预期

如果新增的是 service 或 handler，还应额外验证：

1. 缺失必填字段时返回 `400`
2. 非法实体返回 `404`
3. 如果声明支持 `return_response`，返回结构正确

## 10. 文档更新约定

扩展完成后按需更新：

- 对外接口变化：更新 [API.md](API.md)
- 内部机制变化：更新 [ARCHITECTURE.md](ARCHITECTURE.md)
- 扩展规则变化：更新 [DESIGN_RULES.md](DESIGN_RULES.md)

不要把这些知识重新堆回 `README.md`。

## 11. 新增一个自定义测试环境（YAML 模板 + 最小示例）

如果你要扩展 `POST /api/mock/init_env` 可切换的测试环境，直接在目录里新增一个 YAML：

- `src/fake_homeassistant_v2/data/test_envs/`

### 11.1 YAML 模板

```yaml
env_id: te_your_env_id
default_fault_mode: normal
supported_fault_modes:
  - normal
  - one_shot_network_error
  - fake_success
devices:
  - device_id: device.test_demo
    name: Test Device
    area_id: room.demo
    entities:
      - climate.test_demo
entities:
  - entity_id: climate.test_demo
    domain: climate
    object_id: test_demo
    device_id: device.test_demo
    area_id: room.demo
    platform: mock
    state: cool
    attributes:
      hvac_mode: cool
      hvac_modes: ["off", "cool", "heat"]
      temperature: 24.0
      current_temperature: 28.0
initial_states:
  - entity_id: climate.test_demo
    state: cool
    attributes:
      temperature: 24.0
link_rules:
  - source_domain: climate
    source_service: set_temperature
    target_domain: sensor
    match: same_area_id
    target_device_class: temperature
    propagate: payload:temperature
fault_profiles:
  one_shot_network_error:
    - domain: climate
      service: set_temperature
      entity_id: climate.test_demo
      times: 1
  fake_success:
    - domain: climate
      service: set_temperature
      entity_id: climate.test_demo
```

### 11.2 最小可运行示例

可直接参考已内置样例：

- `src/fake_homeassistant_v2/data/test_envs/te_ac_sensor_v1.yaml`
  - 空调 + 多个温度传感器实体
  - 同房间联动（按 `area_id`）
  - `normal` / `one_shot_network_error` / `fake_success`
- `src/fake_homeassistant_v2/data/test_envs/te_light_sensor_v1.yaml`
  - 灯光 + 光照传感器
  - 灯光亮度变化 → 同房间光照传感器值联动

### 11.3 调用步骤

新增 YAML 后，无需重启进程；直接调用：

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/init_env `
  -H "Content-Type: application/json" `
  -d "{\"env_id\":\"te_your_env_id\"}"
```

指定故障模式：

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/init_env `
  -H "Content-Type: application/json" `
  -d "{\"env_id\":\"te_your_env_id\",\"fault_mode\":\"one_shot_network_error\"}"
```

恢复原始环境：

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/original_env
```

### 11.4 link_rules 配置规则

`link_rules` 定义服务调用后的跨实体联动。每条规则包含以下字段：

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `source_domain` | 是 | — | 触发联动的源实体 domain，如 `light`、`climate` |
| `source_service` | 是 | — | 触发联动的服务名，如 `turn_on`、`set_temperature` |
| `target_domain` | 是 | — | 被联动影响的目标实体 domain，如 `sensor` |
| `match` | 否 | `same_area_id` | 匹配策略（见下方） |
| `match_key` | 否 | — | `match=source_entity` 时指定精确 entity_id |
| `target_device_class` | 否 | — | 进一步限制目标实体的 device_class，如 `illuminance` |
| `propagate` | 否 | `state` | 传播给目标的值来源（见下方） |
| `target_action` | 否 | — | 设为 `dry_run` 时只记录不执行 |

**match 匹配策略**：

| 值 | 含义 |
|----|------|
| `same_area_id` | 匹配与源实体 `area_id` 相同的目标实体（默认） |
| `same_device` | 匹配与源实体 `device_id` 相同的目标实体 |
| `source_entity` | 精确匹配 `match_key` 指定的 entity_id |

**propagate 传播值来源**：

| 格式 | 含义 | 示例 |
|------|------|------|
| `state` | 源实体当前 state 值 | `"on"` / `"cool"` |
| `attr:字段名` | 源实体当前 attributes 中指定字段 | `attr:brightness` → `204` |
| `payload:字段名` | 服务调用 payload 中指定字段 | `payload:temperature` → `25` |

**climate 联动示例**（温度为传感器值）：

```yaml
link_rules:
  - source_domain: climate
    source_service: set_temperature
    target_domain: sensor
    target_device_class: temperature
    match: same_area_id
    propagate: payload:temperature
```

**light 联动示例**（亮度值同步到光照传感器）：

```yaml
link_rules:
  - source_domain: light
    source_service: turn_on
    target_domain: sensor
    target_device_class: illuminance
    match: same_area_id
    propagate: attr:brightness
```

### 11.5 常见约束

- `default_fault_mode` 必须包含在 `supported_fault_modes` 里。
- `fault_profiles` 的 key 必须是 `supported_fault_modes` 中声明过的模式。
- 若想触发“同房间联动”，源实体和目标实体都必须设置 `area_id`，且相同。
- `fake_success` 的语义是“接口成功但不写状态”，不是返回错误。

测试环境 API 详细行为见：

- [TEST_ENVIRONMENTS.md](TEST_ENVIRONMENTS.md)

## 12. 动态 `base_env`（非 YAML）

`base_env` 是运行时动态生成的内置测试环境，来源为：

- `fake_homeassitant_try/copied_data/device_registry.json`
- `fake_homeassitant_try/copied_data/entity_registry.json`
- `fake_homeassitant_try/copied_data/entities.json`

特点：

- 不需要在 `src/fake_homeassistant_v2/data/test_envs/` 下新增 YAML。
- 仍通过 `POST /api/mock/init_env` 切换：`{"env_id":"base_env"}`。
- 仅支持 `normal` 故障模式（不用于故障注入）。
- `legacy_root` 不可访问时，不注册该环境。
