"""/market —— T2.3 本地人才市场（挂牌 / 下架 / 浏览 / 档案）。

**作用域**（设计 §6/D4）：市场是**受控的跨公司读取域**。
- 读（列表/档案）：任何登录用户可读**公开投影**（只含白名单字段，见 schemas/market.py）；
- 写（挂牌/下架）：仅限本公司持有的 person 与本公司发起的挂牌 —— 否则 404（不泄露存在性）。

不含任何交易语义（无价格/报单/结算 —— M1，设计 D10/D2）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.enums import MarketListingStatus
from app.models.position import PositionDefinition
from app.repositories import market as market_repo
from app.schemas.market import (
    MarketCandidateOut,
    MarketFitOut,
    MarketListingCreateIn,
    MarketListingOut,
    MarketListingPageOut,
    RecruitIn,
    RecruitOut,
)
from app.services import market as market_service
from app.services import recruitment as recruitment_service
from app.talent.fit import service as fit_service
from app.talent.market import read_model as market_read_model
from app.talent.market.contracts import MarketSearchQuery

router = APIRouter(prefix="/market", tags=["market"])


def _http_error(exc: market_service.MarketError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.reason)


def _market_position_or_404(
    db: Session, position_definition_id: int, company_id: int | None
) -> PositionDefinition:
    """市场侧职位解析：本公司职位或全局模板；其它一律 404（不泄露存在性）。"""
    position = db.get(PositionDefinition, position_definition_id)
    if position is None or (position.company_id is not None and position.company_id != company_id):
        raise HTTPException(status_code=404, detail="position not found")
    return position


@router.post("/listings", response_model=MarketListingOut)
def create_listing(
    payload: MarketListingCreateIn,
    response: Response,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """挂牌（幂等）：同 person 重复挂牌返回既有 active 挂牌（200），新建 201。

    资格不足（未 ready / 已入职）→ 409 + reason（`not_ready` / `employed`）。
    """
    try:
        view, created = market_service.list_person(
            db,
            person_id=payload.person_id,
            company_id=company_id,
            quality_tier=payload.quality_tier,
        )
    except market_service.MarketError as exc:
        raise _http_error(exc) from exc
    response.status_code = 201 if created else 200
    return {
        "listing_id": view.listing_id,
        "identity_id": view.identity_id,
        "name": view.name,
        "avatar": "",
        "origin": view.origin,
        "cultivation_state": view.cultivation_state,
        "status": view.status.value,
        "quality_tier": view.quality_tier,
        "listed_at": view.listed_at,
        "closed_at": view.closed_at,
        "listed_by": str(view.listed_by_participant_id),
    }


@router.delete("/listings/{listing_id}", status_code=204)
def delete_listing(
    listing_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> Response:
    """下架（幂等）：重复下架仍 204；非本公司挂牌 → 404。"""
    try:
        market_service.delist(db, listing_id=listing_id, company_id=company_id)
    except market_service.MarketError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.get("/listings", response_model=MarketListingPageOut)
def list_listings(
    text: str | None = Query(None, description="按姓名 / 身份 ID 模糊搜索"),
    origin: str | None = Query(None, description="trained | blank | issued"),
    quality_tier: str | None = Query(None, description="发行方档位（T2.4）"),
    position_definition_id: int | None = Query(
        None, description="带此参数时附 Fit 摘要并按匹配度排序（不筛人：未知仍列出）"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """在市人才列表（仅 active 挂牌，公开投影）。

    `position_definition_id`（T2.5）：附 Fit 摘要 + 按匹配度排序 —— **永不剔除**
    未评估的人（Unknown != Bad）。
    """
    position = (
        _market_position_or_404(db, position_definition_id, company_id)
        if position_definition_id is not None
        else None
    )
    query = MarketSearchQuery(
        text=text,
        origin=origin,
        quality_tier=quality_tier,
        position_definition_id=position_definition_id,
        limit=limit,
        offset=offset,
    )
    return market_service.search(db, query, position=position, company_id=company_id)


@router.get("/listings/{listing_id}", response_model=MarketCandidateOut)
def get_listing(
    listing_id: int,
    timeline_limit: int = Query(default=50, ge=1, le=200),
    evidence_limit: int = Query(default=20, ge=0, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """候选人公开档案：时间线 / 人格 / 能力画像（score+confidence 并列）/ 证据下钻。

    只对 **active** 挂牌开放：下架或被招募后不再对外可读（404）。
    """
    try:
        return market_service.listing_detail(
            db, listing_id, timeline_limit=timeline_limit, evidence_limit=evidence_limit
        )
    except market_service.MarketError as exc:
        raise _http_error(exc) from exc


@router.get("/listings/{listing_id}/fit", response_model=MarketFitOut)
def get_listing_fit(
    listing_id: int,
    position_definition_id: int = Query(..., description="职位（本公司或全局模板）"),
    profile_version_id: int | None = Query(None, description="显式画像版本（缺省 ACTIVE）"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """候选人 × 职位 的 Fit（市场公开投影）。

    只对 **active** 挂牌开放；返回 score/confidence/coverage/missing，
    **不含** inputs_hash 与 owner id（设计 §6.2）。
    """
    listing = market_repo.get_listing(db, listing_id)
    if listing is None or listing.status != MarketListingStatus.active.value:
        raise HTTPException(status_code=404, detail="listing_not_found")
    position = _market_position_or_404(db, position_definition_id, company_id)
    try:
        result = fit_service.calculate_person_fit(
            db,
            person_id=int(listing.person_id),
            position_definition_id=int(position.id),
            company_id=company_id,
            profile_version_id=profile_version_id,
        )
    except fit_service.FitDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return market_read_model.candidate_fit_out(listing_id, result)


@router.post("/listings/{listing_id}/recruit", response_model=RecruitOut)
def recruit_listing(
    listing_id: int,
    payload: RecruitIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """招募在市候选人（T2.6）：把既有 Person 变成本公司员工。

    - **不复制** Person 的 knowledge / traits / evidence / assessment / education；
      `identity_id` 与 `person_id` 保持不变（I1–I4），历史 provenance 不改写（I5）；
    - 只有 **active 挂牌** 可被招募（I7）；重复/并发招募 → 409（条件关闭 + 唯一约束，I8）；
    - 未知 listing → 404；已关闭 → 409 `listing_not_active`；别家公司部门/编制 → 404。
    """
    if company_id is None:
        raise HTTPException(status_code=404, detail="company_not_found")
    try:
        result = recruitment_service.RecruitmentService().recruit_existing_person(
            db,
            listing_id=listing_id,
            company_id=int(company_id),
            department_id=payload.department_id,
            position_slot_id=payload.position_slot_id,
            title=payload.title,
            role=payload.role,
            reason=payload.reason,
        )
    except recruitment_service.RecruitmentError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return {
        "person_id": result.person_id,
        "employee_id": result.employee_id,
        "employee_slug": result.employee_slug,
        "identity_id": result.identity_id,
        "company_id": result.company_id,
        "listing_id": result.listing_id,
        "position_slot_id": result.position_slot_id,
        "assignment_id": result.assignment_id,
    }
