# Fake Home Assistant v2 架构说明

本文档描述项目的内部实现结构、请求流转和持久化方式。它面向维护者，不重复 API 使用说明。

## 1. 核心目标

这个项目不是完整复刻 Home Assistant 内核，而是提供一个足够稳定、可扩展、可持久化的模拟层：

- 对外暴露 Home Assistant 风格 REST API
- 内部按状态、注册表、事件、服务执行分层
- 新增设备和实体时优先走声明式定义
- 复杂副作用用 actions 或 Python handler 补充

## 2. 核心组件

### `StorageManager`

代码位置：

- [runtime.py](../src/fake_homeassistant_v2/runtime.py)

职责：

- 初始化运行时目录
- 负责 JSON/YAML 的原子写入
- 加载和保存设备、实体、service、状态、事件

默认运行时目录结构：

```text
.fake_homeassistant/
  devices/
  entities/
  services/
  state_store.json
  events.json
```

### `RegistryStore`

职责：

- 保存设备定义 `devices`
- 保存实体定义 `entities`
- 保存 service 定义 `services`
- 提供 `get_device()`、`list_devices()` 等设备查询入口
- 提供 `get_entity()`、`list_entities()` 等实体查询入口
- 提供 `get_service()` 等服务查询入口

它管理的是“定义”，不是运行中的状态快照。

### `StateStore`

职责：

- 保存当前实体状态快照
- 维护 `last_changed`、`last_reported`、`last_updated`
- 生成和维护 `context`
- 在状态改变后立即持久化到 `state_store.json`

状态和实体定义分离：

- `EntityDefinition` 负责描述实体是什么
- `StateRecord` 负责描述实体当前是什么状态

### `EventBus`

职责：

- 记录 `call_service`
- 记录 `state_changed`
- 支持手动 `POST /api/events/{event_type}`
- 持久化事件到 `events.json`

这里的事件模型是轻量模拟，不实现完整的 Home Assistant 事件总线语义。

### `ServiceEngine`

职责：

- 根据 `domain + service` 查找 `ServiceDefinition`
- 校验 payload
- 解析 `entity_id`
- 派发给内置 handler 或自定义 handler
- 汇总变更后的 `changed_states`

它是“调用 service”的统一入口，避免把 service 调用逻辑分散到 API 层。

### `ActionRunner`

职责：

- 执行实体定义中的 `actions`
- 支持在实体级别声明简单副作用
- 支持 `call_service`、`set_state`、`fire_event`

它用于解决“纯声明式不够，但没必要写 Python handler”的场景。

### `TestEnvManager`

职责：

- 加载 `src/fake_homeassistant_v2/data/test_envs/*.yaml` 测试环境定义
- 在加载 YAML 后，尝试基于 `legacy_root` 动态注册 `base_env`
- 执行 `init_env` 的全量环境替换
- 保存并恢复原始环境快照（`original_env`）
- 管理当前激活测试环境与故障模式
- 在 `climate.set_temperature` 后按 `link_rules` 执行同房间温度传感器联动

动态 `base_env` 特性：

- 来源：`fake_homeassitant_try/copied_data`
- `env_id` 固定为 `base_env`
- `supported_fault_modes` 固定为 `["normal"]`
- 不依赖单独 YAML 文件
- 当 `legacy_root` 不可访问或解析失败时，不注册该环境

## 3. 主要数据模型

代码位置：

- [models.py](../src/fake_homeassistant_v2/models.py)

关键模型：

- `DeviceDefinition`
  - 设备元数据、厂商、型号、实体列表
- `EntityDefinition`
  - `entity_id`、`domain`、`object_id`
  - `service_profile`
  - `links`
  - `actions`
  - 初始 `state` 和 `attributes`
- `ServiceDefinition`
  - `domain`
  - `service`
  - 字段 schema
  - target 规则
  - handler 绑定
  - 是否支持 `return_response`
- `StateRecord`
  - 当前状态快照
- `EventRecord`
  - 事件记录

## 4. 请求流转

### 4.1 查询状态

`GET /api/states` 或 `GET /api/states/{entity_id}`：

1. FastAPI 入口接收请求
2. 直接查询 `StateStore`
3. 返回状态快照

### 4.2 查询设备注册表

`GET /api/devices` 或 `GET /api/devices/{device_id}`：

1. FastAPI 入口接收请求
2. 查询 `RegistryStore`：
   - 全部设备：`list_devices()` 返回 `self.devices` 字典的所有值
   - 单个设备：`get_device(device_id)` 查找字典，不存在则抛出 `NotFoundError`（404）
3. 对于 `/{device_id}`，附加查询 `StateStore` 获取该设备所有实体（`device.entities`）的当前状态
4. 返回设备定义（及可选的实体状态）

### 4.3 查询实体注册表

`GET /api/entities` 或 `GET /api/entities/{entity_id}`：

1. FastAPI 入口接收请求
2. 查询 `RegistryStore`：
   - 全部实体：`list_entities()` 返回 `self.entities` 字典的所有值
   - 单个实体：`get_entity(entity_id)` 查找字典，不存在则抛出 `NotFoundError`（404）
3. 返回实体定义（`EntityDefinition`），包含 `device_id`、`domain`、`device_class` 等元数据

注意：实体定义（`EntityDefinition`）与实体状态（`StateRecord`）是分离的。定义描述实体是什么，状态描述实体当前是什么状态。

### 4.4 执行 service

`POST /api/services/{domain}/{service}`：

1. API 层读取 payload
2. `ServiceEngine` 查找 `ServiceDefinition`
3. 校验必填字段
4. 触发 `call_service` 事件
5. 进入测试环境故障注入判定（如果当前有激活测试环境）
6. 未命中故障时执行 handler
7. handler 内部通过 `runtime.set_state()` 修改状态
8. `runtime.set_state()` 写入 `StateStore`
9. 触发 `state_changed` 事件
10. 返回变更后的状态列表，或在允许时返回 `service_response`

### 4.5 新增实体

`PUT /api/mock/entities/{entity_id}`：

1. 保存 `EntityDefinition` 到 `entities/`
2. 为该实体创建或补齐状态
3. 将最新状态落盘到 `state_store.json`

### 4.6 新增设备

`PUT /api/mock/devices/{device_id}`：

1. 保存设备定义到 `devices/`
2. 逐个保存设备中的实体定义到 `entities/`
3. 为这些实体补齐状态
4. 持久化状态

### 4.7 切换测试环境

`POST /api/mock/init_env`：

1. `TestEnvManager` 读取并校验 `env_id`
2. 目标环境可来自 YAML 或动态 `base_env`
3. 首次切换时保存原始环境快照
4. 清空当前运行时设备/实体/状态/事件
5. 写入目标环境的设备、实体、初始状态
6. 激活故障模式与故障规则

`POST /api/mock/original_env`：

1. 检查是否存在原始环境快照
2. 用快照覆盖当前运行时数据
3. 清理激活测试环境与故障模式状态

## 5. 持久化规则

### 会持久化的内容

- 设备定义
- 实体定义
- service 定义
- 当前状态
- 事件记录

### 触发时机

- 调用 service 改状态时，会立即写 `state_store.json`
- 调用 `/api/mock/devices/*` 或 `/api/mock/entities/*` 时，会立即写对应定义文件
- 手动触发事件时，会写 `events.json`

### 一个重要细节

`DELETE /api/states/{entity_id}` 当前只删除“状态快照”，不会删除实体定义。

这意味着：

- 如果实体定义仍然存在
- 服务重启并重新加载时
- 该实体状态会再次按定义补齐

## 6. 旧数据导入

代码位置：

- [importer.py](../src/fake_homeassistant_v2/importer.py)
- [legacy_parser.py](../src/fake_homeassistant_v2/legacy_parser.py)

导入器职责：

- 从 `fake_homeassitant_try/copied_data` 读取旧数据
- 导入设备注册表
- 导入实体注册表
- 导入状态快照
- 导入旧 service 描述
- 把部分旧版硬编码行为转成 `links + actions`

`legacy_parser` 职责：

- 提供统一的 legacy 解析入口 `parse_legacy_data()`
- 产出可复用的 `devices/entities/states/services_payload`
- 对设备和实体输出做稳定排序（`device_id`、`entity_id`），保证测试可重复
- 同时被两处复用：
  - 启动阶段旧数据导入（`import_legacy_data`）
  - `TestEnvManager` 动态构建 `base_env`

导入触发条件：

- 运行时 `entities/` 目录为空
- 且配置了可用的旧数据目录

## 7. 扩展点

当前项目支持三层扩展：

1. 声明式扩展
   - 新增设备定义、实体定义、service 定义
2. action 扩展
   - 在实体定义里声明简单联动和副作用
3. handler 扩展
   - 用 Python 实现复杂业务逻辑

推荐优先级：

1. 先尝试声明式
2. 再考虑 actions
3. 最后再写 Python handler

详细步骤见：

- [EXTENDING.md](EXTENDING.md)
