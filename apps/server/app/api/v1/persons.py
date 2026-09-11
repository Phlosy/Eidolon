"""/persons —— T2.1 Person 读面（个人资源，company scope）。

与市场读面（T2.3 `/market/*`）**刻意分开**：这里服务"自有 person"
（培养期持有方 或 本公司在职），市场服务"公开挂牌的 person"（设计 §6/D4）。
无权限一律 404（不泄露存在性）。

端点刻意收敛为 1 个聚合 + 2 个可分页的大集合（避免 chatty）：

    GET /persons/{person_id}             统一投影（identity/traits/competencies/knowledge）
                                          + ?include=timeline,evidence 附带大集合
    GET /persons/{person_id}/timeline    履历时间线（倒序，可分页）
    GET /persons/{person_id}/evidence    证据（倒序，可分页/可过滤，与员工读面同构）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.schemas.cultivation import EducationEventOut
from app.schemas.person import PersonEvidenceOut, PersonProfileOut
from app.schemas.position_fit import PositionFitOut
from app.talent.fit import serializer as fit_serializer
from app.talent.fit import service as fit_service
from app.talent.person import access as person_access
from app.talent.person import read_model

router = APIRouter(prefix="/persons", tags=["person"])


@router.get("/{person_id}", response_model=PersonProfileOut)
def get_person_profile(
    person_id: int,
    include: str = Query(
        default="",
        description=(
            "逗号分隔的可选区块：timeline,evidence"
            "（缺省只返回 identity/traits/competencies/knowledge）"
        ),
    ),
    timeline_limit: int = Query(default=50, ge=1, le=200),
    evidence_limit: int = Query(default=20, ge=0, le=200),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    person = person_access.visible_person_or_404(db, person_id, company_id)
    includes = frozenset(part.strip().lower() for part in include.split(",") if part.strip())
    try:
        return read_model.person_profile(
            db,
            person,
            includes=includes,
            timeline_limit=timeline_limit,
            evidence_limit=evidence_limit,
        )
    except ValueError as exc:  # 未知 include 区块：显式 422，不静默忽略
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{person_id}/timeline", response_model=list[EducationEventOut])
def get_person_timeline(
    person_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """履历时间线（倒序 = 最新在前）：培养 UI / 市场档案 / 员工档案共用同一投影。"""
    person = person_access.visible_person_or_404(db, person_id, company_id)
    return read_model.timeline_out(db, person.id, limit=limit, offset=offset)


@router.get("/{person_id}/evidence", response_model=list[PersonEvidenceOut])
def get_person_evidence(
    person_id: int,
    source_type: str | None = Query(None, description="EvidenceSourceKind，如 edu_course"),
    competency: str | None = Query(None, description="competency code（或数字 id），可选"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    person = person_access.visible_person_or_404(db, person_id, company_id)
    return read_model.evidence_out(
        db,
        person.id,
        limit=limit,
        offset=offset,
        source_type=source_type,
        competency=competency,
    )


@router.get("/{person_id}/fit", response_model=PositionFitOut)
def get_person_fit(
    person_id: int,
    position_definition_id: int = Query(..., description="职位（本公司或全局模板）"),
    profile_version_id: int | None = Query(None, description="显式画像版本（缺省 ACTIVE）"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """Person × Position 的匹配（T2.5）：市场候选人与在册员工共用同一引擎。

    公司边界：person 必须是自有的（T2.1 策略）；职位属本公司或全局模板，否则 404。
    """
    person = person_access.visible_person_or_404(db, person_id, company_id)
    try:
        result = fit_service.calculate_person_fit(
            db,
            person_id=int(person.id),
            position_definition_id=position_definition_id,
            company_id=company_id,
            profile_version_id=profile_version_id,
        )
    except fit_service.FitDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return fit_serializer.serialize(result)
