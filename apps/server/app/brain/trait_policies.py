"""TraitPolicy —— 8 维人格 → BehaviorPolicy v2 advisory sections（P11）。

所有 Trait 通过 TraitPolicy 贡献到 BehaviorPolicy；业务层禁止 `if warmth > 0.7`。

优先级（§十五）：System Safety > Company Policy > Project/Task 约束 > Budget >
Personality —— 人格永远不是最高优先级，且只改变"工作方式"，不改变
success / confidence / competency score / assessment。

确定性：同 traits + 同 context + 同版本 ⇒ 同 advisory 数值（有测试）。
纯函数：本模块**不写任何数据库行**（守卫）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from app.brain.policy import (
    AdaptationPolicy,
    AutonomyPolicy,
    BehaviorPolicy,
    CommunicationPolicy,
    CreativityPolicy,
    RiskPolicy,
    VerificationPolicy,
)
from app.brain.traits import BrainTraits


@dataclass(frozen=True)
class BehaviorContext:
    """行为上下文约束（决定人格能走多远）。默认 = 上下文无关中性任务。"""

    task_type: str = "general"  # general | coding | research | review | incident | learning
    task_priority: int = 0
    project_phase: str = ""
    is_tutorial: bool = False
    learning_mode: bool = False
    deadline_pressure: float = 0.0  # 0..1
    risk_level: float = 0.0  # 0..1（生产风险）
    customer_facing: bool = False
    production_critical: bool = False
    budget_pct: float = 1.0  # 剩余预算比例 0..1

    @property
    def constrained(self) -> bool:
        """安全/高敏环境：收敛探索与冒险（跳过人格对实验/方案数量的放大）。"""
        return (
            self.is_tutorial
            or self.task_type == "incident"
            or self.production_critical
            or self.risk_level >= 0.7
            or self.deadline_pressure >= 0.7
        )


class TraitPolicy(ABC):
    """一个人格维度如何贡献到 BehaviorPolicy（advisory sections）。"""

    key: str = ""

    @abstractmethod
    def contribute(
        self, traits: BrainTraits, context: BehaviorContext, policy: BehaviorPolicy
    ) -> BehaviorPolicy:
        raise NotImplementedError


def _cap(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _band(value: float, low: float = 0.30, high: float = 0.70) -> str:
    if value < low:
        return "low"
    if value < high:
        return "medium"
    return "high"


class CuriosityTraitPolicy(TraitPolicy):
    key = "curiosity"

    def contribute(self, traits, context, policy):
        # 探索/延伸学习深度：curiosity 是既有行为路径（behavior-v1），这里额外提供
        # advisory 探索意图（learning desire），不改变 retrieval/reflection 本体。
        curiosity = traits[self.key]
        return replace(
            policy,
            learning=_learning_desire(policy.learning, curiosity, context),
        )


def _learning_desire(learning, curiosity, context):
    from app.brain.policy import LearningPolicy

    followup = int(round(curiosity * 2))
    if context.constrained:
        followup = 0
    return LearningPolicy(
        followup_topics_per_task=followup,
        followup_priority_score=learning.followup_priority_score,
        priority_score_cap=learning.priority_score_cap,
        topic_source=learning.topic_source,
    )


class WarmthTraitPolicy(TraitPolicy):
    key = "warmth"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        style = "terse" if value < 0.30 else ("warm" if value >= 0.70 else "standard")
        if context.customer_facing and style == "terse":
            style = "standard"  # 面向客户至少标准措辞（安全约束不因人格降到 terse）
        return replace(
            policy,
            communication=CommunicationPolicy(
                communication_style=style,
                explanation_depth=1 if value < 0.30 else (3 if value >= 0.70 else 2),
                mentoring_preference=value >= 0.70,
            ),
        )


class IndependenceTraitPolicy(TraitPolicy):
    key = "independence"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        confirmation = 0.75 - 0.45 * value  # 高独立 → 需求确认阈值高（更少打扰）
        budget = 1 + int(round((1 - value) * 3))  # 低独立 → 更早求助（更少自主步数）
        if context.constrained:
            budget = 1  # 安全环境：自主连续性收敛
            confirmation = max(confirmation, 0.6)

        return replace(
            policy,
            autonomy=AutonomyPolicy(
                confirmation_threshold=round(confirmation, 2),
                autonomous_decision_budget=budget,
            ),
            collaboration=replace(
                policy.collaboration,
                help_request_threshold=round(0.3 + 0.4 * (1 - value), 2),
            ),
        )


class ConscientiousnessTraitPolicy(TraitPolicy):
    key = "conscientiousness"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        passes = 2 if value >= 0.70 else (1 if value >= 0.30 else 0)
        depth = 1 if value >= 0.70 else 0
        return replace(
            policy,
            verification=VerificationPolicy(
                verification_depth=depth,
                self_review_passes=passes,
                checklist_preference=value >= 0.60,
            ),
        )


class CollaborationTraitPolicy(TraitPolicy):
    key = "collaboration"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        return replace(
            policy,
            collaboration=replace(
                policy.collaboration,
                peer_review_preference="high"
                if value >= 0.70
                else ("medium" if value >= 0.30 else "low"),
                knowledge_sharing_preference=value >= 0.60,
                handoff_detail="detailed" if value >= 0.60 else "brief",
            ),
        )


class RiskToleranceTraitPolicy(TraitPolicy):
    key = "risk_tolerance"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        experimental = 1 + int(round(value * 2))
        if context.constrained:
            experimental = 0  # 生产/高敏：不注入实验性方案
        return replace(
            policy,
            risk=RiskPolicy(
                experimental_solution_budget=experimental,
                mature_solution_preference=value < 0.50,
            ),
        )


class AdaptabilityTraitPolicy(TraitPolicy):
    key = "adaptability"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        budget = 1 + int(round(value * 2))
        fallback = round(0.6 - 0.3 * value, 2)
        if context.constrained:
            fallback = max(fallback, 0.7)  # 敏感环境更早切回退方案
        return replace(
            policy,
            adaptation=AdaptationPolicy(
                new_tool_trial_budget=budget,
                fallback_switch_threshold=fallback,
            ),
        )


class CreativityTraitPolicy(TraitPolicy):
    key = "creativity"

    def contribute(self, traits, context, policy):
        value = traits[self.key]
        alternatives = 1 + int(round(value * 2))  # 1..3（安全环境收敛）
        if context.constrained:
            alternatives = 1
        return replace(
            policy,
            creativity=CreativityPolicy(
                alternative_generation=alternatives,
                solution_diversity=round(value, 2),
            ),
            planning=replace(
                policy.planning,
                alternative_solution_limit=alternatives,
                planning_depth=2 if value >= 0.70 else 1,
            ),
        )


TRAIT_POLICIES: dict[str, TraitPolicy] = {
    policy_class.key: policy_class()
    for policy_class in (
        CuriosityTraitPolicy,
        WarmthTraitPolicy,
        IndependenceTraitPolicy,
        ConscientiousnessTraitPolicy,
        CollaborationTraitPolicy,
        RiskToleranceTraitPolicy,
        AdaptabilityTraitPolicy,
        CreativityTraitPolicy,
    )
}


def apply_trait_policies(
    policy: BehaviorPolicy,
    traits: BrainTraits,
    context: BehaviorContext | None = None,
) -> BehaviorPolicy:
    """把 8 维人格按策略顺序贡献到 advisory sections（确定、纯函数）。"""
    context = context or BehaviorContext()
    result = policy
    # Company/task 约束裁剪 personality：探索预算上限（budget_pct）作用于风险与创意量级
    for key in sorted(TRAIT_POLICIES):
        result = TRAIT_POLICIES[key].contribute(traits, context, result)
    return _budget_clamp(result, context)


def _budget_clamp(policy: BehaviorPolicy, context: BehaviorContext) -> BehaviorPolicy:
    if context.budget_pct < 0.3:
        return replace(
            policy,
            creativity=replace(policy.creativity, alternative_generation=1, solution_diversity=0.0),
            risk=replace(policy.risk, experimental_solution_budget=0),
            planning=replace(policy.planning, alternative_solution_limit=1),
        )
    return policy


def working_style_summary(traits: BrainTraits, context: BehaviorContext | None = None) -> list[str]:
    """行为化摘要（Working Style 语义，非裸数值）。"""
    lines: list[str] = []
    independence = traits["independence"]
    lines.append(
        "highly independent"
        if independence >= 0.7
        else ("independent" if independence >= 0.4 else "prefers frequent confirmation")
    )
    conscientiousness = traits["conscientiousness"]
    lines.append(
        "strongly verification-oriented"
        if conscientiousness >= 0.7
        else ("checks when needed" if conscientiousness >= 0.4 else "moves fast")
    )
    curiosity = traits["curiosity"]
    lines.append(
        "highly exploratory"
        if curiosity >= 0.7
        else ("moderately exploratory" if curiosity >= 0.4 else "focuses on the task")
    )
    risk = traits["risk_tolerance"]
    lines.append(
        "risk averse" if risk < 0.4 else ("balances risk" if risk < 0.7 else "open to experiments")
    )
    creativity = traits["creativity"]
    lines.append(
        "highly creative"
        if creativity >= 0.7
        else ("moderately creative" if creativity >= 0.4 else "prefers proven solutions")
    )
    collaboration = traits["collaboration"]
    lines.append(
        "selective collaboration"
        if collaboration < 0.4
        else ("collaborative" if collaboration < 0.7 else "strongly collaborative")
    )
    return lines


def behavior_styles(policy: BehaviorPolicy, traits: BrainTraits) -> dict:
    """Exploration / Verification / Peer Review / Autonomy / Risk / Creativity 档位。"""
    return {
        "exploration": _band(traits["curiosity"]),
        "verification": _band(traits["conscientiousness"]),
        "peer_review": policy.collaboration.peer_review_preference,
        "autonomy": _band(traits["independence"]),
        "risk": _band(traits["risk_tolerance"]),
        "creativity": _band(traits["creativity"]),
    }


def reason_mapping(traits: BrainTraits, policy: BehaviorPolicy) -> list[dict]:
    """每条 advisory 数值的来源映射（可解释）。"""
    return [
        {
            "field": "planning.planning_depth",
            "value": policy.planning.planning_depth,
            "source": "creativity",
        },
        {
            "field": "verification.self_review_passes",
            "value": policy.verification.self_review_passes,
            "source": "conscientiousness",
        },
        {
            "field": "collaboration.peer_review_preference",
            "value": policy.collaboration.peer_review_preference,
            "source": "collaboration",
        },
        {
            "field": "autonomy.confirmation_threshold",
            "value": policy.autonomy.confirmation_threshold,
            "source": "independence",
        },
        {
            "field": "risk.experimental_solution_budget",
            "value": policy.risk.experimental_solution_budget,
            "source": "risk_tolerance",
        },
        {
            "field": "communication.communication_style",
            "value": policy.communication.communication_style,
            "source": "warmth",
        },
        {
            "field": "adaptation.fallback_switch_threshold",
            "value": policy.adaptation.fallback_switch_threshold,
            "source": "adaptability",
        },
        {
            "field": "creativity.alternative_generation",
            "value": policy.creativity.alternative_generation,
            "source": "creativity",
        },
    ]
