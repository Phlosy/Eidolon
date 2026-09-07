"""P6 AssessmentProfile 种子契约（docs/assessment-system.md §2 / evidence-pipeline §11~15）。

锁：四套内置模板幂等 seed；criterion → 多 competency 映射 + contribution_weight；
code+version 唯一（版本化）；按任职职位解析档案。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.assessment import catalog
from app.models.assessment import (
    AssessmentCriterion,
    AssessmentCriterionCompetency,
    AssessmentProfile,
)

PROFILE_CODES = {"software_engineer", "qa_engineer", "researcher", "manager"}
CRITERIA_COUNTS = {"software_engineer": 8, "qa_engineer": 7, "researcher": 7, "manager": 8}


def test_four_profiles_are_seeded_and_idempotent(db):
    profiles = db.scalars(sa.select(AssessmentProfile).where(AssessmentProfile.version == 1)).all()
    assert {profile.code for profile in profiles} == PROFILE_CODES
    for code, count in CRITERIA_COUNTS.items():
        profile = db.scalar(
            sa.select(AssessmentProfile).where(
                AssessmentProfile.code == code, AssessmentProfile.version == 1
            )
        )
        criteria = db.scalars(
            sa.select(AssessmentCriterion).where(AssessmentCriterion.profile_id == profile.id)
        ).all()
        assert len(criteria) == count, f"{code} criteria 数应为 {count}"
    # 幂等：重复 seed 不新增
    before = db.scalar(sa.select(sa.func.count()).select_from(AssessmentProfile))
    assert catalog.seed_profiles(db) == 0
    db.rollback()
    assert db.scalar(sa.select(sa.func.count()).select_from(AssessmentProfile)) == before


def test_criterion_maps_to_multiple_competencies_with_contribution_weights(db):
    """Delivery Reliability → execution(0.6) + quality_reliability(0.4)，不压成一对一。"""
    profile = db.scalar(
        sa.select(AssessmentProfile).where(
            AssessmentProfile.code == "software_engineer", AssessmentProfile.version == 1
        )
    )
    criterion = db.scalar(
        sa.select(AssessmentCriterion).where(
            AssessmentCriterion.profile_id == profile.id,
            AssessmentCriterion.code == "delivery_reliability",
        )
    )
    mappings = db.scalars(
        sa.select(AssessmentCriterionCompetency).where(
            AssessmentCriterionCompetency.criterion_id == criterion.id
        )
    ).all()
    assert len(mappings) == 2
    mapping = {
        db.scalar(
            sa.text("SELECT d.code FROM competency_definitions d WHERE d.id = :id"),
            {"id": m.competency_definition_id},
        ): m.contribution_weight
        for m in mappings
    }
    assert mapping.get("execution") == pytest.approx(0.6)
    assert mapping.get("quality_reliability") == pytest.approx(0.4)


def test_profile_code_version_is_unique(db):
    """同一 (code, version) 不允许重复（版本化模板的机器保证）。"""
    db.add(
        AssessmentProfile(
            code="software_engineer",
            version=1,
            name="dup",
            description="",
            applies_to_kind="position",
            built_in=True,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.commit()
    db.rollback()


def test_resolve_position_profile_by_assignment(db, default_company_id):
    """engineer 任职 ⇒ software_engineer 档案；无任职 ⇒ None。"""
    from app.models.organization import Department, Employee
    from app.models.position import PositionAssignment, PositionDefinition, PositionSlot

    employee = Employee(
        company_id=default_company_id,
        name="Resolve Tester",
        slug="resolve-tester",
        workspace_path="/tmp/resolve-ws",
        memory_namespace="mem-resolve",
    )
    db.add(employee)
    db.flush()
    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == default_company_id, Department.slug == "engineering"
        )
    )
    definition = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert department is not None and definition is not None
    assert catalog.resolve_position_profile(db, int(employee.id)) is None  # 未任职

    slot = PositionSlot(
        company_id=default_company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code="RES-1",
        headcount_index=7777,
        administrative_status="active",
    )
    db.add(slot)
    db.flush()
    db.add(
        PositionAssignment(
            employee_id=employee.id,
            position_slot_id=slot.id,
            department_id=department.id,
            assignment_type="primary",
            is_primary=True,
        )
    )
    db.commit()
    profile = catalog.resolve_position_profile(db, int(employee.id))
    assert profile is not None and profile.code == "software_engineer"
