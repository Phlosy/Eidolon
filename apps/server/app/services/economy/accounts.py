"""AccountService —— 账户生命周期（M1.1b，设计 §10）。

职责边界：**只管账户，不管记账**（记账在 `ledger.py` 的唯一 Posting Core）。
- 开户幂等：唯一约束 + `ON CONFLICT DO NOTHING` + 回查（并发/重试不会产生第二行）；
- 系统账户 bootstrap 幂等：`ensure_system_accounts()` 可在启动/首次使用时反复调用；
- 冻结/解冻/关闭：`frozen` 与 `closed` 一律拒绝过账（由 LedgerService 校验），
  **不提供删除**（历史账本要能解释每一分钱，设计 §10）。

账户身份由 `(actor_kind, actor_ref, currency, kind, subject_ref)` 决定（§9）：
不写死 `company_id`，未来新增 actor 类型不需要改表。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.economy.contracts import (
    SYSTEM_ACCOUNT_KINDS,
    AccountRef,
    EconomicActor,
    EconomyContractError,
    normal_side,
)
from app.models.economy import LedgerAccount
from app.models.enums import Currency, LedgerAccountKind, LedgerAccountStatus, SystemAccountKind
from app.repositories import economy as economy_repo


class AccountError(RuntimeError):
    """账户领域错误（reason code 机器可读；API 层转 HTTP）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


class AccountService:
    """账户开户 / 查询 / 状态管理（不含任何余额修改入口）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------------------------------------------------------- 开户

    def ensure_account(
        self,
        actor: EconomicActor,
        *,
        kind: LedgerAccountKind = LedgerAccountKind.actor,
        currency: Currency = Currency.credit,
        subject_ref: int = 0,
    ) -> LedgerAccount:
        """幂等开户。非法组合（公司持有系统账户等）由 `AccountRef` 契约直接拦下。"""
        AccountRef(actor=actor, currency=currency, kind=kind)  # 契约校验（§9/§10）
        actor_kind, actor_ref = economy_repo.actor_columns(actor)
        return economy_repo.insert_account(
            self.db,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            currency=currency.value,
            kind=kind.value,
            subject_ref=int(subject_ref),
            normal_side=normal_side(kind).value,
        )

    def ensure_escrow_account(
        self, *, escrow_id: int, currency: Currency = Currency.credit
    ) -> LedgerAccount:
        """每个 Escrow 一个独立托管账户（`subject_ref = escrow id`；§10/§23）。

        **刻意不设全局 SYSTEM_ESCROW 池**：托管必须逐笔可归属，否则 "reserved 属于谁"
        会退化成账本外的数字（E30）。
        """
        if int(escrow_id) <= 0:
            raise AccountError("escrow_id must be positive", http_status=422)
        return self.ensure_account(
            EconomicActor.system(SystemAccountKind.escrow),
            kind=LedgerAccountKind.escrow,
            currency=currency,
            subject_ref=int(escrow_id),
        )

    def ensure_system_accounts(self) -> dict[LedgerAccountKind, LedgerAccount]:
        """系统账户 bootstrap（发行 / 财政 / 销毁）—— 幂等，可反复调用（§8）。

        Escrow 账户不在其中：它是**逐笔**账户（见 `ensure_escrow_account`）。
        """
        return {
            kind: self.ensure_account(
                EconomicActor.system(SystemAccountKind(kind.value)),
                kind=kind,
                subject_ref=0,
            )
            for kind in sorted(SYSTEM_ACCOUNT_KINDS - {LedgerAccountKind.escrow}, key=str)
        }

    # ---------------------------------------------------------------- 查询

    def get(self, account_id: int) -> LedgerAccount | None:
        return economy_repo.get_account(self.db, account_id)

    def require(self, account_id: int) -> LedgerAccount:
        account = self.get(account_id)
        if account is None:
            raise AccountError("account_not_found", http_status=404)
        return account

    def find(
        self,
        actor: EconomicActor,
        *,
        kind: LedgerAccountKind = LedgerAccountKind.actor,
        currency: Currency = Currency.credit,
        subject_ref: int = 0,
    ) -> LedgerAccount | None:
        actor_kind, actor_ref = economy_repo.actor_columns(actor)
        return economy_repo.find_account(
            self.db,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            currency=currency.value,
            kind=kind.value,
            subject_ref=int(subject_ref),
        )

    def accounts_for_actor(
        self,
        actor: EconomicActor,
        *,
        currency: Currency | None = None,
        kinds: tuple[LedgerAccountKind, ...] | None = None,
    ) -> list[LedgerAccount]:
        actor_kind, actor_ref = economy_repo.actor_columns(actor)
        return economy_repo.list_accounts_for_actor(
            self.db,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            currency=currency.value if currency is not None else None,
            kinds=tuple(kind.value for kind in kinds) if kinds is not None else None,
        )

    # ---------------------------------------------------------------- 状态

    def _set_status(
        self, account_id: int, status: LedgerAccountStatus, *, reason: str = ""
    ) -> LedgerAccount:
        account = self.require(account_id)
        if account.status == LedgerAccountStatus.closed.value:
            raise AccountError("account_closed")
        try:
            return economy_repo.set_account_status(
                self.db, account_id=account_id, status=status, reason=reason
            )
        except EconomyContractError as exc:  # pragma: no cover - require() 已兜住
            raise AccountError("account_not_found", http_status=404) from exc

    def freeze(self, account_id: int, *, reason: str = "") -> LedgerAccount:
        """冻结：拒绝**任何**过账（含收款），直到解冻。"""
        return self._set_status(
            account_id, LedgerAccountStatus.frozen, reason=reason or "frozen_by_operator"
        )

    def unfreeze(self, account_id: int) -> LedgerAccount:
        account = self.require(account_id)
        if account.status != LedgerAccountStatus.frozen.value:
            raise AccountError("account_not_frozen")
        return economy_repo.set_account_status(
            self.db, account_id=account_id, status=LedgerAccountStatus.active
        )

    def close(self, account_id: int) -> LedgerAccount:
        """关闭（终态）：账户保留在账本里，但永不再过账。"""
        return self._set_status(account_id, LedgerAccountStatus.closed)
