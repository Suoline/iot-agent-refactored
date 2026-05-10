# `retryValidate_home_agent` 可靠性分析报告

## 1. 背景与样本范围

本报告基于以下四个文件做只读分析：

- `smartHome/m_agent/test/test_reliability_code/six_env_runner.py`
- `smartHome/m_agent/common/logs/nested_agent.log`
- `smartHome/m_agent/test/test_reliability_code/six_env_results.json`
- `smartHome/m_agent/agent/retryValidate_home_agent.py`

分析对象是 `run_validate_Agent()` 在六套环境中的执行链路，覆盖三类模式：

- `normal`
- `one_shot_network_error`
- `fake_success`

本次重点日志时间窗口集中在 `2026-05-04 21:54:35` 到 `2026-05-04 22:25:02`，主要对应“把空调温度调高一些”这一轮执行与验证过程，见 `smartHome/m_agent/common/logs/nested_agent.log:14-386`。

## 2. 结果概览

从 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:3-16` 看：

- 6/6 环境初始化成功。
- 6/6 环境恢复成功。
- 两个 `one_shot_network_error` 环境明确失败，`func_ok=false`，并记录了 `POST /api/services/climate/set_temperature` 的 HTTP 503，见 `six_env_results.json:35-52`。
- 两个 `normal` 环境和两个 `fake_success` 环境虽然没有抛异常，但返回内容经常是“无法判断”“缺少历史数据”“请继续提供 token / 历史曲线 / 下一步选择”等长篇交互式文本，而不是机器可消费的验证结论，见 `six_env_results.json:19-32`、`55-68`。

需要额外注意两点：

- `six_env_results.json` 顶层 `summary.func_ok` 为 `0`，但逐环境结果中实际有 4 个环境是 `func_ok=true`。这说明结果汇总本身也不稳定，不能单独依赖顶层 summary 判读真实表现，见 `six_env_results.json:3-16` 与 `:18-69`。
- 当前 runner 中的 `func_ok` 更像“回调是否抛异常”，并不等价于“验证成功”。这正是本轮可靠性分析的核心语义错位。

## 3. 关键问题、证据与改进方向

### 3.1 运行态结果依赖 `base_agent_result.json`，存在串扰和脏读风险

**问题**

`retryValidate_home_agent.py` 没有通过 graph state 传递主流程结果，而是从磁盘文件 `note/base_agent_result.json` 回读 `planning_result` 和 `filter_devices`。

**证据**

- `read_json_file()` 固定读取 `note/base_agent_result.json`，见 `smartHome/m_agent/agent/retryValidate_home_agent.py:18-34`。
- `node_verify()` 直接 `read_json_file("planning_result")` 与 `read_json_file("filter_devices")`，见 `retryValidate_home_agent.py:59-79`。
- 写入端不在验证 agent 内，而是在基础 agent 的 `tool_filter()` / `tool_planner()` 中通过 `update_json_file()` 落盘，见 `smartHome/m_agent/agent/base_home_agent.py:117-145`、`:244-255`、`:340-343`。

**影响**

- 同一进程内连续运行时，验证节点读取到的可能是上一次运行残留结果。
- 多环境循环或并发执行时，存在后写覆盖前写、验证阶段读到不属于本轮任务的结果的风险。
- 日志和结果里出现的大段“继续请选择下一步”“提供 token”内容，也可能被写回同一文件并被后续 run 误读。

**改进建议**

- 用 graph `state` 显式传递 `base_result`、`planning_result`、`filter_devices`。
- 移除 `retryValidate_home_agent.py` 对 `base_agent_result.json` 的运行时依赖；文件最多只保留为调试工件，而不是执行语义的一部分。

### 3.2 `node_run_base()` 丢弃主流程返回值，验证节点只能依赖副作用

**问题**

`node_run_base()` 调用了 `run_ourAgent()`，但完全没有把结果放回 state。

**证据**

- `result = run_ourAgent(task=state["command"])` 后，`update={}`，没有保存任何字段，见 `smartHome/m_agent/agent/retryValidate_home_agent.py:35-43`。
- `node_verify()` 注释里甚至保留了“从 state 中获取 planning_result 和 filter_devices”的旧思路，但实际实现已经退回文件读写，见 `retryValidate_home_agent.py:59-63`。

**影响**

- 验证节点无法确定主流程真正执行了什么。
- 运行链路变成“先调用基础 agent，再赌文件副作用是完整且最新的”，这会直接降低可测性和可复现性。

**改进建议**

- 让 `node_run_base()` 返回结构化执行结果，例如：
  - `base_result.success`
  - `base_result.planning_result`
  - `base_result.filter_devices`
  - `base_result.execution_trace`
- 让 `node_verify()` 只消费 state 中的结构化结果。

### 3.3 `run_check()` 不是专用验证器，而是再次调用通用 `run_ourAgent()`

**问题**

验证节点表面上是“校验”，实际上 `run_check()` 又重新调用了通用家居 agent，相当于把验证任务再次丢回“筛选-规划-执行”的总路由。

**证据**

- `run_check()` 只有一行核心逻辑：`run_ourAgent(task=task)`，见 `smartHome/m_agent/agent/retryValidate_home_agent.py:44-50`。
- 验证节点创建的 agent 只挂了 `run_check` 这个工具，见 `retryValidate_home_agent.py:81-96`。
- 日志显示进入验证节点后，立即再次出现 `home_路由节点 -> home_过滤节点 -> home_规划阶段 -> 执行计划阶段`，见 `smartHome/m_agent/common/logs/nested_agent.log:242-244` 以及后续整段重新规划/执行链路 `:244-386`。

**影响**

- 验证职责漂移成“再跑一遍 agent 并尝试解释结果”。
- 验证阶段会继承主 agent 的所有格式漂移、工具选择偏差和输出习惯。
- 当验证 prompt 本身复杂时，链路会不断膨胀，token 成本和失败面都同步扩大。

**改进建议**

- 把 `run_check()` 改成专用验证工具或专用验证 agent。
- 验证工具只允许做受控动作，例如：读取外围传感器、读取执行时间点、判定验证状态。
- 禁止在验证阶段再次调用通用 `run_ourAgent()`。

### 3.4 `one_shot_network_error` 没有显式重试，503 直接冒泡失败

**问题**

对单次网络错误没有恢复策略，瞬时 503 直接导致整个环境执行失败。

**证据**

- `six_env_results.json` 中两个 `one_shot_network_error` 环境都以相同 503 失败，见 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:35-52`。
- `retryValidate_home_agent.py` 内没有任何显式重试、退避或错误分类逻辑，见全文 `smartHome/m_agent/agent/retryValidate_home_agent.py:1-146`。

**影响**

- `one_shot_network_error` 这种本来最应该由 retry 吸收的错误，被直接暴露成任务失败。
- 六环境测试无法区分“不可恢复错误”和“可自动恢复的瞬时错误”。

**改进建议**

- 对 `HTTP 503`、`URLError`、超时等增加有限次重试。
- 增加指数退避或固定退避。
- 把最终状态区分为 `RETRYABLE_ERROR` 与 `NON_RETRYABLE_ERROR`，并写入验证结果。

### 3.5 `fake_success` 识别不稳定，且验证约束执行不严格

**问题**

`fake_success` 场景下，系统有时能发现“空调设定没有变化”，有时又只给出“无法判断”或继续向用户要历史数据；不同环境之间缺少一致判定。

**证据**

- `te_fake_success_pair_a_v1` 返回了相对明确的 `FAIL`，并指出空调当前设定仍为旧值，见 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:55-60`。
- `te_fake_success_pair_b_v1` 却只返回“无法判断”“请提供 History API / 截图 / SQL”，见 `six_env_results.json:63-68`。
- 验证 prompt 明确要求“不能直接查看设备自身状态”，见 `smartHome/m_agent/agent/retryValidate_home_agent.py:71-78`。
- 但日志里验证链路依然把 `climate.test_*` 实体重新纳入候选设备并读取 `climate` 自身状态，见 `smartHome/m_agent/common/logs/nested_agent.log:58-65`、`:242-243`。

**影响**

- 同一类故障在不同环境下的判定标准不一致。
- 验证规则既不稳定，也没有被严格约束在“只看外围传感器”。
- 最终使得 fake-success 场景既可能被识别为失败，也可能被模糊成“证据不足”。

**改进建议**

- 为 `fake_success` 设计确定性判定规则。
- 如果动作返回成功，但外围证据未发生预期变化，应返回 `FAIL` 或至少结构化 `UNKNOWN`，而不是开放式建议。
- 明确哪些信息允许作为辅助证据，哪些严格禁止读取。

### 3.6 验证输出是长篇对话文本，不是稳定的结构化结果

**问题**

当前返回值几乎都是面向用户的自然语言段落，不适合测试代码稳定消费。

**证据**

- `run_validate_Agent()` 最终只返回 `result["messages"][-1].content`，见 `smartHome/m_agent/agent/retryValidate_home_agent.py:130-141`。
- `normal` 与 `fake_success` 场景的 `func_result` 都是长篇解释性文本，见 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:19-32`、`:55-68`。
- 日志中验证输出反复出现“请选择一种”“告诉我你的选择”“我会继续完成验证”等用户交互式文案，见 `smartHome/m_agent/common/logs/nested_agent.log:382-386`。

**影响**

- `six_env_runner` 无法稳定判断“通过/失败/未知/可重试”。
- 自动化测试只能靠字符串模糊匹配，可靠性很低。

**改进建议**

- 引入明确的验证结果模型，例如：

```json
{
  "success": false,
  "verification_status": "UNKNOWN",
  "reason": "missing_history",
  "evidence": ["sensor.test_living_room_temperature_main current=26.0"],
  "next_action": "collect_history"
}
```

- 将“给用户的解释文案”和“给系统的结构化状态”分离。

### 3.7 上游 `tool_planner` 参数格式漂移，会放大验证链路脆弱性

**问题**

验证链路依赖基础 agent，而基础 agent 本身就存在参数格式漂移。

**证据**

- `tool_planner()` 签名要求 `devices: DeviceIdList`，见 `smartHome/m_agent/agent/base_home_agent.py:278-345`。
- 日志里先传了 list，触发 `Input should be a valid dictionary or instance of DeviceIdList`，随后才改成嵌套字典重试成功，见 `smartHome/m_agent/common/logs/nested_agent.log:215-219`。

**影响**

- 一旦验证阶段复用通用 agent，这类上游格式问题就会再次进入验证链路。
- 验证失败有时不是“验证逻辑失败”，而是“又撞上了一遍 planner 接口不稳定”。

**改进建议**

- 验证阶段不要复用通用 planner。
- 如果必须复用，至少在进入验证前把输入归一化成固定 schema。

### 3.8 约束要求与实际行为不一致：明令不能看设备自身状态，但结果仍混入 climate 自身字段

**问题**

验证 prompt 强调“不能直接查看设备自身状态”，但结果文本经常把 climate 自身 target/state 当作参考，形成规则与实现不一致。

**证据**

- 约束写在 `retryValidate_home_agent.py:71-78`。
- `six_env_results.json` 的 normal 场景直接写入了 `climate.test_living_room_ac_main target: 26.0 °C` 这类参考字段，见 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:19-31`。
- 日志中验证链路也重新读取了 `climate.test_living_room_ac_main` 和 `climate.test_bedroom_ac_main`，见 `smartHome/m_agent/common/logs/nested_agent.log:58-65`、`:220-243`。

**影响**

- 现在的验证既没有严格遵守“只看外围传感器”，也没有明确声明“允许用自身状态作辅助但不作结论依据”。
- 这会让测试目标定义本身变得模糊。

**改进建议**

- 明确验证协议。
- 若严格禁止，则从工具层屏蔽 climate 自身状态读取。
- 若允许辅助参考，则在结构化结果中单独标记 `forbidden_evidence_used=false/true`。

### 3.9 验证失败后仍输出追问式文案，而不是机器可处理的失败类型

**问题**

当验证证据不足时，当前实现会继续生成“请提供 token / 请选择 A/B/C”的后续对话，而不是封装成失败原因。

**证据**

- `normal` 场景中出现“请选择下一步操作”，见 `smartHome/m_agent/test/test_reliability_code/six_env_results.json:19-23`。
- `fake_success_pair_b_v1` 中继续要求用户提供 `History API`、截图或 SQL，见 `six_env_results.json:63-68`。
- 日志尾部同样反复输出“告诉我你选哪种后续方式”，见 `smartHome/m_agent/common/logs/nested_agent.log:382-386`。

**影响**

- 在六环境批量测试里，agent 不再像测试被测对象，而更像在和“人类用户”继续对话。
- 这会直接破坏 reliability runner 的自动化闭环。

**改进建议**

- 为验证链路设置禁止追问的输出约束。
- 证据不足时直接返回 `UNKNOWN` 和 `reason=missing_history` / `reason=missing_sensor`。

## 4. 建议的实现方向

按优先级建议后续实现至少覆盖以下几点：

1. 用 graph `state` 传递 `base_result`、`planning_result`、`filter_devices`，移除 `base_agent_result.json` 的运行时依赖。
2. 让 `node_run_base()` 返回结构化执行结果，`node_verify()` 直接消费该结果。
3. 把 `run_check()` 改成专用验证工具或专用验证 agent，禁止再次调用通用 `run_ourAgent()`。
4. 定义统一的验证结果模型，例如 `PASS / FAIL / UNKNOWN / RETRYABLE_ERROR`。
5. 区分“执行失败”和“验证失败”，再细分“可重试网络错误”“证据不足”“检测到假成功”。
6. 对 `503 / URLError / timeout` 增加有限次重试、退避和重试日志。
7. 对 `fake_success` 增加确定性规则：动作返回成功但外围证据未变化时，稳定落入 `FAIL` 或 `UNKNOWN`。
8. 给验证链路设置最大步数、超时和禁止用户追问的输出约束。
9. 让返回值能被 `six_env_runner` 稳定判定，例如始终包含 `success`、`verification_status`、`reason`。
10. 若继续要求“只用外围传感器”，就把 climate 自身状态读取从验证工具集中移除；否则显式放宽协议并标注证据类别。

## 5. 与六环境对齐的测试建议

后续修改完成后，至少应验证以下场景：

- `te_normal_pair_a_v1` / `te_normal_pair_b_v1`
  - 期望：执行成功，返回结构化 `PASS` 或按规则给出可解释的 `UNKNOWN`。
- `te_one_shot_network_error_pair_a_v1` / `te_one_shot_network_error_pair_b_v1`
  - 期望：第一次 503 被自动重试吸收，不再直接 `callback failed`。
- `te_fake_success_pair_a_v1` / `te_fake_success_pair_b_v1`
  - 期望：稳定识别“动作返回成功但环境未变化”的异常，不应被当作成功。
- 连续多次运行同一指令
  - 期望：不同 run 之间不会通过 `base_agent_result.json` 串扰。
- 验证失败输出
  - 期望：返回结构化状态，不再输出“请提供 token / 请选择下一步”式交互文案。

## 6. 结论

`retryValidate_home_agent.py` 当前最大的可靠性问题不是“重试次数不够”，而是验证职责、状态传递、失败分类和输出协议都还没有收敛。

从六环境日志和结果看，它目前更像“在主 agent 之后再触发一轮对话型 agent”，而不是“对主执行结果做一次可重复、可判定、可测试的验证”。要把它变成可靠测试链路，优先级最高的工作是：

- 去副作用化：从文件共享改成 state 传递。
- 去递归化：验证阶段不再回调通用 agent。
- 去对话化：返回结构化验证状态，而不是继续和用户对话。
- 去偶然化：为网络错误和 fake-success 引入稳定、可重复的判定规则。
