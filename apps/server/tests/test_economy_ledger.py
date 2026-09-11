"""M1.1 账本契约与 Posting Core（docs/m1-economy-design.md §10–§12b；plan §4/M1.1b，A1–A14）。

锁住的是**账本不变量**，不是 CRUD：
- 开户/系统账户 bootstrap 幂等；冻结账户拒绝过账；
- 复式守恒（Σdebit == Σcredit）、正整数金额、单币种、无自指腿；
- mint 增加供给 / burn 减少供给 / transfer 不改变供给（E5/E6）；
- 只有 MonetaryAuthority 能 mint/burn/treasury（E4/E23），令牌不可绕过；
- 幂等：同 key 复用既有交易，绝不再扣一次钱（E10/E12 同族）；
- 资金 CAS：余额不足 → 拒绝且**不留半笔账**（E24/E13）；
- Escrow：fund/release/refund 的锁定份额归因（E7/E25/E30）；
- append-only：没有任何修改历史账本代码路径（E17/E31）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.economy.contracts import EconomicActor
from app.models.economy import LedgerEntry, LedgerTransaction
from app.models.enums import (
    Currency,
    LedgerAccountKind,
    TransactionKind,
)
from app.models.enums import (
    LedgerEntryDirection as D,
)
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountError, AccountService
from app.services.economy.balances import derive_wallets
from app.services.economy.ledger import (
    AuthorityRequired,
    IdempotencyConflict,
    InsufficientFunds,
    LedgerService,
    Posting,
    PostingEntry,
    PostingRejected,
    blueprint_entries,
)
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import (
    rebuild_wallet_projection,
    verify_wallet_projection,
)

SERVER_ROOT = Path(__file__).resolve().parents[1]
_seq = 0


def _company(db, name: str = "EconCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"econ-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _actor(company: Company) -> EconomicActor:
    return EconomicActor.company(company.id)


def _wallet(account_id: int) -> tuple[int, int, int]:
    """(posted, available, reserved) —— 直接读投影缓存（断言值本身）。"""
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = economy_repo.get_projection(db, account_id)
        assert row is not None, f"projection row missing for account {account_id}"
        return (int(row.posted_balance), int(row.available_balance), int(row.reserved_balance))


def _transaction_count(db) -> int:
    return int(db.execute(sa.select(sa.func.count()).select_from(LedgerTransaction)).scalar_one())


# ---------------------------------------------------------------- 账户


def test_system_accounts_bootstrap_is_idempotent(db):
    service = AccountService(db)
    first = service.ensure_system_accounts()
    second = service.ensure_system_accounts()
    assert {kind for kind in first} == {
        LedgerAccountKind.issuance,
        LedgerAccountKind.treasury,
        LedgerAccountKind.burn,
    }
    assert {kind: account.id for kind, account in first.items()} == {
        kind: account.id for kind, account in second.items()
    }
    assert all(account.actor_kind == "system" for account in first.values())
    db.rollback()


def test_ensure_account_is_idempotent_and_unique_per_identity(db):
    company = _company(db)
    service = AccountService(db)
    first = service.ensure_account(_actor(company))
    second = service.ensure_account(_actor(company))
    assert first.id == second.id
    assert first.normal_side == "debit"
    # 不同 kind / 不同 subject_ref 是不同账户
    escrow = service.ensure_escrow_account(escrow_id=7)
    assert escrow.kind == LedgerAccountKind.escrow.value
    assert escrow.subject_ref == 7
    assert escrow.actor_kind == "system"
    assert service.ensure_escrow_account(escrow_id=7).id == escrow.id
    assert service.ensure_escrow_account(escrow_id=8).id != escrow.id


def test_frozen_account_rejects_posting_then_unfreezes(db):
    company = _company(db)
    other = _company(db)
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(company))
    payee = accounts.ensure_account(_actor(other))
    ledger = LedgerService(db)
    MonetaryAuthority(db).mint(actor=_actor(company), amount=500, reason="test", commit=False)

    accounts.freeze(payer.id, reason="audit")
    with pytest.raises(PostingRejected, match="account_not_active"):
        ledger.transfer(
            payer_account_id=payer.id,
            payee_account_id=payee.id,
            amount=100,
            commit=False,
        )
    accounts.unfreeze(payer.id)
    ledger.transfer(
        payer_account_id=payer.id,
        payee_account_id=payee.id,
        amount=100,
        commit=False,
    )
    assert _wallet_pair(db, payer.id)["available"] == 400
    assert _wallet_pair(db, payee.id)["available"] == 100
    db.rollback()


def _wallet_pair(db, account_id: int) -> dict:
    rows = economy_repo.list_projections(db, account_ids=[account_id])
    assert rows
    row = rows[0]
    return {
        "posted": int(row.posted_balance),
        "available": int(row.available_balance),
        "reserved": int(row.reserved_balance),
    }


def test_close_is_terminal(db):
    company = _company(db)
    service = AccountService(db)
    account = service.ensure_account(_actor(company))
    service.close(account.id)
    with pytest.raises(AccountError, match="account_closed"):
        service.freeze(account.id)
    with pytest.raises(PostingRejected, match="account_not_active"):
        LedgerService(db).transfer(
            payer_account_id=account.id, payee_account_id=account.id, amount=1, commit=False
        )
    db.rollback()


# ---------------------------------------------------------------- 校验


def test_blueprint_entries_derive_directions(db):
    company = _company(db)
    account = AccountService(db).ensure_account(_actor(company))
    entries = blueprint_entries(
        TransactionKind.transfer, {"payer": (account.id, 10), "payee": (account.id + 1, 10)}
    )
    assert [entry.direction for entry in entries] == [D.debit, D.credit]
    with pytest.raises(PostingRejected, match="requires legs"):
        blueprint_entries(TransactionKind.transfer, {"payer": (account.id, 10)})
    db.rollback()


def test_unbalanced_and_invalid_amounts_are_rejected(db):
    company = _company(db)
    other = _company(db)
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(company))
    payee = accounts.ensure_account(_actor(other))
    ledger = LedgerService(db)

    unbalanced = Posting(
        transaction_type=TransactionKind.transfer,
        entries=(
            PostingEntry(payer.id, D.debit, 100),
            PostingEntry(payee.id, D.credit, 90),
        ),
    )
    with pytest.raises(PostingRejected, match="posting_unbalanced"):
        ledger.post(unbalanced, commit=False)

    for bad in (0, -5, 1.5):
        with pytest.raises(PostingRejected):
            ledger.transfer(
                payer_account_id=payer.id, payee_account_id=payee.id, amount=bad, commit=False
            )

    with pytest.raises(PostingRejected, match="duplicate_account_leg"):
        ledger.transfer(
            payer_account_id=payer.id, payee_account_id=payer.id, amount=10, commit=False
        )
    db.rollback()


def test_currency_mismatch_is_rejected(db):
    company = _company(db)
    other = _company(db)
    accounts = AccountService(db)
    credit_account = accounts.ensure_account(_actor(company))
    # 故意造一个"另一种货币"的账户行（v1 只有 CREDIT，这里模拟历史/测试数据）
    foreign = economy_repo.insert_account(
        db,
        actor_kind="company",
        actor_ref=other.id,
        currency="TOKEN",
        kind=LedgerAccountKind.actor.value,
        subject_ref=0,
        normal_side="debit",
    )
    with pytest.raises(PostingRejected, match="currency_mismatch"):
        LedgerService(db).transfer(
            payer_account_id=credit_account.id,
            payee_account_id=foreign.id,
            amount=10,
            commit=False,
        )
    db.rollback()


def test_unknown_account_and_unknown_options(db):
    company = _company(db)
    payee = AccountService(db).ensure_account(_actor(company))
    with pytest.raises(PostingRejected, match="account_not_found"):
        LedgerService(db).transfer(
            payer_account_id=999_999, payee_account_id=payee.id, amount=1, commit=False
        )
    escrow = AccountService(db).ensure_escrow_account(escrow_id=99)
    with pytest.raises(PostingRejected, match="unknown options"):
        LedgerService(db).escrow_fund(
            escrow_account_id=escrow.id,
            payer_account_id=payee.id,
            amount=1,
            nonsense="x",
            commit=False,
        )
    db.rollback()


# ---------------------------------------------------------------- 权限（E4/E23）


def test_mint_requires_authority_token(db):
    company = _company(db)
    accounts = AccountService(db)
    beneficiary = accounts.ensure_account(_actor(company))
    issuer = accounts.ensure_system_accounts()[LedgerAccountKind.issuance]
    posting = Posting(
        transaction_type=TransactionKind.mint,
        entries=blueprint_entries(
            TransactionKind.mint,
            {"beneficiary": (beneficiary.id, 100), "issuance": (issuer.id, 100)},
        ),
    )
    with pytest.raises(AuthorityRequired):
        LedgerService(db).post(posting, commit=False)
    # 携带令牌（只有 MonetaryAuthority 拿得到）才放行
    MonetaryAuthority(db).mint(actor=_actor(company), amount=100, reason="test", commit=False)
    assert _wallet_pair(db, beneficiary.id)["available"] == 100
    db.rollback()


def test_only_monetary_authority_module_reaches_the_token():
    """令牌只被 Posting Core 与 MonetaryAuthority 取用；API 层永远拿不到（E23/A18）。"""
    allowed = {
        "app/services/economy/__init__.py",
        "app/services/economy/authority.py",
        "app/services/economy/ledger.py",
        "app/services/economy/monetary.py",
    }
    forbidden_names = {"AUTHORITY_TOKEN", "MonetaryAuthority", "authority"}
    offenders: list[str] = []
    for path in sorted((SERVER_ROOT / "app").rglob("*.py")):
        relative = str(path.relative_to(SERVER_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.endswith("economy.authority") or module.endswith("economy.monetary"):
                    imported.update(alias.name for alias in node.names)
                    imported.add(module.rsplit(".", 1)[-1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith(("economy.authority", "economy.monetary")):
                        imported.add(alias.name.rsplit(".", 1)[-1])
        if imported and relative not in allowed:
            offenders.append(f"{relative}: imports {sorted(imported)}")
        if relative.startswith("app/api/") and imported & forbidden_names:
            offenders.append(f"{relative}: API 不得触达 {sorted(imported & forbidden_names)}")
    assert not offenders, offenders


# ---------------------------------------------------------------- 供给（E5/E6）


def test_mint_transfer_burn_supply_semantics(db):
    first = _company(db)
    second = _company(db)
    authority = MonetaryAuthority(db)
    ledger = LedgerService(db)
    accounts = AccountService(db)

    before = ledger.supply()
    authority.mint(actor=_actor(first), amount=1000, reason="starter", commit=False)
    authority.mint(actor=_actor(second), amount=400, reason="starter", commit=False)
    after_mint = ledger.supply()
    assert after_mint.minted - before.minted == 1400
    assert after_mint.supply - before.supply == 1400
    assert after_mint.burned == before.burned

    payer = accounts.ensure_account(_actor(first))
    payee = accounts.ensure_account(_actor(second))
    ledger.transfer(
        payer_account_id=payer.id,
        payee_account_id=payee.id,
        amount=250,
        reason="services",
        commit=False,
    )
    after_transfer = ledger.supply()
    assert after_transfer.supply == after_mint.supply  # E6：transfer 不改变供给
    assert _wallet_pair(db, payee.id)["available"] == 650

    authority.burn(actor=_actor(first), amount=100, reason="sink", commit=False)
    after_burn = ledger.supply()
    assert after_burn.burned - before.burned == 100
    assert after_burn.supply == after_mint.supply - 100
    db.rollback()


def test_mint_and_burn_require_reason(db):
    company = _company(db)
    authority = MonetaryAuthority(db)
    with pytest.raises(ValueError, match="reason"):
        authority.mint(actor=_actor(company), amount=100, reason="", commit=False)
    with pytest.raises(ValueError, match="reason"):
        authority.burn(actor=_actor(company), amount=100, reason="", commit=False)
    db.rollback()


# ---------------------------------------------------------------- 资金与幂等


def test_transfer_insufficient_funds_leaves_no_trace(db):
    first = _company(db)
    second = _company(db)
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(first))
    payee = accounts.ensure_account(_actor(second))
    ledger = LedgerService(db)
    MonetaryAuthority(db).mint(actor=_actor(first), amount=100, reason="test", commit=True)

    before = _wallet_pair(db, payer.id)
    transactions_before = _transaction_count(db)
    with pytest.raises(InsufficientFunds):
        ledger.transfer(
            payer_account_id=payer.id, payee_account_id=payee.id, amount=101, commit=True
        )
    db.expire_all()
    assert _transaction_count(db) == transactions_before  # 没有半笔账
    assert _wallet_pair(db, payer.id) == before  # 余额未变


def test_transfer_exact_balance_is_allowed(db):
    first = _company(db)
    second = _company(db)
    accounts = AccountService(db)
    payer = accounts.ensure_account(_actor(first))
    payee = accounts.ensure_account(_actor(second))
    ledger = LedgerService(db)
    MonetaryAuthority(db).mint(actor=_actor(first), amount=100, reason="test", commit=False)
    ledger.transfer(payer_account_id=payer.id, payee_account_id=payee.id, amount=100, commit=False)
    assert _wallet_pair(db, payer.id)["available"] == 0
    assert _wallet_pair(db, payee.id)["available"] == 100
    db.rollback()


def test_idempotent_replay_returns_existing_transaction(db):
    company = _company(db)
    authority = MonetaryAuthority(db)
    ledger = LedgerService(db)
    transactions_before = _transaction_count(db)
    supply_before = ledger.supply().supply
    first = authority.mint(
        actor=_actor(company), amount=500, reason="reward", idempotency_key="grant-1", commit=False
    )
    second = authority.mint(
        actor=_actor(company), amount=500, reason="reward", idempotency_key="grant-1", commit=False
    )
    assert first.created is True
    assert second.created is False
    assert first.transaction.id == second.transaction.id
    assert _transaction_count(db) == transactions_before + 1
    assert ledger.supply().supply == supply_before + 500  # 没有发第二次
    db.rollback()


def test_idempotency_key_conflict_on_different_payload(db):
    first = _company(db)
    second = _company(db)
    authority = MonetaryAuthority(db)
    authority.mint(
        actor=_actor(first), amount=500, reason="reward", idempotency_key="dup", commit=False
    )
    with pytest.raises(IdempotencyConflict):
        authority.mint(
            actor=_actor(second), amount=500, reason="reward", idempotency_key="dup", commit=False
        )
    db.rollback()


# ---------------------------------------------------------------- Escrow（E7/E25/E30）


def _mint(db, company: Company, amount: int) -> int:
    monetary = MonetaryAuthority(db)
    monetary.mint(actor=_actor(company), amount=amount, reason="test", commit=False)
    return int(AccountService(db).ensure_account(_actor(company)).id)


def test_escrow_fund_release_reserves_and_zeroes(db):
    buyer = _company(db, "Buyer")
    seller = _company(db, "Seller")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    supply_before = ledger.supply().supply
    buyer_account = _mint(db, buyer, 1000)
    seller_account = accounts.ensure_account(_actor(seller)).id
    escrow = accounts.ensure_escrow_account(escrow_id=42)

    ledger.escrow_fund(
        escrow_account_id=escrow.id, payer_account_id=buyer_account, amount=300, commit=False
    )
    # 锁资不改变净资产，只改变可花额度（设计 §11 的公司钱包示例）
    assert _wallet_pair(db, buyer_account) == {"posted": 1000, "available": 700, "reserved": 300}
    assert _wallet_pair(db, escrow.id) == {"posted": 300, "available": 300, "reserved": 0}
    assert ledger.supply().supply == supply_before + 1000  # E7：Escrow 不改变供给

    ledger.escrow_release(
        escrow_account_id=escrow.id, payee_account_id=seller_account, amount=300, commit=False
    )
    assert _wallet_pair(db, buyer_account) == {"posted": 700, "available": 700, "reserved": 0}
    assert _wallet_pair(db, seller_account)["available"] == 300
    assert _wallet_pair(db, escrow.id) == {"posted": 0, "available": 0, "reserved": 0}  # E25
    assert ledger.supply().supply == supply_before + 1000
    db.rollback()


def test_escrow_refund_returns_to_funder_only(db):
    buyer = _company(db, "RefundBuyer")
    stranger = _company(db, "Stranger")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    buyer_account = _mint(db, buyer, 500)
    stranger_account = accounts.ensure_account(_actor(stranger)).id
    escrow = accounts.ensure_escrow_account(escrow_id=43)
    ledger.escrow_fund(
        escrow_account_id=escrow.id, payer_account_id=buyer_account, amount=200, commit=False
    )

    with pytest.raises(PostingRejected, match="escrow_refund_must_return_to_the_funder"):
        ledger.escrow_refund(
            escrow_account_id=escrow.id, payer_account_id=stranger_account, amount=200, commit=False
        )
    ledger.escrow_refund(
        escrow_account_id=escrow.id, payer_account_id=buyer_account, amount=200, commit=False
    )
    assert _wallet_pair(db, buyer_account) == {"posted": 500, "available": 500, "reserved": 0}
    assert _wallet_pair(db, escrow.id)["available"] == 0
    db.rollback()


def test_escrow_rejects_a_second_funder(db):
    first = _company(db, "Funder1")
    second = _company(db, "Funder2")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    first_account = _mint(db, first, 400)
    second_account = _mint(db, second, 400)
    escrow = accounts.ensure_escrow_account(escrow_id=44)
    ledger.escrow_fund(
        escrow_account_id=escrow.id, payer_account_id=first_account, amount=100, commit=False
    )
    with pytest.raises(PostingRejected, match="escrow_already_funded_by_another_actor"):
        ledger.escrow_fund(
            escrow_account_id=escrow.id, payer_account_id=second_account, amount=100, commit=False
        )
    db.rollback()


def test_escrow_release_without_funder_is_rejected(db):
    payer = _company(db, "NoFunder")
    payee = _company(db, "NoFunderPayee")
    accounts = AccountService(db)
    ledger = LedgerService(db)
    payer_account = _mint(db, payer, 300)
    payee_account = accounts.ensure_account(_actor(payee)).id
    escrow = accounts.ensure_escrow_account(escrow_id=45)
    # 直接把钱转进 escrow（不是 escrow_fund）：账本里没有出资腿 ⇒ 归因失败
    ledger.transfer(
        payer_account_id=payer_account, payee_account_id=escrow.id, amount=50, commit=False
    )
    with pytest.raises(PostingRejected, match="requires_a_funder"):
        ledger.escrow_release(
            escrow_account_id=escrow.id, payee_account_id=payee_account, amount=50, commit=False
        )
    db.rollback()


# ---------------------------------------------------------------- 投影一致 / append-only


def test_projection_matches_ledger_after_posting(db):
    company = _company(db)
    ledger = LedgerService(db)
    supply_before = ledger.supply().supply
    account_id = _mint(db, company, 1234)
    wallets = derive_wallets(db, account_ids=[account_id])
    wallet = wallets[account_id]
    assert _wallet_pair(db, account_id) == {
        "posted": wallet.posted_balance,
        "available": wallet.available_balance,
        "reserved": wallet.reserved_balance,
    }
    assert wallet.last_entry_id is not None
    assert verify_wallet_projection(db).ok
    assert ledger.supply().supply == supply_before + 1234
    db.rollback()


def test_only_the_posting_core_writes_ledger_rows():
    """A9/E27：`ledger_transactions` / `ledger_entries` 只能由 Posting Core（+ 仓储原语）写入。"""
    allowed_prefixes = ("app/repositories/economy.py", "app/services/economy/", "app/models/")
    offenders: list[str] = []
    for path in sorted((SERVER_ROOT / "app").rglob("*.py")):
        relative = str(path.relative_to(SERVER_ROOT))
        if relative.startswith(allowed_prefixes):
            continue
        source = path.read_text(encoding="utf-8")
        for token in (
            "insert_entries(",
            "insert_transaction(",
            "LedgerEntry(",
            "LedgerTransaction(",
        ):
            if token in source:
                offenders.append(f"{relative}: {token}")
    assert not offenders, offenders


def test_ledger_entries_are_append_only():
    """E17/E31：模型没有 updated_at，且仓库/服务层没有 update/delete entry 的代码路径。"""
    assert not hasattr(LedgerEntry, "updated_at")
    forbidden = ("update(LedgerEntry", "delete(LedgerEntry", ".query(LedgerEntry).delete")
    offenders: list[str] = []
    for path in sorted((SERVER_ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        offenders += [
            f"{path.relative_to(SERVER_ROOT)}: {token}" for token in forbidden if token in source
        ]
    assert not offenders, offenders
    # 投影表是唯一允许被 UPDATE 的经济表
    assert "update(WalletProjection" in (SERVER_ROOT / "app/repositories/economy.py").read_text(
        encoding="utf-8"
    )


def test_rebuild_after_posting_keeps_values(db):
    company = _company(db)
    escrow = AccountService(db).ensure_escrow_account(escrow_id=46)
    account_id = _mint(db, company, 900)
    ledger = LedgerService(db)
    ledger.escrow_fund(
        escrow_account_id=escrow.id, payer_account_id=account_id, amount=400, commit=False
    )
    expected = {
        account_id: (wallet.posted_balance, wallet.available_balance, wallet.reserved_balance)
        for account_id, wallet in derive_wallets(db).items()
    }
    rebuild_wallet_projection(db, commit=False)
    after = {
        row.account_id: (
            int(row.posted_balance),
            int(row.available_balance),
            int(row.reserved_balance),
        )
        for row in economy_repo.list_projections(db)
    }
    assert after == expected
    assert after[account_id] == (900, 500, 400)  # 锁定份额在重建后依然可推导（E30）
    assert verify_wallet_projection(db).ok
    db.rollback()


def test_sql_ast_guard_no_direct_balance_mutation_outside_projection():
    """E1：除投影仓库外，没有任何模块直接给 `available_balance` 之类赋值。"""
    offenders: list[str] = []
    for path in sorted((SERVER_ROOT / "app").rglob("*.py")):
        relative = str(path.relative_to(SERVER_ROOT))
        if relative in {"app/repositories/economy.py", "app/models/economy.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr in {
                        "available_balance",
                        "posted_balance",
                        "reserved_balance",
                    }:
                        offenders.append(f"{relative}:{node.lineno} {target.attr}")
    assert not offenders, offenders


def test_currency_is_explicit_in_every_entry(db):
    company = _company(db)
    _mint(db, company, 10)
    db.expire_all()
    currencies = {row for (row,) in db.execute(sa.select(LedgerEntry.currency).distinct()).all()}
    assert currencies == {Currency.credit.value}
    db.rollback()
