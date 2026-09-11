"""M1.1 并发与失败注入（plan §4/M1.1b/M1.1d，A4/A5/A10/A13）。

断言的是**经济不变量**，不是"模拟分布式数据库"：
- 100 余额被两笔 80 同时花：恰好一笔成功（CAS 条件更新 + rowcount 判定，E24）；
- 同一幂等键并发提交：只落一笔交易（唯一索引裁定赢家，输家复用赢家，E10/E12）；
- 失败注入：CAS 之后 / entries 之后异常 ⇒ 整笔回滚（Ledger 与 Projection 都不留痕，E13/E28）；
- 事件只在**真正提交**之后发布，幂等重放不重复发（幂等语义 §34）。

SQLite 单写 + 15s busy timeout：并发写会排队，允许合理的锁等待；不允许的是"两个都成功"。
"""

from __future__ import annotations

import threading

import pytest
import sqlalchemy as sa

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.models.economy import LedgerEntry, LedgerTransaction
from app.models.enums import LedgerAccountKind, TransactionKind
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import (
    InsufficientFunds,
    LedgerService,
    Posting,
    PostingRejected,
    blueprint_entries,
)
from app.services.economy.monetary import MonetaryAuthority

_seq = 0


def _company(db, name: str = "ConcCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"conc-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _actor(company: Company) -> EconomicActor:
    return EconomicActor.company(company.id)


def _wallet(account_id: int) -> dict:
    with SessionLocal() as session:
        row = economy_repo.get_projection(session, account_id)
        if row is None:  # 从未过账 ⇒ 无投影行，等价于 0（缺行不是 drift）
            return {"posted": 0, "available": 0, "reserved": 0}
        return {
            "posted": int(row.posted_balance),
            "available": int(row.available_balance),
            "reserved": int(row.reserved_balance),
        }


def _count(table) -> int:
    with SessionLocal() as session:
        return int(session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())


def test_concurrent_spend_lets_exactly_one_winner(db):
    """经典 double-spend：100 余额，两个线程各花 80 —— 恰好一笔成功、一笔 insufficient。"""
    payer_company = _company(db, "Spender")
    sink_company = _company(db, "Sink")
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(payer_company)).id
    payee = accounts.ensure_account(_actor(sink_company)).id
    MonetaryAuthority(db).mint(actor=_actor(payer_company), amount=100, reason="starter")
    assert _wallet(payer)["available"] == 100

    barrier = threading.Barrier(2)
    results: list[str] = []

    def spend() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                LedgerService(session).transfer(
                    payer_account_id=payer, payee_account_id=payee, amount=80, reason="race"
                )
                results.append("ok")
            except InsufficientFunds:
                results.append("insufficient")
            except Exception as exc:  # pragma: no cover - 失败时把真实原因带出来
                results.append(f"error:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=spend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["insufficient", "ok"], results
    assert _wallet(payer)["available"] == 20  # 不是 -60
    assert _wallet(payee)["available"] == 80
    db.rollback()


def test_concurrent_same_idempotency_key_posts_once(db):
    """同一幂等键并发提交：唯一索引裁定赢家，输家复用同一笔交易（绝不二次过账）。"""
    company = _company(db, "IdemRace")
    other = _company(db, "IdemRaceOther")
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(company)).id
    payee = accounts.ensure_account(_actor(other)).id
    MonetaryAuthority(db).mint(actor=_actor(company), amount=1_000, reason="starter")
    transactions_before = _count(LedgerTransaction)

    barrier = threading.Barrier(2)
    created: list[bool] = []
    failures: list[str] = []

    def pay() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                result = LedgerService(session).transfer(
                    payer_account_id=payer,
                    payee_account_id=payee,
                    amount=250,
                    reason="idem",
                    idempotency_key="race-key-1",
                )
                created.append(result.created)
            except Exception as exc:  # pragma: no cover
                failures.append(f"{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=pay) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures, failures
    assert created.count(True) == 1, created
    assert _count(LedgerTransaction) == transactions_before + 1
    assert _wallet(payer)["available"] == 750  # 只扣了一次
    with SessionLocal() as session:
        keys = [
            row.idempotency_key
            for row in session.scalars(
                sa.select(LedgerTransaction).where(
                    LedgerTransaction.idempotency_key == "race-key-1"
                )
            )
        ]
    assert keys == ["race-key-1"]
    db.rollback()


def test_failure_before_entries_rolls_back_ledger_and_projection(db, monkeypatch):
    """entries 写入失败 ⇒ 交易信封与投影都不留痕（E13）。"""
    company = _company(db, "FailBeforeEntries")
    accounts = AccountService(db)
    account_id = accounts.ensure_account(_actor(company)).id
    MonetaryAuthority(db).mint(actor=_actor(company), amount=500, reason="starter")
    before = {
        "wallet": _wallet(account_id),
        "transactions": _count(LedgerTransaction),
        "entries": _count(LedgerEntry),
    }

    def boom(*args, **kwargs):
        raise RuntimeError("injected failure: entries")

    monkeypatch.setattr(economy_repo, "insert_entries", boom)
    with SessionLocal() as session:
        with pytest.raises(RuntimeError):
            MonetaryAuthority(session).mint(
                actor=_actor(company), amount=100, reason="injected", commit=True
            )
    assert _wallet(account_id) == before["wallet"]
    assert _count(LedgerTransaction) == before["transactions"]
    assert _count(LedgerEntry) == before["entries"]


def test_failure_during_projection_update_rolls_back_the_whole_posting(db, monkeypatch):
    """第二个账户投影更新失败 ⇒ 第一个账户的 CAS 与账本一起回滚（E28：同事务）。"""
    first = _company(db, "FailProjA")
    second = _company(db, "FailProjB")
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(first)).id
    payee = accounts.ensure_account(_actor(second)).id
    db.commit()  # 开户先落库：下面的失败注入用**独立 session**，看不到未提交的账户
    MonetaryAuthority(db).mint(actor=_actor(first), amount=900, reason="starter")
    before = {
        "payer": _wallet(payer),
        "payee": _wallet(payee),
        "transactions": _count(LedgerTransaction),
    }

    real_cas = economy_repo.cas_update_projection
    calls = {"n": 0}

    def flaky_cas(db_session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # 第一个账户已经扣过钱了，第二个账户开始炸
            raise RuntimeError("injected failure: projection")
        return real_cas(db_session, **kwargs)

    monkeypatch.setattr(economy_repo, "cas_update_projection", flaky_cas)
    with SessionLocal() as session:
        with pytest.raises(RuntimeError):
            LedgerService(session).transfer(
                payer_account_id=payer, payee_account_id=payee, amount=400, reason="injected"
            )
    assert _wallet(payer) == before["payer"]
    assert _wallet(payee) == before["payee"]
    assert _count(LedgerTransaction) == before["transactions"]


def test_insufficient_funds_does_not_leave_half_a_posting(db):
    """CAS 拒绝时（rowcount=0）账本不得留下任何腿。"""
    first = _company(db, "EmptyA")
    second = _company(db, "EmptyB")
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(first)).id
    payee = accounts.ensure_account(_actor(second)).id
    db.commit()  # 同上：独立 session 需要看到已提交的账户
    before = {"transactions": _count(LedgerTransaction), "entries": _count(LedgerEntry)}
    with SessionLocal() as session:
        with pytest.raises(InsufficientFunds):
            LedgerService(session).transfer(
                payer_account_id=payer, payee_account_id=payee, amount=1, reason="empty"
            )
    assert _count(LedgerTransaction) == before["transactions"]
    assert _count(LedgerEntry) == before["entries"]
    assert _wallet(payer)["available"] == 0


def test_event_is_published_once_after_commit_and_not_on_replay(db, monkeypatch):
    """`ledger.transaction_posted` 只在真正提交后发一次；幂等重放不重发（§35）。"""
    company = _company(db, "EventCo")
    published: list[dict] = []
    monkeypatch.setattr(
        "app.services.economy.ledger.bus.publish",
        lambda type_, data, **kwargs: published.append({"type": type_, "data": data, **kwargs}),
    )
    authority = MonetaryAuthority(db)
    authority.mint(
        actor=_actor(company), amount=300, reason="event", idempotency_key="evt-1", commit=True
    )
    assert [event["type"] for event in published] == ["ledger.transaction_posted"]
    assert published[0]["data"]["transaction_type"] == "mint"
    assert published[0]["company_id"] == company.id

    authority.mint(
        actor=_actor(company), amount=300, reason="event", idempotency_key="evt-1", commit=True
    )
    assert len(published) == 1  # 幂等重放不发事件
    db.rollback()


def test_uncommitted_posting_is_not_published(db, monkeypatch):
    """`commit=False`（组合事务）不发事件：由调用方在提交后负责。

    否则会出现「事件说发生了、事务却回滚了」。
    """
    company = _company(db, "NoEventCo")
    published: list[dict] = []
    monkeypatch.setattr(
        "app.services.economy.ledger.bus.publish",
        lambda type_, data, **kwargs: published.append({"type": type_}),
    )
    MonetaryAuthority(db).mint(actor=_actor(company), amount=50, reason="no-event", commit=False)
    assert published == []
    db.rollback()


def test_posting_core_is_the_only_writer_and_rejects_bad_shapes(db):
    """统一入口的形状校验：单腿 / 自指 / 账户不存在 —— 全部在写账前拒绝。"""
    company = _company(db, "ShapeCo")
    accounts = AccountService(db)
    account_id = accounts.ensure_account(_actor(company)).id
    ledger = LedgerService(db)

    single_leg = Posting(
        transaction_type=TransactionKind.transfer,
        entries=blueprint_entries(
            TransactionKind.transfer, {"payer": (account_id, 10), "payee": (account_id + 1, 10)}
        )[:1],
    )
    with pytest.raises(PostingRejected):
        ledger.post(single_leg, commit=False)

    duplicate = Posting(
        transaction_type=TransactionKind.transfer,
        entries=blueprint_entries(
            TransactionKind.transfer, {"payer": (account_id, 10), "payee": (account_id, 10)}
        ),
    )
    with pytest.raises(PostingRejected):
        ledger.post(duplicate, commit=False)

    # 系统账户只能由系统主体持有（契约层 AccountRef 校验）
    from app.economy.contracts import AccountRef, EconomyContractError
    from app.models.enums import Currency

    with pytest.raises(EconomyContractError):
        AccountRef(_actor(company), Currency.credit, LedgerAccountKind.issuance)
    db.rollback()
