class EvidenceRef(BaseModel):
    # 证据来源类型：对话轮次、执行轨迹、外部文档或系统事件
    # todo 对话轮次、执行轨迹的区别和定义分别是什么，怎么感觉很相似
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
        # todo：怎么观察到的呢
        "habit",
        # 执行任务时必须满足的约束条件
        # todo：这又是用来干什么的，和下面content的生效条件condition的区别是什么呢？
        "constraint",
        # 可复用的多步骤场景流程或自动化组合
        "routine",
        # 一次具体执行经历，包括过程、结果和上下文
        "episode",
        # 从多次 episode 中总结出的经验、教训或策略
        "reflection",
        # 空间布局、设备之间的相对关系或拓扑关系
        # todo：和location的区别是什么呢
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
        # 来自 Home Assistant registry 的权威事实，如设备、实体、服务定义
        "ha_registry",
        # 来自 Home Assistant 当前状态观测，适合短期感知或稳定事实确认
        "ha_state_observation",
        # 用户明确说出的信息，如别名、偏好、位置、规则
        "user_explicit",
        # 用户对系统已有认知进行纠正，优先级通常最高
        "user_correction",
        # 从用户长期操作行为中归纳出的隐式模式
        "user_behavior",
        # 执行任务后通过结果校验得到的验证结论
        "execution_verification",
        # LLM 根据上下文推断出的候选记忆，默认仅作弱证据
        "llm_inference",
        # 来自外部导入文档、说明书或配置资料的信息
        "imported_doc",
    ]
    # 支撑该记忆成立的证据引用列表，可同时关联多个来源
    evidence_refs: list[EvidenceRef] = []

    # Confidence
    # 初始置信度；通常由 source 对应的先验权威度给出
    confidence: float
    # 重要性权重；影响保留、反思和淘汰优先级
    # todo 重要性权重又是干什么的？Memory Worth（MW）不是动态计算，不存储吗？
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
