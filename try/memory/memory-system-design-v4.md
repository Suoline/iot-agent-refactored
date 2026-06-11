# SAE 记忆方案 v4：结构化记忆 + 生命周期闭环

> 本方案以 v3 为基础，消除冗余字段、明确正交职责、精简 schema，形成可工程落地的统一记忆系统设计。

## 1. 设计目标

在现有 `EntityFact` / `DeviceFact`、Chroma 检索、对话 `add/update/delete` 的基础上补齐四个关键能力:

1. **记忆过期**：不同类型记忆需要不同的有效期和衰减策略。
2. **记忆更新**：更新应区分新增、修正、合并、拆分、取代、失效，并保留旧版本的证据链。
3. **记忆置信度**：检索时系统不仅要知道"相关不相关"，还要知道"可信不可信""是否过时""是否被验证过"。
4. **死记忆治理**：长期未访问的记忆要么重采样要么淘汰。

最终目标：让 SAE 的记忆从"可检索文本片段"升级为"可验证、可过期、可演化的家庭知识层"。

## 2. 调研启发

- **Generative Agents** 按相关性、近期性和重要性共同排序，通过 reflection 总结零散经验为高层知识。
- **MemGPT / Letta** 把短期上下文和长期外部记忆分层管理。
- **Reflexion** 把失败轨迹总结成 episodic memory 用于下一次尝试。
- **CoALA / LangGraph memory** 用 semantic / episodic / procedural memory 分类。
- **Zep / Graphiti** 的时间知识图谱处理"曾经为真、现在失效"的事实。
- **Mem0** 从对话中动态抽取、合并和检索长期记忆。
- **FadeMem / SmartVector / Memory Worth** 提供置信度指数衰减、半衰期模型、hits+/hits- 价值追踪。

## 3. 总体架构

记忆层围绕 `K_ctx` 展开：

```text
K_ctx
├── K_fact       设备固有事实：状态、能力、实体绑定、服务 schema
├── K_local      设备局部上下文：别名、位置、房间、设备特定偏好
├── K_global     家庭全局上下文：用户通用偏好、家庭布局、跨设备关系
├── K_episode    执行经验：某次任务中用了哪些记忆、结果是否成功
└── K_procedure  稳定流程：反复出现的场景规则和自动化习惯
```

| 类型 | 例子 | 默认稳定性 | 用途 |
| --- | --- | --- | --- |
| `K_fact` | 设备有哪些实体、能否调亮度 | 高，但受 HA registry 变化影响 | 能力过滤、执行 grounding |
| `K_local` | "小书灯"指书房台灯 | 中高 | 设备 disambiguation |
| `K_global` | 睡觉时全屋灯应关闭 | 中 | 意图补全、规划约束 |
| `K_episode` | 上次卧室门锁执行失败，因为锁芯 jammed | 低到中 | 失败规避、解释和反思 |
| `K_procedure` | "观影模式"=关主灯、开氛围灯、拉窗帘 | 中高 | 复合任务规划 |

关键原则：

- **结构化记录是事实源，向量库只是索引**。Chroma 保存用于召回的文本和 metadata，真正的生命周期、置信度、版本关系保存在 canonical store 中。
- **当前状态不进入长期记忆**。"客厅灯现在是开着的"只进入运行期 perception，不长期沉淀；除非形成可复用规律。
- **执行前查记忆，执行后反写记忆质量**。每次任务不仅使用记忆，还要记录哪些记忆帮助了任务、哪些误导了任务。

## 4. 记忆条目 schema

统一的 `MemoryRecord`。`EntityFact` 和 `DeviceFact` 可以作为 payload 的一种。

```python
class EvidenceRef(BaseModel):
    # 证据来源类型：对话轮次、执行轨迹、外部文档或系统事件
    ref_type: Literal["turn", "trace", "doc", "event"]
    # 证据在对应来源中的唯一标识
    ref_id: str
    # 证据产生时间；无法确定时允许为空
    timestamp: datetime | None = None


class MemoryRecord(BaseModel):
    # 记忆记录唯一 ID，用于更新、引用和图关系连接
    memory_id: str

    # Scope：挂靠层级
    # 记忆附着的对象层级，决定它主要归属于哪一类实体
    scope: Literal["entity", "device", "room", "user", "home"]
    # 关联设备 ID；scope 不在 device/entity 时可为空
    device_id: str | None = None
    # 关联实体 ID，例如某个 light.xxx 或 sensor.xxx
    entity_id: str | None = None
    # 关联房间 ID，用于位置类和房间级上下文
    room_id: str | None = None
    # 关联用户 ID，用于个人偏好和个人习惯
    user_id: str | None = None

    # Type：记忆语义类型
    # 记忆语义分类，决定默认半衰期、更新策略和使用场景
    memory_type: Literal[
        # 设备或实体具备的稳定能力、可调用动作或服务 schema
        "capability",
        # 用户给设备、实体或场景起的别名/口语称呼
        "alias",
        # 设备、实体或物品所在位置
        "location",
        # 用户明确表达或长期体现出的偏好
        "preference",
        # 在重复行为中观察到的使用习惯
        "habit",
        # 执行任务时必须满足的约束条件
        "constraint",
        # 可复用的多步骤场景流程或自动化组合
        "routine",
        # 一次具体执行经历，包括过程、结果和上下文
        "episode",
        # 从多次 episode 中总结出的经验、教训或策略
        "reflection",
        # 空间布局、设备之间的相对关系或拓扑关系
        "layout_relation",
        # 与安全、风险控制、误操作防护相关的规则
        "safety_rule",
        # 值得长期保存的稳定状态事实，而非瞬时当前状态
        "stable_state_fact",
    ]

    # Content
    # 三元组主语，通常是设备、实体、房间、用户或抽象场景名
    subject: str
    # 三元组谓词，描述关系或属性，例如 located_in / prefers / can_do
    predicate: str
    # 三元组宾语，描述主语的目标值、对象或事实内容
    object: str
    # 生效条件；用于表达“在什么前提下这条记忆成立”
    condition: str | None = None
    # 可执行动作描述；适用于 routine / procedure / capability 等类型
    action: str | None = None
    # 面向检索和展示的自然语言表述
    natural_text: str
    # 结构化扩展负载，保存 schema、参数、统计信息等细节
    structured_payload: dict = {}

    # Evidence（统一引用）
    # 该记忆的主要来源，用于映射来源先验权威度
    source: Literal[
        "ha_registry",
        "ha_state_observation",
        "user_explicit",
        "user_correction",
        "user_behavior",
        "execution_verification",
        "llm_inference",
        "imported_doc",
    ]
    # 支撑该记忆成立的证据引用列表，可同时关联多个来源
    evidence_refs: list[EvidenceRef] = []

    # Confidence
    # 初始置信度；通常由 source 对应的先验权威度给出
    confidence: float
    # 重要性权重；影响保留、反思和淘汰优先级
    importance: float = 0.5
    # 半衰期天数；用于计算有效置信度随时间的衰减
    half_life_days: int
    # 被任务验证为“有帮助/正确”的累计次数
    positive_hits: int = 0
    # 被任务证明“无帮助/错误”的累计次数
    negative_hits: int = 0

    # Time
    # 记忆首次创建时间
    created_at: datetime
    # 最近一次内容或状态更新时间
    updated_at: datetime
    # 最近一次被检索或被任务消费的时间
    last_accessed_at: datetime | None = None
    # 事实被观察到的时间；适合外部观测类记忆
    observed_at: datetime | None = None
    # 事实开始生效的时间
    valid_from: datetime | None = None
    # 事实停止生效的时间；适合时间窗口型记忆
    valid_until: datetime | None = None

    # Status（唯一生命周期状态）
    # 当前生命周期状态，驱动检索优先级和治理动作
    status: Literal[
        # 候选记忆：已抽取但证据还不足，默认不直接高权重参与执行
        "candidate",
        # 生效记忆：当前可信且可用于检索、规划和执行
        "active",
        # 陈旧记忆：未必错误，但置信度已衰减，需要复核或重新验证
        "stale",
        # 冲突记忆：与其他高相关记忆存在矛盾，等待消解
        "conflicted",
        # 被取代记忆：曾经有效，但已被更新版本替换
        "superseded",
        # 失效记忆：事实已不再成立，不应再参与执行
        "expired",
        # 归档记忆：暂不参与执行，但保留用于解释、审计或后续恢复
        "archived",
        # 删除记忆：显式移除，仅保留最小审计痕迹
        "deleted",
    ] = "candidate"
    # 本条记忆取代了哪些旧记忆
    supersedes: list[str] = []
    # 本条记忆被哪条新记忆取代
    superseded_by: str | None = None
    # 与哪些记忆存在内容冲突，待裁决或消解
    conflicts_with: list[str] = []

    # Audit
    # 被访问总次数，用于死记忆治理和价值评估
    access_count: int = 0
    # 被更新总次数，用于判断事实稳定度和演化频率
    update_count: int = 0
    # 最近一次使用该记忆的任务 ID，便于追踪影响范围
    last_used_task_id: str | None = None
```

### 4.1 V4 schema 变更说明

| 变更 | 原因 |
| --- | --- |
| 删除 `layer` 字段 | 与 `status` 重复。检索分层由 status 派生：active/stale → 优先召回，superseded/expired/archived → 仅审计 |
| `scope` 只保留挂靠层级 | `routine`/`episode` 从 scope 移除，它们是 `memory_type` 的职责 |
| 删除 `state` memory_type | 当前状态走 perception 不入长期记忆；需要长期保存的稳定状态事实用 `stable_state_fact` |
| 合并 evidence 字段为 `evidence_refs: list[EvidenceRef]` | 原 `evidence_ids` / `source_turn_id` / `source_trace_id` 三字段合一，统一引用模型 |
| 删除 `source_authority` | 可由 `source` 查来源先验表得到，不必每条记忆都存 |
| 删除 `volatility` | 已有 `half_life_days` 精确表达衰减速度，volatility 只是冗余标签 |
| 删除 `expires_at` | 硬失效用 `status=expired` 表达；时间窗口失效用 `valid_until` |
| 删除 `related_memory_ids` / `depends_on` / `derived_from` | 关系统一由 `MemoryEdge` 管理，避免双写不一致 |

### 4.2 存储方案

> Todo：标签过滤 /关键词匹配/ 向量数据库不用？1 关系图也不用

```text
memory_records.jsonl       # canonical store（事实源）
memory_edges.jsonl         # 关系图
memory_embeddings/chroma   # 检索索引（缓存，非权威）
memory_events.jsonl        # 追加式审计日志
```

后续可替换为 SQLite/Postgres。

## 5. 记忆过期机制

过期分三种：

1. **硬过期**：事实已不应再用于执行。例如设备从 HA registry 移除。触发 `status=expired`。
2. **软过期**：可信度随时间下降。通过 `effective_confidence` 衰减自然体现。
3. **时间窗口失效**：事实只在某段时间有效。通过 `valid_until` 控制。

### 5.1 默认半衰期

| 记忆类型 | 默认半衰期 | 硬过期条件 |
| --- | --- | --- |
| `capability` / `stable_state_fact` | 180 天 | HA registry/entity/service schema 改变 |
| `alias` | 365 天 | 用户明确改名或设备消失 |
| `location` / `layout_relation` | 365 天 | 用户明确移动设备或 HA registry 对应关系变化 |
| `preference` | 180 天 | 用户明确反悔，或长期行为持续相反 |
| `habit` | 90 天 | 新行为证据持续冲突 |
| `routine` | 180 天 | 执行失败率过高或用户撤销 |
| `episode` | 30 天 | 被总结为 reflection/procedure 后可归档 |
| `reflection` | 90 天 | 后续验证无用或被新反思取代 |

注：`ha_state_observation` 类型的当前状态只走 perception，不入 MemoryRecord。

### 5.2 有效置信度（动态计算，不存储）

```text
age_days = now - max(updated_at, observed_at, created_at)
decay = 2 ^ (-age_days / half_life_days)
hit_score = (positive_hits + 1) / (positive_hits + negative_hits + 2)

effective_confidence =
    confidence * decay * (0.7 + 0.3 * hit_score)
```

- 时间越久，置信度自然下降。
- 经常被验证正确的记忆下降慢。
- 被证伪过的记忆即使语义相关，也会被降权。

### 5.3 生命周期状态机

todo：证据足够是怎样才算呢？

```text
candidate
  ├─ 证据足够/用户确认 → active
  └─ 长期未确认 → archived

active
  ├─ effective_confidence < 0.45 → stale
  ├─ 与高置信新事实冲突 → conflicted
  ├─ 被新事实替代 → superseded
  ├─ valid_until 过期 或 HA 权威源失效 → expired
  └─ 用户删除 → deleted

stale
  ├─ 重新验证 → active
  ├─ 长期不用 → archived
  └─ 被证伪 → superseded / expired

conflicted
  ├─ 用户确认其中一条 → active + superseded
  └─ 低风险场景按高置信事实临时使用

archived
  ├─ 被重新召回并验证 → active
  └─ 达到清理周期 → deleted
```

注：`expired` 和 `archived` 默认不参与执行规划，但可参与解释和审计。

## 6. 置信度机制

### 6.1 来源先验表

`source_authority` 不存储在每条记忆中，而是由 `source` 字段查表得到：

| 来源 | 权威度 | 说明 |
| --- | --- | --- |
| `ha_registry` | 0.95 | 设备和实体绑定的权威来源 |
| `user_correction` | 0.95 | 用户纠错优先级最高 |
| `user_explicit` | 0.90 | 用户明确说出的偏好、别名、位置 |
| `execution_verification` | 0.85 | 执行后环境验证得到的结论 |
| `ha_state_observation` | 0.80 | 当前状态可信，但通常不长期保存 |
| `user_behavior` | 0.65 | 从行为推断的习惯，需要多次观察 |
| `imported_doc` | 0.70 | 外部文档，需看来源质量 |
| `llm_inference` | 0.45 | 只作为 candidate，不应直接执行 |

新记忆的初始 `confidence` 默认取来源权威度。

### 6.2 置信度更新

执行后根据结果反写。采用**不对称权重**：

```text
任务成功且该记忆被用到：
  positive_hits += 1
  α_pos = 0.04
  confidence = min(0.99, confidence + α_pos * (1 - confidence))

任务失败且该记忆直接导致失败：
  negative_hits += 1
  α_neg = 0.20                 # α_neg / α_pos = 5 倍，强不对称
  confidence = max(0.01, confidence - α_neg)
  status = "stale" 或 "conflicted"

用户明确纠错：
  old_memory.status = "superseded"
  old_memory.superseded_by = new_memory.memory_id
  new_memory.confidence = 0.95

只是长期没用：
  不修改原始 confidence
  只通过 effective_confidence 自然衰减
```

### 6.3 Memory Worth（MW）四档分类

MW = `(positive_hits + 1) / (positive_hits + negative_hits + 2)`，动态计算，不存储。

| MW 值 | 分类 | 操作 |
| --- | --- | --- |
| > 0.8 | 高价值 | 提升检索优先级；半衰期 ×1.5 延长；可在高风险场景直接使用 |
| 0.4 – 0.8 | 正常 | 标准检索与执行流程 |
| 0.2 – 0.4 | 可疑 | 标记 `needs_verification`，下次使用前追问或交叉验证 |
| < 0.2 | 低价值 | 进入清理候选名单 |

MW 与 `effective_confidence` 互补：confidence 反映"我们相信它正确的程度"，MW 反映"它历史上是否真的有用"（经常用的程度）。两者都低才允许丢弃。

### 6.4 使用阈值

| 场景 | 执行阈值（置信度 | 低于阈值时 |
| --- | --- | --- |
| 查询型任务 | 0.45 | 标注不确定性即可 |
| 普通控制任务 | 0.70 | 追问或选择保守动作 |
| 安全相关任务 | 0.85 | 必须确认 |
| 自动化/持久任务 | 0.85 | 必须确认触发条件和退出条件 |

## 7. 更新机制

todo：那么更新时是每段交互后就更新，还是执行完一次任务后就更新，还是每天更新呢？

那么更新时的实现应该是如何呢？比如用户让关客厅灯，检索出客厅灯是灯1，但实际上已经变成灯2了，然后反馈更改。所以得检索出记忆后，就得在使用这些记忆时补上记忆来源ID。

| 操作 | 触发条件 | 行为 |
| --- | --- | --- |
| `add_candidate` | LLM 推断、单次行为观察 | 写入 candidate，不直接用于执行 |
| `add_active` | 用户明确陈述、HA 权威事实 | 写入 active |
| `merge` | 新旧事实兼容，只是补充细节 | 合并 payload，保留 evidence；触发特征吸收警告检查 |
| `revise` | 同一事实局部修正 | 新版本替代旧版本，旧版本标记 superseded |
| `invalidate` | 事实不再成立 | 标记 expired 或 superseded，不物理删除 |
| `split` | 一条记忆过宽，导致频繁部分匹配 | 拆成多条更细粒度记忆 |
| `delete` | 用户要求删除或隐私清理 | 逻辑删除，并从向量索引移除 |

### 7.1 对话后写入流程

todo：对于第三点如果只是用数据库存储，那么如何找出相似、如何定义相似，：3. MemoryMatcher 查找已有相似或同主语同谓词事实

```text
1. MemoryExtractor 从当前对话抽取候选事实
2. MemoryResolver 判断 scope：device/entity/room/user/home
3. MemoryMatcher 查找已有相似或同主语同谓词事实
4. MemoryUpdatePolicy 决定 add/merge/revise/invalidate/split/delete
5. MemoryWriteGate 检查来源、风险、置信度
6. 写 canonical store
7. 同步更新 Chroma 索引 metadata
```

### 7.2 冲突处理

todo：这个公式里面的几个计算的分数来源于哪里呢？

> **所以这 4 项本质上分别来自**
>
> - source_authority：source 字段 + 来源权威映射表
> - effective_confidence：置信度字段 + 时间字段 + hits 字段
> - recency_score：时间字段
> - evidence_count_score：evidence_refs
>
> 如果再说得更直白一点：
>
> - source_authority 问的是：“这类来源天生靠谱吗？”
> - effective_confidence 问的是：“这条具体记忆现在还靠谱吗？”
> - recency_score 问的是：“这条信息新不新？”
> - evidence_count_score 问的是：“支撑它的证据够不够多？”

```text
conflict_score =
    source_authority(source) * 0.40
  + effective_confidence * 0.30
  + recency_score * 0.20
  + evidence_count_score * 0.10
```

处理规则：

- `user_correction` > `user_explicit` > `user_behavior` > `llm_inference`。
- HA registry 对"设备是否存在、实体绑定、服务 schema"有最高权威。
- 用户对"偏好、别名、房间叫法"有最高权威。
- 两条高置信事实互相冲突 → `conflicted`，下次使用前追问。
- 低置信 candidate 与 active 冲突 → 只记录 candidate，不影响执行。

### 7.3 合并时的特征吸收警告

```text
若两条记忆 M1、M2 满足：
- subject/predicate 相同
- object 存在子集/超集关系（如 "客厅灯" vs "客厅顶灯"）
- 或 condition 不完全重叠

则不能直接 merge。应：
  - 保留两条记忆并建立 specializes/generalizes 关系边（MemoryEdge）
  - 或触发 split：把宽泛的那条拆细
  - 仅在两条 object 完全等价时才允许 merge
```

merge 操作必须记录 `merged_from` 列表与每条源记忆的 `coverage_proof`，否则 maintenance job 会回滚此次合并。

### ~~7.4 级联更新~~

~~智能家居记忆有关系图，更新一条事实可能影响其他事实：~~

- ~~设备位置改变：相关 room clue、routine、layout relation 都要重新评估。~~
- ~~设备能力改变：依赖该能力的 routine 降级为 stale。~~
- ~~用户偏好改变：相关 habit candidate 降权。~~
- ~~执行失败：只惩罚直接相关记忆。~~

~~级联关系通过 `MemoryEdge` 查询（见 §9），不在 MemoryRecord 中冗余存储。~~

~~涟漪传播仅对**负向证据**触发：~~

```text
propagated_penalty = direct_penalty * 0.3 ^ graph_distance
max_distance = 2
direction: only on negative-evidence updates
```

~~正向 hits 不沿图传播，避免一次成功污染整个邻域。~~

## 8. 检索与规划调用

### 8.1 检索流程

```text
输入：用户任务 Q

1. TaskAnalyzer 判断任务需要哪些记忆类型
   - capability/stable_state_fact
   - alias/location/layout
   - preference/habit/constraint
   - routine
   - episode/reflection

2. ScopeFilter 先用 HA 和 K_fact 做硬过滤
   - 只保留理论上能执行任务的设备

3. StatusFilter 按 status 分层召回
   - 优先 active/stale（可用层）
   - superseded/expired/archived 不参与执行，只用于解释

4. ContextRetriever 查 K_local / K_global
   - 解析"卧室""床边""小书灯""观影模式"等线索

5. TemporalConfidenceFilter 过滤
   - status 必须 active 或可用 stale
   - valid_until 未过期
   - effective_confidence 达到场景阈值

6. EvidenceRanker 排序并返回证据包

7. AmbiguityGate 判断是否追问
   - top1 与 top2 分差太小
   - top1 置信度不足
   - 安全相关动作缺少确认
```

### 8.2 排序公式

```text
retrieval_score =
    0.30 * semantic_relevance
  + 0.20 * scope_match
  + 0.20 * effective_confidence
  + 0.10 * recency_score
  + 0.10 * importance
  + 0.10 * memory_worth
```

注：`memory_worth` 由 `(positive_hits + 1) / (positive_hits + negative_hits + 2)` 动态计算。

不同任务可以调权重：

- 查能力：提高 `K_fact` 和 `scope_match` 权重。
- 查别名/位置：提高 `K_local` 和 `semantic_relevance` 权重。
- 查习惯：提高 `confidence`、`positive_hits` 和 `recency` 权重。
- 查自动化：必须同时满足 routine 条件、约束和安全阈值。

### 8.3 返回给 planner 的结构

```json
{
  "candidate_devices": [
    {
      "device_id": "device_123",
      "name": "书房台灯",
      "score": 0.86,
      "confidence": 0.91,
      "matched_memories": [
        {
          "memory_id": "mem_alias_001",
          "type": "alias",
          "text": "用户把书房台灯称为小书灯",
          "confidence": 0.95,
          "status": "active"
        }
      ],
      "missing_info": []
    }
  ],
  "global_constraints": [
    {
      "memory_id": "mem_pref_009",
      "text": "用户睡觉时希望全屋灯关闭",
      "confidence": 0.88
    }
  ],
  "should_ask_user": false,
  "ask_reason": null
}
```

### 8.4 Chroma metadata

Chroma 中缓存的 metadata（检索后必须回 canonical store 复算动态值）：

```json
{
  "memory_id": "mem_001",
  "device_id": "device_123",
  "entity_id": "light.bedside",
  "scope": "device",
  "memory_type": "alias",
  "status": "active",
  "confidence": 0.95,
  "valid_from": "2026-05-16T00:00:00+08:00",
  "valid_until": null,
  "source": "user_explicit",
  "sensitive": false
}
```

注：`effective_confidence` 和 `memory_worth` 不存入 Chroma，检索后动态计算。

## 10. 执行后反馈和反思记忆

### 10.1 每次任务记录 Usage Event

```python
class MemoryUsageEvent(BaseModel):
    task_id: str
    memory_id: str
    used_stage: Literal["device_filter", "constraint_filter", "planning", "execution", "verification"]
    contribution: Literal["helpful", "neutral", "misleading", "unknown"]
    outcome: Literal["success", "partial_success", "failure"]
    verification_delta: float | None = None
    note: str
```

这些 event 用来更新 `positive_hits` / `negative_hits`。

### 10.2 失败后生成 reflection

触发条件：
- 设备选择错误
- 服务调用失败
- 执行后状态未达到目标
- 用户说"不对，不是这个灯"

写入字段：
- failure_type
- wrong_assumption
- corrected_fact
- affected_memory_ids（通过 MemoryEdge 关联）
- future_rule

Reflection 默认进入 `K_episode` 或 `K_procedure`：
- 单次失败：`memory_type=reflection`，半衰期 90 天。
- 多次重复验证：升级为 `memory_type=routine` 或 active preference。

## 11. 候选记忆隔离

LLM 推断和单次行为观察不能直接变成 active 记忆。candidate 状态的记忆不参与执行规划。

candidate → active 的条件：
- 用户明确确认
- 或同类行为重复出现 N 次
- 或执行验证连续成功
- 或 HA registry/状态观测提供权威证据

默认阈值：

| 类型 | 升级条件 |
| --- | --- |
| `alias` | 用户明确说"以后叫它 X" |
| `habit` | 7 天内出现 3 次相同行为，且无反例 |
| `preference` | 用户明确表达，或行为规律 + 追问确认 |
| `routine` | 用户确认自动化规则 |
| `reflection` | 失败后即可 active，但只影响检索排序，不直接控制设备 |

## 12. 维护任务

> Todo：看是不是能放到任意查询里面，来处理类似过期的记忆。或者还是定时。
>
> 那还是定时，避免影响延迟

建议每次会话开始或每天定时执行：

1. **过期扫描**：处理 `valid_until`、HA registry 变化 → 标记 `status=expired`。
2. **置信度衰减**：计算 effective confidence，active 低于阈值进入 stale。
3. **冲突扫描**：同 scope、同 subject/predicate 的高冲突事实进入 conflicted。
4. **重复合并**：高相似、同来源、同含义记忆合并（触发 §7.3 特征吸收警告检查）。
5. **死记忆处理**：长期未访问且低置信的记忆归档（见 §12.1）。
6. **索引刷新**：canonical store 变化后同步 Chroma。
7. **评测日志汇总**：统计 stale recall、wrong device selection、clarification turn。

### 12.1 死记忆检测与重采样

**判定条件**：

```text
is_dead = (access_count == 0) AND (age_days > half_life_days)
       OR (last_accessed_at is None AND age_days > 2 * half_life_days)
```

**处理策略**：

```text
1. 描述诊断
   - 检查 natural_text 是否过于模糊（语义嵌入 entropy 高、与同类记忆区分度低）
   - 若是 → 进入步骤 2（重采样）
   - 若否 → 进入步骤 3（内容老化）

2. 重采样（resampling）
   - 用 LLM 基于 structured_payload + 关联记忆重写更具体的 natural_text
   - 重置 last_accessed_at，给一次新的曝光机会
   - 标记 resampled=True，下次检索时若仍未命中则直接降级

3. 内容老化
   - status 降级为 stale → archived
   - 若 MW < 0.2 或 effective_confidence < 0.3 → 进入清理候选
   - 若 sensitive=true → 标记 needs_review，由人审核

4. 无法判断
   - 标记 needs_review，进入人工/批量审核队列
```

## 13. 新增模块结构

```text
memory/
├── canonical_store.py      # JSONL/SQLite/Postgres，保存 MemoryRecord
├── edge_store.py           # MemoryEdge 存储与查询
├── vector_index.py         # Chroma wrapper，只做索引
├── update_policy.py        # add/merge/revise/invalidate/split/delete
├── confidence.py           # effective_confidence、MW 计算、hits 更新
├── expiration.py           # 半衰期、状态迁移
├── evidence.py             # evidence package 构造
├── usage_tracker.py        # 执行后反馈
├── maintenance.py          # 周期性清理、合并、冲突扫描、死记忆检测
├── resampling.py           # 死记忆重采样
└── schemas.py              # MemoryRecord/EvidenceRef/MemoryEdge/UsageEvent
```

### 13.1 工具接口

```text
memory_search(query, scope, memory_types, min_confidence, include_stale=False)
memory_get(memory_id)
memory_upsert_candidate(candidate_record)
memory_commit(memory_id)
memory_merge(source_ids, merged_record)
memory_revise(old_memory_id, new_record)
memory_invalidate(memory_id, reason)
memory_mark_used(task_id, memory_id, stage)
memory_mark_outcome(task_id, memory_id, contribution, outcome)
memory_maintenance()
```

注：LLM 不应直接写底层 DB。它只能提出候选操作，最终由 deterministic policy gate 执行。

## 14. 评测方案

### 14.1 过期用例

- 用户一周内临时说"客厅灯晚上自动开"，一周后不应继续执行。
- 设备从 HA registry 删除后，相关 capability/location/alias 不再用于规划。
- 当前状态类事实不应被长期复用。

### 14.2 更新用例

- 用户把"卧室灯"从顶灯改指床头灯，旧 alias 被 supersede。
- 用户移动传感器位置，相关 room clue 和 routine 同步更新。
- 用户撤销某个睡眠偏好，旧偏好不再影响后续规划。

### 14.3 置信度用例

- 单次行为推断出的 habit 不能直接用于自动化。
- 多次验证成功的偏好应减少追问。
- 两个候选设备语义相近但置信度差距小，应主动澄清。

### 14.4 闭环反馈用例

- 设备服务调用失败后，相关 reflection 被写入，下次规划避开同一路径。
- 执行成功后，用到的 alias/location 记忆 positive_hits 增加。
- 用户纠错后，误导性记忆 negative_hits 增加并被降级。

### 14.5 死记忆用例

- 一条 90 天未访问的 alias 应被检测为死记忆，触发重采样或降级。
- 重采样后的记忆若在下个周期被成功召回，应升级回 active。
- 特征吸收警告：试图把"客厅灯"与"客厅顶灯"merge 时应触发警告并改为 specializes 关系。

### 14.6 核心指标

| 指标 | 目的 |
| --- | --- |
| Memory precision | 写入的记忆是否真的有用 |
| Stale recall rate | 过期记忆被错误召回的比例 |
| Wrong-device rate | 因记忆错误导致错选设备的比例 |
| Clarification efficiency | 模糊任务下减少追问但不降低正确率 |
| Update correctness | 用户纠错后旧记忆是否失效 |
| Confidence calibration | 高置信记忆实际成功率是否更高 |
| Dead memory rate | 长期未访问记忆占比；重采样后的复活率 |

## 15. 方案总结

> 以结构化事实为核心，以向量检索为入口，以置信度和时间有效性为门控，以执行反馈驱动持续更新。

相比 v3，v4 的核心变更：

1. **消除冗余**：删除 layer/volatility/expires_at/source_authority 存储字段，动态值不落盘。
2. **统一关系模型**：所有记忆间关系由 MemoryEdge 管理，MemoryRecord 不再存关系 id。
3. **统一证据引用**：`evidence_refs: list[EvidenceRef]` 替代三个独立 id 字段。
4. **scope/type 正交**：scope 只表达挂靠层级，memory_type 只表达语义类型，不再交叉。
5. **status 唯一**：生命周期状态只有 status 一个字段，检索分层由 status 派生。

五个关键闭环不变：

1. **时间闭环**：记忆会衰减、过期、归档，不会无限期污染规划。
2. **证据闭环**：每条记忆都有来源、证据和版本关系。
3. **执行闭环**：记忆被用过后，会根据执行结果更新可信度（不对称权重）。
4. **冲突闭环**：新旧事实冲突时不是简单覆盖，而是标记、比较、确认、取代。
5. **治理闭环**：死记忆检测 + 状态降级，确保低价值条目可被重采样或淘汰。

## 16. 参考资料

- Generative Agents: Interactive Simulacra of Human Behavior: https://arxiv.org/abs/2304.03442
- MemGPT: Towards LLMs as Operating Systems: https://arxiv.org/abs/2310.08560
- Reflexion: Language Agents with Verbal Reinforcement Learning: https://arxiv.org/abs/2303.11366
- Cognitive Architectures for Language Agents (CoALA): https://arxiv.org/abs/2309.02427
- LangGraph memory concepts: https://langchain-ai.github.io/langgraph/concepts/memory/
- Zep / Graphiti temporal knowledge graph: https://help.getzep.com/graphiti
- Mem0 long-term memory layer for agents: https://arxiv.org/abs/2504.19413
- Sparse Autoencoders — dead feature detection & resampling: https://transformer-circuits.pub/2023/monosemantic-features
- Memory Worth / FadeMem / SmartVector — exponential confidence decay + hits-based valuation
