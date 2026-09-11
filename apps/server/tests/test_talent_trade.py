"""M1.7 人才商业化（docs/m1-economy-design.md §27/§28；plan §4/M1.7）。

断言的是**跨域不变量**（T2 冻结在旁，M1 只调用）：

- **成交资金**：买方扣款（对价 + 挂牌手续费）、卖方收款**净额**、平台手续费拆进 Treasury/Burn，
  三腿之和 = 对价；**绝不 mint**（E7/E8），只有手续费 burn 腿回收流通；
- **T2 不变量（E18/E19/E20）**：`person_id` / `identity_id` 交易前后不变；
  `persons` / `character_profiles` / `education_events` / 知识 / 特质 / 证据 / 评估 / 能力
  **零改写**（快照对拍）；招聘只走 `RecruitmentService`；
- **原子性（E13/E14/E15）**：锁资 → 招募 → 放款同一事务；招募失败 ⇒ 整笔回滚
  （钱不动、挂牌仍在市、没有半个员工）；
- 条款只有卖方能设、只有 active 挂牌能设；一口价必须按标价、议价需卖方接受；
- 系统/发行方挂牌（无卖方公司）⇒ 成交款进 Treasury。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.models.competency import CompetencyEvidence, EmployeeCompetency
from app.models.cultivation import CharacterProfile, EducationEvent
from app.models.enums import (
    ContractStatus,
    EscrowStatus,
    LedgerAccountKind,
    MarketListingStatus,
    OfferStatus,
    TalentSaleMode,
)
from app.models.knowledge import KnowledgeItem, Skill
from app.models.market import MarketListing
from app.models.organization import Company, Employee
from app.models.person import Person
from app.repositories import economy as economy_repo
from app.repositories import market as market_repo
from app.services.economy.accounts import AccountService
from app.services.economy.costs import FeeService
from app.services.economy.escrow import EscrowService
from app.services.economy.ledger import LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import verify_wallet_projection
from app.services.economy.talent_trade import TalentTradeError, TalentTradeService

_seq = 0


def _company(db, name: str = "TradeCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"trade-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _fund(db, company: Company, amount: int) -> int:
    MonetaryAuthority(db).mint(
        actor=EconomicActor.company(company.id), amount=amount, reason="test"
    )
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


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


def _system_balance(db, kind: LedgerAccountKind) -> int:
    total = 0
    for account in economy_repo.list_accounts_by_kind(db, kinds=(kind.value,)):
        total += LedgerService(db).ledger_balance(int(account.id))
    return total


def _listing_for(db, person_id: int, *, seller_company: Company | None) -> MarketListing:
    """直接建一条 active 挂牌（模拟 T2 挂牌；T2 的挂牌路径由 T2 测试覆盖）。"""
    participant = market_repo.ensure_participant(
        db,
        kind="player_company" if seller_company is not None else "system_issuer",
        company_id=seller_company.id if seller_company is not None else None,
        display_name=seller_company.name if seller_company is not None else "System Issuer",
    )
    listing = MarketListing(
        person_id=int(person_id),
        status=MarketListingStatus.active.value,
        listed_by_participant_id=int(participant.id),
        listed_at=datetime.now(UTC),
    )
    db.add(listing)
    db.commit()
    return listing


def _ready_person(db, *, owner_company: Company | None, name: str = "Talent") -> Person:
    """建一个"在市可招募"的 person（ready + 无在职）。"""
    global _seq
    _seq += 1
    person = Person(slug=f"talent-{_seq}", name=f"{name} {_seq}", avatar="")
    db.add(person)
    db.flush()
    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"EU-TRADE-{_seq}",
            origin="blank",
            lifecycle="ready",
            owner_company_id=owner_company.id if owner_company is not None else None,
        )
    )
    db.commit()
    return person


def _snapshot_t2_assets(db) -> dict[str, int | list]:
    """T2 侧"Person 拥有物"的快照（E19：交易不得改写它们）。"""

    def count(model) -> int:
        return int(db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one())

    def digest(model, *columns) -> list[tuple]:
        rows = db.execute(sa.select(*[getattr(model, c) for c in columns])).all()
        return sorted(tuple(row) for row in rows)

    return {
        "persons": digest(Person, "id", "slug", "name", "avatar"),
        "profiles": digest(
            CharacterProfile,
            "id",
            "person_id",
            "identity_id",
            "origin",
            "lifecycle",
            "owner_company_id",
        ),
        "education": digest(EducationEvent, "id", "person_id", "kind"),
        "knowledge": digest(KnowledgeItem, "id", "owner_person_id", "title"),
        "skills": digest(Skill, "id", "person_id", "name"),
        "evidence": digest(CompetencyEvidence, "id", "person_id", "source_kind"),
        "competencies": digest(EmployeeCompetency, "id", "employee_id")
        if count(EmployeeCompetency)
        else [],
        "education_count": count(EducationEvent),
    }


# ---------------------------------------------------------------- 条款


def test_terms_are_seller_only_and_require_active_listing(db):
    seller = _company(db, "TermsSeller")
    stranger = _company(db, "TermsStranger")
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)

    with pytest.raises(TalentTradeError, match="listing_not_found"):
        service.set_terms(listing.id, company_id=stranger.id, price=5_000)
    with pytest.raises(TalentTradeError, match="price_must_be_positive"):
        service.set_terms(listing.id, company_id=seller.id, price=0)

    terms = service.set_terms(
        listing.id, company_id=seller.id, price=5_000, sale_mode=TalentSaleMode.negotiation
    )
    assert int(terms.price) == 5_000
    assert terms.sale_mode == TalentSaleMode.negotiation.value
    assert terms.seller_company_id == seller.id
    assert service.terms_for(listing.id).price == 5_000
    # 改价是 UPDATE（一条挂牌一套条款）
    updated = service.set_terms(listing.id, company_id=seller.id, price=6_000)
    assert int(updated.id) == int(terms.id)
    assert int(updated.price) == 6_000


def test_terms_require_listing_in_market(db):
    seller = _company(db, "ClosedSeller")
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    listing.status = MarketListingStatus.closed.value
    db.commit()
    with pytest.raises(TalentTradeError, match="listing_not_active"):
        TalentTradeService(db).set_terms(listing.id, company_id=seller.id, price=1_000)


# ---------------------------------------------------------------- 一口价成交


def test_buyout_purchase_moves_money_and_recruits(db):
    seller = _company(db, "BuyoutSeller")
    buyer = _company(db, "BuyoutBuyer")
    seller_account = _fund(db, seller, 1_000)
    buyer_account = _fund(db, buyer, 50_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    identity_id = person_profile_identity(db, int(person.id))

    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    burn_before = _system_balance(db, LedgerAccountKind.burn)
    minted_before = LedgerService(db).supply().minted
    snapshot_before = _snapshot_t2_assets(db)

    service = TalentTradeService(db)
    service.set_terms(listing.id, company_id=seller.id, price=10_000)
    offer, purchase = service.create_offer(
        listing.id, buyer_company_id=buyer.id, amount=10_000, message="buying"
    )
    assert purchase is not None, "一口价应当立即成交"
    assert offer.status == OfferStatus.accepted.value
    assert offer.contract_id == purchase.contract_id

    quote = FeeService(db).contract_quote(10_000)
    assert (purchase.gross, purchase.fee, purchase.net) == (10_000, quote.fee, quote.net)
    assert quote.treasury + quote.burn + quote.net == 10_000

    # 钱：买方扣对价（锁资时已扣）→ 卖方拿净额；手续费进 Treasury/Burn
    assert _wallet(buyer_account) == {"posted": 40_000, "available": 40_000, "reserved": 0}
    assert _wallet(seller_account)["available"] == 1_000 + quote.net
    assert _system_balance(db, LedgerAccountKind.treasury) - treasury_before == quote.treasury
    assert _system_balance(db, LedgerAccountKind.burn) - burn_before == quote.burn
    supply = LedgerService(db).supply()
    assert supply.minted == minted_before  # E8：人才交易绝不 mint
    assert supply.burned - burn_before == quote.burn
    assert supply.supply == supply.minted - supply.burned

    # T2 语义：员工创建、挂牌关闭、identity/person 不变、资产零改写
    employee = db.get(Employee, purchase.employee_id)
    assert employee is not None
    assert int(employee.company_id) == buyer.id
    assert int(employee.person_id or 0) == int(person.id)
    assert (
        economy_repo.get_contract(db, purchase.contract_id).status == ContractStatus.settled.value
    )
    escrow = EscrowService(db).get_for_contract(purchase.contract_id)
    assert escrow.status == EscrowStatus.released.value
    assert EscrowService(db).view(escrow).account_balance == 0  # E25
    assert db.get(MarketListing, listing.id).status == MarketListingStatus.closed.value
    assert int(db.get(MarketListing, listing.id).recruited_company_id or 0) == buyer.id
    assert person_profile_identity(db, int(person.id)) == identity_id
    assert _snapshot_t2_assets(db) == snapshot_before  # E19：Person 拥有物零改写
    assert verify_wallet_projection(db).ok


def test_buyout_requires_exact_price_and_no_self_purchase(db):
    seller = _company(db, "ExactSeller")
    buyer = _company(db, "ExactBuyer")
    _fund(db, buyer, 20_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)
    service.set_terms(listing.id, company_id=seller.id, price=7_000)
    talents_before = len(economy_repo.list_contracts(db, contract_type="talent"))

    with pytest.raises(TalentTradeError, match="buyout_requires_exact_price"):
        service.create_offer(listing.id, buyer_company_id=buyer.id, amount=6_999)
    with pytest.raises(TalentTradeError, match="cannot_buy_your_own_talent"):
        service.create_offer(listing.id, buyer_company_id=seller.id, amount=7_000)
    # 两次拒绝都没有产生 talent 合同（共享测试库：只看增量）
    assert len(economy_repo.list_contracts(db, contract_type="talent")) == talents_before


def test_offer_requires_terms_and_buyer_with_funds(db):
    seller = _company(db, "NoTermsSeller")
    buyer = _company(db, "PoorBuyer")
    _fund(db, buyer, 100)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)

    with pytest.raises(TalentTradeError, match="commercial_terms_not_set"):
        service.create_offer(listing.id, buyer_company_id=buyer.id, amount=1_000)

    service.set_terms(listing.id, company_id=seller.id, price=5_000)
    # 余额不足 ⇒ 整笔回滚：没有合同、没有托管、挂牌仍在市、没有员工
    contracts_before = len(economy_repo.list_contracts(db))
    with pytest.raises(Exception) as excinfo:
        service.create_offer(listing.id, buyer_company_id=buyer.id, amount=5_000)
    assert getattr(excinfo.value, "reason", None) == "insufficient_funds"
    assert len(economy_repo.list_contracts(db)) == contracts_before
    assert db.get(MarketListing, listing.id).status == MarketListingStatus.active.value
    assert (
        int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(Employee)
                .where(Employee.company_id == buyer.id)
            ).scalar_one()
        )
        == 0
    )


# ---------------------------------------------------------------- 议价成交


def test_negotiation_offer_needs_seller_acceptance(db):
    seller = _company(db, "NegoSeller")
    buyer = _company(db, "NegoBuyer")
    seller_account = int(AccountService(db).ensure_account(EconomicActor.company(seller.id)).id)
    buyer_account = _fund(db, buyer, 30_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)
    service.set_terms(
        listing.id, company_id=seller.id, price=9_000, sale_mode=TalentSaleMode.negotiation
    )

    offer, purchase = service.create_offer(
        listing.id, buyer_company_id=buyer.id, amount=8_000, message="offer"
    )
    assert purchase is None
    assert offer.status == OfferStatus.open.value
    # 出价阶段不动钱
    assert _wallet(buyer_account) == {"posted": 30_000, "available": 30_000, "reserved": 0}

    stranger = _company(db, "NegoStranger")
    with pytest.raises(TalentTradeError, match="listing_not_found"):
        service.accept_offer(offer.id, company_id=stranger.id)

    accepted, purchase = service.accept_offer(offer.id, company_id=seller.id)
    assert purchase is not None
    assert accepted.status == OfferStatus.accepted.value
    quote = FeeService(db).contract_quote(8_000)
    assert _wallet(buyer_account)["available"] == 30_000 - 8_000
    assert _wallet(seller_account)["available"] == quote.net
    assert db.get(Employee, purchase.employee_id) is not None
    # 已接受的出价不能再次接受
    with pytest.raises(TalentTradeError, match="offer_not_open"):
        service.accept_offer(offer.id, company_id=seller.id)


def test_double_purchase_is_blocked_by_listing_state(db):
    seller = _company(db, "DoubleSeller")
    first_buyer = _company(db, "DoubleBuyer1")
    second_buyer = _company(db, "DoubleBuyer2")
    _fund(db, first_buyer, 20_000)
    _fund(db, second_buyer, 20_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)
    service.set_terms(listing.id, company_id=seller.id, price=5_000)
    _, purchase = service.create_offer(listing.id, buyer_company_id=first_buyer.id, amount=5_000)
    assert purchase is not None

    # 挂牌已关闭 ⇒ 第二次购买被拒（钱不动）
    with pytest.raises(TalentTradeError, match="listing_not_active"):
        service.create_offer(listing.id, buyer_company_id=second_buyer.id, amount=5_000)
    second_account = int(
        AccountService(db).ensure_account(EconomicActor.company(second_buyer.id)).id
    )
    assert _wallet(second_account)["available"] == 20_000


# ---------------------------------------------------------------- 失败回滚 / 系统卖方


def test_recruitment_failure_rolls_back_the_whole_purchase(db, monkeypatch):
    """招募失败（T2 拒绝）⇒ 整笔回滚：钱不动、挂牌仍在市、没有半个员工（E13/E14/E15）。"""
    from app.services import recruitment as recruitment_service

    seller = _company(db, "FailSeller")
    buyer = _company(db, "FailBuyer")
    buyer_account = _fund(db, buyer, 20_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    service = TalentTradeService(db)
    service.set_terms(listing.id, company_id=seller.id, price=6_000)
    talents_before = len(economy_repo.list_contracts(db, contract_type="talent"))

    def boom(*args, **kwargs):
        raise recruitment_service.RecruitmentError("listing_not_active")

    monkeypatch.setattr(recruitment_service.RecruitmentService, "recruit_existing_person", boom)
    with pytest.raises(recruitment_service.RecruitmentError):
        service.create_offer(listing.id, buyer_company_id=buyer.id, amount=6_000)

    # 钱一分没动、没有新合同、挂牌仍在市、没有半个员工
    assert _wallet(buyer_account) == {"posted": 20_000, "available": 20_000, "reserved": 0}
    assert len(economy_repo.list_contracts(db, contract_type="talent")) == talents_before
    assert db.get(MarketListing, listing.id).status == MarketListingStatus.active.value
    assert (
        int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(Employee)
                .where(Employee.company_id == buyer.id)
            ).scalar_one()
        )
        == 0
    )


def test_system_listed_talent_pays_treasury(db):
    """系统/发行方挂牌（无卖方公司）⇒ 成交款进 Treasury（M1.8 的 NPC 财政再细化）。"""
    buyer = _company(db, "SysBuyer")
    _fund(db, buyer, 20_000)
    person = _ready_person(db, owner_company=None)
    listing = _listing_for(db, int(person.id), seller_company=None)
    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    minted_before = LedgerService(db).supply().minted

    service = TalentTradeService(db)
    terms = service.set_terms(listing.id, company_id=None, price=4_000)
    assert terms.seller_company_id is None
    with pytest.raises(TalentTradeError, match="listing_not_found"):
        # 系统挂牌没有"卖方公司"，玩家不能冒充卖方改条款
        service.set_terms(listing.id, company_id=buyer.id, price=1)

    offer, purchase = service.create_offer(listing.id, buyer_company_id=buyer.id, amount=4_000)
    assert purchase is not None
    assert purchase.seller_company_id is None
    quote = FeeService(db).contract_quote(4_000)
    # 净额 + treasury 手续费都进 Treasury（系统卖方）
    assert (
        _system_balance(db, LedgerAccountKind.treasury) - treasury_before
        == quote.net + quote.treasury
    )
    assert LedgerService(db).supply().minted == minted_before
    assert (
        _wallet(int(AccountService(db).ensure_account(EconomicActor.company(buyer.id)).id))[
            "available"
        ]
        == 16_000
    )


def person_profile_identity(db, person_id: int) -> str | None:
    profile = db.scalars(
        sa.select(CharacterProfile).where(CharacterProfile.person_id == int(person_id))
    ).first()
    return profile.identity_id if profile is not None else None


# ---------------------------------------------------------------- API


def _as_company(db, company_id: int | None):
    from app.api.scope import resolve_company_id
    from app.main import app as fastapi_app

    class _Ctx:
        def __enter__(self):
            db.rollback()
            fastapi_app.dependency_overrides[resolve_company_id] = lambda: company_id
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            fastapi_app.dependency_overrides.pop(resolve_company_id, None)
            db.rollback()
            return False

    return _Ctx()


def test_trade_api_terms_offer_and_buyout(client, db):
    seller = _company(db, "ApiTradeSeller")
    buyer = _company(db, "ApiTradeBuyer")
    stranger = _company(db, "ApiTradeStranger")
    _fund(db, buyer, 40_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)
    identity_before = person_profile_identity(db, int(person.id))

    # 非挂牌方不能设置条款（404，不泄露存在性）
    with _as_company(db, stranger.id):
        denied = client.post(
            f"/api/v1/market/listings/{listing.id}/commercial-terms",
            json={"price": 1_000, "sale_mode": "buyout"},
        )
        assert denied.status_code == 404

    with _as_company(db, seller.id):
        created = client.post(
            f"/api/v1/market/listings/{listing.id}/commercial-terms",
            json={"price": 12_000, "sale_mode": "buyout"},
        )
    assert created.status_code == 201
    assert created.json()["price"] == 12_000
    assert created.json()["seller_company_id"] == seller.id

    # 价格是公开信息
    with _as_company(db, stranger.id):
        terms = client.get(f"/api/v1/market/listings/{listing.id}/commercial-terms").json()
        assert terms["price"] == 12_000

    # 一口价：金额不符 422；买方出价即成交
    with _as_company(db, buyer.id):
        wrong = client.post(f"/api/v1/market/listings/{listing.id}/offers", json={"amount": 11_000})
        assert wrong.status_code == 422
        assert wrong.json()["detail"] == "buyout_requires_exact_price"
        bought = client.post(
            f"/api/v1/market/listings/{listing.id}/offers", json={"amount": 12_000}
        )
    assert bought.status_code == 201
    payload = bought.json()
    assert payload["purchase"] is not None
    assert payload["offer"]["status"] == "ACCEPTED"
    assert payload["purchase"]["buyer_company_id"] == buyer.id
    assert payload["purchase"]["seller_company_id"] == seller.id
    assert payload["purchase"]["gross"] == 12_000
    assert payload["purchase"]["net"] + payload["purchase"]["fee"] == 12_000
    assert person_profile_identity(db, int(person.id)) == identity_before

    # 卖方能在出价列表里看到这条（已成交）记录；买方也能看到自己出的
    with _as_company(db, seller.id):
        offers = client.get(f"/api/v1/market/listings/{listing.id}/offers").json()
        assert [row["status"] for row in offers] == ["ACCEPTED"]
    with _as_company(db, buyer.id):
        offers = client.get(f"/api/v1/market/listings/{listing.id}/offers").json()
        assert len(offers) == 1

    # 成交后挂牌关闭：再出价 409
    with _as_company(db, buyer.id):
        again = client.post(f"/api/v1/market/listings/{listing.id}/offers", json={"amount": 12_000})
        assert again.status_code == 409
        assert again.json()["detail"] == "listing_not_active"


def test_trade_api_negotiation_accept_flow(client, db):
    seller = _company(db, "ApiNegoSeller")
    buyer = _company(db, "ApiNegoBuyer")
    stranger = _company(db, "ApiNegoStranger")
    _fund(db, buyer, 20_000)
    person = _ready_person(db, owner_company=seller)
    listing = _listing_for(db, int(person.id), seller_company=seller)

    with _as_company(db, seller.id):
        client.post(
            f"/api/v1/market/listings/{listing.id}/commercial-terms",
            json={"price": 9_000, "sale_mode": "negotiation"},
        )
    with _as_company(db, buyer.id):
        offered = client.post(
            f"/api/v1/market/listings/{listing.id}/offers",
            json={"amount": 8_000, "message": "best offer"},
        ).json()
    assert offered["purchase"] is None
    offer_id = offered["offer"]["offer_id"]

    # 非卖方不能接受
    with _as_company(db, stranger.id):
        denied = client.post(f"/api/v1/market/offers/{offer_id}/accept")
        assert denied.status_code == 404

    with _as_company(db, seller.id):
        accepted = client.post(f"/api/v1/market/offers/{offer_id}/accept")
    assert accepted.status_code == 200
    purchase = accepted.json()["purchase"]
    assert purchase is not None
    assert purchase["gross"] == 8_000
    assert db.get(Employee, purchase["employee_id"]) is not None
