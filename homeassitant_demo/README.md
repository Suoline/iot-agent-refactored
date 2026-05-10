# Fake Home Assistant v2

一个可扩展的 Home Assistant API 模拟器，目标是：

- 提供接近 Home Assistant 的 REST API 形态
- 支持通过声明式定义快速新增设备、实体和 service
- 在声明式不足时，允许补充 actions 或 Python handler

## 快速开始

推荐使用 conda 环境运行，环境文件已提供：`environment.yml`

环境名：`fake-homeassitant-env`

### 1. 创建并激活环境

macOS/Linux (Bash):

```bash
cd /path/to/homeassitant_demo
conda env create -f environment.yml
conda activate fake-homeassitant-env
```

Windows (PowerShell):

```powershell
cd <path-to>\homeassitant_demo
conda env create -f environment.yml
conda activate fake-homeassitant-env
```

如果环境已经存在，更新依赖：

macOS/Linux (Bash):

```bash
conda env update -f environment.yml --prune
```

Windows (PowerShell):

```powershell
conda env update -f environment.yml --prune
```

### 2. 启动服务

macOS/Linux (Bash):

```bash
python -m fake_homeassistant_v2
```

Windows (PowerShell):

```powershell
python -m fake_homeassistant_v2
```

或者（跨平台）：

```bash
fake-ha
```

默认监听：

- `http://127.0.0.1:8123`

### 3. 快速验证（smoke test）

```bash
curl http://127.0.0.1:8123/api/
curl http://127.0.0.1:8123/api/states
curl http://127.0.0.1:8123/api/services
```

### 4. 运行测试

macOS/Linux (Bash):

```bash
pytest -q
```

Windows (PowerShell):

```powershell
pytest -q
```

## 运行时数据

- 默认运行时目录：项目根目录下 `.fake_homeassistant`
- 首次启动且运行时目录为空时，会自动从 `fake_homeassitant_try/copied_data` 导入旧样本数据
- 如果设置 `FAKE_HA_TOKEN`，则所有 `/api/*` 和 `/api/mock/*` 请求都需要 `Authorization: Bearer ...`

## 文档导航

按下面顺序阅读：

1. [API 文档](docs/API.md)
2. [测试环境总览](docs/TEST_ENV_CATALOG.md)
3. [测试环境调用指南](docs/TEST_ENVIRONMENTS.md)
4. [架构说明](docs/ARCHITECTURE.md)
5. [扩展指南](docs/EXTENDING.md)
6. [设计约定](docs/DESIGN_RULES.md)

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

原因是这个阶段更适合先把长期有效的知识沉淀到项目文档里；等后续你确认会反复让 Codex 帮你扩展设备、实体和 service，再从 [扩展指南](docs/EXTENDING.md) 中提炼一个薄 skill，会更稳定，也更容易维护。
