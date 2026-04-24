# Fake Home Assistant v2

一个可扩展的 Home Assistant API 模拟器，目标是：

- 提供接近 Home Assistant 的 REST API 形态
- 支持通过声明式定义快速新增设备、实体和 service
- 在声明式不足时，允许补充 actions 或 Python handler

## 快速开始

在项目根目录执行：

```powershell
cd F:\coding_workspace\codex_workspace\homeassitant_demo
D:\anaconda\Scripts\pip.exe install -e .[test]
```

启动服务：

```powershell
cd F:\coding_workspace\codex_workspace\homeassitant_demo
D:\anaconda\python.exe -m fake_homeassistant_v2
```

或者：

```powershell
fake-ha
```

默认监听：

- `http://127.0.0.1:8123`

快速验证：

```powershell
curl http://127.0.0.1:8123/api/
curl http://127.0.0.1:8123/api/states
curl http://127.0.0.1:8123/api/services
```

## 运行时数据

- 默认运行时目录：`F:\coding_workspace\codex_workspace\homeassitant_demo\.fake_homeassistant`
- 首次启动且运行时目录为空时，会自动从 `fake_homeassitant_try/copied_data` 导入旧样本数据
- 如果设置 `FAKE_HA_TOKEN`，则所有 `/api/*` 和 `/api/mock/*` 请求都需要 `Authorization: Bearer ...`

## 文档导航

按下面顺序阅读：

1. [API 文档](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\API.md)
2. [测试环境总览](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\TEST_ENV_CATALOG.md)
3. [测试环境调用指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\TEST_ENVIRONMENTS.md)
4. [架构说明](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\ARCHITECTURE.md)
5. [扩展指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\EXTENDING.md)
6. [设计约定](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\DESIGN_RULES.md)

各文档职责：

- `README.md`: 项目用途、安装启动、运行目录、文档导航
- `docs/API.md`: 对外 API、调用方式、错误码、示例
- `docs/TEST_ENV_CATALOG.md`: 测试环境清单总览（环境、设备、空间、故障规则、A/B差异）
- `docs/TEST_ENVIRONMENTS.md`: 测试环境切换、故障模式、恢复原始环境、调用示例
- `docs/ARCHITECTURE.md`: 内部实现与运行机制
- `docs/EXTENDING.md`: 如何新增设备、实体、service、actions、handler
- `docs/DESIGN_RULES.md`: 设计取舍与扩展约束

## 代码入口

- `src/fake_homeassistant_v2/app.py`: FastAPI 入口
- `src/fake_homeassistant_v2/runtime.py`: 运行时核心，包含状态、注册表、事件和服务执行
- `src/fake_homeassistant_v2/importer.py`: 旧样本数据导入
- `src/fake_homeassistant_v2/data/services/base.yaml`: 默认 service 定义

## 关于 Skill

当前仓库先不内置专门的 Codex skill。

原因是这个阶段更适合先把长期有效的知识沉淀到项目文档里；等后续你确认会反复让 Codex 帮你扩展设备、实体和 service，再从 [扩展指南](F:\coding_workspace\codex_workspace\homeassitant_demo\docs\EXTENDING.md) 中提炼一个薄 skill，会更稳定，也更容易维护。
