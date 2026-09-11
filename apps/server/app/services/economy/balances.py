"""派生余额口径 —— **余额的唯一计算入口**（M1.1c，设计 §11/E26/E29/E30）。

账本聚合 → 账户余额 → 主体钱包（posted / available / reserved）。
`post()` 的增量更新、`rebuild_wallet_projection`、`verify_wallet_projection` **共用本模块**，
所以"投影算出来的"和"账本算出来的"不可能是两套规则（否则对账永远对不平）。

口径（设计 §11）：
- `own_balance`：账户自身的账本余额（Σ 由 `balance_delta` 累加，正常余额方向决定符号）；
- actor 账户（非 system 主体）：`available = own_balance`、`reserved = 归因到本主体的 escrow 余额`、
  `posted = available + reserved`（总资产，含锁定）；
- escrow / 系统账户：`reserved = 0`、`available = own_balance`、`posted = own_balance`；
- 归因来源是 `escrow_fund` 的 credit 腿（E30）—— 一笔 Escrow 只有一个出资人（§23）；
  escrow 有余额却找不到出资人 ⇒ 契约破坏，直接报错而不是静默算错。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.economy.contracts import EconomyContractError, balance_from_totals
from app.models.enums import EconomicActorKind, LedgerAccountKind
from app.repositories import economy as economy_repo


@dataclass(frozen=True)
class DerivedWallet:
    """由账本推导出的钱包投影值（不含 version —— 那是投影自己的并发计数）。"""

    account_id: int
    currency: str
    posted_balance: int
    available_balance: int
    reserved_balance: int
    last_entry_id: int | None


def last_entry_ids(db: Session, *, account_ids: list[int] | None = None) -> dict[int, int]:
    """每个账户最后一条 entry 的 id（重建 `last_entry_id` 用）。"""
    from sqlalchemy import func, select

    from app.models.economy import LedgerEntry

    stmt = select(LedgerEntry.account_id, func.max(LedgerEntry.id)).group_by(LedgerEntry.account_id)
    if account_ids is not None:
        if not account_ids:
            return {}
        stmt = stmt.where(LedgerEntry.account_id.in_(account_ids))
    return {int(account_id): int(last_id) for account_id, last_id in db.execute(stmt)}


def derive_wallets(
    db: Session, *, account_ids: list[int] | None = None
) -> dict[int, DerivedWallet]:
    """按账户推导钱包三值（只读账本，不看 `wallet_projection`）。"""
    accounts = {
        int(account.id): account
        for account in (
            economy_repo.list_accounts_by_kind(
                db, kinds=tuple(kind.value for kind in LedgerAccountKind)
            )
            if account_ids is None
            else [economy_repo.get_account(db, account_id) for account_id in account_ids]
        )
        if account is not None
    }
    if not accounts:
        return {}

    totals = economy_repo.aggregate_entry_totals(db, account_ids=sorted(accounts))
    own: dict[int, int] = {}
    for account_id, account in accounts.items():
        debit_total, credit_total = totals.get((account_id, account.currency), (0, 0))
        own[account_id] = balance_from_totals(
            LedgerAccountKind(account.kind), debit_total=debit_total, credit_total=credit_total
        )

    funders = economy_repo.escrow_funder_map(db)
    reserved: dict[int, int] = defaultdict(int)
    for escrow_account_id, funder_account_id in funders.items():
        escrow_balance = own.get(escrow_account_id, 0)
        if escrow_balance < 0:
            raise EconomyContractError(f"escrow account {escrow_account_id} has a negative balance")
        if escrow_balance > 0:
            reserved[funder_account_id] += escrow_balance
    orphan_escrows = {
        account_id
        for account_id, account in accounts.items()
        if account.kind == LedgerAccountKind.escrow.value
        and own.get(account_id, 0) > 0
        and account_id not in funders
    }
    if orphan_escrows:
        raise EconomyContractError(
            "escrow accounts hold funds without a funder leg: "
            + ", ".join(str(account_id) for account_id in sorted(orphan_escrows))
        )

    last_ids = last_entry_ids(db, account_ids=sorted(accounts))
    wallets: dict[int, DerivedWallet] = {}
    for account_id, account in accounts.items():
        own_balance = own[account_id]
        is_actor_wallet = (
            account.kind == LedgerAccountKind.actor.value
            and account.actor_kind != EconomicActorKind.system.value
        )
        if is_actor_wallet:
            held = reserved.get(account_id, 0)
            wallets[account_id] = DerivedWallet(
                account_id=account_id,
                currency=account.currency,
                posted_balance=own_balance + held,
                available_balance=own_balance,
                reserved_balance=held,
                last_entry_id=last_ids.get(account_id),
            )
        else:
            wallets[account_id] = DerivedWallet(
                account_id=account_id,
                currency=account.currency,
                posted_balance=own_balance,
                available_balance=own_balance,
                reserved_balance=0,
                last_entry_id=last_ids.get(account_id),
            )
    return wallets
