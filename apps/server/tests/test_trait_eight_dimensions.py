"""8 维人格注册表（docs/competency-system.md §2 / 用户拍板的第一版 8 个）。

锁四件事：

1. 注册表恰好是这 8 个键（curiosity 已接 BehaviorPolicy；其余 7 维第一阶段只建
   schema / UI / 投影，`affects=()`）；
2. 只有 curiosity 影响行为：其余 7 维取任意值，派生的 BehaviorPolicy **逐字段不变**；
3. 每个 trait 默认值都是注册表默认（缺失键补齐），且值域 0..1；
4. UI 契约数据（label / description / affects_execution）必须能从注册表拿到 ——
   展示"倾向怎样工作"，不展示"加成"。
"""

from __future__ import annotations

import pytest

from app.brain import TRAIT_REGISTRY, BrainTraits, resolve
from app.brain.config import DEFAULT_CONFIG
from app.brain.policy import POLICY_FIELDS
from app.brain.registry import validate_specs
from app.brain.resolver import resolve_with_context
from app.brain.trait_policies import BehaviorContext
from app.models.runtime import EmployeeBrain

EIGHT_TRAITS = {
    "curiosity",
    "warmth",
    "independence",
    "conscientiousness",
    "collaboration",
    "risk_tolerance",
    "adaptability",
    "creativity",
}

#: 核心额度字段（behavior-v1 真正改变 Agent 行为的路径）：7 个新维度不得改变这些；
#: P11 新增的 advisory sections（planning/verification/…/creativity）除外。
CORE_FIELDS = sorted(
    path
    for path in POLICY_FIELDS
    if not path.startswith(
        (
            "planning.",
            "verification.",
            "collaboration.",
            "autonomy.",
            "risk.",
            "communication.",
            "adaptation.",
            "creativity.",
        )
    )
    and path
    not in {
        "runtime.trait_snapshot",
        "runtime.profile_revision",
        "learning.followup_topics_per_task",
        "learning.followup_priority_score",
    }
)


def _brain(traits: dict) -> EmployeeBrain:
    return EmployeeBrain(employee_id=1, curiosity=0.5, traits=traits)


def _quotas(policy, fields: set[str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    fields = fields or set(CORE_FIELDS)
    for path in sorted(fields):
        obj = policy
        for part in path.split("."):
            obj = getattr(obj, part)
        out[path] = str(obj)
    return out


def _peek(policy, path: str):
    obj = policy
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def test_registry_holds_exactly_the_eight_first_version_dimensions():
    validate_specs()
    assert set(TRAIT_REGISTRY) == EIGHT_TRAITS
    for key, spec in TRAIT_REGISTRY.items():
        assert spec.label, f"{key} 缺中文标签"
        assert spec.description, f"{key} 缺行为语义描述（UI 数据契约用）"
        low, high = spec.domain
        assert low <= spec.default <= high


def test_only_curiosity_affects_execution_in_this_phase():
    """其余 7 维 affects=()：注册 ≠ 生效（接入某维是显式 resolver 步骤）。"""
    assert set(TRAIT_REGISTRY["curiosity"].affects) <= POLICY_FIELDS
    for key in EIGHT_TRAITS - {"curiosity"}:
        assert TRAIT_REGISTRY[key].affects == (), f"{key} 不应在本阶段影响任何策略字段"


def test_new_dimensions_change_only_their_advisory_sections():
    """P11：7 个新维度真正生效，但只改各自归属的 advisory section，
    绝不改 behavior-v1 的核心额度（retrieval/reflection/learning）与 runtime 档位。"""
    context = BehaviorContext()
    core = resolve(_brain({"curiosity": 0.5}), DEFAULT_CONFIG)
    assertions = [
        ("warmth", "communication.communication_style"),
        ("independence", "autonomy.confirmation_threshold"),
        ("conscientiousness", "verification.self_review_passes"),
        ("collaboration", "collaboration.peer_review_preference"),
        ("risk_tolerance", "risk.experimental_solution_budget"),
        ("adaptability", "adaptation.fallback_switch_threshold"),
        ("creativity", "creativity.alternative_generation"),
    ]
    for key, field in assertions:
        core_before = _quotas(resolve(_brain({"curiosity": 0.5}), DEFAULT_CONFIG))
        low = resolve_with_context(_brain({"curiosity": 0.5, key: 0.1}), context)
        high = resolve_with_context(_brain({"curiosity": 0.5, key: 0.9}), context)
        # 核心额度逐字不变（7 维不改变真正影响 Agent 行为的 retrieval/reflection/learning）
        assert _quotas(low) == core_before, f"{key} 改了核心额度"
        assert _quotas(high) == core_before, f"{key} 改了核心额度"
        assert low.retrieval == core.retrieval
        # 但确实改变了它应影响的工作方式（v2 advisory）
        assert _peek(low, field) != _peek(high, field), f"{key} 没有影响 {field}"


def test_unwired_traits_still_round_trip_through_brain():
    """即使没接行为，8 维也要能写能读（Schema/UI 数据契约阶段的前提）。"""
    merged = BrainTraits.merge({"curiosity": 0.5}, {"warmth": 0.9, "independence": 0.2})
    assert merged["warmth"] == pytest.approx(0.9)
    assert merged["independence"] == pytest.approx(0.2)
    assert merged["curiosity"] == pytest.approx(0.5)  # 部分更新不覆盖其它维
    assert set(merged) - {"schema_version"} == EIGHT_TRAITS
