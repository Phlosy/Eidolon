"""/economy —— M1.1 经济读面（**只读**：没有 mint/burn/transfer 端点，E23/A18）。

作用域（A17）：一律以当前请求的公司为界 —— 余额与流水都只覆盖**本公司自己的账户**；
跨公司/跨主体一律看不到（市场那样的受控跨公司读取域是 T2 的事，与经济无关）。

写路径只有内部能力：`LedgerService.post()` / `MonetaryAuthority`（无 HTTP 入口）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.economy.contracts import EconomicActor
from app.models.enums import Currency, EconomicActorKind
from app.repositories import economy as economy_repo
from app.schemas.economy import (
    LedgerAccountOut,
    LedgerEntryOut,
    LedgerTransactionOut,
    LedgerTransactionPageOut,
    WalletOut,
)
from app.services.economy.accounts import AccountService

router = APIRouter(prefix="/economy", tags=["economy"])


def _company_actor(company_id: int | None) -> EconomicActor:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return EconomicActor.company(company_id)


def _account_out(account, projection) -> dict:
    return {
        "account_id": int(account.id),
        "kind": account.kind,
        "currency": account.currency,
        "status": account.status,
        "subject_ref": int(account.subject_ref),
        "posted_balance": int(projection.posted_balance) if projection else 0,
        "available_balance": int(projection.available_balance) if projection else 0,
        "reserved_balance": int(projection.reserved_balance) if projection else 0,
        "version": int(projection.version) if projection else 0,
    }


@router.get("/balance", response_model=WalletOut)
def get_balance(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司钱包：`posted`（含锁定）/ `available`（可花）/ `reserved`（Escrow 锁定份额）。

    没有账务活动的公司返回全 0（不创建账户、不伪造数字）。
    """
    actor = _company_actor(company_id)
    accounts = AccountService(db).accounts_for_actor(actor)
    rows = [
        _account_out(account, economy_repo.get_projection(db, int(account.id)))
        for account in accounts
    ]
    currency = rows[0]["currency"] if rows else Currency.credit.value
    return {
        "actor_kind": actor.kind.value,
        "actor_ref": int(actor.ref),
        "currency": currency,
        "posted_balance": sum(row["posted_balance"] for row in rows),
        "available_balance": sum(row["available_balance"] for row in rows),
        "reserved_balance": sum(row["reserved_balance"] for row in rows),
        "accounts": rows,
    }


@router.get("/accounts", response_model=list[LedgerAccountOut])
def list_accounts(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    """本公司账户列表（含投影三值；账户不可删除，关闭后仍在此显示）。"""
    actor = _company_actor(company_id)
    accounts = AccountService(db).accounts_for_actor(actor)
    return [
        _account_out(account, economy_repo.get_projection(db, int(account.id)))
        for account in accounts
    ]


@router.get("/transactions", response_model=LedgerTransactionPageOut)
def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    transaction_type: str | None = Query(default=None),
    reference_type: str | None = Query(default=None),
    reference_id: str | None = Query(default=None),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司流水（分页，倒序）：只返回**触及本公司账户**的交易。

    腿（entries）随交易一起返回 —— 金额与方向是账本的原始事实，不做聚合隐藏。
    """
    actor = _company_actor(company_id)
    account_ids = [int(account.id) for account in AccountService(db).accounts_for_actor(actor)]
    transactions, total = economy_repo.transactions_for_accounts(
        db,
        account_ids=account_ids,
        limit=limit,
        offset=offset,
        transaction_type=transaction_type,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    entries_by_transaction = economy_repo.list_entries_for_transactions(
        db, transaction_ids=[int(transaction.id) for transaction in transactions]
    )
    items = []
    for transaction in transactions:
        entries = entries_by_transaction.get(int(transaction.id), [])
        items.append(
            {
                "transaction_id": int(transaction.id),
                "transaction_type": transaction.transaction_type,
                "currency": transaction.currency,
                "status": transaction.status,
                "reference_type": transaction.reference_type,
                "reference_id": transaction.reference_id,
                "reason": transaction.reason,
                "amount": sum(int(entry.amount) for entry in entries if entry.direction == "debit"),
                "occurred_at": transaction.occurred_at,
                "posted_at": transaction.posted_at,
                "entries": [
                    LedgerEntryOut(
                        account_id=int(entry.account_id),
                        direction=entry.direction,
                        amount=int(entry.amount),
                    )
                    for entry in entries
                ],
            }
        )
    return {
        "items": [LedgerTransactionOut(**item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def actor_kinds() -> list[str]:  # pragma: no cover - 文档/调试辅助
    return [kind.value for kind in EconomicActorKind]
