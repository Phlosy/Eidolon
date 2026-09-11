"""M1.5 公司经营成本（docs/m1-economy-design.md §7/§25/§26；plan §4/M1.5）。

断言的是**Sink 不变量**：
- 算力/培养/手续费**永不 mint**（Sink 只回收）；手续费按 bps 抽、
  按 treasury/burn 拆分且**整数守恒**；
- 算力计量**总是发生**（跑过就是事实），扣款尽力而为：余额不足 ⇒ `unpaid`（未清成本，不是免费），
  且**绝不产生负余额**（E24）；
- 幂等：同 `idempotency_key` 重放不重复扣款（E12 同族）；
- `category` 一等列落库（报表基础）；`category` 不一致的幂等键复用会被拒绝；
- 培养成本由 `cultivation.completed` 消费者驱动（T2 冻结面不改一行）。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.economy import LedgerTransaction
from app.models.enums import EconomicCategory, LedgerAccountKind
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.costs import (
    CompanyCostService,
    ComputeCostService,
    CostError,
    FeeService,
    compute_units_for_duration,
)
from app.services.economy.ledger import IdempotencyConflict, LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import verify_wallet_projection
from app.services.economy.work_orders import WorkOrderService

_seq = 0


def _company(db, name: str = "CostCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"cost-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _fund(db, company: Company, amount: int) -> int:
    MonetaryAuthority(db).mint(
        actor=EconomicActor.company(company.id),
        amount=amount,
        reason="test",
        category=EconomicCategory.starter,
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


def _fee(amount: int) -> int:
    return economic_policy().fee_for(amount)


def _system_balance(db, kind: LedgerAccountKind) -> int:
    accounts = economy_repo.list_accounts_by_kind(db, kinds=(kind.value,))
    total = 0
    for account in accounts:
        total += LedgerService(db).ledger_balance(int(account.id))
    return total


def _supply(db):
    return LedgerService(db).supply()


# ---------------------------------------------------------------- 算力成本


def test_compute_units_are_minutes_with_a_floor():
    assert compute_units_for_duration(None) == 1
    assert compute_units_for_duration(0) == 1
    assert compute_units_for_duration(1) == 1
    assert compute_units_for_duration(60) == 1
    assert compute_units_for_duration(61) == 2
    assert compute_units_for_duration(150.5) == 3


def test_compute_usage_charges_treasury_and_is_idempotent(db):
    company = _company(db, "ComputeCo")
    account_id = _fund(db, company, 10_000)
    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    minted_before = _supply(db).minted
    service = ComputeCostService(db)

    charge = service.record_usage(
        company_id=company.id,
        units=30,
        model="mock-fast",
        idempotency_key="session:unit-test-1",
        duration_seconds=1_800.0,
    )
    assert charge.created is True
    assert charge.amount == 30 * economic_policy().compute_credit_per_unit
    assert charge.charge.paid is True
    assert charge.usage.status == "paid"
    assert charge.usage.treasury_transaction_id is not None
    assert _wallet(account_id)["available"] == 10_000 - charge.amount
    assert _system_balance(db, LedgerAccountKind.treasury) - treasury_before == charge.amount
    assert _supply(db).minted == minted_before  # 成本不是发行
    # category 落库（报表基础）
    transaction = db.get(LedgerTransaction, int(charge.usage.treasury_transaction_id))
    assert transaction.category == EconomicCategory.compute.value
    assert transaction.transaction_type == "treasury_transfer"
    assert transaction.reference_type == "compute_usage"

    # 幂等重放：不再扣一次
    replay = service.record_usage(
        company_id=company.id,
        units=30,
        idempotency_key="session:unit-test-1",
        duration_seconds=1_800.0,
    )
    assert replay.created is False
    assert replay.usage.id == charge.usage.id
    assert _wallet(account_id)["available"] == 10_000 - charge.amount
    assert verify_wallet_projection(db).ok


def test_compute_usage_unpaid_when_company_is_broke(db):
    company = _company(db, "BrokeComputeCo")
    account_id = int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)
    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    minted_before = _supply(db).minted

    charge = ComputeCostService(db).record_usage(
        company_id=company.id, units=5, idempotency_key="session:broke-1"
    )
    # 计量是**事实**：记录照写；扣款失败 ⇒ unpaid（不是免费，也绝不是负余额）
    assert charge.created is True
    assert charge.charge.paid is False
    assert charge.charge.reason == "insufficient_funds"
    assert charge.usage.status == "unpaid"
    assert charge.usage.unpaid_reason == "insufficient_funds"
    assert charge.usage.treasury_transaction_id is None
    assert _wallet(account_id) == {"posted": 0, "available": 0, "reserved": 0}
    assert _system_balance(db, LedgerAccountKind.treasury) == treasury_before
    assert _supply(db).minted == minted_before
    assert verify_wallet_projection(db).ok
    totals = ComputeCostService(db).totals(company_id=company.id)
    assert totals["paid"] == 0
    assert totals["unpaid"] == 5 * economic_policy().compute_credit_per_unit


def test_compute_usage_rejects_non_positive_units(db):
    company = _company(db, "ZeroComputeCo")
    with pytest.raises(CostError, match="compute_units_must_be_positive"):
        ComputeCostService(db).record_usage(
            company_id=company.id, units=0, idempotency_key="session:zero"
        )


# ---------------------------------------------------------------- 手续费


def test_fee_quote_is_conserving_and_bps_driven(db):
    service = FeeService(db)
    policy = economic_policy()
    for amount in (0, 1, 99, 100, 2_000, 12_345):
        quote = service.quote(amount)
        assert quote.fee == amount * policy.market_fee_bps // 10_000
        assert quote.treasury + quote.burn == quote.fee  # 整数守恒
        assert quote.net + quote.fee == amount
    # 纯函数拆分与政策一致
    treasury, burn = service.split(1_000)
    assert treasury + burn == 1_000
    assert (treasury, burn) == (
        int(1_000 * policy.fee_treasury_ratio),
        1_000 - int(1_000 * policy.fee_treasury_ratio),
    )


def test_listing_fee_moves_money_to_treasury_and_burn(db):
    company = _company(db, "ListingFeeCo")
    account_id = _fund(db, company, 20_000)
    before = _supply(db)
    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    burn_before = _system_balance(db, LedgerAccountKind.burn)

    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(company.id),
            title="With fee",
            reward_amount=8_000,
        )
        .order
    )
    quote = FeeService(db).quote(8_000)
    assert order.metadata_json["listing_fee"] == quote.fee
    assert order.metadata_json["listing_fee_paid"] is True

    # 发布方：可花 = 奖励 + 手续费（奖励进托管，手续费是真实支出）
    assert _wallet(account_id) == {
        "posted": 20_000 - quote.fee,
        "available": 20_000 - 8_000 - quote.fee,
        "reserved": 8_000,
    }
    assert _system_balance(db, LedgerAccountKind.treasury) - treasury_before == quote.treasury
    assert _system_balance(db, LedgerAccountKind.burn) - burn_before == quote.burn
    supply = _supply(db)
    assert supply.minted == before.minted  # 手续费不是发行
    assert supply.burned - before.burned == quote.burn  # burn 腿按设计减少流通
    assert supply.supply == supply.minted - supply.burned
    assert verify_wallet_projection(db).ok


def test_listing_fee_unpaid_does_not_block_publication(db):
    """只够锁资、不够手续费 ⇒ 订单照发（钱已托管），手续费记欠费 —— 不产生负余额。"""
    company = _company(db, "FeeUnpaidCo")
    account_id = _fund(db, company, 5_000)
    order = (
        WorkOrderService(db)
        .publish_player_order(
            issuer=EconomicActor.company(company.id),
            title="Fee unpaid",
            reward_amount=5_000,  # 恰好等于全部余额：托管扣完后没钱付手续费
        )
        .order
    )
    assert order.metadata_json["listing_fee_paid"] is False
    assert _wallet(account_id) == {"posted": 5_000, "available": 0, "reserved": 5_000}
    assert verify_wallet_projection(db).ok


def test_company_cost_service_can_split_burn_and_is_idempotent(db):
    company = _company(db, "CostSplitCo")
    account_id = _fund(db, company, 1_000)
    service = CompanyCostService(db)
    charge = service.charge(
        actor=EconomicActor.company(company.id),
        amount=200,
        burn_amount=50,
        category=EconomicCategory.market_fee,
        reason="test",
        reference_type="test",
        reference_id="1",
        idempotency_key="cost:split:1",
        commit=True,
    )
    assert charge.paid is True
    assert charge.treasury_amount == 150
    assert charge.burn_amount == 50
    assert _wallet(account_id)["available"] == 800

    replay = service.charge(
        actor=EconomicActor.company(company.id),
        amount=200,
        burn_amount=50,
        category=EconomicCategory.market_fee,
        reason="test",
        reference_type="test",
        reference_id="1",
        idempotency_key="cost:split:1",
        commit=True,
    )
    assert replay.created is False
    assert _wallet(account_id)["available"] == 800  # 没有第二次扣款

    with pytest.raises(CostError, match="burn_amount_out_of_range"):
        service.charge(
            actor=EconomicActor.company(company.id),
            amount=100,
            burn_amount=101,
            category=EconomicCategory.market_fee,
            reason="test",
            reference_type="test",
            reference_id="2",
            idempotency_key="cost:split:2",
        )


def test_idempotency_key_cannot_be_reused_with_a_different_category(db):
    """幂等键复用但类别不同 ⇒ 拒绝（否则报表会静默串味）。"""
    company = _company(db, "CatConflictCo")
    _fund(db, company, 1_000)
    service = CompanyCostService(db)
    service.charge(
        actor=EconomicActor.company(company.id),
        amount=100,
        category=EconomicCategory.compute,
        reason="test",
        reference_type="test",
        reference_id="1",
        idempotency_key="cost:cat:1",
        commit=True,
    )
    with pytest.raises(IdempotencyConflict):
        service.charge(
            actor=EconomicActor.company(company.id),
            amount=100,
            category=EconomicCategory.training,  # 同 key 不同类别
            reason="test",
            reference_type="test",
            reference_id="1",
            idempotency_key="cost:cat:1",
            commit=True,
        )


# ---------------------------------------------------------------- 培养成本（事件消费者）


def _cultivation_message(*, company_id: int, profile_id: int, person_id: int) -> dict:
    return {
        "type": "cultivation.completed",
        "company_id": company_id,
        "data": {"profile_id": profile_id, "person_id": person_id, "template": "academic"},
    }


def test_training_cost_consumer_charges_the_owner_company(db):
    from app.models.cultivation import CharacterProfile, TrainingProgram
    from app.services.economy.consumers import handle_event

    company = _company(db, "TrainingCo")
    account_id = _fund(db, company, 10_000)
    profile = CharacterProfile(
        person_id=900_001,
        identity_id="EU-TRAIN-1",
        origin="blank",
        owner_company_id=company.id,
    )
    db.add(profile)
    db.flush()
    db.add(
        TrainingProgram(
            person_id=900_001,
            template="academic",
            rng_seed="seed",
            status="completed",
            resource_used={"sessions": 4, "knowledge": 12},
        )
    )
    db.commit()

    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    minted_before = _supply(db).minted

    handle_event(
        _cultivation_message(company_id=company.id, profile_id=profile.id, person_id=900_001)
    )
    expected = 4 * economic_policy().training_credit_per_session
    assert _wallet(account_id)["available"] == 10_000 - expected
    assert _system_balance(db, LedgerAccountKind.treasury) - treasury_before == expected
    assert _supply(db).minted == minted_before  # 培养成本不是发行

    # 事件重放（幂等键 training:profile:<id>）⇒ 不再扣款
    handle_event(
        _cultivation_message(company_id=company.id, profile_id=profile.id, person_id=900_001)
    )
    assert _wallet(account_id)["available"] == 10_000 - expected
    transaction = db.scalars(
        sa.select(LedgerTransaction).where(LedgerTransaction.reference_type == "training_program")
    ).first()
    assert transaction is not None
    assert transaction.category == EconomicCategory.training.value


def test_training_cost_consumer_records_unpaid_without_blocking(db):
    from app.models.cultivation import CharacterProfile, TrainingProgram
    from app.services.economy.consumers import handle_event

    company = _company(db, "BrokeTrainingCo")
    account_id = int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)
    profile = CharacterProfile(
        person_id=900_002, identity_id="EU-TRAIN-2", origin="blank", owner_company_id=company.id
    )
    db.add(profile)
    db.flush()
    db.add(
        TrainingProgram(
            person_id=900_002,
            template="academic",
            rng_seed="seed",
            status="completed",
            resource_used={"sessions": 2},
        )
    )
    db.commit()

    # 不抛异常、不产生负余额（欠费只是"没付成"，事实仍被记录）
    handle_event(
        _cultivation_message(company_id=company.id, profile_id=profile.id, person_id=900_002)
    )
    assert _wallet(account_id) == {"posted": 0, "available": 0, "reserved": 0}


def test_training_cost_consumer_ignores_events_without_a_payer(db):
    from app.services.economy.consumers import handle_event

    # 市场上的 free 角色（无 owner 公司）⇒ 没有付款方，什么都不做（不报错）
    handle_event(
        {
            "type": "cultivation.completed",
            "company_id": None,
            "data": {"profile_id": 1, "person_id": 2},
        }
    )


def test_cost_consumers_are_gated_by_settings():
    """默认关：成本消费者不在启动时注册（生产/测试按需开）。"""
    from app.events.engine import EventEngine
    from app.services.economy import consumers

    engine = EventEngine()
    assert settings.economy_cost_consumers_enabled is False
    consumers.register(engine)
    assert "economy_costs" in engine._names
    assert "cultivation.completed" in consumers.CONSUMED_EVENTS


# ---------------------------------------------------------------- 读面（API）


def test_overview_reports_income_expense_and_categories(client, db):
    from app.api.scope import resolve_company_id
    from app.main import app as fastapi_app
    from app.services.economy.work_orders import WorkOrderService as _WorkOrderService

    company = _company(db, "OverviewCo")
    _fund(db, company, 20_000)
    # 产生一笔有类别的支出：玩家订单挂牌手续费（MARKET_FEE）+ 锁资
    _WorkOrderService(db).publish_player_order(
        issuer=EconomicActor.company(company.id),
        title="Overview order",
        reward_amount=4_000,
    )
    # 一笔算力成本（COMPUTE）
    ComputeCostService(db).record_usage(
        company_id=company.id, units=7, idempotency_key="session:overview-1"
    )

    original = fastapi_app.dependency_overrides.get(resolve_company_id)
    fastapi_app.dependency_overrides[resolve_company_id] = lambda: company.id
    try:
        overview = client.get("/api/v1/economy/overview").json()
        usage = client.get("/api/v1/economy/compute-usage").json()
    finally:
        fastapi_app.dependency_overrides.pop(resolve_company_id, None)
        if original is not None:  # pragma: no cover - 测试里没有既有 override
            fastapi_app.dependency_overrides[resolve_company_id] = original

    assert overview["actor_kind"] == "company"
    assert overview["actor_ref"] == company.id
    assert overview["available_balance"] == 20_000 - 4_000 - _fee(4_000) - 7
    assert overview["reserved_balance"] == 4_000
    # 支出 = 锁资（credit 侧）+ 手续费 + 算力；收入 = 启动资金（STARTER 类别）
    assert overview["expense_total"] == 4_000 + _fee(4_000) + 7
    assert overview["income_total"] == 20_000
    categories = {row["category"]: row for row in overview["by_category"]}
    assert categories["MARKET_FEE"]["expense"] == _fee(4_000)
    assert categories["COMPUTE"]["expense"] == 7
    assert categories["unclassified"]["expense"] == 4_000  # 托管锁资（未分类的玩家转移）
    assert categories["STARTER"]["income"] == 20_000
    assert overview["net_total"] == overview["income_total"] - overview["expense_total"]
    assert overview["compute_paid"] == 7
    assert overview["compute_unpaid"] == 0
    assert usage["total"] >= 1
    assert usage["paid_total"] == 7
    assert usage["items"][0]["status"] == "paid"
    assert usage["items"][0]["units"] == 7


def test_compute_usage_api_is_company_scoped_and_shows_unpaid(client, db):
    from app.api.scope import resolve_company_id
    from app.main import app as fastapi_app

    broke = _company(db, "BrokeApiCo")
    other = _company(db, "OtherApiCo")
    _fund(db, other, 5_000)
    ComputeCostService(db).record_usage(
        company_id=broke.id, units=3, idempotency_key="session:api-unpaid"
    )

    def fetch(company_id: int) -> dict:
        fastapi_app.dependency_overrides[resolve_company_id] = lambda: company_id
        try:
            return client.get("/api/v1/economy/compute-usage").json()
        finally:
            fastapi_app.dependency_overrides.pop(resolve_company_id, None)

    broke_page = fetch(broke.id)
    other_page = fetch(other.id)
    assert broke_page["total"] == 1
    assert broke_page["unpaid_total"] == 3
    assert broke_page["items"][0]["status"] == "unpaid"
    assert broke_page["items"][0]["unpaid_reason"] == "insufficient_funds"
    assert other_page["total"] == 0  # 公司作用域：看不到别人家的用量


def test_overview_is_empty_for_a_fresh_company(client, db):
    from app.api.scope import resolve_company_id
    from app.main import app as fastapi_app

    fresh = _company(db, "FreshOverviewCo")
    fastapi_app.dependency_overrides[resolve_company_id] = lambda: fresh.id
    try:
        overview = client.get("/api/v1/economy/overview").json()
        usage = client.get("/api/v1/economy/compute-usage").json()
    finally:
        fastapi_app.dependency_overrides.pop(resolve_company_id, None)
    assert overview["income_total"] == 0
    assert overview["expense_total"] == 0
    assert overview["net_total"] == 0
    assert overview["by_category"] == []
    assert overview["compute_paid"] == 0 and overview["compute_unpaid"] == 0
    assert usage == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "paid_total": 0,
        "unpaid_total": 0,
    }
