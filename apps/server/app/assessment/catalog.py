"""AssessmentProfile 内置模板（P6）—— 第一版四类：Engineer / QA / Researcher / Manager。

规格：docs/assessment-system.md §2（权重表）+ docs/evidence-pipeline.md §11~§15。
**权重是数据不是硬编码**：seed 幂等写入（code+version 唯一），改模板 = 新 version，
历史 AssessmentRun 仍指向当时的 version（§十）。

criterion → 多 competency 映射（contribution_weight；Σ≤1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import (
    AssessmentCriterion,
    AssessmentCriterionCompetency,
    AssessmentProfile,
)


@dataclass(frozen=True)
class CriterionSpec:
    code: str
    name: str
    description: str
    weight: float
    evidence_kinds: list[str] = field(default_factory=list)
    #: [(competency_code, contribution_weight, evidence_type)]
    competencies: list[tuple[str, float, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileSpec:
    code: str
    name: str
    description: str
    position_codes: tuple[str, ...] = ()
    min_evidence_count: int = 1
    criteria: tuple[CriterionSpec, ...] = ()


def _c(code, name, weight, competencies, kinds=(), description=""):
    return CriterionSpec(
        code=code,
        name=name,
        description=description or name,
        weight=weight,
        evidence_kinds=list(kinds),
        competencies=[(c, w, e) for c, w, e in competencies],
    )


PROFILE_SPECS: tuple[ProfileSpec, ...] = (
    ProfileSpec(
        code="software_engineer",
        name="Software Engineer",
        description="软件工程师考核档案 v1",
        position_codes=("engineer",),
        criteria=(
            _c(
                "correctness",
                "Correctness",
                0.20,
                [
                    ("analysis_problem_solving", 0.5, ""),
                    ("quality_reliability", 0.5, ""),
                ],
                kinds=["test", "review", "task"],
            ),
            _c(
                "requirement_coverage",
                "Requirement Coverage",
                0.15,
                [
                    ("analysis_problem_solving", 0.6, ""),
                    ("planning_organization", 0.4, ""),
                ],
                kinds=["task", "review"],
            ),
            _c(
                "testing",
                "Testing",
                0.15,
                [
                    ("testing", 0.6, ""),
                    ("quality_reliability", 0.4, ""),
                ],
                kinds=["test", "review"],
            ),
            _c(
                "code_quality",
                "Code Quality",
                0.15,
                [
                    ("code_quality", 1.0, ""),
                ],
                kinds=["review", "artifact"],
            ),
            _c(
                "architecture",
                "Architecture",
                0.10,
                [
                    ("system_architecture", 1.0, ""),
                ],
                kinds=["review", "artifact"],
            ),
            _c(
                "delivery_reliability",
                "Delivery Reliability",
                0.10,
                [
                    ("execution", 0.6, ""),
                    ("quality_reliability", 0.4, ""),
                ],
                kinds=["task", "review"],
            ),
            _c(
                "problem_solving",
                "Problem Solving",
                0.10,
                [
                    ("analysis_problem_solving", 0.6, ""),
                    ("decision_making", 0.4, ""),
                ],
                kinds=["task", "review"],
            ),
            _c(
                "efficiency",
                "Efficiency",
                0.05,
                [
                    ("efficiency_resource_awareness", 1.0, ""),
                ],
                kinds=["task"],
            ),
        ),
    ),
    ProfileSpec(
        code="qa_engineer",
        name="QA Engineer",
        description="QA 工程师考核档案 v1",
        position_codes=("qa_engineer",),
        criteria=(
            _c(
                "test_design",
                "Test Design",
                0.15,
                [
                    ("test_design", 0.7, ""),
                    ("quality_reliability", 0.3, ""),
                ],
                kinds=["test"],
            ),
            _c(
                "test_coverage",
                "Test Coverage",
                0.15,
                [
                    ("test_design", 0.5, ""),
                    ("quality_reliability", 0.5, ""),
                ],
                kinds=["test"],
            ),
            _c(
                "defect_discovery",
                "Defect Discovery",
                0.15,
                [
                    ("defect_analysis", 1.0, ""),
                ],
                kinds=["test", "review"],
            ),
            _c(
                "regression_reliability",
                "Regression Reliability",
                0.15,
                [
                    ("regression_testing", 0.7, ""),
                    ("execution", 0.3, ""),
                ],
                kinds=["test"],
            ),
            _c(
                "acceptance_quality",
                "Acceptance Quality",
                0.15,
                [
                    ("acceptance_testing", 0.6, ""),
                    ("quality_reliability", 0.4, ""),
                ],
                kinds=["review", "test"],
            ),
            _c(
                "reporting_quality",
                "Reporting Quality",
                0.10,
                [
                    ("communication", 1.0, ""),
                ],
                kinds=["review", "artifact"],
            ),
            _c(
                "requirement_coverage",
                "Requirement Coverage",
                0.15,
                [
                    ("analysis_problem_solving", 0.5, ""),
                    ("quality_reliability", 0.5, ""),
                ],
                kinds=["review"],
            ),
        ),
    ),
    ProfileSpec(
        code="researcher",
        name="Researcher",
        description="研究员考核档案 v1",
        position_codes=("researcher",),
        criteria=(
            _c(
                "source_quality",
                "Source Quality",
                0.20,
                [
                    ("source_evaluation", 1.0, ""),
                ],
                kinds=["task", "artifact"],
            ),
            _c(
                "evidence_coverage",
                "Evidence Coverage",
                0.15,
                [
                    ("evidence_synthesis", 0.6, ""),
                    ("information_retrieval", 0.4, ""),
                ],
                kinds=["task"],
            ),
            _c(
                "accuracy",
                "Accuracy",
                0.15,
                [
                    ("analysis_problem_solving", 1.0, ""),
                ],
                kinds=["review"],
            ),
            _c(
                "synthesis",
                "Synthesis",
                0.15,
                [
                    ("evidence_synthesis", 1.0, ""),
                ],
                kinds=["artifact", "review"],
            ),
            _c(
                "insight",
                "Insight",
                0.05,
                [
                    ("analysis_problem_solving", 1.0, ""),
                ],
                kinds=["review"],
            ),
            _c(
                "technical_communication",
                "Technical Communication",
                0.10,
                [
                    ("technical_writing", 1.0, ""),
                ],
                kinds=["artifact"],
            ),
            _c(
                "research_efficiency",
                "Research Efficiency",
                0.20,
                [
                    ("efficiency_resource_awareness", 0.5, ""),
                    ("information_retrieval", 0.5, ""),
                ],
                kinds=["task"],
            ),
        ),
    ),
    ProfileSpec(
        code="manager",
        name="Manager / CEO",
        description="管理与领导考核档案 v1（覆盖 ceo / product_manager 任职）",
        position_codes=("ceo", "product_manager"),
        criteria=(
            _c(
                "planning",
                "Planning",
                0.15,
                [
                    ("planning_organization", 1.0, ""),
                ],
                kinds=["task", "review"],
            ),
            _c(
                "delegation",
                "Delegation",
                0.15,
                [
                    ("management_leadership", 0.5, ""),
                    ("delegation", 0.5, ""),
                ],
                kinds=["task", "review"],
            ),
            _c(
                "decision_quality",
                "Decision Quality",
                0.20,
                [
                    ("decision_making", 1.0, ""),
                ],
                kinds=["review"],
            ),
            _c(
                "resource_allocation",
                "Resource Allocation",
                0.15,
                [
                    ("resource_allocation", 1.0, ""),
                ],
                kinds=["task"],
            ),
            _c(
                "risk_management",
                "Risk Management",
                0.05,
                [
                    ("risk_management", 1.0, ""),
                ],
                kinds=["review"],
            ),
            _c(
                "project_outcome",
                "Project Outcome",
                0.15,
                [
                    ("execution", 1.0, ""),
                ],
                kinds=["task"],
            ),
            _c(
                "team_development",
                "Team Development",
                0.05,
                [
                    ("team_development", 1.0, ""),
                ],
                kinds=["review"],
            ),
            _c(
                "communication",
                "Communication",
                0.10,
                [
                    ("communication", 1.0, ""),
                ],
                kinds=["review"],
            ),
        ),
    ),
)


def _comp_id(db: Session, code: str) -> int:
    from app.models.competency import CompetencyDefinition, CompetencyDomain

    value = db.scalar(
        select(CompetencyDefinition.id)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == code,
            CompetencyDomain.company_id.is_(None),
        )
    )
    if value is None:  # pragma: no cover - 目录种子先于 profiles
        raise ValueError(f"competency catalog 缺 {code}")
    return int(value)


def seed_profiles(db: Session) -> int:
    """幂等种子四个内置档案（code+version=1）。返回新建对象数。"""
    created = 0
    for spec in PROFILE_SPECS:
        profile = db.scalar(
            select(AssessmentProfile).where(
                AssessmentProfile.code == spec.code,
                AssessmentProfile.version == 1,
            )
        )
        if profile is None:
            profile = AssessmentProfile(
                code=spec.code,
                version=1,
                name=spec.name,
                description=spec.description,
                applies_to_kind="position",
                min_evidence_count=spec.min_evidence_count,
                half_life_days=90.0,
                algorithm_version="assessment-profile-v1",
                built_in=True,
            )
            db.add(profile)
            db.flush()
            created += 1
        for criterion in spec.criteria:
            existing_criterion = db.scalar(
                select(AssessmentCriterion.id).where(
                    AssessmentCriterion.profile_id == profile.id,
                    AssessmentCriterion.code == criterion.code,
                )
            )
            if existing_criterion is None:
                db.add(
                    AssessmentCriterion(
                        profile_id=profile.id,
                        code=criterion.code,
                        name=criterion.name,
                        description=criterion.description,
                        weight=criterion.weight,
                        order_index=0,
                        evidence_kinds=list(criterion.evidence_kinds),
                    )
                )
                db.flush()
                criterion_row = db.scalar(
                    select(AssessmentCriterion).where(
                        AssessmentCriterion.profile_id == profile.id,
                        AssessmentCriterion.code == criterion.code,
                    )
                )
                created += 1
            else:
                criterion_row = db.get(AssessmentCriterion, existing_criterion)
            for competency_code, contribution, evidence_type in criterion.competencies:
                comp_id = _comp_id(db, competency_code)
                exists = db.scalar(
                    select(AssessmentCriterionCompetency.id).where(
                        AssessmentCriterionCompetency.criterion_id == criterion_row.id,
                        AssessmentCriterionCompetency.competency_definition_id == comp_id,
                    )
                )
                if exists is None:
                    db.add(
                        AssessmentCriterionCompetency(
                            criterion_id=criterion_row.id,
                            competency_definition_id=comp_id,
                            contribution_weight=contribution,
                            evidence_type=evidence_type,
                        )
                    )
                    created += 1
    return created


PROFILE_FOR_POSITION: dict[str, str] = {
    "engineer": "software_engineer",
    "qa_engineer": "qa_engineer",
    "researcher": "researcher",
    "ceo": "manager",
    "product_manager": "manager",
}


def profile_code_for_position(position_code: str | None) -> str | None:
    return PROFILE_FOR_POSITION.get(position_code or "")


def resolve_position_profile(db: Session, employee_id: int) -> AssessmentProfile | None:
    """按员工当前任职职位选择档案模板（无任职/无映射 ⇒ None）。"""
    from app.repositories import position as position_repo

    current = position_repo.employee_current_position(db, employee_id)
    code = current.code if current is not None else None
    profile_code = profile_code_for_position(code)
    if profile_code is None:
        return None
    return db.scalar(
        select(AssessmentProfile).where(
            AssessmentProfile.code == profile_code,
            AssessmentProfile.version == 1,
        )
    )
