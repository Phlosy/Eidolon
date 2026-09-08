"""P11 行为智能 —— 8 TraitPolicy / 确定性 / 上下文约束 / 纯函数守卫。

docs/behavioral-intelligence.md。锁：Trait 改变工作方式，不改变结果/能力/成功；
公司/任务约束（tutorial/incident/production）压过人格；确定性；trait_policies 不写库。
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.brain.policy import DEFAULT_POLICY
from app.brain.resolver import explain_behavior, resolve_with_context
from app.brain.trait_policies import TRAIT_POLICIES, BehaviorContext, apply_trait_policies
from app.brain.traits import BrainTraits
from app.models.runtime import EmployeeBrain

RAW = {
    "curiosity": 0.82,
    "warmth": 0.45,
    "independence": 0.90,
    "conscientiousness": 0.91,
    "collaboration": 0.42,
    "risk_tolerance": 0.25,
    "adaptability": 0.72,
    "creativity": 0.65,
}


def _brain(**overrides) -> EmployeeBrain:
    traits = {**RAW, **overrides}
    return EmployeeBrain(
        employee_id=1, curiosity=traits["curiosity"], traits={"schema_version": 1, **traits}
    )


def test_registry_has_all_eight_trait_policies():
    from app.brain.registry import TRAIT_REGISTRY

    assert (
        set(TRAIT_POLICIES)
        == set(TRAIT_REGISTRY)
        == {
            "curiosity",
            "warmth",
            "independence",
            "conscientiousness",
            "collaboration",
            "risk_tolerance",
            "adaptability",
            "creativity",
        }
    )


def test_same_traits_and_context_yield_same_policy():
    first = resolve_with_context(_brain(), BehaviorContext())
    second = resolve_with_context(_brain(), BehaviorContext())
    assert first.planning == second.planning
    assert first.verification == second.verification
    assert first.communication == second.communication
    assert first.creativity == second.creativity


def test_warmth_changes_communication_style_only():
    low = resolve_with_context(_brain(warmth=0.1), BehaviorContext())
    high = resolve_with_context(_brain(warmth=0.9), BehaviorContext())
    assert low.communication.communication_style == "terse"
    assert high.communication.communication_style == "warm"
    assert high.communication.mentoring_preference is True
    assert low.retrieval == high.retrieval


def test_independence_changes_confirmation_policy():
    low = resolve_with_context(_brain(independence=0.1), BehaviorContext())
    high = resolve_with_context(_brain(independence=0.9), BehaviorContext())
    # 高独立 ⇒ 更少向人确认（threshold 更低）、自主步数更少依赖许可
    assert high.autonomy.confirmation_threshold < low.autonomy.confirmation_threshold
    assert high.autonomy.autonomous_decision_budget <= low.autonomy.autonomous_decision_budget


def test_conscientiousness_changes_verification_policy():
    low = resolve_with_context(_brain(conscientiousness=0.1), BehaviorContext())
    high = resolve_with_context(_brain(conscientiousness=0.9), BehaviorContext())
    assert high.verification.self_review_passes > low.verification.self_review_passes
    assert high.verification.checklist_preference is True


def test_collaboration_changes_peer_review_policy():
    high = resolve_with_context(_brain(collaboration=0.9), BehaviorContext())
    assert high.collaboration.peer_review_preference == "high"
    assert high.collaboration.knowledge_sharing_preference is True


def test_risk_tolerance_changes_experiment_policy():
    low = resolve_with_context(_brain(risk_tolerance=0.1), BehaviorContext())
    high = resolve_with_context(_brain(risk_tolerance=0.9), BehaviorContext())
    assert low.risk.mature_solution_preference is True
    assert high.risk.experimental_solution_budget >= low.risk.experimental_solution_budget


def test_adaptability_changes_tool_switch_policy():
    low = resolve_with_context(_brain(adaptability=0.1), BehaviorContext())
    high = resolve_with_context(_brain(adaptability=0.9), BehaviorContext())
    assert high.adaptation.new_tool_trial_budget > low.adaptation.new_tool_trial_budget


def test_creativity_changes_alternative_solution_budget():
    low = resolve_with_context(_brain(creativity=0.1), BehaviorContext())
    high = resolve_with_context(_brain(creativity=0.9), BehaviorContext())
    assert high.creativity.alternative_generation > low.creativity.alternative_generation
    assert high.planning.alternative_solution_limit == high.creativity.alternative_generation


def test_tutorial_context_collapses_exploration_and_risk():
    normal = resolve_with_context(_brain(), BehaviorContext())
    tutorial = resolve_with_context(_brain(), BehaviorContext(is_tutorial=True))
    assert tutorial.creativity.alternative_generation == 1
    assert tutorial.risk.experimental_solution_budget == 0
    assert tutorial.learning.followup_topics_per_task <= normal.learning.followup_topics_per_task


def test_incident_context_converges_autonomy():
    incident = resolve_with_context(_brain(independence=0.9), BehaviorContext(task_type="incident"))
    assert incident.autonomy.autonomous_decision_budget == 1
    assert incident.creativity.alternative_generation == 1


def test_budget_pressure_clamps_experiments():
    broke = resolve_with_context(
        _brain(risk_tolerance=0.9, creativity=0.9), BehaviorContext(budget_pct=0.1)
    )
    assert broke.risk.experimental_solution_budget == 0
    assert broke.creativity.alternative_generation == 1


def test_trait_changes_do_not_touch_competency_or_success_paths():
    policy = apply_trait_policies(DEFAULT_POLICY, BrainTraits(_brain().traits or {}))
    assert not hasattr(policy, "success_rate")
    assert not hasattr(policy, "competency_score")


def test_explain_behavior_is_machine_readable_and_reasoned():
    exp = explain_behavior(_brain())
    assert exp["behavior_policy_version"] == "v2"
    assert set(exp["trait_snapshot"]) >= set(RAW)
    assert exp["working_style"]
    assert any("independent" in style for style in exp["working_style"])
    assert exp["reasons"] and all("source" in reason for reason in exp["reasons"])


def test_trait_policies_are_pure_and_never_write_db():
    """守卫：trait_policies 不得 import 任何 model / 数据库 / 业务写路径。"""
    root = Path(__file__).resolve().parents[1] / "app" / "brain" / "trait_policies.py"
    source = root.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "app.models" in node.module:
            raise AssertionError(f"trait_policies import 了 model：{node.module}")
        if isinstance(node, ast.Name) and node.id in {
            "EmployeeCompetency",
            "Commit",
            "commit",
        }:
            raise AssertionError(f"trait_policies 引用了写路径名：{node.id}")
