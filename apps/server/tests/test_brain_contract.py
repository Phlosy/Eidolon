"""Brain 契约层测试（设计契约 §17.1）：注册表合法性、traits 读写、API 校验。

这些测试是"新 trait 可加、且不加时行为不变"的机器证明。
"""

from unittest.mock import patch

import pytest

from app.brain import (
    BRAIN_EDITABLE_FIELDS,
    DEFAULT_POLICY,
    BrainTraits,
    resolve,
    trait_spec,
)
from app.brain.config import DEFAULT_CONFIG
from app.brain.policy import POLICY_FIELDS
from app.brain.registry import TRAIT_REGISTRY, affected_fields, validate_specs
from app.brain.traits import TraitOutOfRange, UnknownTrait, write_traits_to_brain
from app.core.config import settings


class FakeBrain:
    def __init__(self, curiosity=None, traits=None, learning_policy=None, interests=None):
        self.curiosity = 0.5 if curiosity is None else curiosity
        self.traits = traits
        self.learning_policy = learning_policy or {}
        self.interests = interests or []


# ---- 注册表合法性 ----


def test_registered_trait_affects_only_existing_policy_fields():
    validate_specs()  # 声明了不存在的字段会直接抛
    for spec in TRAIT_REGISTRY.values():
        assert set(spec.affects) <= POLICY_FIELDS, spec.key


def test_policy_fields_not_declared_by_any_trait_are_trait_invariant():
    """自动枚举：未在 affects 声明的字段，值必须与 trait 完全无关。"""
    undeclared = POLICY_FIELDS - affected_fields()
    assert undeclared, "至少应存在与人格无关的字段（如 priority_score_cap）"
    policies = [resolve(FakeBrain(curiosity=c), DEFAULT_CONFIG) for c in range(0, 101, 5)]
    for path in sorted(undeclared):
        values = set()
        for policy in policies:
            obj = policy
            for part in path.split("."):
                obj = getattr(obj, part)
            values.add(str(obj))
        assert len(values) == 1, f"{path} 随 trait 变化，但未在 TraitSpec.affects 声明：{values}"


def test_adding_an_unregistered_trait_cannot_change_policy():
    """未注册键只被忽略：注册新 trait 之前，它不能改变任何行为。"""
    baseline = resolve(FakeBrain(curiosity=0.5), DEFAULT_CONFIG)
    with_junk = resolve(
        FakeBrain(curiosity=0.5, traits={"schema_version": 1, "curiosity": 0.5, "aggression": 1.0}),
        DEFAULT_CONFIG,
    )
    assert baseline.retrieval == with_junk.retrieval
    assert baseline.learning == with_junk.learning


# ---- traits 读写 ----


def test_missing_brain_and_missing_traits_fall_back_to_defaults():
    assert BrainTraits.from_brain(None).snapshot() == {"curiosity": 0.5}
    assert BrainTraits.from_brain(FakeBrain(traits={})).snapshot() == {"curiosity": 0.5}


def test_legacy_curiosity_column_is_used_until_traits_exist():
    """迁移期：traits 为空时读 legacy 镜像列（§14.1 的回填前后都成立）。"""
    assert BrainTraits.from_brain(FakeBrain(curiosity=0.9, traits=None))["curiosity"] == 0.9


def test_traits_win_over_legacy_mirror():
    brain = FakeBrain(curiosity=0.9, traits={"schema_version": 1, "curiosity": 0.2})
    assert BrainTraits.from_brain(brain)["curiosity"] == 0.2


def test_dirty_trait_values_fall_back_on_read_and_raise_on_write():
    dirty = BrainTraits.from_brain(FakeBrain(traits={"schema_version": 1, "curiosity": 5.0}))
    assert dirty["curiosity"] == 0.5  # 读侧回落，不抛
    with pytest.raises(TraitOutOfRange):
        BrainTraits.build({"curiosity": 5.0})  # 写侧必须报错（API 转 422）
    with pytest.raises(UnknownTrait):
        BrainTraits.build({"aggression": 0.5})
    with pytest.raises(TraitOutOfRange):
        BrainTraits.build({"curiosity": "high"})
    with pytest.raises(TraitOutOfRange):
        BrainTraits.build({"curiosity": True})  # bool 不是数值


def test_higher_schema_version_still_resolves_and_backfills_defaults():
    brain = FakeBrain(traits={"schema_version": 99, "curiosity": 0.8})
    traits = BrainTraits.from_brain(brain)
    assert traits["curiosity"] == 0.8
    assert set(traits.snapshot()) == set(TRAIT_REGISTRY)  # 缺失键全部补齐默认值


def test_write_traits_syncs_legacy_mirror_column():
    brain = FakeBrain(curiosity=0.1)
    write_traits_to_brain(brain, BrainTraits.build({"curiosity": 0.77}))
    assert brain.traits == {"schema_version": 1, "curiosity": 0.77}
    assert brain.curiosity == 0.77  # 双写：镜像列不漂移


def test_preview_policy_mirrors_runtime_fallbacks():
    """预览与运行时共用回落规则：学习关闭 / 开关关闭 / 越界 / 未知 trait。"""
    from app.brain import preview_policy

    high = preview_policy({"curiosity": 0.9})
    assert high.runtime.band == "high"
    assert high.retrieval.include_candidate_skills is True
    # 越界夹紧而不是报错：滑杆拖到边界也应给出可读预览
    assert preview_policy({"curiosity": 1.4}).runtime.band == high.runtime.band
    # 学习关闭 ⇒ 与 resolve() 相同地回到默认锚点（而不是"高好奇心 + 不学习"的幻觉）
    assert preview_policy({"curiosity": 0.9}, learning_enabled=False) == DEFAULT_POLICY
    with patch.object(settings, "behavior_policy_enabled", False):
        assert preview_policy({"curiosity": 0.9}) == DEFAULT_POLICY
    # 未注册特质必须响亮失败，避免前端拼错字段后静默显示默认档位
    with pytest.raises(UnknownTrait):
        preview_policy({"sass": 0.9})


def test_band_boundaries_come_from_config():
    assert BrainTraits({"curiosity": 0.29}).band("curiosity") == "low"
    assert BrainTraits({"curiosity": 0.30}).band("curiosity") == "moderate"
    assert BrainTraits({"curiosity": 0.70}).band("curiosity") == "high"


def test_brain_editable_fields_cover_schema():
    assert {"curiosity", "traits", "personality"} <= BRAIN_EDITABLE_FIELDS
    assert "employee_id" not in BRAIN_EDITABLE_FIELDS
    assert trait_spec("curiosity") is not None and trait_spec("nope") is None


# ---- API 校验（§17.1 的 422 面）----


def test_api_rejects_out_of_range_curiosity(client, employees_by_slug):
    employee = employees_by_slug["alice"]
    response = client.patch(f"/api/v1/employees/{employee['id']}/brain", json={"curiosity": 5.0})
    assert response.status_code == 422, response.text


def test_api_rejects_unregistered_trait(client, employees_by_slug):
    employee = employees_by_slug["alice"]
    response = client.patch(
        f"/api/v1/employees/{employee['id']}/brain", json={"traits": {"aggression": 0.5}}
    )
    assert response.status_code == 422, response.text
    assert "aggression" in response.text  # 错误信息要说清是哪个键


def test_api_traits_and_mirror_stay_in_sync(client, employees_by_slug):
    employee = employees_by_slug["alice"]
    response = client.patch(
        f"/api/v1/employees/{employee['id']}/brain",
        json={"traits": {"curiosity": 0.83}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["traits"]["curiosity"] == pytest.approx(0.83)
    assert body["curiosity"] == pytest.approx(0.83)
    assert body["behavior"]["band"] == "high"
    assert body["behavior"]["policy_version"] == DEFAULT_POLICY.runtime.policy_version


def test_api_schema_version_cannot_be_forged(client, employees_by_slug):
    employee = employees_by_slug["alice"]
    response = client.patch(
        f"/api/v1/employees/{employee['id']}/brain",
        json={"traits": {"curiosity": 0.4}},
    )
    assert response.json()["traits"]["schema_version"] == BrainTraits.SCHEMA_VERSION
