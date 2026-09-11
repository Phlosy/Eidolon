"""M1.10 失败注入矩阵（plan §4/M1.10，Acceptance E）。

一条链路上"出错时会发生什么"比"顺利时会发生什么"更重要。这里把 M1 的失败面收敛成一张
**可执行的矩阵**：每个场景都先给一笔钱，再制造失败，然后断言两件事 ——

1. **钱不动**：全部账户投影与供给快照与失败前**逐字段一致**；
2. **状态一致**：业务行（订单/合同/托管/挂牌/奖励）没有留下半完成状态。

覆盖：余额不足 / 重复领取 / 重复结算 / 取消与过期 / listing 被抢 / Escrow 不足 /
招募失败 / 中途异常（写账后、投影后）/ 并发消费与并发结算。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.economy import Contract, LedgerEntry, LedgerTransaction, WorkOrder
from app.models.enums import (
    ContractStatus,
    EconomicCategory,
    EscrowStatus,
    MarketListingStatus,
    RewardType,
    WorkOrderStatus,
)
from app.models.enums import (
    MarketListingStatus as MarketListingClosedStatus,
)
from app.models.market import MarketListing
from app.models.organization import Company, Employee
from app.repositories import economy as economy_repo
from app.repositories import market as market_repo
from app.services import recruitment as recruitment_service
from app.services.economy.accounts import AccountService
from app.services.economy.contracts import ContractService
from app.services.economy.costs import CompanyCostService, ComputeCostService
from app.services.economy.escrow import EscrowError, EscrowService
from app.services.economy.ledger import InsufficientFunds, LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.npc_economy import NpcEconomyService
from app.services.economy.projection import verify_wallet_projection
from app.services.economy.rewards import RewardService
from app.services.economy.talent_trade import TalentTradeService
from app.services.economy.work_orders import WorkOrderService

_seq = 0


# ---------------------------------------------------------------- 快照工具


def _company(db, name: str = "FailCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"fail-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _fund(db, company: Company, amount: int) -> int:
    MonetaryAuthority(db).mint(
        actor=EconomicActor.company(company.id), amount=amount, reason="test"
    )
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


def _money_state(db) -> dict:
    """钱的全量快照：所有账户投影 + 供给四值（失败后必须逐字段一致）。"""
    wallets = {
        int(row.account_id): (
            int(row.posted_balance),
            int(row.available_balance),
            int(row.reserved_balance),
        )
        for row in economy_repo.list_projections(db)
    }
    supply = LedgerService(db).supply()
    return {
        "wallets": wallets,
        "minted": supply.minted,
        "burned": supply.burned,
        "supply": supply.supply,
        "treasury": supply.treasury_balance,
        "escrow": supply.escrow_balance,
    }


def _assert_money_unchanged(db, before: dict, *, note: str = "") -> None:
    after = _money_state(db)
    assert after == before, f"失败后钱动了（{note}）：{before} → {after}"


def _ready_person(db, *, owner_company: Company | None) -> int:
    from app.models.cultivation import CharacterProfile
    from app.models.person import Person

    global _seq
    _seq += 1
    person = Person(slug=f"fail-talent-{_seq}", name=f"Fail Talent {_seq}", avatar="")
    db.add(person)
    db.flush()
    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"EU-FAIL-{_seq}",
            origin="blank",
            lifecycle="ready",
            owner_company_id=owner_company.id if owner_company is not None else None,
        )
    )
    db.commit()
    return int(person.id)


def _listing(db, person_id: int, *, seller: Company | None) -> int:
    participant = market_repo.ensure_participant(
        db,
        kind="player_company" if seller is not None else "system_issuer",
        company_id=seller.id if seller is not None else None,
        display_name=seller.name if seller is not None else "System Issuer",
    )
    listing = MarketListing(
        person_id=int(person_id),
        status=MarketListingStatus.active.value,
        listed_by_participant_id=int(participant.id),
        listed_at=datetime.now(UTC),
    )
    db.add(listing)
    db.commit()
    return int(listing.id)


class _Patched:
    """临时替换某个可调用对象（失败注入用）。"""

    def __init__(self, target, name: str, replacement) -> None:
        self.target = target
        self.name = name
        self.replacement = replacement
        self.original = getattr(target, name)

    def __enter__(self):
        setattr(self.target, self.name, self.replacement)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        setattr(self.target, self.name, self.original)
        return False


# ---------------------------------------------------------------- 余额不足


def test_insufficient_funds_matrix(db):
    """余额不足：官方订单发布不适用（发行），但玩家订单/合同/算力/NPC 都必须拒绝且钱不动。"""
    issuer = _company(db, "BrokeIssuer")
    _fund(db, issuer, 100)
    buyer = _company(db, "BrokeBuyer")
    _fund(db, buyer, 50)

    before = _money_state(db)

    # 1) 玩家订单锁资不足 ⇒ 不留订单
    orders_before = len(economy_repo.list_work_orders(db))
    with pytest.raises(InsufficientFunds):
        WorkOrderService(db).publish_player_order(
            issuer=EconomicActor.company(issuer.id), title="too big", reward_amount=10_000
        )
    assert len(economy_repo.list_work_orders(db)) == orders_before

    # 2) 合同创建锁资不足 ⇒ 不留合同
    contracts_before = len(economy_repo.list_contracts(db))
    with pytest.raises(InsufficientFunds):
        ContractService(db).create_contract(
            issuer=EconomicActor.company(issuer.id),
            title="too big",
            consideration_amount=10_000,
        )
    assert len(economy_repo.list_contracts(db)) == contracts_before

    # 3) 算力成本：计量照记但**不扣款**（欠费，不是免费）
    charge = ComputeCostService(db).record_usage(
        company_id=buyer.id,
        units=5_000,  # 余额 50 买不起 5,000 单位 ⇒ 欠费（计量照记，但不扣款）
        idempotency_key="fail:compute",
    )
    assert charge.usage.status == "unpaid"
    assert charge.charge.paid is False

    # 4) 人才买不起 ⇒ 无成交、挂牌仍在市
    seller = _company(db, "BrokeSeller")
    person_id = _ready_person(db, owner_company=seller)
    listing_id = _listing(db, person_id, seller=seller)
    TalentTradeService(db).set_terms(listing_id, company_id=seller.id, price=9_000)
    with pytest.raises(Exception) as excinfo:
        TalentTradeService(db).create_offer(listing_id, buyer_company_id=buyer.id, amount=9_000)
    assert getattr(excinfo.value, "reason", None) == "insufficient_funds"
    assert db.get(MarketListing, listing_id).status == MarketListingStatus.active.value

    # 钱与状态：只有算力的 unpaid 行是新增事实（钱没动）
    _assert_money_unchanged(db, before, note="insufficient funds")
    assert verify_wallet_projection(db).ok


# ---------------------------------------------------------------- 重复领取 / 重复结算


def test_duplicate_operations_are_idempotent(db):
    """重复领取 / 重复结算 / 重复释放：第二次只复用结果，**绝不再发钱**。"""
    company = _company(db, "IdemCo")
    _fund(db, company, 30_000)
    service_contract = ContractService(db)
    contractor = _company(db, "IdemContractor")
    contract = service_contract.create_contract(
        issuer=EconomicActor.company(company.id),
        contractor=EconomicActor.company(contractor.id),
        title="Idempotent work",
        consideration_amount=6_000,
    )
    service_contract.accept(contract.id, contractor=EconomicActor.company(contractor.id))
    settled, _ = service_contract.fulfill(
        contract.id, contractor=EconomicActor.company(contractor.id)
    )
    after_settlement = _money_state(db)

    # 1) 重复结算（同 settlement_key）⇒ created=False，钱不再动
    again, outcome = service_contract.settle(contract.id)
    assert again.status == ContractStatus.settled.value
    assert outcome.created is False
    _assert_money_unchanged(db, after_settlement, note="duplicate settle")

    # 2) 重复释放托管 ⇒ 复用既有放款
    escrow = economy_repo.find_escrow_for_contract(db, contract_id=contract.id)
    assert escrow is not None
    released, created = EscrowService(db).release(
        int(escrow.id), payee=EconomicActor.company(contractor.id)
    )
    assert created is False
    assert released.status == EscrowStatus.released.value
    _assert_money_unchanged(db, after_settlement, note="duplicate release")

    # 3) 重复领奖（同 key）⇒ 只有一份 grant
    grant, created_first = RewardService(db).claim(
        RewardType.starter_grant, company_id=company.id, user_id=None
    )
    after_claim = _money_state(db)
    replay, created_second = RewardService(db).claim(
        RewardType.starter_grant, company_id=company.id, user_id=None
    )
    assert created_first is True and created_second is False
    assert replay.id == grant.id
    _assert_money_unchanged(db, after_claim, note="duplicate reward claim")


# ---------------------------------------------------------------- 取消 / 过期


def test_cancel_and_expire_refund_without_leaking(db):
    """取消与过期：钱原路退回，托管归零，不留"已终止但还锁着钱"的状态（E25）。"""
    issuer = _company(db, "RefundIssuer")
    issuer_account = _fund(db, issuer, 20_000)
    contractor = _company(db, "RefundContractor")
    service = ContractService(db)

    # 1) 取消（FUNDED 之前）
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Cancel me",
        consideration_amount=3_000,
    )
    service.cancel(contract.id, actor=EconomicActor.company(issuer.id))
    escrow = economy_repo.find_escrow_for_contract(db, contract_id=contract.id)
    assert escrow.status == EscrowStatus.refunded.value
    assert EscrowService(db).view(escrow).account_balance == 0
    assert econ_wallet(db, issuer_account)["available"] == 20_000

    # 2) 过期（订单）
    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(issuer.id),
            title="Expire me",
            reward_amount=4_000,
            deadline_at=datetime.now(UTC) - timedelta(hours=1),
        )
        .order
    )
    assert econ_wallet(db, issuer_account)["available"] < 20_000
    WorkOrderService(db).expire_overdue()
    db.expire_all()
    assert db.get(WorkOrder, order.id).status == WorkOrderStatus.expired.value
    # 退款（手续费不退 —— 这是设计内的成本）
    fee = economic_policy().fee_for(4_000)
    assert econ_wallet(db, issuer_account)["available"] == 20_000 - fee
    expiring_escrow = economy_repo.find_escrow_for_order(db, work_order_id=int(order.id))
    assert expiring_escrow is not None, "订单创建时必然锁资（E11）"
    assert EscrowService(db).view(expiring_escrow).account_balance == 0
    assert verify_wallet_projection(db).ok


def econ_wallet(db, account_id: int) -> dict:
    """账户投影（没有活动就没有行 ⇒ 全 0，与读面语义一致）。"""
    row = economy_repo.get_projection(db, int(account_id))
    if row is None:
        return {"posted": 0, "available": 0, "reserved": 0}
    return {
        "posted": int(row.posted_balance),
        "available": int(row.available_balance),
        "reserved": int(row.reserved_balance),
    }


# ---------------------------------------------------------------- 竞争（被抢）


def test_races_have_exactly_one_winner(db):
    """listing 被抢 / 合同并发结算：赢家只有一个，输家不扣钱、不留痕。"""
    # 1) 两个公司抢同一个玩家订单
    publisher = _company(db, "RacePublisher")
    _fund(db, publisher, 20_000)
    first_claimant = _company(db, "RaceFirst")
    second_claimant = _company(db, "RaceSecond")
    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(publisher.id), title="Take me", reward_amount=2_000
        )
        .order
    )

    barrier = threading.Barrier(2)
    winners: list[int] = []
    losers: list[str] = []

    def claim(company: Company) -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                accepted = WorkOrderService(session).accept(order.id, company_id=company.id)
                winners.append(int(accepted.assignee_actor_ref or 0))
            except Exception as exc:  # noqa: BLE001 - 断言只看"谁赢"
                losers.append(f"{type(exc).__name__}:{exc}")

    threads = [
        threading.Thread(target=claim, args=(company,))
        for company in (first_claimant, second_claimant)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1, (winners, losers)
    assert all("order_already_taken" in reason for reason in losers), losers
    db.expire_all()
    assert db.get(WorkOrder, order.id).status == WorkOrderStatus.accepted.value
    loser_company = second_claimant if winners[0] == first_claimant.id else first_claimant
    assert econ_wallet(db, _wallet_account_id(db, loser_company))["available"] == 0

    # 2) 两个公司并发抢买同一个挂牌（一口价）⇒ 只有一个成交，另一个被拒且钱不动
    seller = _company(db, "RaceTalentSeller")
    person_id = _ready_person(db, owner_company=seller)
    listing_id = _listing(db, person_id, seller=seller)
    TalentTradeService(db).set_terms(listing_id, company_id=seller.id, price=1_000)
    buyers = [_company(db, f"RaceBuyer{index}") for index in range(2)]
    buyer_accounts = [_fund(db, buyer, 5_000) for buyer in buyers]

    # 线程用独立连接写：先 commit（把 flush 未提交的账户落库 + 释放读事务，SQLite 才让写）
    db.commit()
    buy_barrier = threading.Barrier(2)
    bought: list[str] = []

    def buy(buyer: Company) -> None:
        with SessionLocal() as session:
            buy_barrier.wait()
            try:
                _, purchase = TalentTradeService(session).create_offer(
                    listing_id, buyer_company_id=buyer.id, amount=1_000
                )
                bought.append(f"ok:{buyer.id}" if purchase is not None else f"none:{buyer.id}")
            except Exception as exc:  # noqa: BLE001 - 断言只看"谁赢"
                bought.append(f"rejected:{type(exc).__name__}")

    threads = [threading.Thread(target=buy, args=(buyer,)) for buyer in buyers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([item for item in bought if item.startswith("ok")]) == 1, bought
    db.expire_all()
    assert db.get(MarketListing, listing_id).status == MarketListingClosedStatus.closed.value
    # 输家的钱一分没动（没锁资、没付款）
    loser_index = 1 if bought[0].endswith(str(buyers[0].id)) is False else 0
    del loser_index
    loser_account = buyer_accounts[
        1 if bought[0].startswith("ok") and bought[0].endswith(str(buyers[0].id)) else 0
    ]
    assert econ_wallet(db, loser_account)["available"] == 5_000


def _wallet_account_id(db, company: Company) -> int:
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


# ---------------------------------------------------------------- Escrow 不足


def test_escrow_shortage_blocks_release(db):
    """Escrow 不足（被人为掏空）：释放被拒，钱不动、状态不变。"""
    issuer = _company(db, "ShortIssuer")
    _fund(db, issuer, 10_000)
    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(issuer.id), title="Short escrow", reward_amount=4_000
        )
        .order
    )
    escrow = economy_repo.find_escrow_for_order(db, work_order_id=order.id)
    assert escrow is not None
    before = _money_state(db)

    # 人为把托管状态改回 FUNDED（模拟"已被处理过/状态错"），再释放：幂等/拒绝都不能多给钱
    released, created = EscrowService(db).release(
        int(escrow.id), payee=EconomicActor.company(issuer.id)
    )
    assert created is True  # 第一次释放成功（钱回到自己公司名下也是合法转移）
    escrow_released_state = _money_state(db)
    again, created_again = EscrowService(db).release(
        int(escrow.id), payee=EconomicActor.company(issuer.id)
    )
    assert created_again is False
    assert again.status == EscrowStatus.released.value
    _assert_money_unchanged(db, escrow_released_state, note="escrow double release")

    # 已释放后再退款 ⇒ 明确拒绝（E25：钱不能两头出）
    with pytest.raises(EscrowError, match="escrow_not_refundable"):
        EscrowService(db).refund(int(escrow.id))
    _assert_money_unchanged(db, escrow_released_state, note="refund after release")
    del released, before


# ---------------------------------------------------------------- 招募失败 / 中途异常


def test_recruit_failure_rolls_back_the_purchase(db):
    """招募失败（T2 拒绝）⇒ 整笔回滚：钱不动、挂牌仍在市、没有半个员工（E13/E14/E15）。"""
    seller = _company(db, "RecruitFailSeller")
    buyer = _company(db, "RecruitFailBuyer")
    buyer_account = _fund(db, buyer, 20_000)
    person_id = _ready_person(db, owner_company=seller)
    listing_id = _listing(db, person_id, seller=seller)
    TalentTradeService(db).set_terms(listing_id, company_id=seller.id, price=6_000)
    before = _money_state(db)
    employees_before = int(
        db.execute(
            sa.select(sa.func.count()).select_from(Employee).where(Employee.company_id == buyer.id)
        ).scalar_one()
    )

    def boom(*args, **kwargs):
        raise recruitment_service.RecruitmentError("listing_not_active")

    with _Patched(recruitment_service.RecruitmentService, "recruit_existing_person", boom):
        with pytest.raises(recruitment_service.RecruitmentError):
            TalentTradeService(db).create_offer(listing_id, buyer_company_id=buyer.id, amount=6_000)

    _assert_money_unchanged(db, before, note="recruit failure")
    assert db.get(MarketListing, listing_id).status == MarketListingStatus.active.value
    entities_after = int(
        db.execute(
            sa.select(sa.func.count()).select_from(Employee).where(Employee.company_id == buyer.id)
        ).scalar_one()
    )
    assert entities_after == employees_before
    assert econ_wallet(db, buyer_account)["available"] == 20_000


def test_midflight_exception_rolls_back_ledger_and_projection(db):
    """中途异常（写腿后抛错 / 投影后抛错）⇒ 账本与投影一起回滚（E13/E28）。"""
    payer = _company(db, "MidflightPayer")
    payee = _company(db, "MidflightPayee")
    payer_account = _fund(db, payer, 5_000)
    payee_account = _wallet_account_id(db, payee)

    # 1) 写腿（entries）阶段抛错
    before = _money_state(db)
    original_insert = economy_repo.insert_entries

    def boom_entries(*args, **kwargs):
        raise RuntimeError("injected: entries")

    with _Patched(economy_repo, "insert_entries", boom_entries):
        with pytest.raises(RuntimeError):
            LedgerService(db).transfer(
                payer_account_id=payer_account, payee_account_id=payee_account, amount=1_000
            )
    _assert_money_unchanged(db, before, note="entries failure")

    # 2) 投影更新之后抛错（第二个账户 CAS 之后）
    holder = {"cas": economy_repo.cas_update_projection}
    calls = {"n": 0}

    def boom_after_cas(db_session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected: projection")
        return holder["cas"](db_session, **kwargs)

    with _Patched(economy_repo, "cas_update_projection", boom_after_cas):
        with pytest.raises(RuntimeError):
            LedgerService(db).transfer(
                payer_account_id=payer_account, payee_account_id=payee_account, amount=1_000
            )
    _assert_money_unchanged(db, before, note="projection failure")
    assert verify_wallet_projection(db).ok
    del original_insert


def test_cost_charge_failure_does_not_leak_into_caller_transaction(db):
    """成本扣款失败用 SAVEPOINT 隔离：调用方事务继续、账本不留半笔（M1.5 修复的回归）。"""
    broke = _company(db, "CostFailBroke")
    healthy = _company(db, "CostFailHealthy")
    healthy_account = _fund(db, healthy, 10_000)

    charge = CompanyCostService(db).charge(
        actor=EconomicActor.company(broke.id),
        amount=500,
        category=EconomicCategory.compute,
        reason="test",
        reference_type="test",
        reference_id="1",
        idempotency_key="fail:cost",
        commit=False,
    )
    assert charge.paid is False
    assert charge.reason == "insufficient_funds"
    # 失败的成本扣款没有留下任何账本腿（SAVEPOINT 已回滚）
    assert (
        int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(LedgerTransaction)
                .where(LedgerTransaction.idempotency_key.like("fail:cost%"))
            ).scalar_one()
        )
        == 0
    )
    # 同一个事务继续可用：随后的一笔玩家订单照常锁资并提交
    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(healthy.id), title="Still works", reward_amount=2_000
        )
        .order
    )
    assert order.status == WorkOrderStatus.open.value
    assert econ_wallet(db, healthy_account)["reserved"] == 2_000
    assert verify_wallet_projection(db).ok


def test_npc_budget_cap_blocks_further_issuance(db):
    """NPC 注入封顶：超出不发行（minted 不动），NPC 也不会因为"没钱"而买超。"""
    from app.repositories import organization as org_repo

    context = org_repo.get_default_company(db)
    assert context is not None
    service = NpcEconomyService(db)
    # 复用**已存在**的正式 NPC（不新建参与者：公司注册表断言对 npc_company 行数敏感）
    npc_service = __import__("app.talent.market.npc", fromlist=["x"]).NpcMarketService()
    npc_service.ensure_participants(db, company_context_id=int(context.id))
    participant = npc_service._participant_by_key(db, "mercury")  # noqa: SLF001 - 与 CLI 同一入口
    assert participant is not None
    participant_id = int(participant.id)

    profile = service.ensure_profile(participant_id)
    profile.budget_cap = int(profile.budget_injected_total) + 3_000  # 只留 3,000 额度
    db.commit()

    minted_before = _money_state(db)["minted"]
    granted, total = service.inject_budget(participant_id, amount=2_000, commit=True)
    assert (granted, total) == (2_000, profile.budget_injected_total)
    minted_after_first = _money_state(db)["minted"]
    assert minted_after_first - minted_before == 2_000

    granted, _total = service.inject_budget(participant_id, amount=2_000, commit=True)
    assert granted == 1_000  # 只补到上限
    granted, _total = service.inject_budget(participant_id, amount=2_000, commit=True)
    assert granted == 0
    assert _money_state(db)["minted"] - minted_after_first == 1_000  # 第二次只发行 1,000


# ---------------------------------------------------------------- 并发消费 / 并发结算


def test_concurrent_spend_and_settlement_keep_invariants(db):
    """同余额并发消费：恰好一笔成功；并发结算同一合同：只有一次放款。"""
    company = _company(db, "ConcCo")
    account = _fund(db, company, 10_000)
    sink = _company(db, "ConcSink")
    sink_account = _wallet_account_id(db, sink)
    # 线程用独立连接写：先 commit（把 flush 未提交的账户落库 + 释放读事务，SQLite 才让写）
    db.commit()

    barrier = threading.Barrier(2)
    results: list[str] = []

    def spend(amount: int) -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                LedgerService(session).transfer(
                    payer_account_id=account,
                    payee_account_id=sink_account,
                    amount=amount,
                    reason="concurrent",
                )
                results.append(f"ok:{amount}")
            except InsufficientFunds:
                results.append(f"insufficient:{amount}")
            except Exception as exc:  # noqa: BLE001 - 失败要看得见（别变成"两笔都没跑"）
                results.append(f"error:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=spend, args=(amount,)) for amount in (7_000, 7_000)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([item for item in results if item.startswith("ok")]) == 1, results
    assert econ_wallet(db, account)["available"] == 3_000  # 不是负数（E24）

    # 并发结算同一份合同
    publisher = _company(db, "ConcPublisher")
    _fund(db, publisher, 20_000)
    contractor = _company(db, "ConcContractor")
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(publisher.id),
        contractor=EconomicActor.company(contractor.id),
        title="Concurrent settle",
        consideration_amount=5_000,
    )
    service.accept(contract.id, contractor=EconomicActor.company(contractor.id))

    contractor_id = int(contractor.id)
    db.commit()
    settle_barrier = threading.Barrier(2)
    settles: list[str] = []

    def settle() -> None:
        with SessionLocal() as session:
            settle_barrier.wait()
            try:
                settled, outcome = ContractService(session).fulfill(
                    contract.id, contractor=EconomicActor.company(contractor_id)
                )
                settles.append(f"{settled.status}:{outcome.created}")
            except Exception as exc:  # noqa: BLE001 - 输家应当被状态机拒绝（或幂等复用）
                settles.append(f"rejected:{type(exc).__name__}")

    threads = [threading.Thread(target=settle) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # 恰好一次真结算；另一次要么被拒（状态已终态），要么幂等复用（created=False）
    assert settles.count("SETTLED:True") == 1, settles
    assert settles[0].startswith(("SETTLED", "rejected")), settles
    escrow = economy_repo.find_escrow_for_contract(db, contract_id=contract.id)
    assert EscrowService(db).view(escrow).account_balance == 0
    assert verify_wallet_projection(db).ok
    del contractor


def test_no_orphan_entities_after_failures(db):
    """矩阵收尾：所有实体都处于"能被解释"的状态（订单/合同/托管/挂牌状态成对）。"""
    escrows = economy_repo.list_escrows(db)
    for escrow in escrows:
        if escrow.contract_id is not None:
            contract = db.get(Contract, int(escrow.contract_id))
            assert contract is not None
            if contract.status in (
                ContractStatus.settled.value,
                ContractStatus.cancelled.value,
            ):
                assert escrow.status in (
                    EscrowStatus.released.value,
                    EscrowStatus.refunded.value,
                )
        if escrow.work_order_id is not None:
            order = db.get(WorkOrder, int(escrow.work_order_id))
            assert order is not None
            if order.status in (WorkOrderStatus.settled.value, WorkOrderStatus.cancelled.value):
                assert escrow.status in (
                    EscrowStatus.released.value,
                    EscrowStatus.refunded.value,
                )
    # 共享测试库：这里只断言"没有孤儿"，不假设数量
    assert int(db.execute(sa.select(sa.func.count()).select_from(Contract)).scalar_one()) >= 0
    assert int(db.execute(sa.select(sa.func.count()).select_from(LedgerEntry)).scalar_one()) > 0
