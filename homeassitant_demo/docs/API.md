# Fake Home Assistant v2 API 文档

本文档只说明当前模拟器已经提供的 API、调用方式，以及当前内置支持的 service。

如果你需要了解内部实现或扩展方式，请看：

- [测试环境调用指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\TEST_ENVIRONMENTS.md)
- [架构说明](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\ARCHITECTURE.md)
- [扩展指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\EXTENDING.md)
- [设计约定](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\DESIGN_RULES.md)

## 基础信息

- 默认地址：`http://127.0.0.1:8123`
- 数据格式：`application/json`
- 认证方式：可选 Bearer Token

如果设置了环境变量 `FAKE_HA_TOKEN`，所有 `/api/*` 和 `/api/mock/*` 请求都必须带：

```http
Authorization: Bearer your-token
```

## 1. REST API 总览

当前已经实现的 Home Assistant 风格 API：

- `GET /api/`
- `GET /api/config`
- `GET /api/events`
- `GET /api/services`
- `GET /api/states`
- `GET /api/states/{entity_id}`
- `POST /api/states/{entity_id}`
- `DELETE /api/states/{entity_id}`
- `POST /api/events/{event_type}`
- `POST /api/services/{domain}/{service}`

当前已经实现的扩展管理 API：

- `PUT /api/mock/devices/{device_id}`
- `PUT /api/mock/entities/{entity_id}`
- `POST /api/mock/reload`
- `POST /api/mock/init_env`
- `POST /api/mock/original_env`

## 2. 通用调用约定

### 2.1 查询实体状态

查询所有实体：

```powershell
curl http://127.0.0.1:8123/api/states
```

查询单个实体：

```powershell
curl http://127.0.0.1:8123/api/states/light.philips_cn_1061200910_lite_s_2
```

### 2.2 手动写入状态

```powershell
curl -X POST http://127.0.0.1:8123/api/states/sensor.demo_virtual `
  -H "Content-Type: application/json" `
  -d "{\"state\":\"on\",\"attributes\":{\"unit_of_measurement\":\"x\"}}"
```

行为说明：

- 如果实体已存在，返回 `200`
- 如果实体不存在，会自动创建一个临时实体并返回 `201`

### 2.3 删除状态

```powershell
curl -X DELETE http://127.0.0.1:8123/api/states/sensor.demo_virtual
```

### 2.4 触发事件

```powershell
curl -X POST http://127.0.0.1:8123/api/events/demo_event `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"hello\"}"
```

### 2.5 获取服务清单

```powershell
curl http://127.0.0.1:8123/api/services
```

返回结果按 `domain -> services` 分组，包含：

- 服务名
- 描述
- 字段定义
- target 规则
- handler 标识
- 是否支持 `return_response`

## 3. 如何调用实体操作

对实体执行操作，统一走：

```http
POST /api/services/{domain}/{service}
Content-Type: application/json
```

请求体通常至少包含：

- `entity_id`

例如：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/light/turn_on `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"light.philips_cn_1061200910_lite_s_2\"}"
```

部分 service 支持 `return_response=true`：

```powershell
curl -X POST "http://127.0.0.1:8123/api/services/media_player/media_next_track?return_response=true" `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"media_player.xiaomi_cn_701074704_l15a\"}"
```

如果服务支持该参数，返回格式为：

```json
{
  "changed_states": [...],
  "service_response": {...}
}
```

否则返回的是变更后的状态列表：

```json
[
  {
    "entity_id": "...",
    "state": "...",
    "attributes": {...}
  }
]
```

## 4. 当前内置支持的 domain 和 service

### 4.1 `homeassistant`

- `turn_on`
- `turn_off`
- `toggle`
- `update_entity`
- `save_persistent_states`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/homeassistant/toggle `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":[\"switch.demo_1\",\"light.demo_2\"]}"
```

### 4.2 `switch`

- `turn_on`
- `turn_off`
- `toggle`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/switch/toggle `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"switch.demo_socket\"}"
```

### 4.3 `light`

- `turn_on`
- `turn_off`
- `toggle`

`light.turn_on` 额外支持：

- `brightness`
- `brightness_pct`
- `brightness_step_pct`
- `color_temp`
- `color_temp_kelvin`
- `effect`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/light/turn_on `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"light.philips_cn_1061200910_lite_s_2\",\"brightness_pct\":50}"
```

### 4.4 `number`

- `set_value`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/number/set_value `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"number.lumi_cn_551385025_mcn001_indicator_brightness_p_6_3\",\"value\":80}"
```

### 4.5 `text`

- `set_value`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/text/set_value `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"text.lumi_cn_551385025_mcn001_effective_time_p_6_2\",\"value\":\"21:00-09:00\"}"
```

### 4.6 `select`

- `select_first`
- `select_last`
- `select_next`
- `select_previous`
- `select_option`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/select/select_option `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"select.lumi_cn_551385025_mcn001_status_p_6_1\",\"option\":\"Close\"}"
```

### 4.7 `button`

- `press`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/button/press `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"button.philips_cn_1061200910_lite_toggle_a_2_1\"}"
```

说明：

- 某些按钮实体在导入后带有动作脚本，按下按钮时会联动其他实体
- 例如旧样本中的台灯按钮会联动对应的 `light.*`

### 4.8 `media_player`

- `volume_set`
- `volume_up`
- `volume_down`
- `volume_mute`
- `media_play`
- `media_pause`
- `media_play_pause`
- `media_stop`
- `media_previous_track`
- `media_next_track`

其中支持 `return_response=true` 的 service：

- `media_previous_track`
- `media_next_track`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/media_player/volume_set `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"media_player.xiaomi_cn_701074704_l15a\",\"volume_level\":0.6}"
```

### 4.9 `notify`

- `send_message`

示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/services/notify/send_message `
  -H "Content-Type: application/json" `
  -d "{\"entity_id\":\"notify.xiaomi_cn_701074704_l15a_execute_text_directive_a_7_4\",\"message\":\"播放 Shape of You\"}"
```

## 5. 扩展管理 API

这些接口不是 Home Assistant 官方接口，而是本项目为“方便新增设备/实体”额外提供的管理接口。

### 5.1 新增或覆盖设备

```http
PUT /api/mock/devices/{device_id}
```

示例：

```powershell
curl -X PUT http://127.0.0.1:8123/api/mock/devices/device.demo_lamp `
  -H "Content-Type: application/json" `
  -d "{
    \"device\": {
      \"device_id\": \"device.demo_lamp\",
      \"name\": \"Demo Lamp\",
      \"manufacturer\": \"Demo\",
      \"model\": \"Lamp\",
      \"entities\": []
    },
    \"entities\": [
      {
        \"entity_id\": \"switch.demo_lamp_power\",
        \"domain\": \"switch\",
        \"object_id\": \"demo_lamp_power\",
        \"device_id\": \"device.demo_lamp\",
        \"state\": \"off\",
        \"attributes\": {
          \"friendly_name\": \"Demo Lamp Power\"
        }
      }
    ]
  }"
```

### 5.2 新增或覆盖实体

```http
PUT /api/mock/entities/{entity_id}
```

示例：

```powershell
curl -X PUT http://127.0.0.1:8123/api/mock/entities/fan.demo `
  -H "Content-Type: application/json" `
  -d "{
    \"entity\": {
      \"entity_id\": \"fan.demo\",
      \"domain\": \"fan\",
      \"object_id\": \"demo\",
      \"state\": \"off\",
      \"attributes\": {
        \"percentage\": 0
      }
    }
  }"
```

### 5.3 重新加载磁盘定义

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/reload
```

### 5.4 切换到测试环境

```http
POST /api/mock/init_env
Content-Type: application/json
```

请求体：

```json
{
  "env_id": "te_ac_sensor_v1",
  "fault_mode": "one_shot_network_error"
}
```

说明：

- `env_id` 必填
- `fault_mode` 可选；默认使用测试环境定义中的默认模式（未定义时为 `normal`）
- 支持的故障模式由测试环境定义决定
- 当前内置可用环境包括：
  - `te_ac_sensor_v1`（示例环境）
  - `te_normal_pair_a_v1`
  - `te_normal_pair_b_v1`
  - `te_one_shot_network_error_pair_a_v1`
  - `te_one_shot_network_error_pair_b_v1`
  - `te_fake_success_pair_a_v1`
  - `te_fake_success_pair_b_v1`
  - `base_env`（运行时从 `fake_homeassitant_try/copied_data` 动态构建）
- 六套 `te_*_pair_*_v1` 环境均为单模式固定环境：
  - `te_normal_pair_*_v1` 仅支持 `normal`
  - `te_one_shot_network_error_pair_*_v1` 仅支持 `one_shot_network_error`
  - `te_fake_success_pair_*_v1` 仅支持 `fake_success`
  - 对这些环境传入不支持的 `fault_mode` 会返回 `400`
- `base_env` 仅支持 `normal` 模式；传入其他 `fault_mode` 会返回 `400`
- 详细说明见：[测试环境调用指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\TEST_ENVIRONMENTS.md)

`base_env` 调用示例：

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/init_env `
  -H "Content-Type: application/json" `
  -d "{\"env_id\":\"base_env\"}"
```

### 5.5 恢复原始环境

```powershell
curl -X POST http://127.0.0.1:8123/api/mock/original_env
```

说明：

- 仅当当前进程内调用过 `init_env` 并保存了快照时可恢复
- 若没有可恢复快照，返回 `400`

## 6. 错误处理

常见错误码：

- `400`: 请求参数非法，例如缺少字段、字段不符合约束、请求了不支持 `return_response` 的 service
- `401`: 启用了 token 鉴权，但请求未带正确 Bearer Token
- `404`: 不存在的实体或 service
- `409`: `device_id` 或 `entity_id` 路径参数与请求体不一致
- `503`: 测试环境故障注入触发的模拟网络错误

## 7. 建议调用顺序

接入一个外部应用时，通常按下面顺序调用：

1. `GET /api/` 确认服务在线
2. `GET /api/services` 获取当前支持的 domain/service
3. `GET /api/states` 或 `GET /api/states/{entity_id}` 获取实体状态
4. `POST /api/services/{domain}/{service}` 执行操作
5. 再次查询 `/api/states/{entity_id}` 验证结果

## 8. 当前限制

- 默认只监听 `127.0.0.1:8123`
- 当前只实现 REST 核心接口，没有实现 WebSocket API
- 设备注册表和实体注册表的外部查询接口暂未单独开放
- 不是完整的 Home Assistant 内核，只模拟常用 API 和若干内置行为
