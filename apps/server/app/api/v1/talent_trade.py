"""人才商业化 API（M1.7，设计 §27/§28）—— T2 市场之上的 M1 交易面。

**为什么不改 `market.py`**：T2 已冻结，且它定义的是"可发现性"（谁在市场里）；
价格/成交是 M1 的语义。因此这里用一个**共享 `/market` 前缀**的新 router 挂 M1 的端点，
T2 的市场 API 一行不动（设计 §27：不把 MarketListing 改造成金融订单）。

| 端点 | 谁 | 语义 |
| --- | --- | --- |
| `GET /market/listings/{id}/commercial-terms` | 任何登录用户 | 读价格（公开市场信息） |
| `POST /market/listings/{id}/commercial-terms` | **卖方（挂牌方公司）** | 设置/更新价格与出售模式 |
| `POST /market/listings/{id}/offers` | **买方公司** | 出价；一口价按标价立即成交 |
| `POST /offers/{offer_id}/accept` | **卖方** | 接受议价 ⇒ 成交（合同 + 锁资 + 招募 + 放款） |

成交是**一个事务**：锁资 → T2 招募 → 放款（§27 funded → recruit → release），
失败整笔回滚（钱不动、挂牌仍在市、没有半个员工）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.repositories import economy as economy_repo
from app.schemas.economy import (
    CommercialTermsIn,
    CommercialTermsOut,
    OfferCreateIn,
    OfferOut,
    TalentOfferResultOut,
    TalentPurchaseOut,
)
from app.services.economy.talent_trade import (
    PurchaseResult,
    TalentTradeError,
    TalentTradeService,
)

router = APIRouter(prefix="/market", tags=["talent-trade"])


def _company_or_404(company_id: int | None) -> int:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return int(company_id)


def _terms_out(terms) -> dict:
    return {
        "listing_id": int(terms.listing_id),
        "seller_company_id": int(terms.seller_company_id)
        if terms.seller_company_id is not None
        else None,
        "price": int(terms.price),
        "currency": terms.currency,
        "sale_mode": terms.sale_mode,
        "policy_version": terms.policy_version,
    }


def _offer_out(offer) -> dict:
    return {
        "offer_id": int(offer.id),
        "contract_type": offer.contract_type,
        "listing_id": int(offer.listing_id) if offer.listing_id else None,
        "work_order_id": int(offer.work_order_id) if offer.work_order_id else None,
        "from_company_id": int(offer.from_actor_ref)
        if offer.from_actor_kind == "company"
        else None,
        "to_company_id": int(offer.to_actor_ref)
        if offer.to_actor_kind == "company" and offer.to_actor_ref
        else None,
        "amount": int(offer.amount),
        "currency": offer.currency,
        "message": offer.message,
        "status": offer.status,
        "contract_id": int(offer.contract_id) if offer.contract_id else None,
        "responded_at": offer.responded_at,
        "expires_at": offer.expires_at,
    }


def _purchase_out(purchase: PurchaseResult | None) -> dict | None:
    if purchase is None:
        return None
    return TalentPurchaseOut(
        listing_id=purchase.listing_id,
        contract_id=purchase.contract_id,
        offer_id=purchase.offer_id,
        buyer_company_id=purchase.buyer_company_id,
        seller_company_id=purchase.seller_company_id,
        gross=purchase.gross,
        fee=purchase.fee,
        net=purchase.net,
        fee_treasury=purchase.fee_treasury,
        fee_burn=purchase.fee_burn,
        employee_id=purchase.employee_id,
        person_id=purchase.person_id,
        identity_id=purchase.identity_id,
        settlement_transaction_id=purchase.settlement_transaction_id,
        created=purchase.created,
    ).model_dump()


@router.get("/listings/{listing_id}/commercial-terms", response_model=CommercialTermsOut)
def get_commercial_terms(
    listing_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """读挂牌的售价与出售模式（价格是公开市场信息）。"""
    _company_or_404(company_id)
    service = TalentTradeService(db)
    service._require_listing(listing_id)  # noqa: SLF001 - 未知挂牌 404（不泄露存在性）
    terms = service.terms_for(listing_id)
    if terms is None:
        raise HTTPException(status_code=404, detail="commercial_terms_not_set")
    return _terms_out(terms)


@router.post(
    "/listings/{listing_id}/commercial-terms",
    response_model=CommercialTermsOut,
    status_code=201,
)
def set_commercial_terms(
    listing_id: int,
    payload: CommercialTermsIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """设置/更新售价与出售模式（**只有挂牌方公司本人**；非挂牌方 404）。"""
    current = _company_or_404(company_id)
    service = TalentTradeService(db)
    try:
        terms = service.set_terms(
            listing_id,
            company_id=current,
            price=payload.price,
            sale_mode=payload.sale_mode,
            metadata=payload.metadata,
        )
    except TalentTradeError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return _terms_out(terms)


@router.post("/listings/{listing_id}/offers", response_model=TalentOfferResultOut, status_code=201)
def create_offer(
    listing_id: int,
    payload: OfferCreateIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """买方出价。**一口价（buyout）按标价立即成交**；议价（negotiation）生成待接受出价。"""
    current = _company_or_404(company_id)
    service = TalentTradeService(db)
    try:
        offer, purchase = service.create_offer(
            listing_id,
            buyer_company_id=current,
            amount=payload.amount,
            message=payload.message,
        )
    except TalentTradeError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    except Exception as exc:  # 招募/资金层的领域错误：原样转 409（含 reason）
        reason = getattr(exc, "reason", None)
        if reason is None:
            raise
        raise HTTPException(status_code=getattr(exc, "http_status", 409), detail=reason) from exc
    return {"offer": _offer_out(offer), "purchase": _purchase_out(purchase)}


@router.get("/listings/{listing_id}/offers", response_model=list[OfferOut])
def list_listing_offers(
    listing_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    """挂牌的出价列表：**卖方看全部，买方只看自己出的**（不泄露他人报价）。"""
    current = _company_or_404(company_id)
    service = TalentTradeService(db)
    listing = service._require_listing(listing_id)  # noqa: SLF001
    is_seller = True
    try:
        service._require_seller(listing=listing, company_id=current)  # noqa: SLF001
    except TalentTradeError:
        is_seller = False
    offers = economy_repo.list_offers(db, listing_id=int(listing.id))
    if not is_seller:
        offers = [
            offer
            for offer in offers
            if offer.from_actor_kind == "company" and int(offer.from_actor_ref) == current
        ]
    return [_offer_out(offer) for offer in offers]


@router.post("/offers/{offer_id}/accept", response_model=TalentOfferResultOut)
def accept_offer(
    offer_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """卖方接受出价 ⇒ 成交（合同 + 锁资 + T2 招募 + 放款，同一事务）。"""
    current = _company_or_404(company_id)
    service = TalentTradeService(db)
    try:
        offer, purchase = service.accept_offer(offer_id, company_id=current)
    except TalentTradeError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    except Exception as exc:
        reason = getattr(exc, "reason", None)
        if reason is None:
            raise
        raise HTTPException(status_code=getattr(exc, "http_status", 409), detail=reason) from exc
    return {"offer": _offer_out(offer), "purchase": _purchase_out(purchase)}
