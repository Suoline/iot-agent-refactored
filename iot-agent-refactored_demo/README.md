# iot-agent-refactored_demo

## 项目简介
本项目是一个面向智能家居场景的 Agent 实验与评测代码库。
核心目标是让智能体围绕自然语言指令完成设备筛选、任务规划、动作执行，并通过测试集评估结果表现。

## 目录说明
- `smartHome/m_agent/agent`：智能体主流程与工具编排（路由、筛选、规划、执行）。
- `smartHome/m_agent/common`：公共配置与基础能力（日志、LLM 配置读取、全局配置）。
- `smartHome/m_agent/test`：测试集、基线对比与测试结果处理。
- `smartHome/m_agent/memory`：模拟 Home Assistant API 客户端。

## 环境要求
本地运行前必须使用 Conda 环境 `mySmart_env`。
本仓库当前不做代码级环境强校验，按文档约束执行。

## 快速开始

在仓库根目录执行：

```powershell
conda activate mySmart_env
cd iot-agent-refactored_demo
python smartHome/m_agent/test/test_home_agent_v2/test_runner.py
```

说明：
- 前提：需先启动模拟 Home Assistant 后端（见 `homeassitant_demo/` 目录）
- 测试入口支持中断续跑，可通过 Ctrl+C 随时中断

## 配置说明
- 配置文件：`smartHome/m_agent/common/llm_config.ini`
- 包含 LLM 提供商（newapi／uniapi／deepseek 等）、API Key、模型名
- 包含 Home Assistant 连接配置
- 包含 LangSmith 追踪配置
- **不要提交真实密钥到 Git**

---

## 隐私处理机制

### 概述

当使用 `codec_home_agent.py` 中的 `run_codec_Agent()` 运行智能体时，所有发送给 LLM 的消息会在调用前先经过**隐私编码**，将敏感信息替换为 `@语义名@` 形式的占位符；LLM 返回后再**解码**还原原始值。

核心思路：**LLM 不知道真实数据是什么，只知道数据的语义类型**（如"某个 entity_id""某个 IP 地址"）。

### 两阶段编码管道

**阶段一：本地正则规则（无 LLM 调用，零延迟）**

五条正则模式匹配明确格式的敏感字段，直接替换为 `@semantic_name@` 令牌：

| 匹配内容 | 模式 | 替换为 |
|---------|------|--------|
| IPv4 地址 | `(\d{1,3}\.){3}\d{1,3}` | `@ip_address@` |
| ISO 8601 时间戳 | `YYYY-MM-DDTHH:MM:SS+08:00` | `@timestamp@` |
| 日期 | `YYYY-MM-DD` | `@date@` |
| 32 位十六进制 ID | `[0-9a-fA-F]{32}` | `@context_id@` |
| entity_id | `domain.object_id` 格式 | `@entity_id@` |

**阶段二：LLM 兜底识别（按需触发）**

本地规则无法覆盖的自然语言敏感内容（如 WiFi SSID、用户姓名、设备位置描述），通过一次批量 LLM 调用来识别。LLM 返回一个 `{"原始值": "语义名"}` 的 JSON 映射，合并到全局令牌表。

`_should_use_llm` 决定是否触发：
- 文本已有本地变更——且不含敏感关键词（wifi、ssid、token、密码等）→ 跳过
- 文本为空或已是纯令牌 → 跳过
- 其余情况 → 触发 LLM

### 全局令牌映射与缓存

| 结构 | 作用 |
|------|------|
| `_ENCODE_MAP` | 原始值 → `@token@`，全局唯一 |
| `_DECODE_MAP` | `@token@` → 原始值，ENCODE_MAP 的逆 |
| `_TEXT_CACHE` | 原文 → 编码后文本，命中则跳过所有处理 |

关键行为：
- **同一原始值永远映射到同一令牌**——多轮对话中令牌一致
- **同一原文命中缓存**——不重复调用 LLM
- **批量编码**：`encode_messages()` 先收集所有消息中的全部字符串，去重后一次 LLM 调用处理整批
- 令牌冲突自动重编号：不同原始值被映射为同一语义名时，追加 `_02`、`_03` 后缀

### 完整数据流

```
用户任务: "关闭 climate.test_bedroom_ac_main，WiFi: shuashua"
    │
    ▼
┌─ before_model hook: log_before() ─────────────────────┐
│  encode_messages(messages)                             │
│    ├─ _collect_strings → 扁平化所有字符串               │
│    ├─ _apply_local_rules → entity_id 变 @entity_id@     │
│    ├─ _should_use_llm → "shuashua" 需 LLM              │
│    └─ _llm_mapping_for_texts → {"shuashua":"wifi_ssid"}│
│  state.messages = 编码后消息                            │
└────────────────────────────────────────────────────────┘
    │
    ▼
  LLM 看到: "关闭 @entity_id@，WiFi: @wifi_ssid@"
    │
    ▼
┌─ after_model hook: log_response() ────────────────────┐
│  transform_messages(messages, decode_text)              │
│    ├─ _replace_from_map → @entity_id@ 还原             │
│    └─ _evaluate_arithmetic → 支持安全算术求值           │
│  state.messages = 解码后消息（下游代码无感知）           │
└────────────────────────────────────────────────────────┘
```

### 安全算术求值

解码时 `@token@` 可参与简单算术（如 LLM 输出 `@brightness@/3`），通过 AST 白名单求值：
- 允许：`+` `-` `*` `/` 及一元 `+`/`-`
- 禁止：函数调用、属性访问、导入等危险操作
- 排除日期/时间格式避免误解析

### 如何启用

```python
from smartHome.m_agent.agent.codec_home_agent import run_codec_Agent

result = run_codec_Agent("关闭卧室灯")  # 自动启用隐私编码
```

或手动：

```python
from smartHome.m_agent.common.global_config import GLOBALCONFIG
GLOBALCONFIG.privacy_protection_enabled = True
# 之后所有 agent 调用自动走编码/解码
```

### 代码入口（推荐阅读顺序）

| 阅读顺序 | 文件 | 看什么 |
|---------|------|--------|
| 1 | `agent/codec_home_agent.py` | 一行开关 `privacy_protection_enabled = True` |
| 2 | `agent/hooks/langchain_middleware.py` | `log_before`（编码钩子）、`log_response`（解码钩子） |
| 3 | `agent/utils/privacy_codex.py` | `encode_messages()`、`decode_text()`、本地规则、缓存、批量编码 |
| 4 | `agent/utils/llm_privacy_handler.py` | `LLMPrivacyHandler` 类：LLM 调用、映射解析、算术求值 |
| — | `common/global_config.py` | `privacy_protection_enabled` 开关 |
| — | `test/test_code/test_privacy_codex.py` | 单元测试：编码/解码、缓存命中、批量调用 |

### 隐私 LLM 配置

隐私编码使用的 LLM 固定读取 `[deepseek]` 配置节，**不受 `selected_llm_provider` 影响**。如需更换，修改 `_get_privacy_handler()` 中读取的配置节名。

---

## 注意事项
- 项目包含对 Home Assistant 接口能力的调用，默认存在本地接口依赖（`127.0.0.1:8123`）。
- `_TEXT_CACHE` 在进程生命周期内持续增长不淘汰，重启后清空。
- 常见启动失败排查：
  - `ModuleNotFoundError`：确认已激活 `mySmart_env` 且依赖已安装。
  - 接口连接失败（如连接 `127.0.0.1:8123` 被拒绝）：确认本地服务可达。
  - 配置读取异常：检查 `llm_config.ini` 各配置节与键名是否完整。
