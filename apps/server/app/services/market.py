"""MarketService（T2.3）—— 挂牌/下架编排（设计 §8：适配器管资源，服务管编排）。

职责边界：
- **资格**：交给 `eligibility.can_list`（唯一判定处），本模块不复制三轴逻辑；
- **资源**：交给 `MarketAdapter`（本地实现 `LocalMarketAdapter`）；
- **事件**：只在**真实发生**时发（幂等重复挂牌不重发），载荷是"已经发生"的事实；
- **公司边界**：挂牌方必须是该 person 的持有公司（`character_profiles.owner_company_id`），
  下架/关闭必须是挂牌方公司本人 —— 否则 404（不泄露存在性，设计 §8）。

招募（T2.6）不在这里：它是跨实体领域事务（校验 listing + 建 Employee + 建任职 + 发事件），
归 `services/recruitment.py`。本模块只提供 `close_listing_for_recruitment` 供其调用。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.repositories import cultivation as cultivation_repo
from app.repositories import market as market_repo
from app.repositories import organization as org_repo
from app.talent.market import eligibility
from app.talent.market.contracts import MarketListingView, MarketSearchQuery
from app.talent.market.local_adapter import LocalMarketAdapter


class MarketError(RuntimeError):
    """市场领域错误（reason code 机器可读；API 层转 HTTP）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def _adapter() -> LocalMarketAdapter:
    return LocalMarketAdapter()


def list_person(
    db: Session,
    *,
    person_id: int,
    company_id: int | None,
    quality_tier: str | None = None,
) -> tuple[MarketListingView, bool]:
    """把本公司持有的 person 挂牌 → (挂牌视图, 是否本次新建)。

    幂等：已挂牌时返回既有挂牌（created=False，不重复发事件）。
    资格不足（未 ready / 已入职）→ MarketError(409, reason)。
    """
    profile = cultivation_repo.get_profile_by_person(db, person_id)
    if profile is None or company_id is None or profile.owner_company_id != company_id:
        raise MarketError("person_not_found", http_status=404)

    company = org_repo.get_company(db, company_id)
    participant = market_repo.ensure_participant(
        db,
        kind=MarketParticipantKind.player_company.value,
        company_id=company_id,
        display_name=company.name if company is not None else "",
    )
    return list_for_participant(
        db,
        person_id=person_id,
        participant_id=int(participant.id),
        quality_tier=quality_tier,
        company_id=company_id,
    )


def list_for_participant(
    db: Session,
    *,
    person_id: int,
    participant_id: int,
    quality_tier: str | None = None,
    company_id: int | None = None,
) -> tuple[MarketListingView, bool]:
    """以任意**市场参与者**的名义挂牌（玩家公司 / 系统发行方 / T2.7 NPC 供给）。

    资格仍走 `eligibility.can_list`（发行方也不能挂牌未结业/已入职的人）；
    公司边界由调用方负责（玩家路径 = `list_person` 的持有校验；发行方路径 = 系统角色）。
    事件只在此处发布 —— 所有供给予路径共用一份事实。
    """
    decision = eligibility.can_list(db, person_id)
    if not decision.allowed:
        if decision.reason is eligibility.EligibilityReason.already_listed:
            existing = _adapter().get_listing_for_person(db, person_id)
            assert existing is not None
            return existing, False
        raise MarketError(decision.reason.value)

    view = _adapter().list_candidate(
        db,
        person_id=person_id,
        listed_by_participant_id=participant_id,
        quality_tier=quality_tier,
    )
    db.commit()
    bus.publish(
        "market.listed",
        {
            "listing_id": view.listing_id,
            "person_id": view.person_id,
            "identity_id": view.identity_id,
            "participant_id": view.listed_by_participant_id,
            "quality_tier": view.quality_tier,
        },
        company_id=company_id,
    )
    return view, True


def delist(
    db: Session,
    *,
    listing_id: int,
    company_id: int | None,
    reason: str = "delisted",
) -> bool:
    """下架挂牌（幂等）：只有挂牌方公司本人可以下架。

    返回是否真的关掉了一行 active（重复下架 → False；API 仍返回 204）。
    """
    listing = market_repo.get_listing(db, listing_id)
    if listing is None:
        raise MarketError("listing_not_found", http_status=404)
    if not market_repo.is_player_participant_of(
        db, int(listing.listed_by_participant_id), company_id
    ):
        raise MarketError("listing_not_found", http_status=404)  # 不泄露存在性

    closed = _adapter().delist_candidate(db, person_id=int(listing.person_id), reason=reason)
    db.commit()
    if closed:
        bus.publish(
            "market.delisted",
            {
                "listing_id": int(listing.id),
                "person_id": int(listing.person_id),
                "reason": reason,
            },
            company_id=company_id,
        )
    return closed


def close_listing_for_recruitment(
    db: Session,
    *,
    listing_id: int,
    company_id: int,
    employee_id: int,
    reason: str = "recruited",
) -> bool:
    """T2.6 招募专用：条件关闭 active 挂牌并把招募方回填到行上。

    **不 commit**（调用方持有事务）：招募必须在同一事务里完成"关闭 + 建 Employee"，
    任一步失败一起回滚。返回 False = 已被别人关掉（rowcount=0）→ 调用方据此拒绝双招。
    """
    listing = market_repo.get_listing(db, listing_id)
    if listing is None:
        return False
    return market_repo.close_active_listing(
        db,
        listing_id,
        reason=reason,
        recruited_company_id=company_id,
        recruited_employee_id=employee_id,
    )


def search(db: Session, query: MarketSearchQuery) -> dict:
    """市场检索（公开投影，见 market/read_model.listing_page）。"""
    from app.talent.market import read_model as market_read_model

    return market_read_model.listing_page(db, query)


def listing_detail(db: Session, listing_id: int, *, timeline_limit: int, evidence_limit: int):
    """在人才的公开档案（只允许 active listing —— 下架/关闭后不再对外可读）。"""
    listing = market_repo.get_listing(db, listing_id)
    if listing is None or listing.status != MarketListingStatus.active.value:
        raise MarketError("listing_not_found", http_status=404)
    from app.talent.market import read_model as market_read_model

    return market_read_model.candidate_profile(
        db, listing, timeline_limit=timeline_limit, evidence_limit=evidence_limit
    )
