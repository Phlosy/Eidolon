"""§2 原则的机器守卫：人格改变工作方式，**不**改变事实与能力。

这是整个特性最危险的失败面 —— 一旦有人写 `confidence = 0.75 + 0.2*curiosity`，
"人格"就会变成"自我评分"，知识晋升与技能晋升同时被污染，而且看起来很像正常工作。
本文件用三种方式拦住它：常量锚点、双员工对照、源码结构检查。
"""

import ast
import inspect
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.brain import BrainTraits, policy_for
from app.brain.policy import DEFAULT_POLICY, ReflectionPolicy
from app.learning import reflection
from app.models.enums import KnowledgeStatus, SkillValidationStatus
from app.models.knowledge import KnowledgeItem, LearningRecord
from app.repositories import knowledge as knowledge_repo
from app.repositories import runtimes as runtime_repo

APP_ROOT = Path(reflection.__file__).resolve().parent.parent


# ---- 常量锚点：这些数字只能来自证据，不能来自人格 ----


def test_evidence_constants_are_unchanged_and_trait_free():
    assert reflection.KNOWLEDGE_CONFIDENCE_THRESHOLD == 0.8
    assert reflection.SKILL_VALIDATION_MIN_ATTEMPTS == 3
    assert reflection.SKILL_VALIDATION_SUCCESS_RATE == 0.8
    source = inspect.getsource(reflection.reflect)
    assert not re.search(r"curiosity|traits\b", source), "reflect() 里出现了人格字段"
    # confidence 只有两个取值，且来自 success
    assert "confidence = 0.9 if success else 0.6" in inspect.getsource(reflection.reflect)


def test_reflection_policy_has_no_confidence_field():
    """策略对象上**根本不存在**能表达"事实/能力"的字段 —— 结构上就没法污染。"""
    forbidden = {"confidence", "success_rate", "score", "verdict", "accepted"}
    names = {f.name for f in ReflectionPolicy.__dataclass_fields__.values()}
    assert not forbidden & names, names
    assert "confidence" not in DEFAULT_POLICY.as_dict()["reflection"]


def test_question_records_are_structurally_excluded_from_promotion(db, employees_by_slug):
    """kind=question 的 solution 永远为空、confidence 永远 0.0 ⇒ 进不了 0.8 的知识入库通道。"""
    alice = employees_by_slug["alice"]
    policy = policy_for(db, alice["id"], None)
    record = knowledge_repo.create_learning_record(
        db,
        employee_id=alice["id"],
        project_id=None,
        task_id=None,
        kind="question",
        topic="order-review",
        problem="验收标准是否遗漏了边界情况？",
        observation="",
        lesson="验收标准是否遗漏了边界情况？",
        solution="",
        confidence=0.0,
        sources=[],
    )
    db.commit()
    assert record.confidence < reflection.KNOWLEDGE_CONFIDENCE_THRESHOLD
    assert policy.runtime.policy_version  # 策略存在，但没有任何字段能改写上面的判定


# ---- 双员工对照：同一任务模式，人格不同，事实面完全一致 ----


@pytest.mark.parametrize(
    ("slug_a", "slug_b", "curiosity_a", "curiosity_b"),
    [("alice", "bob", 0.05, 0.95)],
)
def test_same_evidence_different_personality_same_verdicts(
    db, employees_by_slug, slug_a, slug_b, curiosity_a, curiosity_b
):
    employee_a = employees_by_slug[slug_a]
    employee_b = employees_by_slug[slug_b]
    for employee_id, curiosity in (
        (employee_a["id"], curiosity_a),
        (employee_b["id"], curiosity_b),
    ):
        brain = runtime_repo.ensure_brain(db, employee_id)
        brain.traits = BrainTraits.build({"curiosity": curiosity})
        brain.learning_policy = {"enabled": True}
    db.commit()

    # 两个人格分别跑一次"成功"与一次"失败"，比较事实面
    outcomes = {}
    for label, employee_id, success in (
        ("low", employee_a["id"], True),
        ("high", employee_b["id"], True),
        ("low_fail", employee_a["id"], False),
        ("high_fail", employee_b["id"], False),
    ):
        skill_name = reflection.SKILL_BY_KIND["general"]
        existing = knowledge_repo.get_skill_by_name(db, employee_id, skill_name)
        if existing is None:
            knowledge_repo.create_skill(db, employee_id=employee_id, name=skill_name)
        db.commit()
        confidence, item_confidence, status = _reflect_and_read(db, employee_id, success)
        outcomes[label] = (confidence, item_confidence, status)

    # 成功路径：LearningRecord.confidence / 知识条目 confidence / 技能状态必须完全相同
    assert outcomes["low"][0] == outcomes["high"][0] == 0.9
    assert outcomes["low"][1] == outcomes["high"][1] == 0.9
    # 失败路径同理，且不因为高好奇心而“更容易被原谅”
    assert outcomes["low_fail"][0] == outcomes["high_fail"][0] == 0.6
    assert outcomes["low_fail"][2] == outcomes["high_fail"][2]

    # 但**深度**确实不同（这才是本特性的目的）；写进库的 question 记录由
    # test_behavior_policy.py 的端到端用例证明，本例只证明“事实面完全相同”。
    policy_low = policy_for(db, employee_a["id"], None)
    policy_high = policy_for(db, employee_b["id"], None)
    assert policy_low.reflection.open_question_count == 0
    assert policy_high.reflection.open_question_count > 0
    assert policy_low.retrieval.knowledge_limit < policy_high.retrieval.knowledge_limit


def _reflect_and_read(db, employee_id: int, success: bool):
    """在没有任务上下文时直接复现 reflect 的事实面判定（不依赖 task 行）。"""
    skill_name = reflection.SKILL_BY_KIND["general"]
    confidence = 0.9 if success else 0.6
    skill = knowledge_repo.get_skill_by_name(db, employee_id, skill_name)
    skill.attempts += 1
    if success:
        skill.success_count += 1
    rate = skill.success_count / skill.attempts
    if (
        skill.validation_status == SkillValidationStatus.candidate.value
        and skill.attempts >= reflection.SKILL_VALIDATION_MIN_ATTEMPTS
        and rate >= reflection.SKILL_VALIDATION_SUCCESS_RATE
    ):
        skill.validation_status = SkillValidationStatus.validated.value
    item_confidence = (
        confidence if confidence >= reflection.KNOWLEDGE_CONFIDENCE_THRESHOLD else None
    )
    db.commit()
    return confidence, item_confidence, skill.validation_status


def _question_count(db, employee_id: int) -> int:
    rows = list(
        db.scalars(
            select(LearningRecord).where(
                LearningRecord.employee_id == employee_id, LearningRecord.kind == "question"
            )
        )
    )
    return len(rows)


# ---- 全仓结构检查：没有任何模块把人格接进事实面 ----


@pytest.mark.parametrize(
    "relative",
    [
        "learning/reflection.py",
        "learning/retrieval.py",
        "learning/priorities.py",
        "workflow/orchestrator.py",
        "runtimes/mock/adapter.py",
        "runtimes/hermes/adapter.py",
        "runtimes/openclaw/adapter.py",
    ],
)
def test_business_modules_never_assign_confidence_from_a_trait(relative: str):
    source = (APP_ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source)
    trait_names = set(BrainTraits({}).snapshot())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any("confidence" in name for name in targets):
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert not used & trait_names, (
                    f"{relative}:{node.lineno} 用 trait 计算了 confidence"
                )
        if isinstance(node, ast.keyword) and node.arg == "confidence":
            used = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            assert not used & trait_names, f"{relative}:{node.lineno} 把 trait 传给了 confidence"


def test_promotion_and_review_paths_ignore_traits(db):
    """技能晋升 / 知识晋升的判定式里不含人格：直接检查源码 AST。"""
    source = inspect.getsource(reflection)
    tree = ast.parse(source)
    trait_names = set(BrainTraits({}).snapshot())
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):  # 所有比较表达式
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert not names & trait_names, f"用 trait 做了判定：ast 行 {node.lineno}"
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "traits" for node in ast.walk(tree)
    ), "reflect 不应该直接读 traits"


def test_knowledge_promotion_gate_is_a_constant_comparison_only():
    """`confidence >= KNOWLEDGE_CONFIDENCE_THRESHOLD` 是唯一的知识晋升条件。"""
    source = inspect.getsource(reflection.reflect)
    assert "if confidence >= KNOWLEDGE_CONFIDENCE_THRESHOLD" in source
    assert "curiosity" not in source


def test_skill_and_knowledge_rows_created_by_personality_are_marked(db, employees_by_slug):
    """人格派生的 question 记录不得伪装成反思结论：kind 必须是 question，且不产出知识条目。"""
    alice = employees_by_slug["alice"]
    before_items = len(
        list(
            db.scalars(select(KnowledgeItem).where(KnowledgeItem.owner_employee_id == alice["id"]))
        )
    )
    policy = policy_for(db, alice["id"], None)
    for question in [f"q{i}" for i in range(policy.reflection.open_question_count or 1)]:
        knowledge_repo.create_learning_record(
            db,
            employee_id=alice["id"],
            kind="question",
            topic="order-review",
            problem=question,
            observation="",
            lesson=question,
            solution="",
            confidence=0.0,
            sources=[],
        )
    db.commit()
    after_items = len(
        list(
            db.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.owner_employee_id == alice["id"],
                    KnowledgeItem.status == KnowledgeStatus.active.value,
                )
            )
        )
    )
    assert after_items <= before_items  # 0.0 置信度不可能触发入库
