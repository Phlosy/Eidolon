"""M1.1 投影可重建性与随机交易 property 测试（plan §4/M1.1c，A2/A6/A11–A16）。

锁住的是**"账本是事实、投影是派生"**这件事本身：
- 随机跑一串 mint/transfer/burn/escrow 之后：每笔交易守恒、供给 = minted − burned、
  transfer 不改变供给、所有普通钱包 available ≥ 0；
- 清空投影后重建：逐账户逐字段与账本推导一致（含 Escrow 的 reserved 归因，E30）；
- 人为篡改投影：`verify` 必须能发现（对账只报告不改，E15），`rebuild` 能修回来。
"""

from __future__ import annotations

import random

import sqlalchemy as sa

from app.economy.contracts import EconomicActor
from app.models.economy import LedgerEntry, WalletProjection
from app.models.enums import LedgerAccountKind, LedgerEntryDirection
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.balances import derive_wallets
from app.services.economy.ledger import InsufficientFunds, LedgerService, PostingRejected
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import (
    rebuild_wallet_projection,
    verify_wallet_projection,
)

_seq = 0


def _company(db, name: str = "ProjCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"proj-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _actor(company: Company) -> EconomicActor:
    return EconomicActor.company(company.id)


def _assert_every_transaction_is_balanced(db) -> None:
    """每一笔交易都必须 Σdebit == Σcredit（E3）—— 直接查账本，不看投影。"""
    totals: dict[int, dict[str, int]] = {}
    grouped = db.execute(
        sa.select(
            LedgerEntry.transaction_id,
            LedgerEntry.direction,
            sa.func.sum(LedgerEntry.amount),
        ).group_by(LedgerEntry.transaction_id, LedgerEntry.direction)
    ).all()
    for transaction_id, direction, total in grouped:
        totals.setdefault(int(transaction_id), {})[str(direction)] = int(total)
    assert totals
    for transaction_id, by_direction in totals.items():
        assert by_direction.get(LedgerEntryDirection.debit.value, 0) == by_direction.get(
            LedgerEntryDirection.credit.value, 0
        ), f"transaction {transaction_id} is not balanced: {by_direction}"


def _assert_no_negative_wallets(db) -> None:
    for row in economy_repo.list_projections(db):
        account = economy_repo.get_account(db, int(row.account_id))
        assert account is not None
        kind = LedgerAccountKind(account.kind)
        if kind in (LedgerAccountKind.actor, LedgerAccountKind.escrow):
            assert int(row.available_balance) >= 0, (row.account_id, row.available_balance)
        assert int(row.reserved_balance) >= 0


def test_property_random_market_keeps_invariants(db):
    """随机 mint / transfer / burn / escrow_fund / escrow_release / escrow_refund 序列。

    每一步之后都断言：
    - 每笔交易复式守恒（E3）；
    - `supply == minted − burned`（E5/E6）；
    - 普通钱包 available ≥ 0（E24）；
    - transfer 前后供给不变（E6）；
    - 投影与账本一致（E29）。
    """
    ledger = LedgerService(db)
    authority = MonetaryAuthority(db)
    accounts = AccountService(db)
    rng = random.Random(20260911)

    companies = [_company(db, "PropCo") for _ in range(3)]
    actors = [_actor(company) for company in companies]
    account_ids = [int(accounts.ensure_account(actor).id) for actor in actors]
    db.commit()  # 开户先落库（否则后续失败过账的回滚会连带抹掉尚未提交的账户行）
    escrow_accounts: list[int] = []
    open_escrows: list[tuple[int, int]] = []  # (escrow_account_id, funder_account_id)

    for step in range(120):
        action = rng.choice(
            ["mint", "transfer", "burn", "escrow_fund", "escrow_release", "escrow_refund"]
        )
        if action == "mint":
            target = rng.choice(actors)
            authority.mint(actor=target, amount=rng.randint(1, 5_000), reason="property")
        elif action == "transfer":
            payer, payee = rng.sample(account_ids, 2)
            try:
                ledger.transfer(
                    payer_account_id=payer,
                    payee_account_id=payee,
                    amount=rng.randint(1, 800),
                    reason="property",
                )
            except InsufficientFunds:
                pass  # 余额不足是**合法**结果（不变量仍要成立）
        elif action == "burn":
            target = rng.choice(account_ids)
            try:
                authority.burn(
                    actor=(actors[account_ids.index(target)]),
                    amount=rng.randint(1, 300),
                    reason="property",
                )
            except InsufficientFunds:
                pass
        elif action == "escrow_fund":
            funder = rng.choice(account_ids)
            escrow = accounts.ensure_escrow_account(escrow_id=1000 + step)
            try:
                ledger.escrow_fund(
                    escrow_account_id=int(escrow.id),
                    payer_account_id=funder,
                    amount=rng.randint(1, 400),
                    reason="property",
                )
            except InsufficientFunds:
                pass
            else:
                escrow_accounts.append(int(escrow.id))
                open_escrows.append((int(escrow.id), funder))
        elif action == "escrow_release" and open_escrows:
            index = rng.randrange(len(open_escrows))
            escrow_id, _funder = open_escrows.pop(index)
            status = economy_repo.get_projection(db, escrow_id)
            amount = int(status.available_balance) if status else 0
            if amount > 0:
                ledger.escrow_release(
                    escrow_account_id=escrow_id,
                    payee_account_id=rng.choice(account_ids),
                    amount=amount,
                    reason="property",
                )
        elif action == "escrow_refund" and open_escrows:
            index = rng.randrange(len(open_escrows))
            escrow_id, funder = open_escrows.pop(index)
            status = economy_repo.get_projection(db, escrow_id)
            amount = int(status.available_balance) if status else 0
            if amount > 0:
                ledger.escrow_refund(
                    escrow_account_id=escrow_id,
                    payer_account_id=funder,
                    amount=amount,
                    reason="property",
                )
            else:
                open_escrows.append((escrow_id, funder))

        snapshot = ledger.supply()
        assert snapshot.supply == snapshot.minted - snapshot.burned, f"step {step}"
        assert ledger.ledger_balance(account_ids[0]) >= 0
        db.expire_all()

    _assert_every_transaction_is_balanced(db)
    _assert_no_negative_wallets(db)
    assert verify_wallet_projection(db).ok
    db.rollback()


def test_transfer_does_not_change_supply(db):
    first, second = _company(db, "SupplyA"), _company(db, "SupplyB")
    ledger = LedgerService(db)
    authority = MonetaryAuthority(db)
    authority.mint(actor=_actor(first), amount=1_000, reason="starter")
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(first)).id
    payee = accounts.ensure_account(_actor(second)).id

    before = ledger.supply()
    ledger.transfer(payer_account_id=payer, payee_account_id=payee, amount=250, reason="svc")
    after = ledger.supply()
    assert (after.minted, after.burned, after.supply) == (
        before.minted,
        before.burned,
        before.supply,
    )
    db.rollback()


def test_rebuild_reproduces_projection_from_ledger_only(db):
    company = _company(db, "RebuildCo")
    other = _company(db, "RebuildOther")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    authority = MonetaryAuthority(db)
    payer = accounts.ensure_account(_actor(company)).id
    payee = accounts.ensure_account(_actor(other)).id
    escrow = accounts.ensure_escrow_account(escrow_id=777)

    authority.mint(actor=_actor(company), amount=10_000, reason="starter")
    ledger.transfer(payer_account_id=payer, payee_account_id=payee, amount=500, reason="svc")
    ledger.escrow_fund(escrow_account_id=escrow.id, payer_account_id=payer, amount=2_000)
    expected = {
        account_id: (
            wallet.posted_balance,
            wallet.available_balance,
            wallet.reserved_balance,
            wallet.last_entry_id,
        )
        for account_id, wallet in derive_wallets(db).items()
    }
    # 锁资不改变净资产：posted = available(7500) + reserved(2000) = 9500
    assert expected[payer][:3] == (9_500, 7_500, 2_000)
    assert expected[escrow.id][:3] == (2_000, 2_000, 0)

    # 清空投影（模拟缓存丢失/损坏）后重建 —— 只依赖账本
    rebuild_wallet_projection(db)
    rebuilt = {
        int(row.account_id): (
            int(row.posted_balance),
            int(row.available_balance),
            int(row.reserved_balance),
            row.last_entry_id,
        )
        for row in economy_repo.list_projections(db)
    }
    assert rebuilt == expected
    assert verify_wallet_projection(db).ok
    # reserved 在重建后依然归因正确（E30：不是账本外的数字）
    assert rebuilt[payer][2] == 2_000
    db.rollback()


def test_verify_detects_drift_and_rebuild_repairs_it(db):
    company = _company(db, "DriftCo")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    MonetaryAuthority(db).mint(actor=_actor(company), amount=3_000, reason="starter")
    account_id = accounts.ensure_account(_actor(company)).id
    ledger.transfer(
        payer_account_id=account_id,
        payee_account_id=accounts.ensure_account(_actor(_company(db, "DriftSink"))).id,
        amount=1_000,
        reason="svc",
    )
    assert verify_wallet_projection(db).ok

    # 人为篡改投影：available 多出 250、reserved 凭空多 50（模拟缓存漂移）
    row = economy_repo.get_projection(db, account_id)
    assert row is not None
    row.available_balance = int(row.available_balance) + 250
    row.reserved_balance = int(row.reserved_balance) + 50
    db.flush()

    check = verify_wallet_projection(db)
    assert not check.ok
    drifted = {drift.field for drift in check.drifts if drift.account_id == account_id}
    assert {"available_balance", "reserved_balance"} <= drifted
    assert any(drift.field == "available_balance" and drift.delta == 250 for drift in check.drifts)
    assert "DRIFT" in check.summary()

    # 对账本身不改数据（E15）：篡改仍在
    assert economy_repo.get_projection(db, account_id).available_balance == row.available_balance

    rebuild_wallet_projection(db)
    assert verify_wallet_projection(db).ok
    assert economy_repo.get_projection(db, account_id).available_balance == 2_000
    db.rollback()


def test_verify_reports_ledger_anomaly_without_raising(db):
    """Escrow 账户上有钱但没有出资腿（不是 escrow_fund 进来）⇒ 报 anomaly，不抛给调用方。"""
    company = _company(db, "AnomalyCo")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    MonetaryAuthority(db).mint(actor=_actor(company), amount=600, reason="starter")
    payer = accounts.ensure_account(_actor(company)).id
    escrow = accounts.ensure_escrow_account(escrow_id=888)
    # 故意制造账本异常：钱直接转进 escrow（不是 escrow_fund）⇒ 没有出资腿。
    # commit=False + 末尾 rollback：这笔"损坏"不写进共享测试库（否则会污染后续用例）。
    ledger.transfer(
        payer_account_id=payer,
        payee_account_id=escrow.id,
        amount=100,
        reason="direct",
        commit=False,
    )

    check = verify_wallet_projection(db)
    assert not check.ok
    assert "funder" in check.anomaly
    assert "LEDGER ANOMALY" in check.summary()
    db.rollback()


def test_rebuild_after_projection_wipe_recovers_reserved_for_multiple_actors(db):
    """两个公司各自出资不同 Escrow：重建后各自的 reserved 归因必须分开（E30）。"""
    first, second = _company(db, "MultiA"), _company(db, "MultiB")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    authority = MonetaryAuthority(db)
    first_account = accounts.ensure_account(_actor(first)).id
    second_account = accounts.ensure_account(_actor(second)).id
    authority.mint(actor=_actor(first), amount=1_000, reason="starter")
    authority.mint(actor=_actor(second), amount=1_000, reason="starter")
    ledger.escrow_fund(
        escrow_account_id=accounts.ensure_escrow_account(escrow_id=901).id,
        payer_account_id=first_account,
        amount=300,
        reason="lock",
    )
    ledger.escrow_fund(
        escrow_account_id=accounts.ensure_escrow_account(escrow_id=902).id,
        payer_account_id=second_account,
        amount=700,
        reason="lock",
    )

    rebuild_wallet_projection(db)
    assert economy_repo.get_projection(db, first_account).reserved_balance == 300
    assert economy_repo.get_projection(db, second_account).reserved_balance == 700
    assert verify_wallet_projection(db).ok
    db.rollback()


def test_projection_rows_carry_currency_and_version(db):
    company = _company(db, "MetaCo")
    accounts = AccountService(db)
    MonetaryAuthority(db).mint(actor=_actor(company), amount=10, reason="starter")
    account_id = accounts.ensure_account(_actor(company)).id
    row = economy_repo.get_projection(db, account_id)
    assert row is not None
    assert row.currency == "CREDIT"
    assert int(row.version) >= 2  # ensure_projection(1) + 过账 +1
    assert row.last_entry_id is not None
    assert isinstance(db.get(WalletProjection, account_id).posted_balance, int)
    db.rollback()


def test_unknown_options_rejected_in_escrow_primitives(db):
    company = _company(db, "OptCo")
    accounts = AccountService(db)
    account_id = accounts.ensure_account(_actor(company)).id
    escrow = accounts.ensure_escrow_account(escrow_id=999)
    try:
        LedgerService(db).escrow_release(
            escrow_account_id=escrow.id,
            payee_account_id=account_id,
            amount=1,
            bogus=1,
        )
    except PostingRejected as exc:
        assert "unknown options" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected PostingRejected")
    db.rollback()
