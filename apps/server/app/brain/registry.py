"""Trait 声明注册表：一个 trait 能左右哪些策略字段，声明即权限。

新增人格参数（creativity / risk_tolerance / conscientiousness / …）只需要：
1. 在这里加一条 TraitSpec；
2. 在 resolver 里补该 trait 对策略字段的映射（或复用已有 projector）。
**不需要**改 workflow / learning / runtimes 的结构，也不需要 migration（traits 存 JSON）。
"""

from collections.abc import Iterator
from dataclasses import dataclass

from app.brain.policy import POLICY_FIELDS


class InvalidTraitSpec(ValueError):
    """TraitSpec.affects 指向了不存在的策略字段——注册即权限，所以必须显式声明且合法。"""


def validate_specs() -> None:
    """启动期/测试期自检：任何 trait 声明了不存在的策略字段就直接失败。"""
    for spec in TRAIT_REGISTRY.values():
        unknown = sorted(set(spec.affects) - POLICY_FIELDS)
        if unknown:
            raise InvalidTraitSpec(f"trait {spec.key} 声明了不存在的策略字段：{unknown}")


@dataclass(frozen=True)
class TraitSpec:
    key: str
    label: str
    default: float
    domain: tuple[float, float] = (0.0, 1.0)
    # 允许影响的策略字段（点号路径）。未声明的字段解析器必须保持默认值。
    affects: tuple[str, ...] = ()
    # 给 UI / 文档用的行为语义描述（只描述工作方式，绝不写"加成"，见 competency-system.md §2）
    description: str = ""

    def clamp(self, value: float) -> float:
        low, high = self.domain
        return min(high, max(low, value))

    def in_domain(self, value: float) -> bool:
        low, high = self.domain
        return low <= value <= high


CURIOUSITY = TraitSpec(
    key="curiosity",
    label="好奇心",
    default=0.5,
    description="探索倾向：专注当前任务 ↔ 主动查阅相邻知识",
    affects=(
        "retrieval.knowledge_limit",
        "retrieval.include_candidate_skills",
        "retrieval.novel_topic_ratio",
        "retrieval.max_context_items",
        "reflection.open_question_count",
        "reflection.alternative_hypotheses",
        "reflection.note_style",
        "learning.followup_topics_per_task",
        "learning.followup_priority_score",
        "learning.topic_source",
        "runtime.work_directives",
        "runtime.band",
        "runtime.trait_snapshot",
        "runtime.profile_revision",
    ),
)


# 第一版正式 8 维（docs/competency-system.md §2 / 用户拍板）。
# `curiosity` 已接入 BehaviorPolicy；其余 7 维第一阶段**只建 schema / UI / 投影**，
# affects=() —— 注册 ≠ 生效（tests/test_brain_extensibility.py 证明过这条性质）。
# 未来接入某维时只需：把对应字段加进 affects + 在 resolver 里消费，业务层零改动。
WARMTH = TraitSpec(
    key="warmth",
    label="热情与亲和",
    default=0.5,
    description="热情与亲和：克制事务化 ↔ 积极回应、解释、鼓励",
)
INDEPENDENCE = TraitSpec(
    key="independence",
    label="独立倾向",
    default=0.5,
    description="独立倾向：频繁确认 ↔ 自主拆解推进",
)
CONSCIENTIOUSNESS = TraitSpec(
    key="conscientiousness",
    label="严谨倾向",
    default=0.5,
    description="严谨倾向：快速推进 ↔ 检查、验证、守流程",
)
COLLABORATION = TraitSpec(
    key="collaboration",
    label="协作倾向",
    default=0.5,
    description="协作倾向：独立完成 ↔ 沟通、peer review、分享",
)
RISK_TOLERANCE = TraitSpec(
    key="risk_tolerance",
    label="风险偏好",
    default=0.5,
    description="风险偏好：成熟方案 ↔ 实验性技术",
)
ADAPTABILITY = TraitSpec(
    key="adaptability",
    label="变化适应",
    default=0.5,
    description="变化适应：偏好稳定 ↔ 新岗位/新栈快速调整",
)
CREATIVITY = TraitSpec(
    key="creativity",
    label="创新倾向",
    default=0.5,
    description="创新倾向：既有方案 ↔ 替代方案与新组合",
)

TRAIT_REGISTRY: dict[str, TraitSpec] = {
    spec.key: spec
    for spec in (
        CURIOUSITY,
        WARMTH,
        INDEPENDENCE,
        CONSCIENTIOUSNESS,
        COLLABORATION,
        RISK_TOLERANCE,
        ADAPTABILITY,
        CREATIVITY,
    )
}


def trait_spec(key: str) -> TraitSpec | None:
    return TRAIT_REGISTRY.get(key)


def is_registered(key: str) -> bool:
    return key in TRAIT_REGISTRY


def registered_traits() -> Iterator[TraitSpec]:
    yield from TRAIT_REGISTRY.values()


def defaults() -> dict[str, float]:
    return {spec.key: spec.default for spec in TRAIT_REGISTRY.values()}


def affected_fields() -> set[str]:
    """所有 trait 声明过的策略字段并集（用于"人格不得触达"的反向断言）。"""
    return {path for spec in TRAIT_REGISTRY.values() for path in spec.affects}
