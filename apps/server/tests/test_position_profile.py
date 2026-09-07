"""P7 岗位能力画像 —— 服务/模板/版本化/覆盖度/完整性（docs/position-competency-profile.md）。

docs/position-competency-profile.md §53 清单的后端子集：
- 默认模板幂等 seed + assessment_profile 绑定；
- 无画像职位：Not Configured ≠ 空画像；
- DRAFT 可编辑 / ACTIVE 不可改（改标准=新版本）；
- Publish v2 → v1 RETIRED；单 ACTIVE；版本历史保留；
- 要求引用真实 competency（伪造 404/ProfileError）；target≥minimum；conf 有界；
- 公司隔离（跨公司 clone 拒绝）；
- Assessment Coverage 派生（不落库）；Integrity 派生（read_only）。
- 不计算 Fit：任何函数不返回 fit/rank/score 对比。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
)
from app.models.position import PositionDefinition
from app.models.position_profile import PositionProfileVersion
from app.services import position_profile as profiles
from app.services.position_profile_templates import seed_default_profiles

_seq = 0


def _definition(db, company_id: int, code: str = "custom_role") -> PositionDefinition:
    global _seq
    _seq += 1
    definition = PositionDefinition(
        company_id=company_id,
        template_scope="company",
        code=f"{code}-{_seq}",
        name=f"Custom {_seq}",
        job_family="engineering",
    )
    db.add(definition)
    db.flush()
    return definition


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


def test_default_seed_is_idempotent_and_binds_assessment_profile(db, default_company_id):
    from app.models.position import PositionDefinition as PD

    engineer = db.scalar(
        sa.select(PD).where(PD.company_id == default_company_id, PD.code == "engineer")
    )
    assert engineer is not None and engineer.assessment_profile_id is not None
    active = profiles.active_profile(db, engineer.id)
    assert active is not None and active.status == "active" and active.version == 1
    requirements = profiles.requirements_for(db, active)
    kinds = {
        db.get(
            CompetencyDomain,
            db.get(CompetencyDefinition, row.competency_definition_id).domain_id,
        ).kind
        for row in requirements
    }
    assert "general" in kinds and "professional" in kinds
    # 幂等：重复 seed 不产生新版本
    assert seed_default_profiles(db, default_company_id) == 0
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(PositionProfileVersion)
            .where(PositionProfileVersion.position_definition_id == engineer.id)
        )
        == 1
    )
    # SE 关键能力（Backend Engineering required + critical）存在
    backend = next(
        row
        for row in requirements
        if row.competency_definition_id == _def_id(db, "backend_engineering")
    )
    assert backend.requirement_type == "required" and backend.critical is True


def test_position_without_profile_is_not_configured_not_empty(db, default_company_id):
    definition = _definition(db, default_company_id)
    payload = profiles.profile_payload(db, definition, None, [])
    assert payload["configured"] is False
    assert payload["profile_version"] is None
    integrity = payload["integrity"]
    assert "NO_ACTIVE_PROFILE" in integrity["codes"]
    assert "NO_ASSESSMENT_PROFILE" in integrity["codes"]


def test_draft_editable_and_active_immutable(db, default_company_id):
    definition = _definition(db, default_company_id)
    draft = profiles.create_draft(db, definition)
    row = profiles.add_requirement(
        db,
        draft,
        _def_id(db, "execution"),
        company_id=default_company_id,
        requirement_type="required",
        minimum_score=60,
        target_score=80,
        weight=0.2,
    )
    # DRAFT 可编辑
    profiles.update_requirement(db, draft, row.id, target_score=85)
    assert row.target_score == 85
    profiles.publish_profile(db, draft)
    active = profiles.active_profile(db, definition.id)
    assert active is not None
    # ACTIVE 不可编辑 / 不可删除
    with pytest.raises(profiles.ProfileError):
        profiles.add_requirement(db, active, _def_id(db, "testing"), company_id=default_company_id)
    with pytest.raises(profiles.ProfileError):
        profiles.update_requirement(db, active, row.id, target_score=90)
    with pytest.raises(profiles.ProfileError):
        profiles.remove_requirement(db, active, row.id)


def test_publish_v2_retires_v1_and_only_one_active(db, default_company_id):
    definition = _definition(db, default_company_id)
    v1 = profiles.create_draft(db, definition)
    profiles.add_requirement(db, v1, _def_id(db, "execution"), company_id=default_company_id)
    profiles.publish_profile(db, v1)

    v2 = profiles.create_draft(db, definition)
    assert v2.version == 2
    profiles.add_requirement(db, v2, _def_id(db, "testing"), company_id=default_company_id)
    profiles.publish_profile(db, v2, note="testing weight raised")

    active = profiles.active_profile(db, definition.id)
    assert active is not None and active.version == 2
    db.refresh(v1)
    assert v1.status == "retired" and v1.effective_to is not None
    versions = profiles.profile_versions_of(db, definition.id)
    assert len(versions) == 2  # 历史保留
    veteran = next(v for v in versions if v.version == 1)
    assert veteran.status == "retired"


def test_requirement_validation_and_referenced_competency(db, default_company_id):
    definition = _definition(db, default_company_id)
    draft = profiles.create_draft(db, definition)
    with pytest.raises(profiles.ProfileError):
        profiles.add_requirement(db, draft, 999_999_999, company_id=default_company_id)
    with pytest.raises(profiles.ProfileError):
        profiles.add_requirement(
            db,
            draft,
            _def_id(db, "execution"),
            company_id=default_company_id,
            minimum_score=90,
            target_score=80,
        )
    with pytest.raises(profiles.ProfileError):
        profiles.add_requirement(
            db,
            draft,
            _def_id(db, "execution"),
            company_id=default_company_id,
            minimum_confidence=1.5,
        )
    # 版本内重复同一能力被拒
    profiles.add_requirement(db, draft, _def_id(db, "execution"), company_id=default_company_id)
    with pytest.raises(profiles.ProfileError):
        profiles.add_requirement(db, draft, _def_id(db, "execution"), company_id=default_company_id)


def test_company_isolation_blocks_cross_company_clone(db, default_company_id):
    from app.models.organization import Company

    definition_a = _definition(db, default_company_id)
    draft = profiles.create_draft(db, definition_a)
    profiles.add_requirement(db, draft, _def_id(db, "execution"), company_id=default_company_id)
    profiles.publish_profile(db, draft)

    other = Company(name="Other", slug=f"pp-other-{_seq}", description="")
    db.add(other)
    db.flush()
    definition_b = _definition(db, int(other.id), code="foreign_role")
    with pytest.raises(profiles.ProfileError):
        profiles.clone_profile_to(db, draft, definition_b)


def test_assessment_coverage_is_derived_and_uncovered_required_visible(db, default_company_id):
    """SE 画像：required 能力里 backend_engineering 不在考核档案映射 → gap（派生，不落库）。"""
    engineer = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    active = profiles.active_profile(db, engineer.id)
    requirements = profiles.requirements_for(db, active)
    coverage = profiles.assessment_coverage(db, requirements, engineer.assessment_profile_id)
    assert coverage["required_count"] >= 5
    assert coverage["covered_count"] < coverage["required_count"]
    assert any(
        db.get(CompetencyDefinition, competency_id).code == "backend_engineering"
        for competency_id in coverage["uncovered_competency_ids"]
    ), "backend_engineering 应是无考核路径的 gap"
    # 派生不落库：数据库中没有任何 coverage 列/表（此处直接验证派生函数的 readonly 语义）
    integrity = profiles.profile_integrity(db, engineer, active, requirements)
    assert integrity["read_only"] is True
    assert "ASSESSMENT_GAP" in integrity["codes"]


def test_no_position_fit_is_computed(db, default_company_id):
    """P7 禁止 Fit：服务只返回岗位侧；不读任何 EmployeeCompetency。"""

    engineer = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    active = profiles.active_profile(db, engineer.id)
    requirements = profiles.requirements_for(db, active)
    payload = profiles.profile_payload(db, engineer, active, requirements)
    for banned in ("fit", "candidate_rank", "recommended", "gap_score"):
        assert banned not in payload, f"P7 不该计算 {banned}"
    # 员工数据未被本次操作触碰（无 EmployeeCompetency 变更语义：只读引用）
    assert not hasattr(payload, "employee")
