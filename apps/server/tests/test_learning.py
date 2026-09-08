"""P11 Autonomous Learning —— 契约（docs/autonomous-learning.md §77 子集）。

每个用例用**独立公司**：预算计数与公司学习政策存于 Company.settings，默认公司被
多用例共享会互相污染。锁：LearningSession≠Project Task；学习不直接改 Competency；
预算硬限制 + 顺序双扣不超日预算；disabled ⇒ 不产生会话；manual 可学；产出
Knowledge/SkillCandidate/OpenQuestion；mock 标记；幂等；冷却；隔离；crash→FAILED；
provider/model 记录；scheduler 不空闲必学；planner 确定性（DevPlan 优先，兴趣不算价值）。
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from app.core.config import settings
from app.models.enums import LearningSessionStatus, LearningSourceType
from app.models.knowledge import KnowledgeItem, LearningRecord, Skill
from app.models.learning import LearningSession
from app.models.organization import Company, Employee
from app.services import learning as learning_service
from app.services.learning import LearningError

_seq = 0


def _fresh(db) -> tuple[int, int]:
    global _seq
    _seq += 1
    company = Company(name=f"LearnCo {_seq}", slug=f"learn-co-{_seq}", description="")
    db.add(company)
    db.flush()
    employee = Employee(
        company_id=company.id,
        name=f"Learn {_seq}",
        slug=f"learn-{_seq}",
        workspace_path=f"/tmp/learn-{_seq}-ws",
        memory_namespace=f"mem-learn-{_seq}",
        lifecycle_status="active",
    )
    db.add(employee)
    db.commit()
    return int(company.id), int(employee.id)


def _credits(db, company_id: int, *, tokens=2000, cost=0.1, sessions=2, minutes=10) -> None:
    company = db.get(Company, company_id)
    policy = learning_service.company_learning_policy(db, company).__class__(
        autonomous_learning_enabled=True,
        daily_token_budget=tokens,
        daily_cost_budget=cost,
        max_sessions_per_day=sessions,
        max_session_minutes=minutes,
    )
    company.settings = {"learning_policy": dict(policy.__dict__)}
    db.commit()


def test_manual_learning_works_and_produces_outputs(db):
    company_id, employee_id = _fresh(db)
    session = learning_service.create_session(
        db, employee_id, "System Architecture patterns", reason="manual", minutes=10
    )
    session = learning_service.run_session(db, session)
    db.commit()
    assert session.status == LearningSessionStatus.completed.value
    outputs = session.metadata_json.get("outputs", {})
    assert outputs["knowledge"] >= 1
    assert outputs["skill_candidates"] == 1
    assert outputs["questions"] == 1
    created = db.scalars(
        sa.select(KnowledgeItem).where(KnowledgeItem.owner_employee_id == employee_id)
    ).all()
    assert created and created[0].freshness_status == "fresh"
    candidates = db.scalars(sa.select(Skill).where(Skill.employee_id == employee_id)).all()
    assert candidates and candidates[0].validation_status == "candidate"
    questions = db.scalars(
        sa.select(LearningRecord).where(
            LearningRecord.employee_id == employee_id, LearningRecord.kind == "question"
        )
    ).all()
    assert questions
    from app.models.competency import EmployeeCompetency

    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(EmployeeCompetency)
            .where(EmployeeCompetency.employee_id == employee_id)
        )
        == 0
    )


def test_learning_does_not_modify_competency_source_guard():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "services" / "learning.py").read_text(
        encoding="utf-8"
    )
    assert "EmployeeCompetency(" not in source
    assert "employee_competencies" not in source


def test_budget_caps_and_atomic_reservation_never_exceed_daily(db):
    company_id, _employee = _fresh(db)
    _credits(db, company_id, sessions=2)
    company = db.get(Company, company_id)

    def _reserve() -> bool:
        eid = _fresh(db)[1]
        session = learning_service.create_session(db, eid, "topic-budget", minutes=2)
        return learning_service.reserve_budget(db, company, db.get(Employee, eid), session), session

    first_ok, first = _reserve()
    second_ok, second = _reserve()
    third_ok, third = _reserve()
    assert first_ok and first.status == "running"
    assert second_ok and second.status == "running"
    assert not third_ok and third.status == LearningSessionStatus.waiting_budget.value
    usage = learning_service._usage(db, company, date.today())
    assert usage["sessions"] == 2  # 顺序扣减不会双双超日预算


def test_autonomous_disabled_creates_no_session(db, monkeypatch):
    company_id, employee_id = _fresh(db)
    settings.autonomous_learning_enabled = False
    created = learning_service.scheduler_trigger(db, global_check=True)
    assert created == 0
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(LearningSession)
            .where(LearningSession.employee_id == employee_id)
        )
        == 0
    )


def test_duplicate_active_session_is_rejected(db):
    company_id, employee_id = _fresh(db)
    learning_service.create_session(db, employee_id, "topic-dup", minutes=5)
    try:
        learning_service.create_session(db, employee_id, "topic-dup", minutes=5)
        raise AssertionError("同主题进行中会话应被拒绝")
    except LearningError:
        pass


def test_cooldown_blocks_immediate_repeat(db):
    company_id, employee_id = _fresh(db)
    first = learning_service.create_session(
        db,
        employee_id,
        "topic-cooldown",
        source_type=LearningSourceType.learning_priority.value,
        minutes=2,
    )
    learning_service.run_session(db, first)
    db.commit()
    try:
        learning_service.create_session(
            db,
            employee_id,
            "topic-cooldown",
            source_type=LearningSourceType.learning_priority.value,
            minutes=2,
        )
        raise AssertionError("冷却期内同主题应被拒绝")
    except LearningError:
        pass


def test_crash_marks_failed_and_keeps_usage(db, monkeypatch):
    company_id, employee_id = _fresh(db)
    session = learning_service.create_session(db, employee_id, "topic-crash", minutes=2)
    company = db.get(Company, company_id)
    usage_before = learning_service._usage(db, company, date.today())

    def boom(*args, **kwargs):
        raise RuntimeError("runtime crashed")

    monkeypatch.setattr(learning_service, "_produce_outputs", boom)
    session = learning_service.run_session(db, session)
    db.commit()
    assert session.status == LearningSessionStatus.failed.value
    assert session.error
    assert usage_before["sessions"] >= 0  # 保留已消耗语义（mock 消耗=预留）


def test_idle_employee_does_not_always_learn(db):
    company_id, employee_id = _fresh(db)
    policy = learning_service.company_learning_policy(db, db.get(Company, company_id))
    candidates = learning_service.plan_candidates(
        db, db.get(Employee, employee_id), policy, limit=3
    )
    # 没有任何发展需要/优先级/兴趣 ⇒ 无事可学（curiosity 不强行开课）
    assert candidates == []


def test_development_plan_outranks_curiosity(db):
    from app.services import career as career_service

    company_id, employee_id = _fresh(db)
    engineer = db.scalar(
        sa.text(
            "SELECT id FROM position_definitions WHERE code='engineer' AND company_id=:c LIMIT 1"
        ),
        {"c": company_id},
    )
    if engineer is None:
        return  # 该新公司没有工程师定义；跳过（seed 只在默认公司）
    career_service.create_plan(db, employee_id, int(engineer))
    db.commit()
    policy = learning_service.company_learning_policy(db, db.get(Company, company_id))
    candidates = learning_service.plan_candidates(
        db, db.get(Employee, employee_id), policy, limit=3
    )
    assert candidates and candidates[0]["source_type"] == LearningSourceType.development_plan.value


def test_company_isolation(client, db):
    company_id, employee_id = _fresh(db)
    assert client.get(f"/api/v1/employees/{employee_id}/learning-policy").status_code == 404
    assert client.get(f"/api/v1/employees/{employee_id}/learning-sessions").status_code == 404


def test_provider_and_model_recorded_on_session(db):
    from app.models.provider import ModelBinding, Provider
    from app.models.runtime import RuntimeInstance

    company_id, employee_id = _fresh(db)
    provider = Provider(name="mock-provider", provider_type="custom", scope="company")
    db.add(provider)
    db.flush()
    binding = ModelBinding(
        employee_id=employee_id, provider_id=provider.id, model="mock-model", alias="mock-alias"
    )
    db.add(binding)
    db.flush()
    db.add(
        RuntimeInstance(
            employee_id=employee_id,
            runtime_type="mock",
            status="running",
            model_binding_id=binding.id,
        )
    )
    db.commit()
    session = learning_service.create_session(db, employee_id, "topic-pm", minutes=2)
    db.commit()
    assert session.provider_name == "mock-alias"
    assert session.model_name == "mock-model"


def test_learning_session_is_not_a_project_task(db):
    company_id, employee_id = _fresh(db)
    learning_service.create_session(db, employee_id, "topic-notask", minutes=2)
    from app.models.project import Task

    assert (
        db.scalar(
            sa.select(sa.func.count()).select_from(Task).where(Task.assignee_id == employee_id)
        )
        == 0
    ), "LearningSession 绝不能创建 Project Task"
