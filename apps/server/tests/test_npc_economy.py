"""M1.8 NPC 经济（docs/m1-economy-design.md §29；plan §4/M1.8）。

断言的是**发行边界**：NPC 的钱只有两个来源 —— 系统注入（mint，受 `budget_cap` 封顶）
与收入（转移）；NPC 自己出手**只是转移**（E7/E8：`minted` 不变、supply 恒定）。
出手规则是 §29 的 deterministic 字面实现（无 LLM）：

```
available >= price  and  price <= max_price  and  fit >= threshold
```

关键用例：注入是 mint 且封顶、不会买超、判定规则逐条、成交后人才离场（T2 consumed）、
`run_round` 在预算内成交至少一单且每单可解释（acceptance）、dry-run 不动钱、
NPC 收入先只记录（账本按类别汇总）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.models.competency import CompetencyDefinition, CompetencyDomain, EmployeeCompetency
from app.models.cultivation import CharacterProfile
from app.models.economy import LedgerTransaction
from app.models.enums import (
    EconomicCategory,
    MarketListingStatus,
)
from app.models.market import MarketListing, MarketParticipant
from app.models.organization import Company
from app.models.person import Person
from app.repositories import economy as economy_repo
from app.repositories import market as market_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.npc_economy import NpcEconomyError, NpcEconomyService
from app.services.economy.talent_trade import TalentTradeService
from app.talent.market import eligibility
from app.talent.market import npc as npc_market

_seq = 0


def _company(db, name: str = "NpcCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"npc-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _wallet(account_id: int) -> dict:
    with SessionLocal() as session:
        row = economy_repo.get_projection(session, account_id)
        if row is None:
            return {"posted": 0, "available": 0, "reserved": 0}
        return {
            "posted": int(row.posted_balance),
            "available": int(row.available_balance),
            "reserved": int(row.reserved_balance),
        }


def _supply(db):
    return LedgerService(db).supply()


def _definition_id(db, code: str) -> int:
    return int(
        db.scalar(
            sa.select(CompetencyDefinition.id)
            .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
            .where(CompetencyDefinition.code == code, CompetencyDomain.company_id.is_(None))
        )
    )


def _listed_talent(
    db, *, client=None, scores: dict[str, tuple[int, float]] | None = None, name: str = "NpcTalent"
) -> tuple[int, int]:
    """建一个"在市 + 有 fit 分"的候选人并挂牌（复用 T2 的公开路径或直接落库）。

    返回 `(person_id, listing_id)`。`T2 的挂牌路径由 T2 测试覆盖` —— 这里为了聚焦 M1
    的钱，直接建 person/profile/listing（与 tests/test_market_npc.py 同一手法）。
    """
    global _seq
    _seq += 1
    person = Person(slug=f"npc-talent-{_seq}", name=f"{name} {_seq}", avatar="")
    db.add(person)
    db.flush()
    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"EU-NPC-{_seq}",
            origin="blank",
            lifecycle="ready",
            owner_company_id=None,
        )
    )
    for code, (score, confidence) in (scores or {}).items():
        db.add(
            EmployeeCompetency(
                person_id=int(person.id),
                employee_id=None,  # person-only：候选人没有 employee 行
                competency_definition_id=_definition_id(db, code),
                score=score,
                confidence=confidence,
                status="assessed",
                evidence_count=6,
            )
        )
    db.flush()
    participant = market_repo.ensure_participant(
        db, kind="system_issuer", company_id=None, display_name="System Issuer"
    )
    listing = MarketListing(
        person_id=int(person.id),
        status=MarketListingStatus.active.value,
        listed_by_participant_id=int(participant.id),
        listed_at=datetime.now(UTC),
    )
    db.add(listing)
    db.commit()
    return int(person.id), int(listing.id)


def _npc_participant_id(db, key: str, *, context_company_id: int) -> int:
    """**共享**的正式 NPC 参与者（`NPC_SPECS` 里的 xinghai/mercury）——只用于 `run_round` 测试。"""
    service = npc_market.NpcMarketService()
    service.ensure_participants(db, company_context_id=int(context_company_id))
    participant = service._participant_by_key(db, key)  # noqa: SLF001 - 测试与 CLI 同一入口
    assert participant is not None
    return int(participant.id)


def _local_npc(db, name: str = "Test NPC") -> int:
    """**用例本地**的 npc_company 参与者（独立钱包/档案，避免用例之间共享余额）。"""
    global _seq
    _seq += 1
    participant = MarketParticipant(kind="npc_company", display_name=f"{name} {_seq}")
    db.add(participant)
    db.commit()
    return int(participant.id)


# ---------------------------------------------------------------- 预算注入


def test_budget_injection_mints_and_is_capped(db):
    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Budget NPC")
    profile = service.ensure_profile(participant_id)
    profile.budget_cap = 8_000  # 测试用小额度，便于验证封顶
    profile.fit_threshold_bps = 7_000
    db.commit()

    minted_before = _supply(db).minted
    account_id = int(service.wallet(participant_id).id)
    granted, total = service.inject_budget(participant_id, amount=5_000, commit=True)
    assert (granted, total) == (5_000, 5_000)
    assert _wallet(account_id)["available"] == 5_000
    assert _supply(db).minted - minted_before == 5_000  # 注入 = mint（计入发行，§29）
    transaction = db.scalars(
        sa.select(LedgerTransaction).where(
            LedgerTransaction.category == EconomicCategory.npc_budget.value
        )
    ).first()
    assert transaction is not None and transaction.transaction_type == "mint"

    # 封顶：再注入只能拿到剩余额度，之后 0
    granted, total = service.inject_budget(participant_id, amount=5_000, commit=True)
    assert (granted, total) == (3_000, 8_000)
    granted, total = service.inject_budget(participant_id, amount=5_000, commit=True)
    assert (granted, total) == (0, 8_000)
    assert _wallet(account_id)["available"] == 8_000
    assert _supply(db).minted - minted_before == 8_000  # 累计注入 = 8,000（没有被超发）


def test_injection_rejects_non_positive_amount(db):
    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Negative NPC")
    with pytest.raises(NpcEconomyError, match="injection_must_be_positive"):
        service.inject_budget(participant_id, amount=0)


# ---------------------------------------------------------------- 判定规则


def test_decision_rules_are_deterministic(db):
    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Decision NPC")
    profile = service.ensure_profile(participant_id)
    profile.budget_cap = 10_000
    profile.max_price = 6_000
    profile.fit_threshold_bps = 7_000
    db.commit()
    service.inject_budget(participant_id, amount=10_000, commit=True)

    assert service.decide(participant_id=participant_id, price=5_000, fit_score=0.9).allowed is True
    assert service.decide(participant_id=participant_id, price=5_000, fit_score=0.9).reason == "ok"
    too_expensive = service.decide(participant_id=participant_id, price=6_001, fit_score=0.9)
    assert (too_expensive.allowed, too_expensive.reason) == (False, "price_above_max_price")
    low_fit = service.decide(participant_id=participant_id, price=5_000, fit_score=0.69)
    assert (low_fit.allowed, low_fit.reason) == (False, "fit_below_threshold")
    no_fit = service.decide(participant_id=participant_id, price=5_000, fit_score=None)
    assert (no_fit.allowed, no_fit.reason) == (False, "no_fit_score")
    # 预算不足：价格在上限内、但超过钱包余额（10,000 已注入）
    profile.max_price = 50_000
    db.commit()
    broke = service.decide(participant_id=participant_id, price=20_000, fit_score=0.9)
    assert (broke.allowed, broke.reason) == (False, "insufficient_budget")
    assert service.decide(participant_id=participant_id, price=10_000, fit_score=0.9).allowed
    # 禁用后一律不出手
    profile.enabled = False
    db.commit()
    disabled = service.decide(participant_id=participant_id, price=1_000, fit_score=0.9)
    assert (disabled.allowed, disabled.reason) == (False, "npc_disabled")


# ---------------------------------------------------------------- 成交与钱


def test_npc_purchase_moves_money_without_minting(db):
    context = _company(db, "NpcBuyContext")
    seller = _company(db, "NpcSeller")
    seller_account = int(AccountService(db).ensure_account(EconomicActor.company(seller.id)).id)
    person_id, listing_id = _listed_talent(db, scores={})
    # 卖方公司挂牌（覆盖系统挂牌之外的路径）
    participant = market_repo.ensure_participant(
        db, kind="player_company", company_id=seller.id, display_name=seller.name
    )
    db.get(MarketListing, listing_id).listed_by_participant_id = int(participant.id)
    db.commit()
    TalentTradeService(db).set_terms(listing_id, company_id=seller.id, price=4_000)

    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Buyer NPC")
    service.inject_budget(participant_id, amount=6_000, commit=True)
    npc_account = int(service.wallet(participant_id).id)
    minted_before = _supply(db).minted
    seller_before = _wallet(seller_account)["available"]

    purchase = service.purchase_talent(
        participant_id,
        listing_id,
        fit_score=0.95,
        npc_name="星海科技",
        company_context_id=context.id,
        commit=True,
    )
    assert purchase is not None
    assert purchase.price == 4_000
    # 钱：NPC 付、卖方收；绝不 mint（E7/E8）
    assert _wallet(npc_account)["available"] == 2_000
    assert _wallet(seller_account)["available"] - seller_before == 4_000
    assert _supply(db).minted == minted_before
    supply = _supply(db)
    assert supply.supply == supply.minted - supply.burned
    # 人才离场（T2 语义）+ 挂牌关闭
    listing = db.get(MarketListing, listing_id)
    assert listing.status == MarketListingStatus.closed.value
    assert int(listing.recruited_participant_id or 0) == participant_id
    assert eligibility.market_state(db, int(person_id)).value in {"consumed", "unavailable"}


def test_npc_will_not_overspend_and_respects_max_price(db):
    context = _company(db, "NpcBudgetContext")
    person_a, listing_a = _listed_talent(db, name="Affordable")
    person_b, listing_b = _listed_talent(db, name="TooExpensive")
    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Budget NPC")
    profile = service.ensure_profile(participant_id)
    profile.max_price = 10_000
    profile.deals_per_round = 5
    db.commit()
    service.inject_budget(participant_id, amount=3_000, commit=True)
    npc_account = int(service.wallet(participant_id).id)

    # 余额 3,000：只能买得起 2,000 的那单；10,000 的那单会被余额/上限拦住
    Trade = TalentTradeService(db)
    buyer = _company(db, "NpcTermsSetter")
    for listing_id, price in ((listing_a, 2_000), (listing_b, 10_000)):
        listing = db.get(MarketListing, listing_id)
        participant = market_repo.ensure_participant(
            db, kind="player_company", company_id=buyer.id, display_name=buyer.name
        )
        listing.listed_by_participant_id = int(participant.id)
        db.commit()
        Trade.set_terms(listing_id, company_id=buyer.id, price=price)

    bought = service.purchase_talent(
        participant_id, listing_a, fit_score=0.9, npc_name="星海科技", company_context_id=context.id
    )
    assert bought is not None
    assert _wallet(npc_account)["available"] == 1_000
    # 下一单：max_price 先拦（10,000 > 10,000 不成立？相等被允许）→ 用预算拦：1,000 < 10,000
    assert (
        service.decide(participant_id=participant_id, price=10_000, fit_score=0.9).reason
        == "insufficient_budget"
    )
    declined = service.purchase_talent(
        participant_id, listing_b, fit_score=0.9, npc_name="星海科技", company_context_id=context.id
    )
    assert declined is None
    assert db.get(MarketListing, listing_b).status == MarketListingStatus.active.value
    assert _wallet(npc_account)["available"] == 1_000  # 不会买超
    del person_a, person_b


def test_purchase_requires_price_and_active_listing(db):
    context = _company(db, "NpcGuardContext")
    person_id, listing_id = _listed_talent(db, name="NoPrice")
    del person_id
    service = NpcEconomyService(db)
    participant_id = _local_npc(db, "Guard NPC")
    service.inject_budget(participant_id, amount=5_000, commit=True)

    with pytest.raises(NpcEconomyError, match="listing_has_no_price"):
        service.purchase_talent(
            participant_id, listing_id, fit_score=0.9, company_context_id=context.id
        )
    listing = db.get(MarketListing, listing_id)
    listing.status = MarketListingStatus.closed.value
    db.commit()
    with pytest.raises(NpcEconomyError, match="listing_not_active"):
        service.purchase_talent(
            participant_id, listing_id, fit_score=0.9, company_context_id=context.id
        )


# ---------------------------------------------------------------- 一轮活动（acceptance）


def _npc_spec_scores(key: str, *, bump: int = 5) -> dict[str, tuple[int, float]]:
    """按某个 NPC 的招聘要求造出"高分"候选人分数（fit 才算得出来）。"""
    spec = next(row for row in npc_market.NPC_SPECS if row.key == key)
    return {
        req.competency_code: (min(100, req.target_score + bump), 0.95) for req in spec.requirements
    }


def test_run_round_settles_within_budget_and_is_explainable(db):
    context = _company(db, "NpcRoundContext")
    seller = _company(db, "NpcRoundSeller")
    seller_account = int(AccountService(db).ensure_account(EconomicActor.company(seller.id)).id)
    participant = market_repo.ensure_participant(
        db, kind="player_company", company_id=seller.id, display_name=seller.name
    )
    # 两个候选人：一个便宜且高 fit（按 xinghai 的要求造分），一个贵（超 max_price）
    cheap_person, cheap_listing = _listed_talent(
        db, scores=_npc_spec_scores("xinghai"), name="Cheap"
    )
    pricey_person, pricey_listing = _listed_talent(
        db, scores=_npc_spec_scores("xinghai"), name="Pricey"
    )
    del pricey_person
    for listing_id in (cheap_listing, pricey_listing):
        db.get(MarketListing, listing_id).listed_by_participant_id = int(participant.id)
    db.commit()
    terms_service = TalentTradeService(db)
    terms_service.set_terms(cheap_listing, company_id=seller.id, price=2_000)
    terms_service.set_terms(pricey_listing, company_id=seller.id, price=45_000)

    service = NpcEconomyService(db)
    # 共享的正式 NPC（xinghai）：把档案设成已知状态，断言全部走增量
    npc_id = _npc_participant_id(db, "xinghai", context_company_id=context.id)
    profile = service.ensure_profile(npc_id)
    profile.max_price = 5_000
    profile.deals_per_round = 1
    profile.fit_threshold_bps = 1  # 测试聚焦"钱与规则"，fit 分布由 T2 测试覆盖
    profile.budget_cap = int(profile.budget_injected_total) + 60_000
    db.commit()
    budget_before = service.available_budget(npc_id)

    minted_before = _supply(db).minted
    outcomes = service.run_round(company_context_id=context.id, only_npc_key="xinghai", inject=True)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.participant_id == npc_id
    assert len(outcome.purchased) >= 1, outcome  # acceptance：一轮至少一单
    purchase = outcome.purchased[0]
    assert purchase.price == 2_000
    # 账本可解释：注入 = mint；成交 = 转移（NPC 余额 = 之前 + 注入 − 成交价）
    assert _supply(db).minted - minted_before == outcome.budget_injected
    assert _wallet(seller_account)["available"] >= 2_000
    assert service.available_budget(npc_id) == budget_before + outcome.budget_injected - 2_000
    assert db.get(MarketListing, pricey_listing).status == MarketListingStatus.active.value
    assert eligibility.market_state(db, int(cheap_person)).value in {"consumed", "unavailable"}
    supply = _supply(db)
    assert supply.supply == supply.minted - supply.burned


def test_run_round_dry_run_does_not_move_money(db):
    context = _company(db, "NpcDryContext")
    seller = _company(db, "NpcDrySeller")
    seller_account = int(AccountService(db).ensure_account(EconomicActor.company(seller.id)).id)
    participant = market_repo.ensure_participant(
        db, kind="player_company", company_id=seller.id, display_name=seller.name
    )
    _, listing_id = _listed_talent(db, scores=_npc_spec_scores("mercury"), name="DryRun")
    db.get(MarketListing, listing_id).listed_by_participant_id = int(participant.id)
    db.commit()
    TalentTradeService(db).set_terms(listing_id, company_id=seller.id, price=1_000)

    service = NpcEconomyService(db)
    npc_id = _npc_participant_id(db, "mercury", context_company_id=context.id)
    profile = service.ensure_profile(npc_id)
    profile.fit_threshold_bps = 1
    db.commit()
    budget_before = service.available_budget(npc_id)
    minted_before = _supply(db).minted

    outcomes = service.run_round(
        company_context_id=context.id, only_npc_key="mercury", dry_run=True
    )
    outcome = outcomes[0]
    assert outcome.dry_run is True
    assert all(item.transaction_id is None for item in outcome.purchased)
    assert _supply(db).minted == minted_before  # dry-run 连注入都不做
    assert service.available_budget(npc_id) == budget_before
    assert _wallet(seller_account)["available"] == 0
    assert db.get(MarketListing, listing_id).status == MarketListingStatus.active.value


def test_income_summary_records_ledger_category_flows(db):
    """NPC 收入先只记录：从账本按类别汇总（§29 不做经营模拟）。"""
    payer = _company(db, "NpcIncomePayer")
    payer_account = int(AccountService(db).ensure_account(EconomicActor.company(payer.id)).id)
    MonetaryAuthority(db).mint(actor=EconomicActor.company(payer.id), amount=3_000, reason="test")
    service = NpcEconomyService(db)
    npc_id = _local_npc(db, "Income NPC")
    npc_account = int(service.wallet(npc_id).id)

    # 模拟一次"NPC 服务收入"：玩家公司付钱给 NPC
    LedgerService(db).transfer(
        payer_account_id=payer_account,
        payee_account_id=npc_account,
        amount=1_000,
        reason="npc_service_sale",
        reference_type="service_sale",
        reference_id="1",
        category=EconomicCategory.service_sale,
    )
    summary = service.income_summary(npc_id)
    assert summary.get(EconomicCategory.service_sale.value) == 1_000
    assert service.available_budget(npc_id) == 1_000
    assert _wallet(payer_account)["available"] == 2_000


def test_npc_economy_has_no_player_api_and_only_system_injection_mints():
    """边界守卫：NPC 经济没有玩家路由；mint 只出现在注入路径里。"""
    import ast
    from pathlib import Path as PathLib

    from app.main import app as fastapi_app

    paths = fastapi_app.openapi()["paths"]
    assert not [path for path in paths if "npc" in path and "economy" in path]

    server_root = PathLib(__file__).resolve().parents[1]
    source_path = server_root / "app/services/economy/npc_economy.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    mint_calls: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if inner.func.attr == "mint":
                        mint_calls[node.lineno] = node.name
    assert set(mint_calls.values()) == {"inject_budget"}, mint_calls
