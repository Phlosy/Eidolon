"""BehaviorPolicy 契约：派生、不可变、业务侧唯一可读的行为对象。

原则（docs/employee-brain-behavior-policy.md §2）：人格改变**工作方式**，不改变事实与能力。
所以这里**故意没有** confidence / success_rate 之类的字段——它们属于证据面，真源在
`learning/reflection.py` 的结果统计里，不接受 trait 输入。
"""

from dataclasses import dataclass, field, fields

POLICY_VERSION = "behavior-v1"
#: P11 行为策略 v2：新增的 advisory 维度（Planning/Verification/Collaboration/Autonomy/
#: Risk/Communication/Adaptation/Creativity）版本号。会真实改变 Agent 工作方式的
#: retrieval/reflection/learning 路径仍由 `POLICY_VERSION`（behavior-v1）描述，避免
#: 既有行为锚点漂移；v2 维度以 advisory 形式进入 projection/snapshot/explanation。
BEHAVIOR_POLICY_VERSION = "v2"


@dataclass(frozen=True)
class RetrievalPolicy:
    """本次任务允许带多少"与验收标准无直接关系"的相邻经验。"""

    knowledge_limit: int = 5  # 取代历史常量 retrieval.TOP_KNOWLEDGE
    include_candidate_skills: bool = False  # 是否把未验证技能也交给 agent
    # 候选技能的准入线随策略下发（策略是唯一载体，retrieval 不回头读 config）。
    # 门槛用的是**已观测成功率**：Skill 上没有 confidence 列，成功率才是事实来源。
    candidate_min_success_rate: float = 0.70
    candidate_min_attempts: int = 1
    novel_topic_ratio: float = 0.0  # 相邻（非直接命中）主题占比
    max_context_items: int = 8  # 上下文条目硬上限（token 兜底）


@dataclass(frozen=True)
class ReflectionPolicy:
    """反思的**深度**：多问几个未解问题、多列几个备选假设。只改深度与措辞。"""

    open_question_count: int = 0
    alternative_hypotheses: int = 0
    note_style: str = "standard"  # standard | exploratory


@dataclass(frozen=True)
class LearningPolicy:
    """任务之后留下的延伸学习项。"""

    followup_topics_per_task: int = 0
    # 延伸项分数（floor + trait*span，被 cap 钉住）：resolver 算好，业务侧不再读 trait。
    followup_priority_score: int = 0
    priority_score_cap: int = 69  # 严格低于 FAILURE_PRIORITY_SCORE(70)
    topic_source: str = "kind_map"  # 固定映射表，不抽任务文本


@dataclass(frozen=True)
class RuntimeBehaviorProfile:
    """给 Runtime 的行为投影载荷（prompt 内联 + brain/runtime 文件）。"""

    trait_snapshot: tuple[tuple[str, float], ...] = ()
    work_directives: tuple[str, ...] = ()
    band: str = "moderate"  # low | moderate | high
    profile_revision: int = 0
    policy_version: str = POLICY_VERSION

    def as_dict(self) -> dict:
        return {
            "trait_snapshot": [[key, value] for key, value in self.trait_snapshot],
            "work_directives": list(self.work_directives),
            "band": self.band,
            "profile_revision": self.profile_revision,
            "policy_version": self.policy_version,
        }


# ---------------------------------------------------------------------------
# P11 v2 advisory sections：只改变"工作方式"，不改变结果/成功/能力。
# 不进 as_dict（不污染既有 payload/事件）；投影与解释另走 behavior_style_summary。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningPolicy:
    """规划维度（advisory）。"""

    planning_depth: int = 1  # 1..3：拆解/里程碑细化程度
    alternative_solution_limit: int = 1  # 候选方案数（1 = 直接采用单一成熟解）


@dataclass(frozen=True)
class VerificationPolicy:
    """验证维度。"""

    verification_depth: int = 0  # 0..2：自检/核对深度
    self_review_passes: int = 0  # 自我 Review 轮数
    checklist_preference: bool = False  # 是否倾向逐条核对 Acceptance Criteria


@dataclass(frozen=True)
class CollaborationPolicy:
    """协作维度。"""

    peer_review_preference: str = "low"  # low | medium | high
    help_request_threshold: float = 0.5  # 不确定到什么程度才请求帮助
    knowledge_sharing_preference: bool = False
    handoff_detail: str = "brief"  # brief | detailed


@dataclass(frozen=True)
class AutonomyPolicy:
    """自主维度。"""

    confirmation_threshold: float = 0.5  # 需要向人确认的敏感度
    autonomous_decision_budget: int = 1  # 允许自主连续推进的步骤数


@dataclass(frozen=True)
class RiskPolicy:
    """风险偏好。"""

    experimental_solution_budget: int = 1  # 允许试用的实验性方案数（上限受 context 约束）
    mature_solution_preference: bool = True


@dataclass(frozen=True)
class CommunicationPolicy:
    """沟通风格。"""

    communication_style: str = "standard"  # terse | standard | warm
    explanation_depth: int = 1  # 1..3 解释详细度
    mentoring_preference: bool = False


@dataclass(frozen=True)
class AdaptationPolicy:
    """适应维度。"""

    new_tool_trial_budget: int = 1  # 允许尝试新工具的频度
    fallback_switch_threshold: float = 0.5  # 环境变化时切换回退方案的阈值


@dataclass(frozen=True)
class CreativityPolicy:
    """创新维度。"""

    alternative_generation: int = 1  # 生成替代方案数量（1 = 仅成熟解）
    solution_diversity: float = 0.0  # 0..1 方案多样性倾向


@dataclass(frozen=True)
class BehaviorPolicy:
    retrieval: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    reflection: ReflectionPolicy = field(default_factory=ReflectionPolicy)
    learning: LearningPolicy = field(default_factory=LearningPolicy)
    runtime: RuntimeBehaviorProfile = field(default_factory=RuntimeBehaviorProfile)
    # P11 v2 advisory sections（不参与 as_dict / 结果判定）
    planning: PlanningPolicy = field(default_factory=PlanningPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    collaboration: CollaborationPolicy = field(default_factory=CollaborationPolicy)
    autonomy: AutonomyPolicy = field(default_factory=AutonomyPolicy)
    risk: RiskPolicy = field(default_factory=RiskPolicy)
    communication: CommunicationPolicy = field(default_factory=CommunicationPolicy)
    adaptation: AdaptationPolicy = field(default_factory=AdaptationPolicy)
    creativity: CreativityPolicy = field(default_factory=CreativityPolicy)

    def as_dict(self) -> dict:
        """给 send_task context、事件 payload 与 API 用；**不含**任何 truth/结果口径。"""
        return {
            "policy_version": self.runtime.policy_version,
            "profile_revision": self.runtime.profile_revision,
            "band": self.runtime.band,
            "work_directives": list(self.runtime.work_directives),
            "traits": {key: value for key, value in self.runtime.trait_snapshot},
            "retrieval": {
                "knowledge_limit": self.retrieval.knowledge_limit,
                "include_candidate_skills": self.retrieval.include_candidate_skills,
                "candidate_min_success_rate": self.retrieval.candidate_min_success_rate,
                "novel_topic_ratio": self.retrieval.novel_topic_ratio,
                "max_context_items": self.retrieval.max_context_items,
            },
            "reflection": {
                "open_question_count": self.reflection.open_question_count,
                "alternative_hypotheses": self.reflection.alternative_hypotheses,
                "note_style": self.reflection.note_style,
            },
            "learning": {
                "followup_topics_per_task": self.learning.followup_topics_per_task,
                "followup_priority_score": self.learning.followup_priority_score,
                "priority_score_cap": self.learning.priority_score_cap,
                "topic_source": self.learning.topic_source,
            },
        }


# 关闭开关 / 无 brain / 新员工的兜底：**逐字等于**本次改造前的常量行为（回滚语义锚点）。
DEFAULT_POLICY = BehaviorPolicy()


_POLICY_CLASSES = {
    "retrieval": RetrievalPolicy,
    "reflection": ReflectionPolicy,
    "learning": LearningPolicy,
    "runtime": RuntimeBehaviorProfile,
    "planning": PlanningPolicy,
    "verification": VerificationPolicy,
    "collaboration": CollaborationPolicy,
    "autonomy": AutonomyPolicy,
    "risk": RiskPolicy,
    "communication": CommunicationPolicy,
    "adaptation": AdaptationPolicy,
    "creativity": CreativityPolicy,
}

# "retrieval.knowledge_limit" 这样的点号路径集合，TraitSpec.affects 的唯一合法取值域。
POLICY_FIELDS: frozenset[str] = frozenset(
    f"{section}.{f.name}" for section, cls in _POLICY_CLASSES.items() for f in fields(cls)
)

# Brain 上可被 API 编辑的字段白名单。**存在这里而不是任何 schema 里**（§4.4），
# 避免 `for field, value in payload: setattr()` 把新字段变成可写入的后台入口。
BRAIN_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "personality",
        "goals",
        "interests",
        "learning_policy",
        "memory_policy",
        "curiosity",
        "traits",
    }
)
