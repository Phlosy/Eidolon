"""Economy repository（M1.1）—— 账本持久化原语（不做业务判断）。

**分层纪律**：本模块只提供 SQL 原语；"能不能过账 / 能不能花"由
`app/services/economy/*` 判定（否则规则会散落到两个地方）。

关键原语：
- `ensure_account` / `ensure_projection`：幂等开户（唯一约束 + `ON CONFLICT DO NOTHING` + 回查赢家，
  与 drive / market 的既有幂等先例一致）——重复调用（含并发）不会产生第二行；
- `cas_update_projection`：**条件更新 + rowcount 判定**（E24 的并发落点）。资金不足时
  rowcount = 0，调用方据此回滚整笔过账；
- `aggregate_entry_totals` / `escrow_funder_map`：重建与对账的聚合口径 —— **只读账本**，
  不看 `wallet_projection`（否则"用投影验证投影"毫无意义，E29）；
- `transactions_for_accounts`：读 API 的公司作用域查询（只返回触及本公司账户的交易）。
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.economy.contracts import EconomicActor, EconomyContractError
from app.models.base import utcnow
from app.models.economy import (
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    RewardGrant,
    WalletProjection,
)
from app.models.enums import (
    Currency,
    EconomicActorKind,
    LedgerAccountKind,
    LedgerAccountStatus,
    LedgerEntryDirection,
    LedgerTransactionStatus,
    RewardStatus,
    TransactionKind,
)


def actor_columns(actor: EconomicActor) -> tuple[str, int]:
    """主体 → `(actor_kind, actor_ref)`。

    系统主体（发行/财政/销毁）没有宿主行，`actor_ref = 0` —— 同一主体内的区别由
    `kind` 承担（`issuance` / `treasury` / `burn` 各自唯一），唯一约束因此依然成立。
    """
    if actor.kind is EconomicActorKind.system:
        return (actor.kind.value, 0)
    return (actor.kind.value, int(actor.ref))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- accounts


def get_account(db: Session, account_id: int) -> LedgerAccount | None:
    return db.get(LedgerAccount, account_id)


def find_account(
    db: Session,
    *,
    actor_kind: str,
    actor_ref: int,
    currency: str,
    kind: str,
    subject_ref: int = 0,
) -> LedgerAccount | None:
    return db.scalars(
        select(LedgerAccount).where(
            LedgerAccount.actor_kind == actor_kind,
            LedgerAccount.actor_ref == actor_ref,
            LedgerAccount.currency == currency,
            LedgerAccount.kind == kind,
            LedgerAccount.subject_ref == subject_ref,
        )
    ).first()


def list_accounts_for_actor(
    db: Session,
    *,
    actor_kind: str,
    actor_ref: int,
    currency: str | None = None,
    kinds: tuple[str, ...] | None = None,
) -> list[LedgerAccount]:
    stmt = select(LedgerAccount).where(
        LedgerAccount.actor_kind == actor_kind, LedgerAccount.actor_ref == actor_ref
    )
    if currency is not None:
        stmt = stmt.where(LedgerAccount.currency == currency)
    if kinds is not None:
        stmt = stmt.where(LedgerAccount.kind.in_(kinds))
    return list(db.scalars(stmt.order_by(LedgerAccount.id)))


def list_accounts_by_kind(db: Session, *, kinds: tuple[str, ...]) -> list[LedgerAccount]:
    return list(
        db.scalars(
            select(LedgerAccount).where(LedgerAccount.kind.in_(kinds)).order_by(LedgerAccount.id)
        )
    )


def insert_account(
    db: Session,
    *,
    actor_kind: str,
    actor_ref: int,
    currency: str,
    kind: str,
    subject_ref: int,
    normal_side: str,
) -> LedgerAccount:
    """幂等开户：唯一约束 + `ON CONFLICT DO NOTHING` + 回查（不靠"先查再插"）。"""
    values = {
        "actor_kind": actor_kind,
        "actor_ref": actor_ref,
        "currency": currency,
        "kind": kind,
        "subject_ref": subject_ref,
        "normal_side": normal_side,
        "status": LedgerAccountStatus.active.value,
        "frozen_reason": "",
    }
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        db.execute(sqlite_insert(LedgerAccount).values(**values).on_conflict_do_nothing())
    else:  # pragma: no cover - 本仓库部署为 SQLite；保留可移植分支
        db.execute(
            LedgerAccount.__table__.insert().values(**values).prefix_with("ON CONFLICT DO NOTHING")
        )
    db.flush()
    account = find_account(
        db,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        currency=currency,
        kind=kind,
        subject_ref=subject_ref,
    )
    assert account is not None  # 冲突后必然能回查到赢家
    return account


def set_account_status(
    db: Session, *, account_id: int, status: LedgerAccountStatus, reason: str = ""
) -> LedgerAccount:
    account = get_account(db, account_id)
    if account is None:
        raise EconomyContractError(f"account {account_id} not found")
    account.status = status.value
    account.frozen_reason = reason if status is LedgerAccountStatus.frozen else ""
    db.flush()
    return account


# --------------------------------------------------------------------------- transactions


def get_transaction(db: Session, transaction_id: int) -> LedgerTransaction | None:
    return db.get(LedgerTransaction, transaction_id)


def find_transaction_by_idempotency(db: Session, key: str) -> LedgerTransaction | None:
    return db.scalars(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == key)
    ).first()


def insert_transaction(db: Session, **values: object) -> LedgerTransaction:
    transaction = LedgerTransaction(**values)
    db.add(transaction)
    db.flush()
    return transaction


def insert_entries(db: Session, rows: list[dict]) -> list[LedgerEntry]:
    entries = [LedgerEntry(**row) for row in rows]
    db.add_all(entries)
    db.flush()
    return entries


def list_entries(db: Session, *, transaction_id: int) -> list[LedgerEntry]:
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == transaction_id)
            .order_by(LedgerEntry.id)
        )
    )


def list_entries_for_transactions(
    db: Session, *, transaction_ids: list[int]
) -> dict[int, list[LedgerEntry]]:
    """批量取腿（读 API 避免 N+1）。"""
    if not transaction_ids:
        return {}
    grouped: dict[int, list[LedgerEntry]] = defaultdict(list)
    for entry in db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.transaction_id.in_(transaction_ids))
        .order_by(LedgerEntry.id)
    ):
        grouped[int(entry.transaction_id)].append(entry)
    return dict(grouped)


def transactions_for_accounts(
    db: Session,
    *,
    account_ids: list[int],
    limit: int,
    offset: int,
    transaction_type: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
) -> tuple[list[LedgerTransaction], int]:
    """公司作用域流水：只返回**触及本公司账户**的交易（按 id 倒序，分页）。

    count 与分页共用同一个已过滤 id 子查询 —— 否则会变成 cartesian product（统计值会被放大）。
    """
    if not account_ids:
        return [], 0
    touching = select(LedgerEntry.transaction_id).where(LedgerEntry.account_id.in_(account_ids))
    filtered = select(LedgerTransaction.id).where(LedgerTransaction.id.in_(touching))
    if transaction_type is not None:
        filtered = filtered.where(LedgerTransaction.transaction_type == transaction_type)
    if reference_type is not None:
        filtered = filtered.where(LedgerTransaction.reference_type == reference_type)
    if reference_id is not None:
        filtered = filtered.where(LedgerTransaction.reference_id == reference_id)

    matched = filtered.subquery()
    total = int(db.execute(select(func.count()).select_from(matched)).scalar_one())
    transactions = list(
        db.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.id.in_(select(matched.c.id)))
            .order_by(LedgerTransaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return transactions, total


# --------------------------------------------------------------------------- 聚合（重建/对账/供给）


def aggregate_entry_totals(
    db: Session, *, account_ids: list[int] | None = None
) -> dict[tuple[int, str], tuple[int, int]]:
    """按账户聚合 (Σdebit, Σcredit) —— **只读 Ledger Entries**（E29 的前提）。

    不含非 posted 交易（v1 全为 posted，但口径写死在查询里以免将来放宽）。
    """
    stmt = (
        select(
            LedgerEntry.account_id,
            LedgerEntry.currency,
            LedgerEntry.direction,
            func.sum(LedgerEntry.amount),
        )
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(LedgerTransaction.status == LedgerTransactionStatus.posted.value)
        .group_by(LedgerEntry.account_id, LedgerEntry.currency, LedgerEntry.direction)
    )
    if account_ids is not None:
        if not account_ids:
            return {}
        stmt = stmt.where(LedgerEntry.account_id.in_(account_ids))

    totals: dict[tuple[int, str], tuple[int, int]] = {}
    for account_id, currency, direction, total in db.execute(stmt):
        debits, credits = totals.get((int(account_id), str(currency)), (0, 0))
        if direction == LedgerEntryDirection.debit.value:
            debits += int(total)
        else:
            credits += int(total)
        totals[(int(account_id), str(currency))] = (debits, credits)
    return totals


def escrow_funder_map(db: Session) -> dict[int, int]:
    """`escrow account_id → 出资账户 account_id`（来自 `escrow_fund` 的 credit 腿）。

    **reserved 的唯一事实来源**（E30）：出资关系写在 entry 里，重建时可重新推导。
    一笔 Escrow 只有一个出资人（设计 §23）；出现第二个出资人即契约破坏 —— 抛错而不是
    "取第一个"，否则会静默算错 reserved。
    """
    escrow_ids = {
        int(account.id)
        for account in db.scalars(
            select(LedgerAccount).where(LedgerAccount.kind == LedgerAccountKind.escrow.value)
        )
    }
    if not escrow_ids:
        return {}

    rows = db.execute(
        select(
            LedgerEntry.transaction_id,
            LedgerEntry.account_id,
            LedgerEntry.direction,
        )
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerTransaction.transaction_type == TransactionKind.escrow_fund.value,
            LedgerTransaction.status == LedgerTransactionStatus.posted.value,
        )
    ).all()

    legs: dict[int, dict[str, list[int]]] = defaultdict(lambda: {"debit": [], "credit": []})
    for transaction_id, account_id, direction in rows:
        legs[int(transaction_id)][str(direction)].append(int(account_id))

    funders: dict[int, int] = {}
    for transaction_id, by_direction in legs.items():
        escrows = [a for a in by_direction["debit"] if a in escrow_ids]
        payers = [a for a in by_direction["credit"] if a not in escrow_ids]
        if len(payers) != 1:
            raise EconomyContractError(
                f"escrow_fund transaction {transaction_id} must have exactly one funder leg"
            )
        for escrow_id in escrows:
            previous = funders.get(escrow_id)
            if previous is not None and previous != payers[0]:
                raise EconomyContractError(
                    f"escrow account {escrow_id} has multiple funders "
                    f"({previous}, {payers[0]}); v1 requires a single funder"
                )
            funders[escrow_id] = payers[0]
    return funders


def system_supply_totals(db: Session) -> tuple[int, int]:
    """(ΣISSUANCE 的 credit, ΣBURN 的 debit) —— Total Supply 的两个输入。"""
    rows = db.execute(
        select(
            LedgerAccount.kind,
            LedgerEntry.direction,
            func.sum(LedgerEntry.amount),
        )
        .join(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id)
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerTransaction.status == LedgerTransactionStatus.posted.value,
            LedgerAccount.kind.in_(
                [LedgerAccountKind.issuance.value, LedgerAccountKind.burn.value]
            ),
        )
        .group_by(LedgerAccount.kind, LedgerEntry.direction)
    ).all()
    minted = 0
    burned = 0
    for kind, direction, total in rows:
        if (
            kind == LedgerAccountKind.issuance.value
            and direction == LedgerEntryDirection.credit.value
        ):
            minted += int(total)
        elif kind == LedgerAccountKind.burn.value and direction == LedgerEntryDirection.debit.value:
            burned += int(total)
    return minted, burned


# --------------------------------------------------------------------------- projection


def get_projection(db: Session, account_id: int) -> WalletProjection | None:
    return db.get(WalletProjection, account_id)


def list_projections(
    db: Session, *, account_ids: list[int] | None = None
) -> list[WalletProjection]:
    stmt = select(WalletProjection).order_by(WalletProjection.account_id)
    if account_ids is not None:
        if not account_ids:
            return []
        stmt = stmt.where(WalletProjection.account_id.in_(account_ids))
    return list(db.scalars(stmt))


def ensure_projection(db: Session, *, account_id: int, currency: str) -> WalletProjection:
    """确保投影行存在（幂等；CAS 的前提是行已存在，否则 rowcount=0 会被误判为余额不足）。"""
    existing = get_projection(db, account_id)
    if existing is not None:
        return existing
    values = {
        "account_id": account_id,
        "currency": currency,
        "posted_balance": 0,
        "available_balance": 0,
        "reserved_balance": 0,
        "version": 1,
        "last_entry_id": None,
        "updated_at": utcnow(),
    }
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        db.execute(sqlite_insert(WalletProjection).values(**values).on_conflict_do_nothing())
    else:  # pragma: no cover - 本仓库部署为 SQLite；保留可移植分支
        db.execute(
            WalletProjection.__table__.insert()
            .values(**values)
            .prefix_with("ON CONFLICT DO NOTHING")
        )
    db.flush()
    projection = get_projection(db, account_id)
    assert projection is not None
    return projection


def cas_update_projection(
    db: Session,
    *,
    account_id: int,
    posted_delta: int,
    available_delta: int,
    reserved_delta: int,
    last_entry_id: int | None,
    required_available: int = 0,
) -> int:
    """**条件更新 + rowcount 判定**（E24/E28）。

    - `required_available`：过账前必须满足的最小可花余额（由服务层按 AccountKind 决定；
      系统账务侧传 0 = 不做资金校验）；
    - 返回 rowcount：1 = 成功；0 = 资金不足或投影行缺失 —— 调用方必须回滚整笔过账，
      **不得**自行修补（"先查再改"会引入 race，设计 §33）。

    金额全部是整数增量，且 `version` 每次 +1（CAS 乐观并发计数，供对账/调试观测）。
    """
    stmt = (
        update(WalletProjection)
        .where(
            WalletProjection.account_id == account_id,
            WalletProjection.available_balance >= required_available,
        )
        .values(
            posted_balance=WalletProjection.posted_balance + posted_delta,
            available_balance=WalletProjection.available_balance + available_delta,
            reserved_balance=WalletProjection.reserved_balance + reserved_delta,
            version=WalletProjection.version + 1,
            last_entry_id=last_entry_id,
            updated_at=utcnow(),
        )
    )
    return int(db.execute(stmt).rowcount)


def delete_all_projections(db: Session) -> int:
    """清空投影（重建的第一步；**不动账本**）。"""
    deleted = int(db.query(WalletProjection).delete())
    db.flush()
    return deleted


def insert_projection_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    db.add_all([WalletProjection(**row) for row in rows])
    db.flush()
    return len(rows)


def projection_currency(account: LedgerAccount) -> str:
    return account.currency or Currency.credit.value


# --------------------------------------------------------------------------- reward grants


def get_reward_grant(db: Session, grant_id: int) -> RewardGrant | None:
    return db.get(RewardGrant, grant_id)


def find_reward_grant(
    db: Session,
    *,
    reward_type: str,
    actor_kind: str,
    actor_ref: int,
    reference_key: str,
) -> RewardGrant | None:
    return db.scalars(
        select(RewardGrant).where(
            RewardGrant.reward_type == reward_type,
            RewardGrant.actor_kind == actor_kind,
            RewardGrant.actor_ref == actor_ref,
            RewardGrant.reference_key == reference_key,
        )
    ).first()


def insert_reward_grant(db: Session, **values: object) -> RewardGrant:
    grant = RewardGrant(**values)
    db.add(grant)
    db.flush()
    return grant


def list_reward_grants(
    db: Session,
    *,
    company_id: int | None = None,
    actor_kind: str | None = None,
    actor_ref: int | None = None,
    reward_type: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[RewardGrant]:
    stmt = select(RewardGrant)
    if company_id is not None:
        stmt = stmt.where(RewardGrant.company_id == company_id)
    if actor_kind is not None:
        stmt = stmt.where(RewardGrant.actor_kind == actor_kind)
    if actor_ref is not None:
        stmt = stmt.where(RewardGrant.actor_ref == actor_ref)
    if reward_type is not None:
        stmt = stmt.where(RewardGrant.reward_type == reward_type)
    if statuses is not None:
        stmt = stmt.where(RewardGrant.status.in_(statuses))
    stmt = stmt.order_by(RewardGrant.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def latest_posted_reward_grant(
    db: Session,
    *,
    reward_type: str,
    actor_kind: str,
    actor_ref: int,
) -> RewardGrant | None:
    """最近一次**已过账**的某类奖励（救援金冷却判定用；只认 POSTED，作废/失败不算）。"""
    return db.scalars(
        select(RewardGrant)
        .where(
            RewardGrant.reward_type == reward_type,
            RewardGrant.actor_kind == actor_kind,
            RewardGrant.actor_ref == actor_ref,
            RewardGrant.status == RewardStatus.posted.value,
        )
        .order_by(RewardGrant.id.desc())
    ).first()
