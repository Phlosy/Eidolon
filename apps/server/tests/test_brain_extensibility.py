"""第二个 trait 的接入演练（设计契约 §16 PR7 / §17.1）。

v1 只落地 `curiosity`，但整份设计的卖点是"加人格不改业务层"。这个文件用*真实*的第二特质
`methodical` 把这句话拆开验证：

* 注册表 + traits + API + 投影这条**垂直通道**已经 trait 无关（改注册表即可写入/读出）；
* 但"注册"不等于"生效"——`_policy_from` 不读它，所有额度必须逐字不变（fail-safe，
  避免注册顺序不同导致线上行为漂移）；
* 撤销注册后，已落库的 traits 仍然可读（优雅降级，不会把老员工打成 500）。

真正让人格起作用只有第 4 个测试演示的那一步：在 resolver 里显式消费它。
"""

import pytest

from app.brain import (
    DEFAULT_POLICY,
    TRAIT_REGISTRY,
    BrainTraits,
    TraitSpec,
    resolve,
)
from app.brain.config import DEFAULT_CONFIG
from app.brain.policy import POLICY_FIELDS
from app.brain.registry import validate_specs
from app.brain.resolver import _policy_from
from app.brain.traits import UnknownTrait, write_traits_to_brain
from app.models.runtime import EmployeeBrain

QUOTA_FIELDS = sorted(POLICY_FIELDS - {"runtime.trait_snapshot", "runtime.profile_revision"})


def _register(spec: TraitSpec):
    TRAIT_REGISTRY[spec.key] = spec


@pytest.fixture
def methodical():
    """临时注册第二个特质；测完还原，避免污染同进程其它用例。"""
    spec = TraitSpec(
        key="methodical",
        default=0.5,
        label="条理性",
        affects=(),  # 尚未被 resolver 消费：注册表合法性不该依赖"生效"
    )
    _register(spec)
    try:
        yield spec
    finally:
        TRAIT_REGISTRY.pop(spec.key, None)


def _brain(raw_traits=None, curiosity=0.5) -> EmployeeBrain:
    """未入库的 brain 实例即可：resolve 只读 traits / curiosity 镜像。"""
    return EmployeeBrain(employee_id=1, curiosity=curiosity, traits=raw_traits)


def _values(policy):
    result = {}
    for path in QUOTA_FIELDS:
        obj = policy
        for part in path.split("."):
            obj = getattr(obj, part)
        result[path] = str(obj)
    return result


def test_registering_a_trait_alone_changes_no_quota(methodical):
    """注册 ≠ 生效：resolver 不读它，所有额度必须与 v1 完全一致。"""
    validate_specs()
    brain_a = _brain({"curiosity": 0.9})
    brain_b = _brain({"curiosity": 0.9, "methodical": 0.0})
    before = resolve(brain_a, DEFAULT_CONFIG)
    after = resolve(brain_b, DEFAULT_CONFIG)

    assert _values(before) == _values(after), "未消费的 trait 改变了行为"
    assert after.retrieval == before.retrieval


def test_new_trait_flows_through_the_brain_channel(methodical):
    """traits → snapshot → revision → API 读出：这条通道与特质名无关。"""
    brain = _brain({"curiosity": 0.5})
    write_traits_to_brain(brain, BrainTraits.build({"curiosity": 0.5, "methodical": 0.9}))
    policy = resolve(brain, DEFAULT_CONFIG)

    snapshot = dict(policy.runtime.trait_snapshot)
    assert snapshot["curiosity"] == 0.5
    assert snapshot["methodical"] == 0.9
    # 8 维注册后 snapshot 是完整键集（缺失键补齐注册表默认）；methodical 是额外临时维
    assert set(snapshot) == set(TRAIT_REGISTRY)
    # 人格变了就换修订号：投影文件必须能区分"只加了一个未生效特质"与"什么都没变"
    assert (
        policy.runtime.profile_revision
        != resolve(_brain({"curiosity": 0.5}), DEFAULT_CONFIG).runtime.profile_revision
    )
    assert policy.runtime.band == DEFAULT_POLICY.runtime.band


def test_unregistering_degrades_gracefully(methodical):
    """撤掉注册后老行仍可读：宽松读路径忽略未知键，写路径才 fail-closed。"""
    traits = BrainTraits.build({"curiosity": 0.5, "methodical": 0.9})
    TRAIT_REGISTRY.pop("methodical")

    with pytest.raises(UnknownTrait):
        BrainTraits.build({"methodical": 0.9})  # 写入仍然拒绝未知特质
    coerced = BrainTraits.coerce(traits)
    assert "methodical" not in coerced.snapshot()  # 未知键被安全忽略
    assert coerced.snapshot()["curiosity"] == 0.5
    # 落库的未知键被忽略 ⇒ 与"从未有过该特质"的行完全同策略
    assert resolve(_brain(traits), DEFAULT_CONFIG) == resolve(_brain(None), DEFAULT_CONFIG)


def test_resolver_opt_in_is_the_only_remaining_step(
    methodical, monkeypatch, client, employees_by_slug
):
    """演示 PR7 的全部工作量：一处 resolver 读取 + 一行注册表声明。"""
    original = _policy_from

    def patched(traits, config):
        policy = original(traits, config)
        if traits.get("methodical", 0.5) >= 0.75:
            from dataclasses import replace

            policy = replace(policy, reflection=replace(policy.reflection, note_style="planned"))
        return policy

    from app.brain import resolver

    monkeypatch.setattr(resolver, "_policy_from", patched)

    employee = employees_by_slug["alice"]
    response = client.patch(
        f"/api/v1/employees/{employee['id']}/brain",
        json={"traits": {"methodical": 0.9}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["traits"]["methodical"] == pytest.approx(0.9)
    assert body["traits"]["curiosity"] == pytest.approx(body["curiosity"])  # 镜像未被覆盖
    # 消费侧（retrieval / reflection / orchestrator）一行没改：它们只读 BehaviorPolicy
    assert body["behavior"]["reflection"]["note_style"] == "planned"
