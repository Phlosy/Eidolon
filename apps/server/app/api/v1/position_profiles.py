"""Position Competency Profile API（P7，docs/position-competency-profile.md §20~§21）。

读：get profile（versions 历史）/ org 列表摘要（batch，无 N+1）/ templates。
写：create draft、clone、add/update/remove requirement（仅 DRAFT）、publish/retire。

禁止：直接在 Employee/Position 员工侧改岗位标准；编辑只发生于 profile version；
ACTIVE/RETIRED 不可改（改标准=新建版本）。无 Fit 计算，无 Employee 数据改动。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.assessment import AssessmentProfile
from app.models.competency import (
    PositionCompetencyRequirement,
)
from app.models.position import PositionDefinition
from app.models.position_profile import PositionProfileVersion
from app.schemas.position_profile import (
    CloneIn,
    PositionCompetencyProfileOut,
    PositionProfileSummaryOut,
    PublishIn,
    RequirementIn,
    RequirementPatch,
    RetireIn,
)
from app.services import position_profile as profiles

router = APIRouter(tags=["position-profile"])


def _position_or_404(
    db: Session, position_id: int, company_id: int | None, *, write: bool = False
) -> PositionDefinition:
    position = db.get(PositionDefinition, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="position not found")
    if position.company_id is not None and position.company_id != company_id:
        raise HTTPException(status_code=404, detail="position not found")
    if write and position.company_id is None:
        raise HTTPException(status_code=403, detail="system template 只读；请克隆到本公司")
    return position


def _version_or_404(db: Session, version_id: int, company_id: int | None) -> PositionProfileVersion:
    version = db.get(PositionProfileVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="profile version not found")
    position = db.get(PositionDefinition, version.position_definition_id)
    if position is None or (company_id is not None and position.company_id != company_id):
        raise HTTPException(status_code=404, detail="profile version not found")
    return version


def _profile_error(exc: profiles.ProfileError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _requirement_counts(db: Session, version_ids: list[int]) -> dict[int, int]:
    if not version_ids:
        return {}
    rows = db.execute(
        select(PositionCompetencyRequirement.position_profile_version_id, func.count())
        .where(PositionCompetencyRequirement.position_profile_version_id.in_(version_ids))
        .group_by(PositionCompetencyRequirement.position_profile_version_id)
    ).all()
    return {int(version_id): int(count) for version_id, count in rows}


@router.get("/position-profiles", response_model=list[PositionProfileSummaryOut])
def list_position_profiles(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """组织列表摘要（batch：一次查版本 + 一次查计数，不逐个 N+1）。"""
    positions = list(
        db.scalars(
            select(PositionDefinition)
            .where(PositionDefinition.company_id == company_id)
            .order_by(PositionDefinition.id)
        )
    )
    versions = list(
        db.scalars(
            select(PositionProfileVersion).where(
                PositionProfileVersion.position_definition_id.in_(
                    [position.id for position in positions]
                )
            )
        )
    )
    active_by_position: dict[int, PositionProfileVersion] = {}
    all_version_ids: list[int] = []
    for version in versions:
        all_version_ids.append(version.id)
        if version.status == profiles.PROFILE_STATUS_ACTIVE:
            active_by_position.setdefault(version.position_definition_id, version)
    counts = _requirement_counts(db, all_version_ids)
    assessments = {
        profile.id: profile
        for profile in db.scalars(
            select(AssessmentProfile).where(
                AssessmentProfile.id.in_(
                    {
                        position.assessment_profile_id
                        for position in positions
                        if position.assessment_profile_id
                    }
                )
            )
        )
    }
    out = []
    for position in positions:
        active = active_by_position.get(position.id)
        assessment = (
            assessments.get(position.assessment_profile_id)
            if position.assessment_profile_id
            else None
        )
        out.append(
            {
                "position_definition_id": position.id,
                "code": position.code,
                "name": position.name,
                "active_version": active.version if active else None,
                "profile_status": active.status if active else None,
                "requirement_count": sum(
                    counts.get(version.id, 0)
                    for version in versions
                    if version.position_definition_id == position.id
                ),
                "assessment_profile_code": assessment.code if assessment else None,
            }
        )
    return out


@router.get("/position-profiles/templates")
def list_profile_templates(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """可作 Clone 模板的公司 ACTIVE 画像（position code/version/requirement 数）。"""
    positions = list(
        db.scalars(select(PositionDefinition).where(PositionDefinition.company_id == company_id))
    )
    versions = [profiles.active_profile(db, position.id) for position in positions]
    versions = [version for version in versions if version is not None]
    counts = _requirement_counts(db, [version.id for version in versions])
    return [
        {
            "template_version_id": version.id,
            "position_code": next(
                p.code for p in positions if p.id == version.position_definition_id
            ),
            "position_name": next(
                p.name for p in positions if p.id == version.position_definition_id
            ),
            "version": version.version,
            "requirement_count": counts.get(version.id, 0),
        }
        for version in versions
    ]


@router.get(
    "/position-definitions/{position_id}/competency-profile",
    response_model=PositionCompetencyProfileOut,
)
def get_position_profile(
    position_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """当前画像：优先 ACTIVE，其次 DRAFT；无版本 = Not Configured（≠空画像）。"""
    position = _position_or_404(db, position_id, company_id)
    version = profiles.active_profile(db, position.id) or profiles.draft_profile(db, position.id)
    requirements = profiles.requirements_for(db, version) if version else []
    payload = profiles.profile_payload(db, position, version, requirements)
    versions = profiles.profile_versions_of(db, position.id)
    counts = _requirement_counts(db, [v.id for v in versions])
    payload["versions"] = [
        {
            "id": v.id,
            "version": v.version,
            "status": v.status,
            "effective_from": v.effective_from,
            "effective_to": v.effective_to,
            "published_at": v.published_at,
            "published_note": v.published_note,
            "requirement_count": counts.get(v.id, 0),
        }
        for v in versions
    ]
    return payload


@router.post(
    "/position-definitions/{position_id}/competency-profile/versions",
    response_model=PositionCompetencyProfileOut,
    status_code=201,
)
def create_profile_draft(
    position_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    position = _position_or_404(db, position_id, company_id, write=True)
    version = profiles.create_draft(db, position)
    db.commit()
    requirements = profiles.requirements_for(db, version)
    payload = profiles.profile_payload(db, position, version, requirements)
    payload["versions"] = []
    return payload


@router.post(
    "/position-definitions/{position_id}/competency-profile/clone",
    response_model=PositionCompetencyProfileOut,
    status_code=201,
)
def clone_profile(
    position_id: int,
    payload: CloneIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    position = _position_or_404(db, position_id, company_id, write=True)
    template = _version_or_404(db, payload.template_version_id, company_id)
    try:
        version = profiles.clone_profile_to(db, template, position)
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    db.commit()
    requirements = profiles.requirements_for(db, version)
    out = profiles.profile_payload(db, position, version, requirements)
    out["versions"] = []
    return out


@router.post(
    "/position-profile-versions/{version_id}/requirements",
    status_code=201,
)
def add_requirement(
    version_id: int,
    payload: RequirementIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    version = _version_or_404(db, version_id, company_id)
    position = db.get(PositionDefinition, version.position_definition_id)
    assert position is not None
    try:
        row = profiles.add_requirement(
            db,
            version,
            payload.competency_definition_id,
            company_id=position.company_id or company_id,
            requirement_type=payload.requirement_type,
            minimum_score=payload.minimum_score,
            target_score=payload.target_score,
            minimum_confidence=payload.minimum_confidence,
            critical=payload.critical,
            priority=payload.priority,
            weight=payload.weight,
            notes=payload.notes,
        )
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    db.commit()
    return profiles._requirement_payload(db, row)


@router.patch(
    "/position-profile-versions/{version_id}/requirements/{requirement_id}",
)
def update_requirement(
    version_id: int,
    requirement_id: int,
    payload: RequirementPatch,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    version = _version_or_404(db, version_id, company_id)
    try:
        row = profiles.update_requirement(db, version, requirement_id, **payload.model_dump())
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    db.commit()
    return profiles._requirement_payload(db, row)


@router.delete(
    "/position-profile-versions/{version_id}/requirements/{requirement_id}",
    status_code=204,
)
def remove_requirement(
    version_id: int,
    requirement_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> None:
    version = _version_or_404(db, version_id, company_id)
    try:
        profiles.remove_requirement(db, version, requirement_id)
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    db.commit()


@router.post("/position-profile-versions/{version_id}/publish")
def publish_profile(
    version_id: int,
    payload: PublishIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    version = _version_or_404(db, version_id, company_id)
    try:
        profiles.publish_profile(db, version, note=payload.note)
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    return {
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
    }


@router.post("/position-profile-versions/{version_id}/retire")
def retire_profile(
    version_id: int,
    payload: RetireIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    version = _version_or_404(db, version_id, company_id)
    try:
        profiles.retire_profile(db, version, note=payload.note)
    except profiles.ProfileError as exc:
        raise _profile_error(exc) from exc
    return {"version_id": version.id, "version": version.version, "status": version.status}
