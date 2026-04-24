# iot-agent-refactored_demo

## 项目简介
本项目是一个面向智能家居场景的 Agent 实验与评测代码库。  
核心目标是让智能体围绕自然语言指令完成设备筛选、任务规划、动作执行，并通过测试集评估结果表现。

## 目录说明
- `smartHome/m_agent/agent`：智能体主流程与工具编排（如路由、筛选、规划、执行）。
- `smartHome/m_agent/common`：公共配置与基础能力（如日志、LLM 配置读取、全局配置）。
- `smartHome/m_agent/test`：测试集、基线对比与测试结果处理代码。

## 环境要求
本地运行前必须使用 Conda 环境 `mySmart_env`。  
本仓库当前不做代码级环境强校验，按文档约束执行。

## 快速开始（Windows / PowerShell）
在仓库根目录执行：

```powershell
conda activate mySmart_env
$env:PYTHONPATH=".;smartHome\m_agent\test\test_code"
python -m smartHome.m_agent.test.test_code.test_entry
```

说明：
- 上述命令使用测试入口脚本启动评测流程。
- 请确保你已在 `mySmart_env` 中安装本项目所需依赖（仓库当前未提供统一依赖锁定文件）。

## 配置说明
- 配置文件路径：`smartHome/m_agent/common/llm_config.ini`
- 需要按本地环境填写模型服务与 Home Assistant 相关配置。
- `llm_config.ini` 中包含敏感信息（如 API Key / Token），不要在 README 或提交记录中暴露真实密钥。

## 注意事项
- 项目包含对 Home Assistant 接口能力的调用，默认存在本地接口依赖（如 `127.0.0.1:8123` 场景）。
- 常见启动失败排查：
  - `ModuleNotFoundError`：确认已激活 `mySmart_env` 且依赖已安装。
  - 接口连接失败（如连接 `127.0.0.1:8123` 被拒绝）：确认本地服务可达或检查接口地址配置。
  - 配置读取异常（如 `llm_config.ini` 字段缺失）：检查配置节与键名是否完整。
