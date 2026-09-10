"""MarketAdapter —— 市场**资源**读写契约（T2.0 冻结，T2.3 由 LocalMarketAdapter 实现）。

边界（设计 §8 / D9）：

- Adapter 只管市场资源：挂牌 / 下架 / 查询挂牌；（T2.4 的发行投放经 IssuerService 调它）
- **招募不在 Adapter 里** —— 招募是跨实体领域事务（校验 listing + 建 Employee + 建任职 + 发事件），
  归 `RecruitmentService`（T2.6）。把它塞进"资源适配器"会让适配器承担编排职责，
  也让未来 RemoteMarketAdapter 被迫理解本地事务语义。
- 可见性/资格判定不在 Adapter 里：由 `MarketEligibility`（T2.2）与 `MarketService`（T2.3）负责。

签名保持**同步**：本地 T2 与项目 sync service 层一致。M2 若需要异步远端实现，
新增 `AsyncMarketAdapter` Protocol，**不修改**本契约（设计 §8.2）。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from app.talent.market.contracts import MarketListingView, MarketSearchQuery


class MarketAdapter(Protocol):
    """市场资源适配器（本地实现 T2.3；远端实现属 M2）。"""

    def list_candidate(
        self,
        db: Session,
        *,
        person_id: int,
        listed_by_participant_id: int,
        quality_tier: str | None = None,
    ) -> MarketListingView:
        """挂牌：同一 person 至多一条 active（部分唯一索引兜底，幂等收敛）。"""
        ...

    def delist_candidate(self, db: Session, *, person_id: int, reason: str = "") -> bool:
        """下架当前 active 挂牌；无可下架时返回 False（幂等，不抛错）。"""
        ...

    def get_listing(self, db: Session, listing_id: int) -> MarketListingView | None:
        """按挂牌 id 取记录（含已关闭行 —— 历史可见，是否对外由 service 决定）。"""
        ...

    def get_listing_for_person(self, db: Session, person_id: int) -> MarketListingView | None:
        """取某 person 的 active 挂牌；无则 None。"""
        ...

    def search_listings(self, db: Session, query: MarketSearchQuery) -> list[MarketListingView]:
        """检索 active 挂牌（只返回索引级投影；人员档案由 Person Read Model 提供）。"""
        ...
