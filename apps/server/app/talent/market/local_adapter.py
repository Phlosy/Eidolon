"""LocalMarketAdapter —— `MarketAdapter` Protocol 的本地实现（T2.3）。

只做**市场资源**：挂牌 / 下架 / 查询（设计 §8 边界）。资格判定在
`eligibility.py`、招募事务在 `services/recruitment.py`（T2.6）：
适配器不理解"能不能挂牌"，也不理解"招走"的业务语义。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.market import MarketListing
from app.repositories import market as market_repo
from app.talent.market.contracts import (
    MarketListingStatus,
    MarketListingView,
    MarketSearchQuery,
)


def _to_view(listing: MarketListing, person, profile) -> MarketListingView:  # noqa: ANN001
    return MarketListingView(
        listing_id=int(listing.id),
        person_id=int(listing.person_id),
        identity_id=profile.identity_id if profile is not None else "",
        name=person.name,
        origin=profile.origin if profile is not None else "",
        cultivation_state=profile.lifecycle if profile is not None else "",
        status=MarketListingStatus(listing.status),
        quality_tier=listing.quality_tier,
        listed_by_participant_id=int(listing.listed_by_participant_id),
        listed_at=listing.listed_at,
        closed_at=listing.closed_at,
    )


class LocalMarketAdapter:
    """本地 SQLAlchemy 实现（T2.3；远端实现属 M2，不改本契约）。"""

    def list_candidate(
        self,
        db: Session,
        *,
        person_id: int,
        listed_by_participant_id: int,
        quality_tier: str | None = None,
    ) -> MarketListingView:
        listing, _created = market_repo.create_active_listing(
            db,
            person_id=person_id,
            listed_by_participant_id=listed_by_participant_id,
            quality_tier=quality_tier,
        )
        return self._view_for(db, listing)

    def delist_candidate(self, db: Session, *, person_id: int, reason: str = "") -> bool:
        listing = market_repo.get_active_listing_for_person(db, person_id)
        if listing is None:
            return False  # 幂等：没有 active 挂牌可下架
        return market_repo.close_active_listing(db, int(listing.id), reason=reason or "delisted")

    def get_listing(self, db: Session, listing_id: int) -> MarketListingView | None:
        rows = market_repo.listing_rows(db, listing_id=listing_id, active_only=False)
        if not rows:
            return None
        listing, person, profile = rows[0]
        return _to_view(listing, person, profile)

    def get_listing_for_person(self, db: Session, person_id: int) -> MarketListingView | None:
        listing = market_repo.get_active_listing_for_person(db, person_id)
        if listing is None:
            return None
        return self._view_for(db, listing)

    def search_listings(self, db: Session, query: MarketSearchQuery) -> list[MarketListingView]:
        rows = market_repo.listing_rows(
            db,
            active_only=True,
            text=query.text,
            origin=query.origin,
            quality_tier=query.quality_tier,
            limit=query.limit,
            offset=query.offset,
        )
        return [_to_view(listing, person, profile) for listing, person, profile in rows]

    # ---- 内部 ----

    @staticmethod
    def _view_for(db: Session, listing: MarketListing) -> MarketListingView:
        rows = market_repo.listing_rows(db, listing_id=int(listing.id), active_only=False)
        assert rows, "刚写入的挂牌行必须能读回"
        row_listing, person, profile = rows[0]
        return _to_view(row_listing, person, profile)
