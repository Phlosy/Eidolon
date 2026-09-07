"""Position Candidate Analysis API（P9 §25）—— 一个职位 × 一批人才的候选分析（只读）。

不做自动任命/调岗；不提供全公司 ranking（P10）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.position import PositionDefinition
from app.schemas.position_candidates import CandidateAnalysisOut
from app.services import candidate_analysis as candidates

router = APIRouter(tags=["position-candidates"])


@router.get(
    "/position-definitions/{position_definition_id}/candidates",
    response_model=CandidateAnalysisOut,
)
def position_candidates(
    position_definition_id: int,
    include_assigned: bool = Query(
        default=False, description="默认只分析 AVAILABLE；true 时加入已任职"
    ),
    q: str | None = Query(None, alias="search", description="按姓名/简称搜索"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    position = db.get(PositionDefinition, position_definition_id)
    if position is None:
        raise HTTPException(status_code=404, detail="position not found")
    if position.company_id is not None and position.company_id != company_id:
        raise HTTPException(status_code=404, detail="position not found")
    try:
        return candidates.analyze(
            db,
            position_definition_id=position_definition_id,
            include_assigned=include_assigned,
            search=q,
            limit=limit,
            offset=offset,
        )
    except candidates.fit_service.FitDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
