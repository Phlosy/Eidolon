"""TalentTradeService —— 人才商业化（M1.7，设计 §27/§28）。

```
T2 MarketListing（可发现性，冻结不动）
  + M1 CommercialTerms（price / sale_mode）        ← 卖方设置
  ↓ 买方出价（buyout 时按价即成交；negotiation 时等卖方接受）
Offer
  ↓ 卖方接受（或 buyout 立即执行）
Talent Contract（M1.6，type=talent）→ Escrow 锁资（买方出资）
  ↓ **同一事务**：先招募（T2 RecruitmentService，commit=False）
  ↓ 招募成功 → 交付结算（放款给卖方 + 平台手续费；§27：funded → recruit → release）
Employee 创建 / Listing 关闭 / `talent.purchased`
```

**冻结纪律**：
- **E18**：不改写 T2 的历史 provenance（`owner_company_id` / `identity_id` 等一行不动）；
- **E19/E20**：**只调用** T2 的 `RecruitmentService`，不复制 Person 的知识/特质/证据/评估/教育，
  也不重新实现招聘；
- **E13/E14/E15**：锁资、招募、放款在**同一个事务**里 —— 招募失败 ⇒ 整笔回滚（钱不动、
  Listing 仍是 active、没有半个员工）；招募成功而放款失败同样整笔回滚；
- 金额与模式是 M1 的附加事实（独立表），T2 的 `MarketListing` 保持"可发现性"语义（§27）。

**收款方**：挂牌公司（`terms.seller_company_id`）；系统/发行方挂牌（NULL）⇒ 成交款进 **Treasury**
（M1.8 的 NPC 财政再细化）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor
from app.economy.policy import EconomicPolicy, economic_policy
from app.events.bus import bus
from app.models.base import utcnow
from app.models.economy import Contract, Offer
from app.models.enums import (
    ContractType,
    MarketListingStatus,
    MarketParticipantKind,
    OfferStatus,
    SystemAccountKind,
    TalentSaleMode,
)
from app.repositories import economy as economy_repo
from app.repositories import market as market_repo
from app.services.economy.contracts import ContractService, OfferService
from app.services.recruitment import RecruitmentService

logger = get_logger(__name__)


class TalentTradeError(RuntimeError):
    """人才交易领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class PurchaseResult:
    """一次成交的完整结果（合同 / 结算 / 招募）。"""

    listing_id: int
    contract_id: int
    offer_id: int | None
    buyer_company_id: int
    seller_company_id: int | None
    gross: int
    fee: int
    net: int
    fee_treasury: int
    fee_burn: int
    employee_id: int
    person_id: int
    identity_id: str | None
    settlement_transaction_id: int
    created: bool


class TalentTradeService:
    """人才挂牌的商业条款 + 买卖编排（唯一的成交入口）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()
        self.contracts = ContractService(db, policy=self.policy)
        self.offers = OfferService(db, policy=self.policy)

    # ---------------------------------------------------------------- 条款

    def set_terms(
        self,
        listing_id: int,
        *,
        company_id: int | None,
        price: int,
        sale_mode: TalentSaleMode = TalentSaleMode.buyout,
        metadata: dict | None = None,
        commit: bool = True,
    ):
        """设置/更新挂牌的商业条款（**只有挂牌方公司本人**可以设置）。

        挂牌必须在市（active）；`price` 必须为正整数（金额是一等列）。
        """
        listing = self._require_listing(listing_id)
        self._require_seller(listing=listing, company_id=company_id)
        if int(price) <= 0:
            raise TalentTradeError("price_must_be_positive", http_status=422)
        if listing.status != MarketListingStatus.active.value:
            raise TalentTradeError("listing_not_active")

        terms = economy_repo.upsert_terms(
            self.db,
            listing_id=int(listing.id),
            seller_company_id=int(company_id) if company_id is not None else None,
            price=int(price),
            currency="CREDIT",
            sale_mode=TalentSaleMode(sale_mode).value,
            policy_version=self.policy.version,
            metadata_json=dict(metadata or {}),
        )
        if commit:
            self.db.commit()
            bus.publish(
                "talent.terms_set",
                {
                    "listing_id": int(listing.id),
                    "price": int(terms.price),
                    "sale_mode": terms.sale_mode,
                },
                company_id=int(company_id) if company_id is not None else None,
            )
        return terms

    def terms_for(self, listing_id: int):
        """读条款（公开信息：价格本身就是要展示的市场信息）。"""
        return economy_repo.get_terms_by_listing(self.db, listing_id=int(listing_id))

    # ---------------------------------------------------------------- 出价

    def create_offer(
        self,
        listing_id: int,
        *,
        buyer_company_id: int,
        amount: int,
        message: str = "",
        commit: bool = True,
    ) -> tuple[Offer, PurchaseResult | None]:
        """买方出价。

        - **buyout（一口价）**：金额必须等于标价 ⇒ 立即成交（返回 `PurchaseResult`）；
        - **negotiation（可议价）**：生成 OPEN 的 Offer，等卖方 `accept_offer`；
        - 卖方不能给自己出价（无意义交易）；挂牌必须在市、条款必须已设置。
        """
        listing = self._require_listing(listing_id)
        terms = self._require_terms(listing_id)
        if listing.status != MarketListingStatus.active.value:
            raise TalentTradeError("listing_not_active")
        if int(amount) <= 0:
            raise TalentTradeError("offer_amount_must_be_positive", http_status=422)
        if terms.seller_company_id is not None and int(terms.seller_company_id) == int(
            buyer_company_id
        ):
            raise TalentTradeError("cannot_buy_your_own_talent", http_status=422)

        mode = TalentSaleMode(terms.sale_mode)
        if mode is TalentSaleMode.buyout and int(amount) != int(terms.price):
            raise TalentTradeError("buyout_requires_exact_price", http_status=422)

        offer = self.offers.create_offer(
            from_actor=EconomicActor.company(int(buyer_company_id)),
            to_actor=(
                EconomicActor.company(int(terms.seller_company_id))
                if terms.seller_company_id is not None
                else EconomicActor.system(SystemAccountKind.treasury)
            ),
            amount=int(amount),
            contract_type=ContractType.talent,
            listing_id=int(listing.id),
            message=message,
            terms={"sale_mode": mode.value, "listing_id": int(listing.id)},
            commit=False,
        )
        if mode is TalentSaleMode.buyout:
            purchase = self._purchase(
                listing=listing,
                terms=terms,
                buyer_company_id=int(buyer_company_id),
                amount=int(amount),
                offer=offer,
                commit=commit,
            )
            return offer, purchase
        if commit:
            self.db.commit()
        return offer, None

    def accept_offer(
        self, offer_id: int, *, company_id: int | None, commit: bool = True
    ) -> tuple[Offer, PurchaseResult]:
        """卖方接受出价 ⇒ 成交（合同 + 锁资 + 招募 + 放款，全在同一事务）。"""
        offer = economy_repo.get_offer(self.db, offer_id)
        if offer is None:
            raise TalentTradeError("offer_not_found", http_status=404)
        if offer.listing_id is None:
            raise TalentTradeError("offer_is_not_a_talent_offer", http_status=422)
        listing = self._require_listing(int(offer.listing_id))
        terms = self._require_terms(int(offer.listing_id))
        self._require_seller(listing=listing, company_id=company_id)
        if offer.status != OfferStatus.open.value:
            raise TalentTradeError(f"offer_not_open:{offer.status}")

        purchase = self._purchase(
            listing=listing,
            terms=terms,
            buyer_company_id=int(offer.from_actor_ref),
            amount=int(offer.amount),
            offer=offer,
            commit=commit,
        )
        self.db.expire_all()
        accepted = economy_repo.get_offer(self.db, offer_id)
        assert accepted is not None
        return accepted, purchase

    # ---------------------------------------------------------------- 成交

    def _purchase(
        self,
        *,
        listing,
        terms,
        buyer_company_id: int,
        amount: int,
        offer: Offer | None,
        commit: bool,
    ) -> PurchaseResult:
        """成交核心：锁资 → 招募（T2）→ 放款，**同一个事务**（失败整笔回滚）。"""
        seller_company_id = (
            int(terms.seller_company_id) if terms.seller_company_id is not None else None
        )
        buyer = EconomicActor.company(int(buyer_company_id))
        seller = (
            EconomicActor.company(seller_company_id)
            if seller_company_id is not None
            else EconomicActor.system(SystemAccountKind.treasury)
        )
        try:
            # 1) 合同（type=talent）：买方出资、卖方收款；**创建即锁资对价**
            contract = self.contracts.create_contract(
                issuer=buyer,
                contractor=seller,
                title=f"Talent purchase: listing #{int(listing.id)}",
                consideration_amount=int(amount),
                contract_type=ContractType.talent,
                subject="market talent acquisition",
                terms={
                    "listing_id": int(listing.id),
                    "sale_mode": TalentSaleMode(terms.sale_mode).value,
                },
                reference_type="market_listing",
                reference_id=str(int(listing.id)),
                metadata={"person_id": int(listing.person_id)},
                commit=False,
            )
            # 2) 卖方接受 ⇒ ACTIVE → FUNDED（资金已托管）
            contract = self.contracts.accept(contract.id, contractor=seller, commit=False)

            # 3) **先招募**（T2 唯一入口，不复制 Person 数据；事务归调用方）
            recruitment = RecruitmentService().recruit_existing_person(
                self.db,
                listing_id=int(listing.id),
                company_id=int(buyer_company_id),
                title="",
                role=None,
                reason="talent_purchase",
                commit=False,
            )

            # 4) 招募成功 ⇒ 交付并结算（放款给卖方 + 平台手续费；§27 funded → recruit → release）
            settled, settlement = self.contracts.fulfill(
                contract.id, contractor=seller, commit=False
            )

            # 5) Offer 收口（buyout 时是自动接受）
            if offer is not None:
                economy_repo.transition_offer(
                    self.db,
                    offer_id=int(offer.id),
                    from_statuses=(OfferStatus.open.value,),
                    status=OfferStatus.accepted.value,
                    responded_at=utcnow(),
                    contract_id=int(settled.id),
                )
            settled.metadata_json = {
                **(settled.metadata_json or {}),
                "talent_purchase": {
                    "listing_id": int(listing.id),
                    "person_id": int(listing.person_id),
                    "employee_id": int(recruitment.employee_id),
                    "buyer_company_id": int(buyer_company_id),
                    "seller_company_id": seller_company_id,
                    "gross": settlement.gross,
                    "fee": settlement.fee,
                    "net": settlement.net,
                },
            }
            self.db.flush()
            if commit:
                self.db.commit()
                self._publish_purchase(
                    listing=listing,
                    contract=settled,
                    settlement=settlement,
                    recruitment=recruitment,
                    buyer_company_id=int(buyer_company_id),
                    seller_company_id=seller_company_id,
                )
        except Exception:
            if commit:
                self.db.rollback()
            raise

        return PurchaseResult(
            listing_id=int(listing.id),
            contract_id=int(settled.id),
            offer_id=int(offer.id) if offer is not None else None,
            buyer_company_id=int(buyer_company_id),
            seller_company_id=seller_company_id,
            gross=settlement.gross,
            fee=settlement.fee,
            net=settlement.net,
            fee_treasury=settlement.fee_treasury,
            fee_burn=settlement.fee_burn,
            employee_id=int(recruitment.employee_id),
            person_id=int(recruitment.person_id),
            identity_id=recruitment.identity_id,
            settlement_transaction_id=int(settlement.transaction_id),
            created=True,
        )

    def _publish_purchase(
        self,
        *,
        listing,
        contract: Contract,
        settlement,
        recruitment,
        buyer_company_id: int,
        seller_company_id: int | None,
    ) -> None:
        """提交后发事件：`talent.purchased`（本域）+ `person.recruited`（T2 语义，载荷同 T2）。"""
        bus.publish(
            "talent.purchased",
            {
                "listing_id": int(listing.id),
                "contract_id": int(contract.id),
                "person_id": int(recruitment.person_id),
                "identity_id": recruitment.identity_id,
                "employee_id": int(recruitment.employee_id),
                "buyer_company_id": int(buyer_company_id),
                "seller_company_id": seller_company_id,
                "amount": settlement.gross,
                "fee": settlement.fee,
                "net": settlement.net,
                "transaction_id": int(settlement.transaction_id),
            },
            company_id=int(buyer_company_id),
            actor_employee_id=int(recruitment.employee_id),
        )
        bus.publish(
            "person.recruited",
            {
                "person_id": int(recruitment.person_id),
                "employee_id": int(recruitment.employee_id),
                "company_id": int(buyer_company_id),
                "listing_id": int(listing.id),
                "identity_id": recruitment.identity_id,
                "position_slot_id": recruitment.position_slot_id,
            },
            company_id=int(buyer_company_id),
            actor_employee_id=int(recruitment.employee_id),
        )

    # ---------------------------------------------------------------- 内部

    def _require_listing(self, listing_id: int):
        listing = market_repo.get_listing(self.db, int(listing_id))
        if listing is None:
            raise TalentTradeError("listing_not_found", http_status=404)
        return listing

    def _require_terms(self, listing_id: int):
        terms = economy_repo.get_terms_by_listing(self.db, listing_id=int(listing_id))
        if terms is None:
            raise TalentTradeError("commercial_terms_not_set", http_status=409)
        return terms

    def _require_seller(self, *, listing, company_id: int | None) -> None:
        """卖方身份校验：**必须是挂牌方公司本人**（否则 404，不泄露存在性）。

        `company_id=None` 只允许系统/发行方挂牌（M1.4 `issue_talent` 的产出）——
        那类挂牌没有卖方公司，价格由系统侧（CLI/发行工具）设置；**玩家永远不可能**成为它的卖方。
        """
        participant_id = int(listing.listed_by_participant_id)
        if company_id is None:
            participant = market_repo.get_participant(self.db, participant_id)
            if (
                participant is None
                or participant.kind == MarketParticipantKind.player_company.value
            ):
                raise TalentTradeError("listing_not_found", http_status=404)
            return
        if not market_repo.is_player_participant_of(self.db, participant_id, company_id):
            raise TalentTradeError("listing_not_found", http_status=404)


__all__ = ["PurchaseResult", "TalentTradeError", "TalentTradeService"]
