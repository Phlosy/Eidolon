"""EscrowService —— 托管（M1.4，设计 §19/§23）。

```
payer（发布方）──fund──▶ 独立托管账户（kind=escrow, subject_ref=escrow.id）
                              │
                条件满足 ──release──▶ payee（承接方）
                取消/过期 ──refund──▶ payer（原出资人）
```

**纪律**：
- 资金只经 `LedgerService` 的 `escrow_fund` / `escrow_release` / `escrow_refund` 三条腿组合移动
  （唯一 Posting Core，E27）——本模块不自己写账；
- **Supply 不变**（E7）：托管只是把钱从付款人账户挪进托管账户，再挪给收款人，从不 mint；
- **一单一 Escrow**（`uq_escrow_work_order`）：重复发布/重试由唯一约束收敛；
- **一笔 Escrow 只有一个出资人**（M1.1 冻结的归因规则）：`refund` 只能退回原出资人
  （Posting Core 会拒绝退给第三方）；
- **release 与 refund 竞争由 CAS 裁定**（§33）：`transition_escrow(from_statuses=(FUNDED,))`
  的 rowcount 决定谁赢，输家回滚；
- 释放/退回后托管账户余额必须归零（E25），`reserved` 由账本归因自动回落（E30）；
- 幂等：重复 release/refund 返回既有状态（不重复转账）；`amount` 由订单决定，调用方不能多领。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor
from app.models.base import as_utc, utcnow
from app.models.economy import Escrow, LedgerAccount
from app.models.enums import Currency, EconomicActorKind, EscrowStatus
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService

logger = get_logger(__name__)


class EscrowError(RuntimeError):
    """托管领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class EscrowView:
    """托管状态视图（读面/返回用）。"""

    escrow_id: int
    work_order_id: int | None
    status: str
    amount: int
    currency: str
    payer_actor_kind: str
    payer_actor_ref: int
    payee_actor_kind: str | None
    payee_actor_ref: int | None
    escrow_account_id: int | None
    account_balance: int


class EscrowService:
    """托管资金流（fund / release / refund / expire）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------------------------------------------------------- 建单 + 锁资

    def fund_for_order(
        self,
        *,
        order_id: int,
        payer: EconomicActor,
        amount: int,
        payee: EconomicActor | None = None,
        expires_at: datetime | None = None,
        metadata: dict | None = None,
        commit: bool = False,
    ) -> Escrow:
        """为订单锁资（**发布前必须完成**，E11）。

        顺序：建 escrow 行（UNFUNDED）→ 建独立托管账户 → `escrow_fund` 过账 → CAS 置 FUNDED。
        余额不足时 `LedgerService` 的 CAS 会让整笔事务回滚 —— **不会留下"已发布但没锁资"的订单**。
        """
        existing = economy_repo.find_escrow_for_order(self.db, work_order_id=order_id)
        if existing is not None:
            if int(existing.amount) != int(amount):
                raise EscrowError("escrow_amount_mismatch", http_status=409)
            return existing

        payer_kind, payer_ref = economy_repo.actor_columns(payer)
        payee_kind, payee_ref = (
            economy_repo.actor_columns(payee) if payee is not None else (None, None)
        )
        data = {
            "work_order_id": order_id,
            "payer_actor_kind": payer_kind,
            "payer_actor_ref": payer_ref,
            "payee_actor_kind": payee_kind,
            "payee_actor_ref": payee_ref,
            "amount": int(amount),
            "currency": Currency.credit.value,
            "status": EscrowStatus.unfunded.value,
            "expires_at": expires_at,
            "metadata_json": dict(metadata or {}),
        }
        try:
            escrow = economy_repo.insert_escrow(self.db, **data)
        except IntegrityError:
            # 并发发布同一订单：唯一约束裁定赢家；输家回滚后复用既有 escrow
            self.db.rollback()
            winner = economy_repo.find_escrow_for_order(self.db, work_order_id=order_id)
            if winner is None:  # pragma: no cover - 冲突必然来自同订单
                raise
            return winner

        account = self._ensure_account(escrow=escrow)
        posting = LedgerService(self.db).escrow_fund(
            escrow_account_id=int(account.id),
            payer_account_id=self._actor_account_id(payer),
            amount=int(amount),
            reason=f"escrow:{order_id}",
            reference_type="escrow",
            reference_id=str(int(escrow.id)),
            idempotency_key=f"escrow_fund:{int(escrow.id)}",
            metadata={"work_order_id": order_id},
            commit=False,
        )
        if (
            economy_repo.transition_escrow(
                self.db,
                escrow_id=int(escrow.id),
                from_statuses=(EscrowStatus.unfunded.value,),
                to_status=EscrowStatus.funded,
                funded_transaction_id=int(posting.transaction.id),
                funded_at=utcnow(),
            )
            == 0
        ):  # pragma: no cover - UNFUNDED 是唯一初始态
            raise EscrowError("escrow_not_fundable")
        self.db.flush()
        self.db.expire_all()
        funded = self._require(int(escrow.id))
        if commit:
            self.db.commit()
        logger.info("escrow funded order=%s escrow=%s amount=%d", order_id, escrow.id, amount)
        return funded

    # ---------------------------------------------------------------- 放款 / 退款

    def release(
        self,
        escrow_id: int,
        *,
        payee: EconomicActor,
        commit: bool = False,
    ) -> tuple[Escrow, bool]:
        """放款给收款人（`FUNDED → RELEASED`）。返回 `(escrow, created)`；重复调用幂等。"""
        escrow = self._require(escrow_id)
        if escrow.status == EscrowStatus.released.value:
            return escrow, False
        if escrow.status != EscrowStatus.funded.value:
            raise EscrowError(f"escrow_not_releasable:{escrow.status}")

        # CAS 先占位：与 refund 竞争时只有一个赢家（§33）
        if (
            economy_repo.transition_escrow(
                self.db,
                escrow_id=escrow_id,
                from_statuses=(EscrowStatus.funded.value,),
                to_status=EscrowStatus.released,
                released_at=utcnow(),
                payee_actor_kind=economy_repo.actor_columns(payee)[0],
                payee_actor_ref=economy_repo.actor_columns(payee)[1],
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(escrow_id)
            if current.status == EscrowStatus.released.value:
                return current, False
            raise EscrowError(f"escrow_not_releasable:{current.status}")

        account = self._account(escrow)
        posting = LedgerService(self.db).escrow_release(
            escrow_account_id=int(account.id),
            payee_account_id=self._actor_account_id(payee),
            amount=int(escrow.amount),
            reason=f"escrow_release:{escrow.work_order_id}",
            reference_type="escrow",
            reference_id=str(escrow_id),
            idempotency_key=f"escrow_release:{escrow_id}",
            commit=False,
        )
        escrow.released_transaction_id = int(posting.transaction.id)
        self.db.flush()
        self.db.expire_all()
        released = self._require(escrow_id)
        if commit:
            self.db.commit()
        logger.info("escrow released escrow=%s amount=%d", escrow_id, escrow.amount)
        return released, True

    def refund(self, escrow_id: int, *, commit: bool = False) -> tuple[Escrow, bool]:
        """退回原出资人（`FUNDED → REFUNDED`）。返回 `(escrow, created)`；重复调用幂等。"""
        escrow = self._require(escrow_id)
        if escrow.status == EscrowStatus.refunded.value:
            return escrow, False
        if escrow.status != EscrowStatus.funded.value:
            raise EscrowError(f"escrow_not_refundable:{escrow.status}")

        if (
            economy_repo.transition_escrow(
                self.db,
                escrow_id=escrow_id,
                from_statuses=(EscrowStatus.funded.value,),
                to_status=EscrowStatus.refunded,
                refunded_at=utcnow(),
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(escrow_id)
            if current.status == EscrowStatus.refunded.value:
                return current, False
            raise EscrowError(f"escrow_not_refundable:{current.status}")

        account = self._account(escrow)
        payer = self._payer(escrow)
        posting = LedgerService(self.db).escrow_refund(
            escrow_account_id=int(account.id),
            payer_account_id=self._actor_account_id(payer),
            amount=int(escrow.amount),
            reason=f"escrow_refund:{escrow.work_order_id}",
            reference_type="escrow",
            reference_id=str(escrow_id),
            idempotency_key=f"escrow_refund:{escrow_id}",
            commit=False,
        )
        escrow.refunded_transaction_id = int(posting.transaction.id)
        self.db.flush()
        self.db.expire_all()
        refunded = self._require(escrow_id)
        if commit:
            self.db.commit()
        logger.info("escrow refunded escrow=%s amount=%d", escrow_id, escrow.amount)
        return refunded, True

    # ---------------------------------------------------------------- 过期

    def expire(self, *, now: datetime | None = None, commit: bool = True) -> int:
        """把过期未处理的托管退款（`FUNDED → REFUNDED` + 订单由调用方/扫描一起推进）。"""
        moment = now or utcnow()
        expired = 0
        for escrow in economy_repo.list_escrows(self.db, statuses=(EscrowStatus.funded.value,)):
            expires_at = as_utc(escrow.expires_at)
            if expires_at is None or expires_at >= moment:
                continue
            _, created = self.refund(int(escrow.id), commit=False)
            if created:
                expired += 1
        if commit and expired:
            self.db.commit()
        return expired

    # ---------------------------------------------------------------- 读面

    def get_for_order(self, order_id: int) -> Escrow | None:
        return economy_repo.find_escrow_for_order(self.db, work_order_id=order_id)

    def view_for_order(self, order_id: int) -> EscrowView | None:
        escrow = self.get_for_order(order_id)
        if escrow is None:
            return None
        return self.view(escrow)

    def view(self, escrow: Escrow) -> EscrowView:
        balance = 0
        if escrow.escrow_account_id is not None:
            account = economy_repo.get_account(self.db, int(escrow.escrow_account_id))
            if account is not None:
                balance = LedgerService(self.db).ledger_balance(int(account.id))
        return EscrowView(
            escrow_id=int(escrow.id),
            work_order_id=int(escrow.work_order_id) if escrow.work_order_id else None,
            status=escrow.status,
            amount=int(escrow.amount),
            currency=escrow.currency,
            payer_actor_kind=escrow.payer_actor_kind,
            payer_actor_ref=int(escrow.payer_actor_ref),
            payee_actor_kind=escrow.payee_actor_kind,
            payee_actor_ref=int(escrow.payee_actor_ref) if escrow.payee_actor_ref else None,
            escrow_account_id=int(escrow.escrow_account_id) if escrow.escrow_account_id else None,
            account_balance=balance,
        )

    # ---------------------------------------------------------------- 内部

    def _ensure_account(self, *, escrow: Escrow) -> LedgerAccount:
        account = AccountService(self.db).ensure_escrow_account(escrow_id=int(escrow.id))
        if escrow.escrow_account_id is None:
            escrow.escrow_account_id = int(account.id)
            self.db.flush()
        return account

    def _account(self, escrow: Escrow) -> LedgerAccount:
        if escrow.escrow_account_id is None:  # pragma: no cover - FUNDED 必然有账户
            raise EscrowError("escrow_account_missing")
        account = economy_repo.get_account(self.db, int(escrow.escrow_account_id))
        if account is None:  # pragma: no cover - 账户不可删除（M1.1 §10）
            raise EscrowError("escrow_account_missing")
        return account

    def _payer(self, escrow: Escrow) -> EconomicActor:
        return EconomicActor(
            kind=EconomicActorKind(escrow.payer_actor_kind), ref=int(escrow.payer_actor_ref)
        )

    def _actor_account_id(self, actor: EconomicActor) -> int:
        return int(AccountService(self.db).ensure_account(actor).id)

    def _require(self, escrow_id: int) -> Escrow:
        escrow = economy_repo.get_escrow(self.db, escrow_id)
        if escrow is None:
            raise EscrowError("escrow_not_found", http_status=404)
        return escrow
