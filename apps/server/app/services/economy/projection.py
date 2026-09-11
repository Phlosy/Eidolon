"""投影重建与对账（M1.1c，设计 §11，E29/E15）。

- `rebuild_wallet_projection`：**清空 → 由账本重算 → 写回**（绝不读旧投影）；
- `verify_wallet_projection`：**只读**比对"账本推导值 vs 投影缓存"，报告 drift 但不修
  （运维决定何时 rebuild；`--apply` 是人的动作，不是自动修复）。

两者与 `post()` 共用 `balances.derive_wallets` —— 口径只有一个（E26），否则对账永远对不平。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomyContractError
from app.models.base import utcnow
from app.repositories import economy as economy_repo
from app.services.economy.balances import DerivedWallet, derive_wallets

logger = get_logger(__name__)

_FIELDS = ("posted_balance", "available_balance", "reserved_balance")


@dataclass(frozen=True)
class ProjectionDrift:
    """一处不一致：账本说是 `ledger`，投影里是 `projection`。"""

    account_id: int
    field: str
    ledger: int | None
    projection: int | None
    delta: int | None
    kind: str = "mismatch"  # mismatch | missing | orphan

    def describe(self) -> str:
        if self.kind == "missing":
            return f"account {self.account_id}: projection row missing"
        if self.kind == "orphan":
            return f"account {self.account_id}: projection row without ledger account"
        return (
            f"account {self.account_id} {self.field}: ledger={self.ledger} "
            f"projection={self.projection} delta={self.delta}"
        )


@dataclass(frozen=True)
class ProjectionCheck:
    """对账结果（`ok=False` 时逐条列出 drift / anomaly）。"""

    ok: bool
    accounts_checked: int
    drifts: tuple[ProjectionDrift, ...]
    anomaly: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"OK ({self.accounts_checked} accounts reconciled)"
        if self.anomaly:
            return f"LEDGER ANOMALY: {self.anomaly}"
        return f"DRIFT ({len(self.drifts)} issues)\n" + "\n".join(
            drift.describe() for drift in self.drifts
        )


def rebuild_wallet_projection(db: Session, *, commit: bool = True) -> int:
    """由账本重建全部投影行，返回重建行数（E29）。

    `version` 从 1 重新开始 —— 它是缓存自己的并发计数，不是业务事实；
    `last_entry_id` 由账本推出（追查"最后一次变化是哪条 entry"）。
    """
    try:
        wallets = derive_wallets(db)
        economy_repo.delete_all_projections(db)
        rows = [
            {
                "account_id": wallet.account_id,
                "currency": wallet.currency,
                "posted_balance": wallet.posted_balance,
                "available_balance": wallet.available_balance,
                "reserved_balance": wallet.reserved_balance,
                "version": 1,
                "last_entry_id": wallet.last_entry_id,
                "updated_at": utcnow(),
            }
            for wallet in wallets.values()
        ]
        wiped = economy_repo.insert_projection_rows(db, rows)
        if commit:
            db.commit()
        logger.info("wallet projection rebuilt: %d rows", wiped)
        return wiped
    except Exception:
        if commit:
            db.rollback()
        raise


def verify_wallet_projection(db: Session) -> ProjectionCheck:
    """只读对账：账本推导值 vs 投影缓存（不修数据，E15）。

    "未物化" ≠ drift：从未过账的账户没有投影行是正常的（缺行等价于 0）；
    账本上有余额却查不到行/值不等，才算 drift。
    """
    try:
        wallets = derive_wallets(db)
    except EconomyContractError as exc:
        # 账本自身就矛盾（例如 escrow 有余额却没有出资腿）：报成 anomaly，不抛给调用方
        return ProjectionCheck(ok=False, accounts_checked=0, drifts=(), anomaly=str(exc))
    stored = {int(row.account_id): row for row in economy_repo.list_projections(db)}

    drifts: list[ProjectionDrift] = []
    for account_id, wallet in sorted(wallets.items()):
        row = stored.pop(account_id, None)
        if row is None:
            # 从未过账的账户没有投影行是**正常**的（缺行等价于 0）；
            # 但账本上明明有钱却没有行 ⇒ 真 drift（需要 rebuild）。
            if (
                wallet.posted_balance,
                wallet.available_balance,
                wallet.reserved_balance,
            ) == (0, 0, 0):
                continue
            drifts.append(
                ProjectionDrift(
                    account_id=account_id,
                    field="row",
                    ledger=wallet.posted_balance,
                    projection=None,
                    delta=None,
                    kind="missing",
                )
            )
            continue
        for field in _FIELDS:
            ledger_value = int(getattr(wallet, field))
            projection_value = int(getattr(row, field))
            if ledger_value != projection_value:
                drifts.append(
                    ProjectionDrift(
                        account_id=account_id,
                        field=field,
                        ledger=ledger_value,
                        projection=projection_value,
                        delta=projection_value - ledger_value,
                    )
                )
    for account_id in sorted(stored):
        drifts.append(
            ProjectionDrift(
                account_id=account_id,
                field="row",
                ledger=None,
                projection=int(stored[account_id].posted_balance),
                delta=None,
                kind="orphan",
            )
        )
    return ProjectionCheck(ok=not drifts, accounts_checked=len(wallets), drifts=tuple(drifts))


def wallet_rows_for_actor(db: Session, *, account_ids: list[int]) -> list[DerivedWallet]:
    """读 API 用：按账户给出账本推导值（读路径也走同一口径）。"""
    wallets = derive_wallets(db, account_ids=account_ids)
    return [wallets[account_id] for account_id in account_ids if account_id in wallets]
