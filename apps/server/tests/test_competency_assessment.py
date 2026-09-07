"""P5：Competency 基础聚合器的不变量（docs/assessment-system.md §4/§5 第一版）。

锁死的机器证明：
 1. 确定性：同证据集 + 同算法版本 ⇒ 同 inputs_hash、同输出（可重放）；
 2. 无证据不编分：没有 Evidence 的员工不落任何 employee_competencies 行（unrated）；
 3. score 与 confidence 独立（低证据高 signal ⇒ 高分低置信；多源低 signal ⇒ 低分高置信）；
 4. 单调性：固定时间下追加证据不降 confidence；
 5. trend 来自评估历史（相邻两次 run 的能力分差），不是随机；
 6. SkillUsage（人评 useful + 技能映射能力）可以成为 Evidence，且幂等只产生一次；
 7. Evidence 归属正确公司/员工（跨公司不串）。

每个测试都造**自己的**员工 —— 这些用例会真的写 Evidence/run 行，共享“干净”的
seed 员工会互相污染。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models.competency import (
    AssessmentRun,
    CompetencyEvidence,
    EmployeeCompetency,
)
from app.models.enums import CompetencyStatus, EvidenceSourceKind
from app.models.knowledge import Skill, SkillUsage
from app.models.organization import Company, Employee
from app.services import competency as svc

_T0 = datetime.now(UTC) - timedelta(days=5)
_tag = 0


def _hire(db, company_id: int | None = None, company: Company | None = None) -> int:
    """造一个最小员工（当前公司或指定公司），返回 id。"""
    global _tag
    _tag += 1
    if company is None:
        employee = Employee(
            company_id=company_id,
            name=f"Competency Tester {_tag}",
            slug=f"comp-test-{_tag}",
            workspace_path=f"/tmp/comp-{_tag}-ws",
            memory_namespace=f"mem-comp-{_tag}",
        )
        db.add(employee)
        db.flush()
        return int(employee.id)
    employee = Employee(
        company_id=company.id,
        name=f"Competency Tester {_tag}",
        slug=f"comp-other-{_tag}",
        workspace_path=f"/tmp/comp-o-{_tag}-ws",
        memory_namespace=f"mem-comp-o-{_tag}",
    )
    db.add(employee)
    db.flush()
    return int(employee.id)


def _other_company(db) -> Company:
    company = Company(name=f"Other {_tag}", slug=f"other-comp-{_tag}", description="")
    db.add(company)
    db.flush()
    return company


def _def_id(db, code: str = "testing") -> int:
    value = db.scalar(
        sa.text(
            "SELECT d.id FROM competency_definitions d"
            " JOIN competency_domains dm ON dm.id = d.domain_id"
            " WHERE d.code = :code AND dm.company_id IS NULL"
        ),
        {"code": code},
    )
    assert value is not None, f"catalog 缺 {code}"
    return int(value)


def _add_evidence(
    db,
    employee_id: int,
    definition_id: int,
    *,
    signal: int = 80,
    kind: str = EvidenceSourceKind.test.value,
    ref: str = "TEST-1",
    project: int = 1,
) -> CompetencyEvidence:
    evidence = CompetencyEvidence(
        employee_id=employee_id,
        competency_definition_id=definition_id,
        source_kind=kind,
        source_id=project * 1000,
        source_ref=ref,
        signal=signal,
        occurred_at=_T0,
        metadata_json={"project_id": project},
    )
    db.add(evidence)
    db.flush()
    return evidence


def test_no_evidence_means_no_row_and_no_invented_score(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    db.commit()
    run = svc.assess_employee_competencies(db, employee_id, commit=True)
    assert run.status == "completed"
    assert run.outputs == {}
    rows = db.scalars(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    ).all()
    assert rows == []


def test_first_assessment_creates_row(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    _add_evidence(db, employee_id, definition_id, signal=90)
    _add_evidence(db, employee_id, definition_id, kind="artifact", ref="ART-1", project=2)
    db.commit()

    run = svc.assess_employee_competencies(db, employee_id, commit=True)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == definition_id,
        )
    )
    assert row is not None
    assert row.score is not None and 0 <= row.score <= 100
    assert row.confidence is not None and 0.0 <= row.confidence <= 1.0
    assert row.evidence_count == 2
    assert row.status in {CompetencyStatus.provisional.value, CompetencyStatus.assessed.value}
    assert row.trend is None  # 第一次评估没有历史可比
    assert run.outputs[str(definition_id)]["score"] == row.score
    assert run.evidence_ids and len(run.evidence_ids) == 2


def test_score_and_confidence_are_independent_axes(db, default_company_id):
    """低证据高 signal（高分低置信） vs 多源低 signal（低分高置信）。"""
    alice = _hire(db, default_company_id)
    definition_id = _def_id(db)
    _add_evidence(db, alice, definition_id, signal=95)
    db.commit()
    out_a = svc.assess_employee_competencies(db, alice, commit=True).outputs[str(definition_id)]

    bob = _hire(db, default_company_id)
    for index, kind in enumerate(
        (
            EvidenceSourceKind.test.value,
            EvidenceSourceKind.review.value,
            EvidenceSourceKind.artifact.value,
            EvidenceSourceKind.task.value,
            EvidenceSourceKind.user_feedback.value,
        )
    ):
        _add_evidence(
            db, bob, definition_id, signal=55, kind=kind, ref=f"LOW-{index}", project=index + 1
        )
    db.commit()
    out_b = svc.assess_employee_competencies(db, bob, commit=True).outputs[str(definition_id)]

    assert out_a["confidence"] < out_b["confidence"], (out_a, out_b)
    assert out_a["score"] > out_b["score"], (out_a, out_b)


def test_aggregation_is_deterministic_and_replayable(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _add_evidence(db, employee_id, _def_id(db), signal=85, project=7)
    db.commit()
    first = svc.assess_employee_competencies(db, employee_id, commit=True)
    second = svc.assess_employee_competencies(db, employee_id, commit=True)
    assert first.inputs_hash == second.inputs_hash
    # 同输入同输出（审计输出归一化到 6 位小数）。previous_score / trend 属于 run 历史：
    # 第二条 run 的 previous = 第一条的 score，因此 trend=0（STABLE）—— 都是确定性的产物。
    for definition_id, payload in first.outputs.items():
        replay = second.outputs[definition_id]
        for key in ("score", "confidence", "evidence_count", "status", "n_units"):
            assert payload[key] == replay[key], (key, payload, replay)
        assert replay["previous_score"] == payload["score"]
        assert payload["trend"] is None and replay["trend"] == 0


def test_inputs_hash_changes_when_a_signal_changes(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    evidence = _add_evidence(db, employee_id, definition_id, signal=70)
    db.commit()
    before = svc.inputs_hash_of(employee_id=employee_id, evidence_rows=[evidence])
    evidence.signal = 95
    db.commit()
    after = svc.inputs_hash_of(employee_id=employee_id, evidence_rows=[evidence])
    assert before != after


def test_appending_evidence_never_lowers_confidence(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            competency_definition_id=definition_id,
            source_kind=EvidenceSourceKind.test.value,
            source_ref="T0",
            signal=80,
            occurred_at=_T0,
        )
    )
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)
    row_before = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            competency_definition_id=definition_id,
            source_kind=EvidenceSourceKind.review.value,
            source_ref="T1",
            signal=85,
            occurred_at=_T0,
            metadata_json={"project_id": 2},
        )
    )
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)
    row_after = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row_after.confidence >= row_before.confidence - 1e-6


def test_pure_group_computation_is_deterministic(db, default_company_id):
    """纯函数层：同证据 + 同 window_end ⇒ 逐字相同（不依赖调用时刻）。"""
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    rows = [
        _add_evidence(db, employee_id, definition_id, signal=90, project=1),
        _add_evidence(
            db, employee_id, definition_id, signal=70, kind="review", ref="R-1", project=2
        ),
    ]
    window_end = _T0 + timedelta(days=1)
    db.commit()
    first = svc.compute_for_group(rows, window_end=window_end)
    second = svc.compute_for_group(rows, window_end=window_end)
    assert first == second
    assert first.score == second.score and first.confidence == second.confidence
    assert svc.inputs_hash_of(employee_id=employee_id, evidence_rows=rows) == (
        svc.inputs_hash_of(employee_id=employee_id, evidence_rows=list(reversed(rows)))
    )  # hash 与证据顺序无关


def test_trend_comes_from_assessment_history(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    _add_evidence(db, employee_id, definition_id, signal=60)
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row.trend is None  # 首次无历史

    _add_evidence(db, employee_id, definition_id, signal=95, kind="artifact", project=9)
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row.trend is not None and row.trend > 0
    assert row.trend_window == 2


def test_skill_usage_becomes_evidence_once(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db)
    skill = Skill(
        employee_id=employee_id,
        name="Playwright E2E Testing",
        description="",
        competency_definition_id=definition_id,
    )
    db.add(skill)
    db.flush()
    usage = SkillUsage(employee_id=employee_id, skill_id=skill.id, outcome="useful", success=True)
    db.add(usage)
    db.flush()

    evidence = svc.skill_usage_evidence_for(db, usage)
    db.flush()  # SessionLocal autoflush=False：断言前把挂起的证据真正写出去
    assert evidence is not None
    assert evidence.source_kind == EvidenceSourceKind.skill_usage.value
    assert evidence.competency_definition_id == definition_id
    assert evidence.source_ref == f"skill:Playwright E2E Testing#usage:{usage.id}"
    again = svc.skill_usage_evidence_for(db, usage)
    db.flush()
    count = db.scalar(
        sa.select(sa.func.count())
        .select_from(CompetencyEvidence)
        .where(CompetencyEvidence.source_id == usage.id)
    )
    assert again.id == evidence.id and count == 1  # 幂等

    not_useful = SkillUsage(
        employee_id=employee_id, skill_id=skill.id, outcome="not_useful", success=False
    )
    db.add(not_useful)
    db.flush()
    assert svc.skill_usage_evidence_for(db, not_useful) is None  # 没人确认有用 ⇒ 无证据

    run = svc.assess_employee_competencies(db, employee_id, commit=True)
    assert str(definition_id) in run.outputs
    assert run.outputs[str(definition_id)]["evidence_count"] >= 1


def test_evidence_is_scoped_to_employee_and_company(db, default_company_id):
    """A 员工/公司的证据不会算进 B。"""
    alice = _hire(db, default_company_id)
    definition_id = _def_id(db)
    _add_evidence(db, alice, definition_id, signal=88)
    db.commit()

    other_company = _other_company(db)
    other = _hire(db, company=other_company)
    db.commit()
    svc.assess_employee_competencies(db, other, commit=True)
    assert (
        db.scalar(sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == other))
        is None
    ), "别的员工的证据被算进了这个人 —— 边界破了"
    runs = db.scalars(sa.select(AssessmentRun).where(AssessmentRun.employee_id == other)).all()
    assert runs and all(run.outputs == {} for run in runs)
