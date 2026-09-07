"""Position Profile Service —— 岗位能力画像（P7，docs/position-competency-profile.md）。

职责（只做“岗位这一侧”）：
- Draft 创建/编辑（校验 min/target/conf/weight/scope、版本内 competency 唯一）
- Publish（旧 ACTIVE→RETIRED，单 ACTIVE） / Retire
- Clone（从系统/公司模板 → 公司自有 Draft）
- 默认画像 seed（5 套，code 幂等）
- 读取（batch 序列化 + 派生 Coverage / Integrity —— 不落库，ADR-12）

**边界**：这里不计算 Position Fit、不读 EmployeeCompetency、不改任何员工数据；
Position 只回答“这个岗位需要什么能力”。要求挂在 profile_version 下，
ACTIVE/RETIRED 版本不可编辑（改标准 = 新版本）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import (
    AssessmentCriterion,
    AssessmentCriterionCompetency,
    AssessmentProfile,
)
from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
    PositionCompetencyRequirement,
)
from app.models.enums import CompetencyKind
from app.models.position import PositionDefinition
from app.models.position_profile import PositionProfileVersion

REQUIREMENT_TYPES = ("required", "preferred")

PROFILE_STATUS_DRAFT = "draft"
PROFILE_STATUS_ACTIVE = "active"
PROFILE_STATUS_RETIRED = "retired"


class ProfileError(ValueError):
    """画像业务异常 ⇒ API 层转 4xx。"""


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def profile_versions_of(db: Session, position_definition_id: int) -> list[PositionProfileVersion]:
    return list(
        db.scalars(
            select(PositionProfileVersion)
            .where(PositionProfileVersion.position_definition_id == position_definition_id)
            .order_by(PositionProfileVersion.version.desc())
        )
    )


def active_profile(db: Session, position_definition_id: int) -> PositionProfileVersion | None:
    return db.scalar(
        select(PositionProfileVersion).where(
            PositionProfileVersion.position_definition_id == position_definition_id,
            PositionProfileVersion.status == PROFILE_STATUS_ACTIVE,
        )
    )


def draft_profile(db: Session, position_definition_id: int) -> PositionProfileVersion | None:
    return db.scalar(
        select(PositionProfileVersion).where(
            PositionProfileVersion.position_definition_id == position_definition_id,
            PositionProfileVersion.status == PROFILE_STATUS_DRAFT,
        )
    )


def get_version(db: Session, version_id: int) -> PositionProfileVersion | None:
    return db.get(PositionProfileVersion, version_id)


def requirements_for(
    db: Session, version: PositionProfileVersion
) -> list[PositionCompetencyRequirement]:
    return list(
        db.scalars(
            select(PositionCompetencyRequirement)
            .where(PositionCompetencyRequirement.position_profile_version_id == version.id)
            .order_by(PositionCompetencyRequirement.priority, PositionCompetencyRequirement.id)
        )
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_requirement_payload(
    *,
    requirement_type: str,
    minimum_score: int | None,
    target_score: int | None,
    minimum_confidence: float | None,
    weight: float,
) -> None:
    if requirement_type not in REQUIREMENT_TYPES:
        raise ProfileError(f"requirement_type 必须是 {'/'.join(REQUIREMENT_TYPES)}")
    for label, value in (("minimum_score", minimum_score), ("target_score", target_score)):
        if value is not None and not (0 <= int(value) <= 100):
            raise ProfileError(f"{label} 必须在 0~100")
    if (
        minimum_score is not None
        and target_score is not None
        and int(target_score) < int(minimum_score)
    ):
        raise ProfileError("target_score 不能小于 minimum_score")
    if minimum_confidence is not None and not (0.0 <= float(minimum_confidence) <= 1.0):
        raise ProfileError("minimum_confidence 必须在 0~1")
    if weight < 0:
        raise ProfileError("weight 不能为负")


def _require_competency(
    db: Session, competency_definition_id: int, company_id: int
) -> CompetencyDefinition:
    definition = db.get(CompetencyDefinition, competency_definition_id)
    if definition is None:
        raise ProfileError("competency 不存在")
    domain = db.get(CompetencyDomain, definition.domain_id)
    if domain is None or (domain.company_id is not None and domain.company_id != company_id):
        raise ProfileError("competency 不在该公司可见目录")
    return definition


# ---------------------------------------------------------------------------
# Draft / Publish / Retire / Clone
# ---------------------------------------------------------------------------


def create_draft(
    db: Session,
    position: PositionDefinition,
    *,
    user_id: int | None = None,
    clone_version_id: int | None = None,
) -> PositionProfileVersion:
    existing_draft = draft_profile(db, position.id)
    if existing_draft is not None:
        return existing_draft
    active = active_profile(db, position.id)
    next_version = (active.version if active else 0) + 1
    version = PositionProfileVersion(
        position_definition_id=position.id,
        version=next_version,
        status=PROFILE_STATUS_DRAFT,
        created_by_user_id=user_id,
    )
    db.add(version)
    db.flush()
    if clone_version_id is not None:
        source = get_version(db, clone_version_id)
        if source is None or source.position_definition_id != position.id:
            raise ProfileError("clone 源版本不存在")
        for requirement in requirements_for(db, source):
            db.add(
                PositionCompetencyRequirement(
                    position_profile_version_id=version.id,
                    competency_definition_id=requirement.competency_definition_id,
                    requirement_type=requirement.requirement_type,
                    minimum_score=requirement.minimum_score,
                    target_score=requirement.target_score,
                    minimum_confidence=requirement.minimum_confidence,
                    critical=requirement.critical,
                    priority=requirement.priority,
                    weight=requirement.weight,
                    notes=requirement.notes,
                )
            )
        db.flush()
    return version


def add_requirement(
    db: Session,
    version: PositionProfileVersion,
    competency_definition_id: int,
    *,
    company_id: int,
    requirement_type: str = "required",
    minimum_score: int | None = None,
    target_score: int | None = None,
    minimum_confidence: float | None = None,
    critical: bool = False,
    priority: int = 0,
    weight: float = 1.0,
    notes: str = "",
) -> PositionCompetencyRequirement:
    if version.status != PROFILE_STATUS_DRAFT:
        raise ProfileError("只有 DRAFT 版本可以编辑（改正式标准请新建版本）")
    _validate_requirement_payload(
        requirement_type=requirement_type,
        minimum_score=minimum_score,
        target_score=target_score,
        minimum_confidence=minimum_confidence,
        weight=weight,
    )
    definition = _require_competency(db, competency_definition_id, company_id)
    existing = db.scalar(
        select(PositionCompetencyRequirement).where(
            PositionCompetencyRequirement.position_profile_version_id == version.id,
            PositionCompetencyRequirement.competency_definition_id == definition.id,
        )
    )
    if existing is not None:
        raise ProfileError("同一画像版本不能重复声明同一能力")
    row = PositionCompetencyRequirement(
        position_profile_version_id=version.id,
        competency_definition_id=definition.id,
        requirement_type=requirement_type,
        minimum_score=int(minimum_score) if minimum_score is not None else None,
        target_score=int(target_score) if target_score is not None else None,
        minimum_confidence=(float(minimum_confidence) if minimum_confidence is not None else None),
        critical=critical,
        priority=priority,
        weight=weight,
        notes=notes,
    )
    db.add(row)
    db.flush()
    return row


def update_requirement(
    db: Session,
    version: PositionProfileVersion,
    requirement_id: int,
    **changes,
) -> PositionCompetencyRequirement:
    if version.status != PROFILE_STATUS_DRAFT:
        raise ProfileError("只有 DRAFT 版本可以编辑（改正式标准请新建版本）")
    row = db.get(PositionCompetencyRequirement, requirement_id)
    if row is None or row.position_profile_version_id != version.id:
        raise ProfileError("requirement 不存在")
    merged = {
        "requirement_type": row.requirement_type,
        "minimum_score": row.minimum_score,
        "target_score": row.target_score,
        "minimum_confidence": row.minimum_confidence,
        "critical": row.critical,
        "priority": row.priority,
        "weight": row.weight,
        "notes": row.notes,
    }
    merged.update({key: value for key, value in changes.items() if value is not None})
    _validate_requirement_payload(
        requirement_type=merged["requirement_type"],
        minimum_score=merged["minimum_score"],
        target_score=merged["target_score"],
        minimum_confidence=merged["minimum_confidence"],
        weight=merged["weight"],
    )
    row.requirement_type = merged["requirement_type"]
    row.minimum_score = merged["minimum_score"]
    row.target_score = merged["target_score"]
    row.minimum_confidence = merged["minimum_confidence"]
    row.critical = merged["critical"]
    row.priority = merged["priority"]
    row.weight = merged["weight"]
    if "notes" in changes:
        row.notes = changes["notes"]
    db.flush()
    return row


def remove_requirement(db: Session, version: PositionProfileVersion, requirement_id: int) -> None:
    if version.status != PROFILE_STATUS_DRAFT:
        raise ProfileError("只有 DRAFT 版本可以编辑（改正式标准请新建版本）")
    row = db.get(PositionCompetencyRequirement, requirement_id)
    if row is None or row.position_profile_version_id != version.id:
        raise ProfileError("requirement 不存在")
    db.delete(row)
    db.flush()


def publish_profile(
    db: Session,
    version: PositionProfileVersion,
    *,
    user_id: int | None = None,
    note: str = "",
    now: datetime | None = None,
) -> PositionProfileVersion:
    """发布：旧 ACTIVE → RETIRED，本版本 → ACTIVE（单 ACTIVE 由部分唯一索引兜底）。"""
    if version.status != PROFILE_STATUS_DRAFT:
        raise ProfileError("只能发布 DRAFT 版本")
    if not requirements_for(db, version):
        raise ProfileError("不能发布没有能力要求的画像（Not Configured 是有意义的空）")
    moment = now or datetime.now(UTC)
    position_id = version.position_definition_id
    active = active_profile(db, position_id)
    if active is not None:
        active.status = PROFILE_STATUS_RETIRED
        active.effective_to = moment
    version.status = PROFILE_STATUS_ACTIVE
    version.effective_from = moment
    version.effective_to = None
    version.published_at = moment
    version.published_by_user_id = user_id
    version.published_note = note
    # 先落库再发事件（bus 用自己的 session，写锁未释放会撞 SQLite lock —— 与
    # position_service 的模式一致：服务拥有这次状态变更的提交）
    db.commit()
    from app.events.bus import bus

    position = db.get(PositionDefinition, position_id)
    bus.publish(
        "position.profile_published",
        {
            "position_definition_id": position_id,
            "position_code": position.code if position else None,
            "version": version.version,
            "published_by_user_id": user_id,
            "note": note,
        },
        company_id=position.company_id if position else None,
    )
    return version


def retire_profile(
    db: Session,
    version: PositionProfileVersion,
    *,
    note: str = "",
    now: datetime | None = None,
) -> None:
    if version.status != PROFILE_STATUS_ACTIVE:
        raise ProfileError("只能停用 ACTIVE 版本")
    moment = now or datetime.now(UTC)
    version.status = PROFILE_STATUS_RETIRED
    version.effective_to = moment
    db.commit()
    from app.events.bus import bus

    position = db.get(PositionDefinition, version.position_definition_id)
    bus.publish(
        "position.profile_retired",
        {
            "position_definition_id": version.position_definition_id,
            "position_code": position.code if position else None,
            "version": version.version,
            "note": note,
        },
        company_id=position.company_id if position else None,
    )


def clone_profile_to(
    db: Session,
    source_version: PositionProfileVersion,
    target_position: PositionDefinition,
    *,
    user_id: int | None = None,
) -> PositionProfileVersion:
    """把某版本的画像克隆成目标职位的 Draft（公司标准 → 公司自有 Draft 用）。"""
    if (
        target_position.company_id
        != db.get(PositionDefinition, source_version.position_definition_id).company_id
    ):
        raise ProfileError("不能跨公司克隆画像")
    return create_draft(db, target_position, user_id=user_id, clone_version_id=source_version.id)


# ---------------------------------------------------------------------------
# 派生诊断（只读，不落库 —— ADR-12）
# ---------------------------------------------------------------------------


def assessment_criteria_for(db: Session, profile_id: int | None) -> list[AssessmentCriterion]:
    if profile_id is None:
        return []
    return list(
        db.scalars(select(AssessmentCriterion).where(AssessmentCriterion.profile_id == profile_id))
    )


def assessment_covered_competency_ids(db: Session, profile_id: int | None) -> set[int]:
    """该职位绑定的考核档案里，criterion 映射到哪些能力（覆盖面）。"""
    if profile_id is None:
        return set()
    criterion_ids = [criterion.id for criterion in assessment_criteria_for(db, profile_id)]
    if not criterion_ids:
        return set()
    return set(
        int(value)
        for value in db.scalars(
            select(AssessmentCriterionCompetency.competency_definition_id).where(
                AssessmentCriterionCompetency.criterion_id.in_(criterion_ids)
            )
        ).all()
    )


def assessment_coverage(
    db: Session, requirements: list[PositionCompetencyRequirement], profile_id: int | None
) -> dict:
    """Required 能力的考核覆盖度（派生；岗位标准 vs 考核制度是否匹配）。"""
    covered = assessment_covered_competency_ids(db, profile_id)
    required = [row for row in requirements if row.requirement_type == "required"]
    uncovered = [
        row.competency_definition_id
        for row in required
        if row.competency_definition_id not in covered
    ]
    return {
        "required_count": len(required),
        "covered_count": len(required) - len(uncovered),
        "uncovered_competency_ids": uncovered,
    }


def profile_integrity(
    db: Session,
    position: PositionDefinition,
    version: PositionProfileVersion | None,
    requirements: list[PositionCompetencyRequirement],
) -> dict:
    """画像完整性（只读诊断，不自动修）。返回 codes 列表 + 整体状态。"""
    codes: list[str] = []
    if version is None:
        codes.append("NO_ACTIVE_PROFILE")
    if position.assessment_profile_id is None:
        codes.append("NO_ASSESSMENT_PROFILE")
    definitions = {
        definition.id: definition
        for definition in db.scalars(
            select(CompetencyDefinition).where(
                CompetencyDefinition.id.in_({row.competency_definition_id for row in requirements})
            )
        )
    }
    for row in requirements:
        if row.competency_definition_id not in definitions:
            codes.append("MISSING_COMPETENCY")
        if row.minimum_score is not None and row.target_score is not None:
            if row.target_score < row.minimum_score:
                codes.append("INVALID_SCORE_RANGE")
    coverage = assessment_coverage(db, requirements, position.assessment_profile_id)
    if coverage["uncovered_competency_ids"]:
        codes.append("ASSESSMENT_GAP")
    unique = sorted(set(codes))
    status = "VALID" if not unique else ",".join(unique)
    return {
        "status": status,
        "codes": unique,
        "coverage": coverage,
        "read_only": True,
    }


# ---------------------------------------------------------------------------
# 序列化出口（历史/版本独立）
# ---------------------------------------------------------------------------


def _requirement_payload(db: Session, row: PositionCompetencyRequirement) -> dict:
    definition = db.get(CompetencyDefinition, row.competency_definition_id)
    domain = db.get(CompetencyDomain, definition.domain_id) if definition else None
    return {
        "id": row.id,
        "competency_definition_id": row.competency_definition_id,
        "code": definition.code if definition else "",
        "name": definition.name if definition else "",
        "domain_id": definition.domain_id if definition else None,
        "domain_code": domain.code if domain else "",
        "domain_name": domain.name if domain else "",
        "kind": domain.kind if domain else CompetencyKind.general.value,
        "requirement_type": row.requirement_type,
        "minimum_score": row.minimum_score,
        "target_score": row.target_score,
        "minimum_confidence": row.minimum_confidence,
        "critical": row.critical,
        "priority": row.priority,
        "weight": row.weight,
        "notes": row.notes,
    }


def profile_payload(
    db: Session,
    position: PositionDefinition,
    version: PositionProfileVersion | None,
    requirements: list[PositionCompetencyRequirement],
) -> dict:
    """岗位能力画像读面（唯一出口）。Not Configured（无版本）≠ 空画像。"""
    assessment = (
        db.get(AssessmentProfile, position.assessment_profile_id)
        if position.assessment_profile_id
        else None
    )
    payloads = [_requirement_payload(db, row) for row in requirements]
    general = [item for item in payloads if item["kind"] == CompetencyKind.general.value]
    professional = [item for item in payloads if item["kind"] == CompetencyKind.professional.value]
    integrity = profile_integrity(db, position, version, requirements)
    return {
        "position_definition_id": position.id,
        "position_code": position.code,
        "profile_version": version.version if version else None,
        "profile_status": version.status if version else None,
        "configured": version is not None,
        "effective_from": version.effective_from if version else None,
        "effective_to": version.effective_to if version else None,
        "assessment_profile": (
            {
                "id": assessment.id,
                "code": assessment.code,
                "version": assessment.version,
                "name": assessment.name,
                "algorithm_version": assessment.algorithm_version,
            }
            if assessment
            else None
        ),
        "general": general,
        "professional": professional,
        "integrity": integrity,
    }
