"""NpcEconomyService —— NPC 经济（M1.8，设计 §29）。

```
系统按预算注入（MonetaryAuthority.mint → NPC 钱包，**计入发行统计**）
  ↓
NPC 出手（deterministic，无 LLM）：
    available >= price  and  price <= max_price  and  fit >= threshold
  ↓ 同一事务
T2 NpcMarketService.take_candidate(...)（CAS 关闭挂牌，人才离场）
  + 立刻付钱：NPC 钱包 → 卖方（转移，**绝不 mint**，E8）
```

**纪律**：
- NPC 的钱包 = `npc_company` 经济 actor
  （`market_participants(kind=npc_company)`，**不进 companies**，D8）；
- **只有系统侧的注入是 mint**（`inject_budget` → `MonetaryAuthority`）；NPC 自己出手只是转移，
  Total Supply 不变（E7/E8，有测试）；注入受 `budget_cap` 封顶（防无限发行）；
- **参数在档案里，钱在账本里**：可花预算 = NPC 账户的 available（不复制余额）；
- **全 deterministic**：§29 的规则字面实现，不上 LLM；
- 成交走 T2 的**同一个** `take_candidate` 原语（E20：不重新实现"谁被拿走"）；
- 卖方是玩家公司 ⇒ 收款；卖方是系统/发行方 ⇒ 进 Treasury（与 M1.7 同一规则）；
- 「NPC 收入」先只记录：`income_summary()` 从账本按类别汇总（不做经营模拟，§29）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor
from app.economy.policy import EconomicPolicy, economic_policy
from app.events.bus import bus
from app.models.enums import (
    EconomicActorKind,
    EconomicCategory,
    LedgerAccountKind,
    MarketListingStatus,
)
from app.repositories import economy as economy_repo
from app.repositories import market as market_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.talent.fit import service as fit_service
from app.talent.market import npc as npc_market
from app.talent.market.contracts import MarketSearchQuery
from app.talent.market.local_adapter import LocalMarketAdapter

logger = get_logger(__name__)


class NpcEconomyError(RuntimeError):
    """NPC 经济领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class NpcDecision:
    """一次出手判定（deterministic；`reason` 说明为什么买/不买）。"""

    allowed: bool
    reason: str
    price: int
    fit_score: float | None
    available: int
    max_price: int
    threshold_bps: int


@dataclass(frozen=True)
class NpcPurchase:
    """一次 NPC 成交（钱 + 人才离场）。"""

    participant_id: int
    npc_name: str
    listing_id: int
    person_id: int
    identity_id: str | None
    price: int
    fit_score: float | None
    sell_to_company_id: int | None
    transaction_id: int | None


@dataclass(frozen=True)
class NpcRoundOutcome:
    """一轮 NPC 活动的报告（每单可解释：买/不买 + 原因）。"""

    npc_name: str
    participant_id: int
    budget_injected: int
    available: int
    considered: int
    purchased: list[NpcPurchase] = field(default_factory=list)
    skipped: list[NpcDecision] = field(default_factory=list)
    dry_run: bool = False


class NpcEconomyService:
    """NPC 的预算 / 出手 / 成交（deterministic）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()

    # ---------------------------------------------------------------- 开户与预算

    def ensure_profile(
        self, participant_id: int, *, commit: bool = False, only_if_missing: bool = True
    ):
        """确保 NPC 有经济档案（默认值来自政策）；已有档案时原样返回。"""
        existing = economy_repo.get_npc_profile(self.db, participant_id=int(participant_id))
        if existing is not None or only_if_missing:
            if existing is not None:
                return existing
        return economy_repo.insert_npc_profile(
            self.db,
            participant_id=int(participant_id),
            enabled=True,
            budget_cap=int(self.policy.npc_budget_cap),
            budget_injected_total=0,
            max_price=int(self.policy.npc_max_price),
            fit_threshold_bps=int(self.policy.npc_fit_threshold_bps),
            deals_per_round=int(self.policy.npc_deals_per_round),
            policy_version=self.policy.version,
            metadata_json={},
        )

    def wallet(self, participant_id: int):
        """NPC 的钱包账户（`npc_company` actor；M1.1 的 actor 类型直接可用）。"""
        return AccountService(self.db).ensure_account(
            EconomicActor(EconomicActorKind.npc_company, int(participant_id))
        )

    def available_budget(self, participant_id: int) -> int:
        """可花预算 = NPC 账户的 **available**（账本口径，不复制余额）。"""
        account = self.wallet(int(participant_id))
        return LedgerService(self.db).ledger_balance(int(account.id))

    def inject_budget(
        self, participant_id: int, *, amount: int | None = None, commit: bool = True
    ) -> tuple[int, int]:
        """系统注入预算（**属于 mint，计入发行统计**，§29）。返回 `(注入额, 累计注入)`。

        - 受 `budget_cap` 封顶：已达上限 ⇒ 返回 `(0, 累计)`（不报错，但绝不超发）；
        - 幂等键含累计注入额 ⇒ 同一次注入重放不会重复发行。
        """
        profile = self.ensure_profile(int(participant_id))
        injected_total = int(profile.budget_injected_total)
        cap = int(profile.budget_cap)
        wanted = int(amount) if amount is not None else int(self.policy.npc_budget_injection)
        if wanted <= 0:
            raise NpcEconomyError("injection_must_be_positive", http_status=422)
        remaining = cap - injected_total
        if remaining <= 0:
            return 0, injected_total
        granted = min(wanted, remaining)

        MonetaryAuthority(self.db).mint(
            actor=EconomicActor(EconomicActorKind.npc_company, int(participant_id)),
            amount=granted,
            reason="npc_budget",
            reference_type="npc_economy_profile",
            reference_id=str(int(profile.id)),
            idempotency_key=f"npc_budget:{int(participant_id)}:{injected_total + granted}",
            category=EconomicCategory.npc_budget,
            metadata={
                "participant_id": int(participant_id),
                "policy_version": profile.policy_version,
            },
            commit=False,
        )
        profile.budget_injected_total = injected_total + granted
        self.db.flush()
        if commit:
            self.db.commit()
            bus.publish(
                "npc.budget_injected",
                {
                    "participant_id": int(participant_id),
                    "amount": granted,
                    "injected_total": int(profile.budget_injected_total),
                    "budget_cap": cap,
                },
            )
        logger.info(
            "npc budget injected participant=%s amount=%d total=%d cap=%d",
            participant_id,
            granted,
            profile.budget_injected_total,
            cap,
        )
        return granted, int(profile.budget_injected_total)

    # ---------------------------------------------------------------- 出手判定

    def decide(self, *, participant_id: int, price: int, fit_score: float | None) -> NpcDecision:
        """§29 的判定规则（字面实现，deterministic）：
        `available >= price and fit >= threshold and price <= max_price`。
        """
        profile = self.ensure_profile(int(participant_id))
        available = self.available_budget(int(participant_id))
        threshold_bps = int(profile.fit_threshold_bps)
        max_price = int(profile.max_price)
        decision = NpcDecision(
            allowed=False,
            reason="ok",
            price=int(price),
            fit_score=fit_score,
            available=available,
            max_price=max_price,
            threshold_bps=threshold_bps,
        )
        if not profile.enabled:
            return _with_reason(decision, "npc_disabled")
        if int(price) <= 0:
            return _with_reason(decision, "price_must_be_positive")
        if int(price) > max_price:
            return _with_reason(decision, "price_above_max_price")
        if fit_score is None:
            return _with_reason(decision, "no_fit_score")
        if int(round(float(fit_score) * 10_000)) < threshold_bps:
            return _with_reason(decision, "fit_below_threshold")
        if available < int(price):
            return _with_reason(decision, "insufficient_budget")
        return _with_reason(decision, "ok", allowed=True)

    # ---------------------------------------------------------------- 成交

    def purchase_talent(
        self,
        participant_id: int,
        listing_id: int,
        *,
        fit_score: float | None = None,
        npc_name: str = "",
        company_context_id: int,
        price: int | None = None,
        commit: bool = True,
    ) -> NpcPurchase | None:
        """买下某个挂牌人才：判定 → T2 CAS 拿走 → **立刻付钱**（同一事务，转移不 mint）。"""
        listing = market_repo.get_listing(self.db, int(listing_id))
        if listing is None or listing.status != MarketListingStatus.active.value:
            raise NpcEconomyError("listing_not_active")
        terms = economy_repo.get_terms_by_listing(self.db, listing_id=int(listing_id))
        resolved_price = int(price) if price is not None else (int(terms.price) if terms else 0)
        if resolved_price <= 0:
            raise NpcEconomyError("listing_has_no_price", http_status=409)

        decision = self.decide(
            participant_id=int(participant_id), price=resolved_price, fit_score=fit_score
        )
        if not decision.allowed:
            logger.info(
                "npc declined listing=%s participant=%s reason=%s",
                listing_id,
                participant_id,
                decision.reason,
            )
            return None

        seller_company_id = (
            int(terms.seller_company_id)
            if terms is not None and terms.seller_company_id is not None
            else None
        )
        try:
            # 1) T2 拿人（CAS 关闭挂牌；事务归本服务）
            taken = npc_market.NpcMarketService().take_candidate(
                self.db,
                listing_id=int(listing_id),
                person_id=int(listing.person_id),
                identity_id="",
                participant_id=int(participant_id),
                npc_name=npc_name,
                fit_score=fit_score,
                fit_confidence=None,
                company_context_id=int(company_context_id),
                reason="npc_purchased",
                commit=False,
            )
            if not taken:
                if commit:
                    self.db.rollback()
                raise NpcEconomyError("listing_not_active")

            # 2) 立刻付钱：NPC 钱包 → 卖方（转移；绝不 mint，E8）
            transaction_id = self._pay_seller(
                participant_id=int(participant_id),
                seller_company_id=seller_company_id,
                amount=resolved_price,
                listing_id=int(listing_id),
            )
            if commit:
                self.db.commit()
                npc_market.NpcMarketService().publish_candidate_taken(
                    listing_id=int(listing_id),
                    person_id=int(listing.person_id),
                    identity_id="",
                    participant_id=int(participant_id),
                    npc_name=npc_name,
                    fit_score=fit_score,
                    fit_confidence=None,
                    company_context_id=int(company_context_id),
                )
                bus.publish(
                    "npc.talent_purchased",
                    {
                        "participant_id": int(participant_id),
                        "npc_name": npc_name,
                        "listing_id": int(listing_id),
                        "person_id": int(listing.person_id),
                        "price": resolved_price,
                        "seller_company_id": seller_company_id,
                        "transaction_id": transaction_id,
                    },
                    company_id=seller_company_id,
                )
        except Exception:
            if commit:
                self.db.rollback()
            raise

        return NpcPurchase(
            participant_id=int(participant_id),
            npc_name=npc_name,
            listing_id=int(listing_id),
            person_id=int(listing.person_id),
            identity_id=None,
            price=resolved_price,
            fit_score=fit_score,
            sell_to_company_id=seller_company_id,
            transaction_id=transaction_id,
        )

    def _pay_seller(
        self,
        *,
        participant_id: int,
        seller_company_id: int | None,
        amount: int,
        listing_id: int,
    ) -> int:
        """从 NPC 钱包转账给卖方（系统/发行方卖家 ⇒ Treasury 收款）。

        用 `transfer`（不是 escrow）：NPC 交易是**即时结算**，没有需要托管等待的人类对手方。
        """
        accounts = AccountService(self.db)
        if seller_company_id is None:
            seller_account_id = int(
                accounts.ensure_system_accounts()[LedgerAccountKind.treasury].id
            )
        else:
            seller_account_id = int(
                accounts.ensure_account(EconomicActor.company(int(seller_company_id))).id
            )
        posting = LedgerService(self.db).transfer(
            payer_account_id=int(self.wallet(int(participant_id)).id),
            payee_account_id=seller_account_id,
            amount=int(amount),
            reason="npc_talent_purchase",
            reference_type="market_listing",
            reference_id=str(int(listing_id)),
            category=EconomicCategory.talent_purchase,
            idempotency_key=f"npc_purchase:{int(participant_id)}:{int(listing_id)}",
            metadata={
                "participant_id": int(participant_id),
                "seller_company_id": seller_company_id,
            },
            commit=False,
        )
        return int(posting.transaction.id)

    # ---------------------------------------------------------------- 一轮活动

    def run_round(
        self,
        *,
        company_context_id: int,
        only_npc_key: str | None = None,
        participant_ids: list[int] | None = None,
        inject: bool = True,
        dry_run: bool = False,
        commit: bool = True,
    ) -> list[NpcRoundOutcome]:
        """跑一轮 NPC 经济：注入（按需）→ 发现 → Fit → 判定 → 成交（在预算内）。

        `company_context_id`：fit 的职位/口径上下文（NPC 无公司，沿用 T2 NPC 活动的同一约定）；
        钱和参与方始终是 NPC 自己。
        """
        specs = [spec for spec in npc_market.NPC_SPECS if only_npc_key in (None, spec.key)]
        if only_npc_key is not None and not specs:
            raise NpcEconomyError("unknown_npc_key", http_status=404)

        npc_service = npc_market.NpcMarketService()
        npc_service.ensure_participants(self.db, company_context_id=int(company_context_id))
        adapter = LocalMarketAdapter()
        candidates = adapter.search_listings(self.db, MarketSearchQuery(limit=None))
        outcomes: list[NpcRoundOutcome] = []

        targets: list[tuple[str, int, object]] = []
        if participant_ids is not None:
            for participant_id in participant_ids:
                participant = market_repo.get_participant(self.db, int(participant_id))
                if participant is None:
                    raise NpcEconomyError("participant_not_found", http_status=404)
                targets.append(
                    (participant.display_name or f"NPC {participant_id}", int(participant_id), None)
                )
        else:
            for spec in specs:
                participant = npc_service._participant_by_key(self.db, spec.key)  # noqa: SLF001
                if participant is None:  # pragma: no cover - ensure_participants 刚建过
                    continue
                targets.append((spec.display_name, int(participant.id), spec))

        for npc_name, participant_id, spec in targets:
            participant_id = int(participant.id)
            profile = self.ensure_profile(participant_id)
            injected = 0
            if inject and not dry_run:
                # 钱包见底就先补（受封顶）；这是**唯一的发行入口**
                if self.available_budget(participant_id) < int(self.policy.npc_budget_injection):
                    injected, _total = self.inject_budget(participant_id, commit=False)
            # fit 口径：有 spec 用它的招聘标准模板；没有 spec（临时/测试参与者）用系统模板。
            # 注意：这一步不能放进上面的 inject 分支 —— dry-run 也要能算出 fit（只是不动钱）。
            definition = (
                npc_service._ensure_definition(self.db, spec) if spec is not None else None
            )
            min_fit = float(spec.min_fit_score) if spec is not None else 0.0
            min_confidence = float(spec.min_fit_confidence) if spec is not None else 0.0
            purchased: list[NpcPurchase] = []
            skipped: list[NpcDecision] = []
            considered = 0
            scored: list[tuple[float, float, object]] = []
            for view in candidates:
                if any(item.listing_id == view.listing_id for item in purchased):
                    continue
                if definition is None:
                    # 没有 NPC spec（临时/测试参与者）⇒ 用**系统模板**做 fit 口径
                    definition = npc_service.ensure_system_definition(self.db)
                if definition is not None:
                    result = fit_service.calculate_person_fit(
                        self.db,
                        person_id=view.person_id,
                        position_definition_id=int(definition.id),
                        company_id=int(company_context_id),
                    )
                considered += 1
                score = result.known_fit_score
                confidence = result.fit_confidence
                if (
                    score is None
                    or confidence is None
                    or score < min_fit
                    or confidence < min_confidence
                ):
                    continue
                scored.append((float(score), float(confidence), view))
            scored.sort(key=lambda item: (-item[0], -item[1], item[2].listing_id))

            for score, _confidence, view in scored:
                if len(purchased) >= int(profile.deals_per_round):
                    break
                terms = economy_repo.get_terms_by_listing(self.db, listing_id=int(view.listing_id))
                price = int(terms.price) if terms is not None else 0
                decision = self.decide(participant_id=participant_id, price=price, fit_score=score)
                if not decision.allowed:
                    skipped.append(decision)
                    continue
                if dry_run:
                    purchased.append(
                        NpcPurchase(
                            participant_id=participant_id,
                            npc_name=spec.display_name,
                            listing_id=int(view.listing_id),
                            person_id=int(view.person_id),
                            identity_id=view.identity_id,
                            price=price,
                            fit_score=score,
                            sell_to_company_id=(
                                int(terms.seller_company_id)
                                if terms is not None and terms.seller_company_id is not None
                                else None
                            ),
                            transaction_id=None,
                        )
                    )
                    continue
                purchase = self.purchase_talent(
                    participant_id,
                    int(view.listing_id),
                    fit_score=score,
                    npc_name=npc_name,
                    company_context_id=int(company_context_id),
                    price=price,
                    commit=False,
                )
                if purchase is not None:
                    purchased.append(purchase)

            outcomes.append(
                NpcRoundOutcome(
                    npc_name=npc_name,
                    participant_id=participant_id,
                    budget_injected=injected,
                    available=self.available_budget(participant_id),
                    considered=considered,
                    purchased=purchased,
                    skipped=skipped[:10],
                    dry_run=dry_run,
                )
            )

        if commit and not dry_run:
            self.db.commit()
        return outcomes

    # ---------------------------------------------------------------- 收入记录

    def income_summary(self, participant_id: int) -> dict[str, int]:
        """NPC 收入/支出汇总（按业务类别；只记录，不做经营模拟，§29）。"""
        account = self.wallet(int(participant_id))
        totals = economy_repo.category_totals_for_accounts(self.db, account_ids=[int(account.id)])
        summary: dict[str, int] = {}
        for category, (debits, credits) in totals.items():
            summary[category or "unclassified"] = int(debits) - int(credits)
        return summary

    def status(self, *, company_context_id: int) -> list[dict]:
        """NPC 经济状态（CLI/观测：余额/累计注入/上限/参数）。"""
        npc_service = npc_market.NpcMarketService()
        npc_service.ensure_participants(self.db, company_context_id=int(company_context_id))
        rows: list[dict] = []
        for spec in npc_market.NPC_SPECS:
            participant = npc_service._participant_by_key(self.db, spec.key)  # noqa: SLF001
            if participant is None:  # pragma: no cover
                continue
            participant_id = int(participant.id)
            profile = self.ensure_profile(participant_id)
            rows.append(
                {
                    "npc_key": spec.key,
                    "npc_name": spec.display_name,
                    "participant_id": participant_id,
                    "enabled": bool(profile.enabled),
                    "available": self.available_budget(participant_id),
                    "injected_total": int(profile.budget_injected_total),
                    "budget_cap": int(profile.budget_cap),
                    "max_price": int(profile.max_price),
                    "fit_threshold_bps": int(profile.fit_threshold_bps),
                    "deals_per_round": int(profile.deals_per_round),
                }
            )
        return rows


def _with_reason(decision: NpcDecision, reason: str, *, allowed: bool | None = None) -> NpcDecision:
    return NpcDecision(
        allowed=decision.allowed if allowed is None else allowed,
        reason=reason,
        price=decision.price,
        fit_score=decision.fit_score,
        available=decision.available,
        max_price=decision.max_price,
        threshold_bps=decision.threshold_bps,
    )


__all__ = [
    "NpcDecision",
    "NpcEconomyError",
    "NpcEconomyService",
    "NpcPurchase",
    "NpcRoundOutcome",
]
