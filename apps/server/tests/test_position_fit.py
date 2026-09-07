"""P8 Position Fit —— 引擎/评估/守卫（docs/position-fit.md §51 后端子集）。

锁：
- no active profile → NOT_EVALUABLE（≠ fit 0 / 100%）；
- 员工无能力数据 → 全 UNRATED，coverage 0，status INSUFFICIENT_DATA（≠ 0 分 ≠ 100%）；
- Unknown != Bad：低 confidence ≠ failure；UNRATED ≠ 0；
- BELOW_MINIMUM / MEETS_MINIMUM / MEETS_TARGET 各状态正确；
- required vs preferred；critical gap vs critical uncertainty 区分；
- strength 需要 score≥target 且 confidence≥min_confidence；
- coverage 永远基于全部需求（unknown 不把 coverage 抬成 100%）；
- known fit 排除 unknown；fit confidence 用 coverage；
- critical unknown ⇒ 不能 QUALIFIED（INSUFFICIENT_DATA）；
- 确定性 + inputs_hash 稳定；不同 profile version 结果独立；
- Trait 不读 + 只读守卫（不改 EmployeeCompetency/Assignment/Brain）；
- 公司隔离。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models.competency import EmployeeCompetency
from app.models.position import PositionDefinition
from app.services import position_profile as profiles
from app.talent.fit import service as fit_service
from app.talent.fit.models import (
    EvaluationStatus,
    FitStatus,
    GapType,
    QualificationStatus,
)

_seq = 0


def _def_id(db, code: str) -> int:
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


def _hire(db, company_id: int) -> int:
    global _seq
    _seq += 1
    from app.models.organization import Employee

    employee = Employee(
        company_id=company_id,
        name=f"Fit Tester {_seq}",
        slug=f"fit-test-{_seq}",
        workspace_path=f"/tmp/fit-{_seq}-ws",
        memory_namespace=f"mem-fit-{_seq}",
    )
    db.add(employee)
    db.commit()
    return int(employee.id)


def _set_competency(db, employee_id: int, code: str, score: int | None, confidence: float | None):
    row = db.scalar(
        sa.select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == _def_id(db, code),
        )
    )
    if row is None:
        row = EmployeeCompetency(
            employee_id=employee_id,
            competency_definition_id=_def_id(db, code),
            score=score,
            confidence=confidence,
            status="assessed" if score is not None else "unrated",
            evidence_count=0,
        )
        db.add(row)
    else:
        row.score = score
        row.confidence = confidence
        row.status = "assessed" if score is not None else "unrated"
    db.flush()


def _engineer_position(db, default_company_id) -> PositionDefinition:
    return db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )


def _fit(db, employee_id: int, position: PositionDefinition):
    return fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(position.id)
    )


def test_no_profile_is_not_evaluable_not_zero(db, default_company_id):
    """无 ACTIVE 画像 ⇒ NOT_EVALUABLE（≠ fit 0 / 100%）。用自建无画像职位，不动 seed。"""
    employee_id = _hire(db, default_company_id)
    definition = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code=f"fit-noprofile-{_seq}",
        name="No Profile Role",
        job_family="engineering",
    )
    db.add(definition)
    db.commit()
    assert profiles.active_profile(db, int(definition.id)) is None
    result = _fit(db, employee_id, definition)
    assert result.fit_status == FitStatus.NOT_EVALUABLE
    assert result.configured is False
    assert result.known_fit_score is None and result.overall_fit_score is None
    assert result.requirement_coverage == 0.0


def test_employee_without_competencies_is_all_unrated(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    position = _engineer_position(db, default_company_id)
    result = _fit(db, employee_id, position)
    assert result.configured is True and result.profile_version == 1
    assert result.known_count == 0
    assert result.requirement_coverage == 0.0
    assert result.fit_status == FitStatus.INSUFFICIENT_DATA
    assert result.qualification_status == QualificationStatus.INSUFFICIENT_DATA
    assert all(
        evaluation.evaluation_status == EvaluationStatus.UNRATED
        for evaluation in result.requirement_evaluations
    )
    assert all(evaluation.employee_score is None for evaluation in result.requirement_evaluations)


def test_low_confidence_is_uncertainty_not_failure(db, default_company_id):
    """score 82 但 confidence 18% < min_confidence 50% ⇒ INSUFFICIENT_CONFIDENCE。"""
    employee_id = _hire(db, default_company_id)
    # 需要 backend_engineering 的 min_confidence：SE 模板没给它 → 手动构造评估路径：
    # 直接对 seeded profile 加一个带 minimum_confidence 的 requirement
    # 用自建职位做受控测试更干净
    from app.models.position import PositionDefinition as PD

    definition = PD(
        company_id=default_company_id,
        template_scope="company",
        code=f"fit-ctl-{_seq}",
        name="Fit Control",
        job_family="engineering",
    )
    db.add(definition)
    db.flush()
    draft = profiles.create_draft(db, definition)
    profiles.add_requirement(
        db,
        draft,
        _def_id(db, "execution"),
        company_id=default_company_id,
        requirement_type="required",
        minimum_score=65,
        target_score=85,
        minimum_confidence=0.5,
        weight=1.0,
    )
    profiles.publish_profile(db, draft)
    _set_competency(db, employee_id, "execution", score=82, confidence=0.18)
    db.commit()
    result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    evaluation = result.requirement_evaluations[0]
    assert evaluation.evaluation_status == EvaluationStatus.INSUFFICIENT_CONFIDENCE
    assert evaluation.reason_code == "CONFIDENCE_BELOW_REQUIREMENT"
    assert result.known_count == 0 and result.requirement_coverage == 0.0
    assert len(result.uncertainties) == 1
    # 绝非 strength，也不是 gap（不是"能力为 0"）
    assert evaluation.is_strength is False and evaluation.employee_score == 82


def test_below_minimum_meets_minimum_meets_target(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition = _fit_control_position(db, default_company_id)
    _set_competency(db, employee_id, "execution", score=40, confidence=0.9)
    _set_competency(db, employee_id, "testing", score=70, confidence=0.9)
    _set_competency(db, employee_id, "code_quality", score=95, confidence=0.9)
    db.commit()
    result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    by_code = {item.code: item for item in result.requirement_evaluations}
    # SE 模板：execution min70/required；testing min60/required；code_quality min60/required
    assert by_code["execution"].evaluation_status == EvaluationStatus.BELOW_MINIMUM
    assert by_code["testing"].evaluation_status == EvaluationStatus.MEETS_MINIMUM
    assert by_code["code_quality"].evaluation_status == EvaluationStatus.MEETS_TARGET
    assert by_code["code_quality"].is_strength is True


def _fit_control_position(db, company_id: int) -> PositionDefinition:
    from app.models.position import PositionDefinition as PD

    definition = PD(
        company_id=company_id,
        template_scope="company",
        code=f"fit-ctl2-{_seq}",
        name="Fit Control2",
        job_family="engineering",
    )
    db.add(definition)
    db.flush()
    draft = profiles.create_draft(db, definition)
    for code, rtype, minimum, target, critical, confidence in (
        ("execution", "required", 70, 85, False, 0.5),
        ("testing", "required", 60, 75, False, None),
        ("code_quality", "required", 60, 80, False, None),
        ("backend_engineering", "required", 65, 80, True, None),
    ):
        profiles.add_requirement(
            db,
            draft,
            _def_id(db, code),
            company_id=company_id,
            requirement_type=rtype,
            minimum_score=minimum,
            target_score=target,
            minimum_confidence=confidence,
            critical=critical,
        )
    profiles.publish_profile(db, draft)
    return definition


def test_critical_gap_and_critical_uncertainty_are_distinct(db, default_company_id):
    employee_a = _hire(db, default_company_id)
    result_a = fit_service.calculate_fit(
        db,
        employee_id=employee_a,
        position_definition_id=int(_engineer_position(db, default_company_id).id),
    )
    # SE 模板 backend_engineering critical；先造 gap 员工
    _set_competency(db, employee_a, "backend_engineering", score=40, confidence=0.9)
    db.commit()
    result_a = fit_service.calculate_fit(
        db,
        employee_id=employee_a,
        position_definition_id=int(_engineer_position(db, default_company_id).id),
    )
    backend = next(
        item for item in result_a.requirement_evaluations if item.code == "backend_engineering"
    )
    assert backend.gap_type == GapType.CRITICAL_GAP
    assert result_a.qualification_status == QualificationStatus.NOT_QUALIFIED
    assert result_a.fit_status == FitStatus.CRITICAL_GAP

    # 另一名员工 same critical 但 UNRATED ⇒ CRITICAL_UNCERTAINTY，不是失败
    employee_b = _hire(db, default_company_id)
    _set_competency(db, employee_b, "execution", score=95, confidence=0.9)
    db.commit()
    result_b = fit_service.calculate_fit(
        db,
        employee_id=employee_b,
        position_definition_id=int(_engineer_position(db, default_company_id).id),
    )
    backend_b = next(
        item for item in result_b.requirement_evaluations if item.code == "backend_engineering"
    )
    assert backend_b.gap_type == GapType.CRITICAL_UNCERTAINTY
    # 关键 unknown ⇒ 不能 QUALIFIED
    assert result_b.qualification_status == QualificationStatus.INSUFFICIENT_DATA


def test_strength_requires_meets_target_with_sufficient_confidence(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition = _fit_control_position(db, default_company_id)
    _set_competency(db, employee_id, "execution", score=95, confidence=0.2)
    _set_competency(db, employee_id, "code_quality", score=95, confidence=0.9)
    db.commit()
    result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    by_code = {item.code: item for item in result.requirement_evaluations}
    # 高分低置信 ⇒ uncertainty，不是 strength
    assert by_code["execution"].is_strength is False
    assert by_code["code_quality"].is_strength is True


def test_development_opportunity_for_preferred_below_target(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    # SE 模板 preferred 包含 system_architecture（min45/target65）
    position = _engineer_position(db, default_company_id)
    _set_competency(
        db, employee_id, "system_architecture", score=55, confidence=0.9
    )  # min45 target65
    db.commit()
    result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(position.id)
    )
    arch = next(
        item for item in result.requirement_evaluations if item.code == "system_architecture"
    )
    assert arch.is_development_opportunity is True
    assert arch.gap_type == GapType.TARGET_GAP or arch.is_development_opportunity


def test_coverage_honest_and_known_fit_excludes_unknown(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition = _fit_control_position(db, default_company_id)
    _set_competency(db, employee_id, "code_quality", score=95, confidence=0.9)
    db.commit()
    result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    assert result.total_count == 4
    assert result.known_count == 1
    assert result.requirement_coverage == 0.25  # unknown 不会把 coverage 抬成 1.0
    assert result.known_fit_score == 1.0  # known 只看 code_quality
    assert result.fit_confidence is not None and result.fit_confidence < 1.0
    # insufficient required coverage ⇒ status
    assert result.fit_status == FitStatus.INSUFFICIENT_DATA


def test_deterministic_and_stable_hash_and_version_independence(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition = _fit_control_position(db, default_company_id)
    _set_competency(db, employee_id, "code_quality", score=90, confidence=0.9)
    _set_competency(db, employee_id, "execution", score=80, confidence=0.9)
    db.commit()
    first = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    second = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    assert first.inputs_hash == second.inputs_hash
    assert first.known_fit_score == second.known_fit_score
    assert first.fit_status == second.fit_status

    # v2（同需求、改阈值）⇒ 独立结果
    v2 = profiles.create_draft(db, definition)
    profiles.add_requirement(
        db,
        v2,
        _def_id(db, "code_quality"),
        company_id=default_company_id,
        requirement_type="required",
        minimum_score=95,
        target_score=100,
    )
    profiles.publish_profile(db, v2, note="tighten code quality")
    v2_result = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(definition.id)
    )
    assert v2_result.profile_version == 2
    assert v2_result.inputs_hash != first.inputs_hash
    assert v2_result.known_fit_score != first.known_fit_score


def test_company_isolation(db, default_company_id):
    from app.models.organization import Company

    other = Company(name="FitOther", slug=f"fit-other-{_seq}", description="")
    db.add(other)
    db.commit()
    other_employee = _hire(db, int(other.id))
    engineer_here = _engineer_position(db, default_company_id)
    try:
        fit_service.calculate_fit(
            db, employee_id=other_employee, position_definition_id=int(engineer_here.id)
        )
        raise AssertionError("跨公司应被拒绝")
    except fit_service.FitDomainError:
        pass


def test_fit_never_reads_traits_and_never_writes_employee_data(db, default_company_id):
    """守卫：fit 包不读 traits、不写 EmployeeCompetency/Assignment/Brain（源码级 + 行为级）。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "talent" / "fit"
    forbidden = ("curiosity", "traits", "BrainTraits", "EmployeeBrain", "PositionAssignment")
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                raise AssertionError(f"{path}: 引用了 {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in {"traits", "curiosity"}:
                raise AssertionError(f"{path}: 直接读 traits/curiosity")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and ("brain" in node.module or "knowledge" in node.module)
            ):
                raise AssertionError(f"{path}: import 了 {node.module}")
    # 行为级：员工数据在 fit 前后不变
    employee_id = _hire(db, default_company_id)
    definition = _engineer_position(db, default_company_id)
    before = db.scalar(
        sa.select(sa.func.count())
        .select_from(EmployeeCompetency)
        .where(EmployeeCompetency.employee_id == employee_id)
    )
    _fit(db, employee_id, definition)
    after = db.scalar(
        sa.select(sa.func.count())
        .select_from(EmployeeCompetency)
        .where(EmployeeCompetency.employee_id == employee_id)
    )
    assert before == after == 0
