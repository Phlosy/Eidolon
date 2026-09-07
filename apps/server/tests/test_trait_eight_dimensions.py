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

#: 额度字段（除 trait_snapshot / profile_revision 这两个"本来就该随人格变"的元数据）
QUOTA_FIELDS = sorted(POLICY_FIELDS - {"runtime.trait_snapshot", "runtime.profile_revision"})


def _brain(traits: dict) -> EmployeeBrain:
    return EmployeeBrain(employee_id=1, curiosity=0.5, traits=traits)


def _quotas(policy) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in QUOTA_FIELDS:
        obj = policy
        for part in path.split("."):
            obj = getattr(obj, part)
        out[path] = str(obj)
    return out


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


def test_varying_an_unwired_trait_changes_no_quota():
    """把 warmth 从 0.1 拉到 0.9（甚至全部 7 维都拉满），额度必须逐字不变。"""
    baseline = resolve(_brain({"curiosity": 0.5}), DEFAULT_CONFIG)
    for key in EIGHT_TRAITS - {"curiosity"}:
        for value in (0.0, 0.5, 1.0):
            mutated = resolve(_brain({"curiosity": 0.5, key: value}), DEFAULT_CONFIG)
            assert _quotas(mutated) == _quotas(baseline), f"{key}={value} 不该改额度"
            assert mutated.retrieval == baseline.retrieval


def test_unwired_traits_still_round_trip_through_brain():
    """即使没接行为，8 维也要能写能读（Schema/UI 数据契约阶段的前提）。"""
    merged = BrainTraits.merge({"curiosity": 0.5}, {"warmth": 0.9, "independence": 0.2})
    assert merged["warmth"] == pytest.approx(0.9)
    assert merged["independence"] == pytest.approx(0.2)
    assert merged["curiosity"] == pytest.approx(0.5)  # 部分更新不覆盖其它维
    assert set(merged) - {"schema_version"} == EIGHT_TRAITS
