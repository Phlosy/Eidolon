"""默认岗位画像模板（P7）—— Engineer / QA / Researcher / PM / Manager。

docs/position-competency-profile.md §15~§19。**权重是数据不是硬编码**；Fit 时按
Required/Preferred、General/Professional 分组归一（P8 约定）。模板只回答“岗位需要
什么”；员工数据一概不碰。

seed 幂等（code+version）：已有 ACTIVE v1 就跳过。绑定 assessment_profile_id
（P6 档案：engineer→software_engineer、qa→qa_engineer、researcher→researcher、
pm/ceo→manager）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import AssessmentProfile
from app.models.position import PositionDefinition
from app.services import position_profile as profiles

logger = logging.getLogger(__name__)


@dataclass
class TemplateRequirement:
    code: str
    requirement_type: str
    minimum_score: int | None
    target_score: int | None
    minimum_confidence: float | None
    critical: bool = False
    weight: float = 1.0
    notes: str = ""


@dataclass
class PositionTemplate:
    position_code: str
    assessment_profile_code: str
    requirements: tuple[TemplateRequirement, ...]


def _req(code, rtype, minimum, target, *, confidence=None, critical=False, weight=1.0, notes=""):
    return TemplateRequirement(
        code=code,
        requirement_type=rtype,
        minimum_score=minimum,
        target_score=target,
        minimum_confidence=confidence,
        critical=critical,
        weight=weight,
        notes=notes,
    )


SOFTWARE_ENGINEER = PositionTemplate(
    position_code="engineer",
    assessment_profile_code="software_engineer",
    requirements=(
        _req("analysis_problem_solving", "required", 65, 80, weight=0.10),
        _req("planning_organization", "preferred", 50, 70, weight=0.05),
        _req("execution", "required", 70, 85, weight=0.15),
        _req("communication", "preferred", 50, 65, weight=0.05),
        _req("collaboration", "preferred", 50, 70, weight=0.05),
        _req("learning_growth", "preferred", 55, 75, weight=0.05),
        _req("quality_reliability", "required", 65, 80, weight=0.15),
        _req("efficiency_resource_awareness", "preferred", 50, 70, weight=0.05),
        _req("backend_engineering", "required", 65, 80, critical=True, weight=0.15),
        _req("testing", "required", 60, 75, weight=0.10),
        _req("system_architecture", "preferred", 45, 65, weight=0.05),
        _req("code_quality", "required", 60, 80, weight=0.05),
    ),
)

QA_ENGINEER = PositionTemplate(
    position_code="qa_engineer",
    assessment_profile_code="qa_engineer",
    requirements=(
        _req("analysis_problem_solving", "required", 60, 75, weight=0.10),
        _req("planning_organization", "preferred", 50, 70, weight=0.05),
        _req("execution", "required", 60, 80, weight=0.10),
        _req("communication", "required", 60, 80, weight=0.05),
        _req("quality_reliability", "required", 70, 85, weight=0.15),
        _req("efficiency_resource_awareness", "preferred", 45, 65, weight=0.05),
        _req("test_design", "required", 70, 85, critical=True, weight=0.10),
        _req("test_automation", "preferred", 55, 75, weight=0.05),
        _req("defect_analysis", "required", 65, 80, weight=0.10),
        _req("regression_testing", "required", 60, 80, weight=0.05),
        _req("acceptance_testing", "required", 65, 80, weight=0.10),
        _req("quality_assurance", "required", 65, 85, critical=True, weight=0.10),
    ),
)

RESEARCHER = PositionTemplate(
    position_code="researcher",
    assessment_profile_code="researcher",
    requirements=(
        _req("analysis_problem_solving", "required", 70, 85, weight=0.15),
        _req("planning_organization", "preferred", 50, 70, weight=0.10),
        _req("communication", "required", 60, 80, weight=0.10),
        _req("learning_growth", "required", 65, 85, weight=0.05),
        _req("quality_reliability", "required", 60, 80, weight=0.10),
        _req("efficiency_resource_awareness", "required", 65, 80, weight=0.10),
        _req("information_retrieval", "required", 70, 85, weight=0.10),
        _req("source_evaluation", "required", 75, 90, critical=True, weight=0.15),
        _req("evidence_synthesis", "required", 70, 90, critical=True, weight=0.15),
        _req("literature_review", "preferred", 55, 75, weight=0.05),
        _req("experiment_design", "preferred", 55, 75, weight=0.05),
        _req("technical_writing", "required", 65, 85, weight=0.10),
    ),
)

PRODUCT_MANAGER = PositionTemplate(
    position_code="product_manager",
    assessment_profile_code="manager",
    requirements=(
        _req("analysis_problem_solving", "required", 70, 85, weight=0.15),
        _req("planning_organization", "required", 70, 85, weight=0.15),
        _req("communication", "required", 70, 85, weight=0.15),
        _req("collaboration", "preferred", 60, 80, weight=0.05),
        _req("decision_making", "required", 70, 85, weight=0.10),
        _req("management_leadership", "required", 60, 80, weight=0.10),
        _req("requirements_analysis", "required", 70, 85, critical=True, weight=0.10),
        _req("product_design", "preferred", 60, 80, weight=0.05),
        _req("prioritization", "required", 70, 85, critical=True, weight=0.10),
        _req("acceptance_design", "preferred", 60, 80, weight=0.05),
        _req("stakeholder_communication", "preferred", 65, 85, weight=0.05),
    ),
)

CEO_MANAGER = PositionTemplate(
    position_code="ceo",
    assessment_profile_code="manager",
    requirements=(
        _req("analysis_problem_solving", "preferred", 70, 85, weight=0.05),
        _req("planning_organization", "required", 75, 90, weight=0.10),
        _req("communication", "required", 75, 90, weight=0.10),
        _req("collaboration", "preferred", 70, 85, weight=0.05),
        _req("management_leadership", "required", 80, 95, critical=True, weight=0.15),
        _req("decision_making", "required", 80, 95, critical=True, weight=0.15),
        _req("quality_reliability", "required", 65, 85, weight=0.10),
        _req("efficiency_resource_awareness", "preferred", 60, 80, weight=0.05),
        _req("strategic_planning", "required", 80, 95, critical=True, weight=0.10),
        _req("delegation", "required", 75, 90, weight=0.10),
        _req("resource_allocation", "required", 75, 90, weight=0.10),
        _req("risk_management", "preferred", 65, 85, weight=0.05),
        _req("team_development", "preferred", 65, 85, weight=0.05),
        _req("organizational_coordination", "preferred", 65, 85, weight=0.05),
    ),
)

DEFAULT_TEMPLATES: tuple[PositionTemplate, ...] = (
    SOFTWARE_ENGINEER,
    QA_ENGINEER,
    RESEARCHER,
    PRODUCT_MANAGER,
    CEO_MANAGER,
)


def _definition_by_code(db: Session, code: str, company_id: int) -> PositionDefinition | None:
    return db.scalar(
        select(PositionDefinition).where(
            PositionDefinition.company_id == company_id,
            PositionDefinition.code == code,
        )
    )


def seed_default_profiles(db: Session, company_id: int) -> int:
    """为内置职位种子 ACTIVE v1 画像（幂等：已有 v1 就跳过）。返回新建行数。"""
    created = 0
    for template in DEFAULT_TEMPLATES:
        position = _definition_by_code(db, template.position_code, company_id)
        if position is None:
            continue
        existing = profiles.active_profile(db, position.id)
        if existing is not None:
            continue
        version = profiles.create_draft(db, position)
        for requirement in template.requirements:
            try:
                profiles.add_requirement(
                    db,
                    version,
                    _competency_id(db, requirement.code),
                    company_id=company_id,
                    requirement_type=requirement.requirement_type,
                    minimum_score=requirement.minimum_score,
                    target_score=requirement.target_score,
                    minimum_confidence=requirement.minimum_confidence,
                    critical=requirement.critical,
                    weight=requirement.weight,
                    notes=requirement.notes,
                )
            except profiles.ProfileError as exc:  # 目录缺能力：跳过该项不炸 seed
                logger.warning(
                    "模板 %s 缺少能力 %s：%s", template.position_code, requirement.code, exc
                )
                continue
            created += 1
        assessment = db.scalar(
            select(AssessmentProfile).where(
                AssessmentProfile.code == template.assessment_profile_code,
                AssessmentProfile.version == 1,
            )
        )
        if assessment is not None:
            position.assessment_profile_id = assessment.id
        profiles.publish_profile(db, version)
        created += 1
    return created


def _competency_id(db: Session, code: str) -> int:
    from app.models.competency import CompetencyDefinition, CompetencyDomain

    value = db.scalar(
        select(CompetencyDefinition.id)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == code,
            CompetencyDomain.company_id.is_(None),
        )
    )
    if value is None:  # pragma: no cover - 目录种子先于画像
        raise profiles.ProfileError(f"competency catalog 缺 {code}")
    return int(value)
