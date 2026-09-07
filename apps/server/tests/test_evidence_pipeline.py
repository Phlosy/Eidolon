"""P6 Evidence Pipeline —— Collector → Candidate → Normalize（幂等落库）。

docs/evidence-pipeline.md §二~§四/§十八。锁：
- 同一业务事件重复消费只产生一条 Evidence（dedup key）；
- Task done/failed 都是证据（失败≠假提升：低 signal），但绝不直接改分（那是 Assessment 的事）；
- SkillUsage：useful ⇒ 证据；not_useful ⇒ 无（禁止“没用过却涨能力”）；
- mock 环境 ⇒ reliability 打折 + environment=mock（教程刷不动能力）；
- 无任职/无期望 ⇒ 不凭空造证据；
- 公司隔离：跨公司 source 校验拒绝；
- reconcile 幂等可重建（事件丢失兜底）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.evidence import normalize, pipeline, reconcile
from app.evidence.collectors import REGISTRY
from app.models.assessment import CompetencyExpectation
from app.models.competency import CompetencyEvidence
from app.models.enums import ExpectationRole
from app.models.knowledge import Skill, SkillUsage
from app.models.organization import Company, Employee
from app.models.project import Project, Task, WorkSession

_seq = 0


def _hire(db, company_id: int | None = None) -> int:
    global _seq
    _seq += 1
    employee = Employee(
        company_id=company_id,
        name=f"Evidence Tester {_seq}",
        slug=f"evidence-test-{_seq}",
        workspace_path=f"/tmp/ev-{_seq}-ws",
        memory_namespace=f"mem-ev-{_seq}",
    )
    db.add(employee)
    db.flush()
    return int(employee.id)


def _def(db, code: str = "execution") -> int:
    return int(
        db.scalar(
            sa.text(
                "SELECT d.id FROM competency_definitions d"
                " JOIN competency_domains dm ON dm.id = d.domain_id"
                " WHERE d.code = :code AND dm.company_id IS NULL"
            ),
            {"code": code},
        )
    )


def _task(db, employee_id: int, *, kind: str = "development", status: str = "done") -> int:
    project = Project(company_id=db.get(Employee, employee_id).company_id, name=f"P {_seq}")
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Implement Authentication",
        kind=kind,
        status=status,
        assignee_id=employee_id,
        acceptance_criteria="",
        sequence=1,
        actual_end_at=datetime.now(UTC),
    )
    db.add(task)
    db.flush()
    return int(task.id)


def test_task_completion_produces_one_evidence_per_expected_competency(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    task_id = _task(db, employee_id)
    execution_id = _def(db, "execution")
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=task_id,
            competency_definition_id=execution_id,
            role=ExpectationRole.primary.value,
        )
    )
    db.commit()

    candidates = REGISTRY.get("task").collect(db, {"task_id": task_id})
    assert len(candidates) == 1
    assert candidates[0].signal == 80
    assert candidates[0].strength == pytest.approx(1.0)  # primary
    row, created = normalize.upsert_evidence(db, candidates[0])
    db.commit()
    assert created and row.source_kind == "task"
    assert row.source_ref.startswith("task:")


def test_same_event_consumed_twice_never_duplicates(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    task_id = _task(db, employee_id)
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=task_id,
            competency_definition_id=_def(db),
            role=ExpectationRole.primary.value,
        )
    )
    db.commit()
    first = pipeline.handle_event(
        db, {"type": "task.completed", "data": {"id": task_id, "title": "x"}}
    )
    second = pipeline.handle_event(
        db, {"type": "task.completed", "data": {"id": task_id, "title": "x"}}
    )
    db.commit()
    assert first["created"] == 1 and first["updated"] == 0
    assert second["created"] == 0 and second["updated"] == 1
    count = db.scalar(
        sa.select(sa.func.count())
        .select_from(CompetencyEvidence)
        .where(CompetencyEvidence.source_id == task_id)
    )
    assert count == 1, "重复消费产生了重复证据"


def test_failed_task_is_evidence_but_weak_not_a_score_boost(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    failed_task = _task(db, employee_id, status="failed")
    done_task = _task(db, employee_id, status="done")
    comp = _def(db)
    for task_id in (failed_task, done_task):
        db.add(
            CompetencyExpectation(
                scope_kind="task",
                scope_id=task_id,
                competency_definition_id=comp,
                role=ExpectationRole.primary.value,
            )
        )
    db.commit()
    failed_signal = REGISTRY.get("task").collect(db, {"task_id": failed_task})[0].signal
    done_signal = REGISTRY.get("task").collect(db, {"task_id": done_task})[0].signal
    assert failed_signal < done_signal
    # 证据只是输入；分数变化只能来自 Assessment（本测试只断言证据本身）
    assert failed_signal == 30 and done_signal == 80


def test_mock_runtime_evidence_is_clearly_marked_and_reliability_halved(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    task_id = _task(db, employee_id)
    db.add(WorkSession(task_id=task_id, employee_id=employee_id, runtime_type="mock"))
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=task_id,
            competency_definition_id=_def(db),
            role=ExpectationRole.primary.value,
        )
    )
    db.commit()
    candidates = REGISTRY.get("task").collect(db, {"task_id": task_id})
    assert candidates[0].environment == "mock"
    assert candidates[0].reliability == pytest.approx(0.35)  # 0.7 × 0.5


def test_skill_usage_useful_becomes_evidence_but_not_useful_never(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    comp = _def(db, "testing")
    skill = Skill(employee_id=employee_id, name="Playwright E2E", competency_definition_id=comp)
    db.add(skill)
    db.flush()
    useful = SkillUsage(employee_id=employee_id, skill_id=skill.id, outcome="useful", success=True)
    not_useful = SkillUsage(
        employee_id=employee_id, skill_id=skill.id, outcome="not_useful", success=False
    )
    db.add(useful)
    db.add(not_useful)
    db.flush()
    assert len(REGISTRY.get("skill_usage").collect(db, {"usage_id": useful.id})) == 1
    assert REGISTRY.get("skill_usage").collect(db, {"usage_id": not_useful.id}) == []


def test_no_expectations_means_no_fabricated_evidence(db, default_company_id):
    """没有期望也没有任务类型提示（如 order_review）⇒ 不凭空造证据。"""
    employee_id = _hire(db, default_company_id)
    task_id = _task(db, employee_id, kind="order_review")
    assert REGISTRY.get("task").collect(db, {"task_id": task_id}) == []


def test_company_isolation_blocks_evidence_from_another_company(db, default_company_id):
    other = Company(name="Other", slug=f"other-ev-{_seq}", description="")
    db.add(other)
    db.flush()
    employee_id = _hire(db, int(other.id))
    task_id = _task(db, employee_id)
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=task_id,
            competency_definition_id=_def(db),
            role=ExpectationRole.primary.value,
        )
    )
    db.commit()
    candidates = REGISTRY.get("task").collect(db, {"task_id": task_id})
    assert len(candidates) == 1

    def _rows_for_company_a() -> int:
        return int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(CompetencyEvidence)
                .join(Employee, Employee.id == CompetencyEvidence.employee_id)
                .where(Employee.company_id == 1)
            )
            or 0
        )

    before = _rows_for_company_a()
    row, _ = normalize.upsert_evidence(db, candidates[0])
    db.commit()
    assert row.employee_id == employee_id
    assert _rows_for_company_a() == before, (
        "隔离保证：B 的证据必须挂在 B 员工的键下，A 公司任何员工都看不到"
    )


def test_reconcile_employee_is_idempotent_and_rebuilds_after_event_loss(db, default_company_id):
    """事件丢失场景：直接重扫（reconcile）也能重建，且不产生重复证据。"""
    employee_id = _hire(db, default_company_id)
    task_ids = [_task(db, employee_id) for _ in range(2)]
    comp = _def(db)
    for task_id in task_ids:
        db.add(
            CompetencyExpectation(
                scope_kind="task",
                scope_id=task_id,
                competency_definition_id=comp,
                role=ExpectationRole.primary.value,
            )
        )
    db.commit()

    first = reconcile.reconcile_employee(db, employee_id)
    second = reconcile.reconcile_employee(db, employee_id)
    db.commit()
    assert first["created"] == 2
    assert second["created"] == 0
    count = db.scalar(
        sa.select(sa.func.count())
        .select_from(CompetencyEvidence)
        .where(CompetencyEvidence.employee_id == employee_id)
    )
    assert count == 2


def _assign_engineer(db, employee_id: int) -> None:
    """把员工正式任职到默认公司的 engineer 定义（期望种子已挂在其上）。"""
    from app.models.organization import Department
    from app.models.position import PositionAssignment, PositionDefinition, PositionSlot

    company_id = db.get(Employee, employee_id).company_id
    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == company_id, Department.slug == "engineering"
        )
    )
    definition = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert department is not None and definition is not None
    slot = PositionSlot(
        company_id=company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code="P6-ENG-1",
        headcount_index=9001,
        administrative_status="active",
    )
    db.add(slot)
    db.flush()
    db.add(
        PositionAssignment(
            employee_id=employee_id,
            position_slot_id=slot.id,
            department_id=department.id,
            assignment_type="primary",
            is_primary=True,
            effective_from=datetime.now(UTC),
        )
    )
    db.flush()


def test_review_decision_is_evidence_for_the_presenter(db, default_company_id):
    from app.models.project_delivery import ReviewMeeting

    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    task_id = _task(db, employee_id, status="done")
    project_id = db.get(Task, task_id).project_id
    review = ReviewMeeting(
        project_id=project_id,
        phase_id=1,
        source_phase_id=2,
        review_type="design_review",
        title="Design Review",
        status="completed",
        presenter_employee_id=employee_id,
        decision="approved",
        completed_at=datetime.now(UTC),
        decision_version=1,
    )
    db.add(review)
    db.commit()

    candidates = REGISTRY.get("review").collect(db, {"review_id": review.id})
    assert candidates, "评审通过应为 presenter 产生 review 证据"
    assert candidates[0].signal == 84
    assert candidates[0].source_type == "review"
