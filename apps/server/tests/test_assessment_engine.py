"""P6 Assessment Engine v2 —— 确定性、可审计、版本化考核。

docs/assessment-system.md §4~5 正式版 + evidence-pipeline §十九~§三十。

锁：
- 同证据+同 window+同档案版本+同引擎 ⇒ 同 inputs_hash、同输出（可重放）；
- criterion 可映射多能力（贡献权重生效）；
- 无证据/证据不足(<min_evidence_count) 不编分（UNRATED）；
- 单条证据不会产生假高置信度（provisional）；
- score 与 confidence 独立；
- trend 来自相邻两次 run；
- project_end 触发会跑 project 内员工的考核（profile_version 记录在 run）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.assessment import catalog
from app.models.assessment import AssessmentResult
from app.models.competency import AssessmentRun, CompetencyEvidence, EmployeeCompetency
from app.models.enums import CompetencyStatus
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.services import assessment as engine

_seq = 0


def _hire(db, default_company_id: int) -> int:
    global _seq
    _seq += 1
    employee = Employee(
        company_id=default_company_id,
        name=f"Engine Tester {_seq}",
        slug=f"engine-test-{_seq}",
        workspace_path=f"/tmp/eng-{_seq}-ws",
        memory_namespace=f"mem-eng-{_seq}",
    )
    db.add(employee)
    db.flush()
    return int(employee.id)


def _assign_engineer(db, employee_id: int) -> None:
    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == db.get(Employee, employee_id).company_id,
            Department.slug == "engineering",
        )
    )
    definition = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == db.get(Employee, employee_id).company_id,
            PositionDefinition.code == "engineer",
        )
    )
    slot = PositionSlot(
        company_id=definition.company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code=f"ENG-{_seq}",
        headcount_index=8000 + _seq,
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
        )
    )
    db.flush()


def _def(db, code: str) -> int:
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


def _add_evidence(
    db,
    employee_id: int,
    comp_code: str,
    *,
    signal: int,
    kind: str = "task",
    ref: str = "E1",
    project: int = 1,
    days_ago: int = 5,
    strength: float = 1.0,
    reliability: float | None = None,
) -> int:
    row = CompetencyEvidence(
        employee_id=employee_id,
        competency_definition_id=_def(db, comp_code),
        source_kind=kind,
        source_id=project * 1000 + _seq,
        source_ref=ref,
        signal=signal,
        strength=strength,
        reliability=reliability,
        occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
        metadata_json={"project_id": project},
    )
    db.add(row)
    db.flush()
    return int(row.id)


def _seed_engineer_evidence(db, employee_id: int) -> None:
    """给 engineer 期望的多个能力补证据（覆盖多个 criterion）。"""
    _add_evidence(db, employee_id, "execution", signal=82, kind="task", ref="T-exec")
    _add_evidence(db, employee_id, "quality_reliability", signal=85, kind="review", ref="R-q")
    _add_evidence(db, employee_id, "testing", signal=88, kind="test", ref="T-t1")
    _add_evidence(db, employee_id, "testing", signal=84, kind="review", ref="R-t")
    _add_evidence(db, employee_id, "code_quality", signal=86, kind="review", ref="R-cq")
    db.flush()


def test_run_produces_criterion_and_contribution_results_and_updates_competencies(
    db, default_company_id
):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _seed_engineer_evidence(db, employee_id)
    db.commit()

    profile = catalog.resolve_position_profile(db, employee_id)
    run = engine.run_assessment(db, employee_id, profile=profile, commit=True)

    assert run.profile_id == profile.id and run.profile_version == 1
    assert run.assessment_type == "manual"
    # criterion 结果行存在（每个有证据的 criterion）
    criterion_rows = db.scalars(
        sa.select(AssessmentResult).where(
            AssessmentResult.run_id == run.id, AssessmentResult.kind == "criterion"
        )
    ).all()
    assert criterion_rows
    contribution_rows = db.scalars(
        sa.select(AssessmentResult).where(
            AssessmentResult.run_id == run.id, AssessmentResult.kind == "contribution"
        )
    ).all()
    assert contribution_rows
    # 多能力被更新（execution 与 quality_reliability 都有输出）
    for code in ("execution", "quality_reliability", "testing"):
        assert code_mapped(db, run.outputs, code), f"{code} 应有输出"


def code_mapped(db, outputs: dict, code: str) -> bool:
    return any(str(_def(db, code)) == definition_id for definition_id in outputs)


def test_deterministic_replay_and_stable_inputs_hash(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _seed_engineer_evidence(db, employee_id)
    db.commit()
    profile = catalog.resolve_position_profile(db, employee_id)
    first = engine.run_assessment(db, employee_id, profile=profile, commit=True)
    second = engine.run_assessment(db, employee_id, profile=profile, commit=True)
    assert first.inputs_hash == second.inputs_hash
    # 去掉历史字段（previous/trend）后，两条 run 输出完全一致
    for definition_id, payload in first.outputs.items():
        replay = second.outputs.get(definition_id)
        if replay is None:
            continue
        for key in ("score", "confidence", "evidence_count", "status", "raw_score"):
            assert payload[key] == replay[key], (key, payload, replay)


def test_no_evidence_means_unrated_and_no_invented_run(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    db.commit()
    profile = catalog.resolve_position_profile(db, employee_id)
    run = engine.run_assessment(db, employee_id, profile=profile, commit=True)
    assert run.status == "completed"
    assert run.outputs == {}
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(EmployeeCompetency)
            .where(EmployeeCompetency.employee_id == employee_id)
        )
        == 0
    )


def test_min_evidence_threshold_keeps_unrated(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _add_evidence(db, employee_id, "execution", signal=80, kind="task", ref="single")
    db.commit()
    profile = catalog.resolve_position_profile(db, employee_id)
    profile.min_evidence_count = 2
    db.commit()
    run = engine.run_assessment(db, employee_id, profile=profile, commit=True)
    assert "execution" not in code_outputs(db, run.outputs), "证据不足不应给分"
    # 还原共享 seed 档案（该测试改了它，不能污染后续用例）
    profile.min_evidence_count = 1
    db.commit()


def code_outputs(db, outputs: dict) -> list[str]:
    result = []
    for definition_id in outputs:
        code = db.scalar(
            sa.text("SELECT d.code FROM competency_definitions d WHERE d.id = :id"),
            {"id": int(definition_id)},
        )
        result.append(code)
    return result


def test_one_evidence_does_not_produce_fake_high_confidence(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _add_evidence(db, employee_id, "execution", signal=95, kind="task", ref="single")
    db.commit()
    profile = catalog.resolve_position_profile(db, employee_id)
    run = engine.run_assessment(db, employee_id, profile=profile, commit=True)
    codes = code_outputs(db, run.outputs)
    assert "execution" in codes
    execution = run.outputs[str(_def(db, "execution"))]
    assert execution["score"] is not None
    assert execution["confidence"] < 0.4
    row = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row.status == CompetencyStatus.provisional.value


def test_trend_comes_from_prior_assessment(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _add_evidence(db, employee_id, "execution", signal=60, kind="task", ref="t0")
    db.commit()
    profile = catalog.resolve_position_profile(db, employee_id)
    engine.run_assessment(db, employee_id, profile=profile, commit=True)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row.trend is None  # 首次无历史

    _add_evidence(db, employee_id, "execution", signal=95, kind="review", ref="t1", project=9)
    db.commit()
    engine.run_assessment(db, employee_id, profile=profile, commit=True)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    )
    assert row.trend is not None and row.trend > 0


def test_inputs_hash_changes_with_profile_version(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    _seed_engineer_evidence(db, employee_id)
    db.commit()
    profile_v1 = catalog.resolve_position_profile(db, employee_id)
    base = engine.inputs_hash_for(
        employee_id=employee_id,
        profile=profile_v1,
        criteria=[],
        evidence_rows=[],
        window_from=None,
        window_to=datetime.now(UTC),
    )
    import copy

    v2 = copy.copy(profile_v1)
    v2.version = 2
    v2.min_evidence_count = 3
    changed = engine.inputs_hash_for(
        employee_id=employee_id,
        profile=v2,
        criteria=[],
        evidence_rows=[],
        window_from=None,
        window_to=datetime.now(UTC),
    )
    assert base != changed, "档案版本必须是 inputs_hash 的一部分"


def test_project_end_trigger_runs_assessments_for_participants(db, default_company_id):
    from app.models.project import Project, Task

    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    project = Project(company_id=default_company_id, name=f"Proj {_seq}")
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Implement Auth",
        kind="development",
        status="done",
        assignee_id=employee_id,
        sequence=1,
    )
    db.add(task)
    db.flush()
    _add_evidence(
        db, employee_id, "execution", signal=85, kind="task", ref="proj", project=project.id
    )
    db.commit()

    count = engine.run_project_end_assessments(db, int(project.id))
    assert count >= 1
    runs = db.scalars(
        sa.select(AssessmentRun).where(AssessmentRun.employee_id == employee_id)
    ).all()
    assert any(run.assessment_type == "project_end" for run in runs)
    project_run = next(run for run in runs if run.assessment_type == "project_end")
    assert project_run.profile_version == 1
    assert project_run.window_from is not None
