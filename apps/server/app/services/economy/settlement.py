"""SettlementService —— **所有资金终局的唯一入口**（M1.3 首个使用者：官方工作市场）。

设计 §24：

```
settlement_key (unique)      — 幂等锚点（E12）：直接用作 ledger `idempotency_key`
  ↓
校验业务条件（订单/合同/escrow 状态、Evaluation 结果、双方）  ← 由业务服务负责
  ↓
执行 Ledger Transaction（官方=system_mint；玩家=escrow_release，M1.4 接入，接口不变）
  ↓
业务状态更新（SETTLED + settlement_transaction_id）            ← 由业务服务负责
  ↓
事件
```

**纪律**：
- 结算不直接改余额、不自己写 Ledger —— 一律经 `MonetaryAuthority`/`LedgerService.post()`（E1/E27）；
- **幂等**：同 `settlement_key` 重复调用返回既有交易（`created=False`），绝不重复发钱（E12）；
- **原子**：结算与业务状态由调用方放在同一事务里（E13/E14/E15）——
  `WorkOrderService.settle()` 正是这样用的（先落 grant 行 → 结算 → 订单 SETTLED，一起提交）。

M1.3 只实现 `system_mint`（官方发行）；`player_escrow` / `npc_treasury` 在 M1.4/M1.8 接入
（届时只换腿组合，签名与幂等语义不变）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor
from app.models.enums import Currency, FundingMode
from app.repositories import economy as economy_repo
from app.services.economy.ledger import LedgerTransaction, PostingResult
from app.services.economy.monetary import MonetaryAuthority

logger = get_logger(__name__)


class SettlementError(RuntimeError):
    """结算领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class SettlementRequest:
    """一次结算请求（金额 + 受益方 + 业务锚点）。"""

    settlement_key: str
    amount: int
    beneficiary: EconomicActor
    reason: str
    reference_type: str
    reference_id: str
    funding_mode: FundingMode = FundingMode.system_mint
    metadata: dict = field(default_factory=dict)
    currency: Currency = Currency.credit
    #: `player_escrow` 必须给出托管 id（钱从哪一笔 Escrow 放出来）
    escrow_id: int | None = None


@dataclass(frozen=True)
class SettlementResult:
    """结算结果。`created=False` = 命中幂等键、复用既有交易（没有再次发钱）。"""

    settlement_key: str
    transaction: LedgerTransaction
    result: PostingResult
    created: bool


class SettlementService:
    """终局资金入口（v1：官方发行）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _settle_from_escrow(self, request: SettlementRequest, *, commit: bool) -> SettlementResult:
        """玩家托管放款（`player_escrow`）：钱从该笔 Escrow 账户转给收款人，**不 mint**。"""
        from app.services.economy.escrow import EscrowError, EscrowService

        if request.escrow_id is None:
            raise SettlementError("escrow_id_required", http_status=422)
        service = EscrowService(self.db)
        try:
            released, created = service.release(
                int(request.escrow_id), payee=request.beneficiary, commit=False
            )
        except EscrowError as exc:
            raise SettlementError(exc.reason, http_status=exc.http_status) from exc
        if int(released.amount) != int(request.amount):
            raise SettlementError("escrow_amount_mismatch")
        transaction_id = released.released_transaction_id
        if transaction_id is None:  # pragma: no cover - RELEASED 必然有交易
            raise SettlementError("escrow_release_transaction_missing")
        transaction = economy_repo.get_transaction(self.db, int(transaction_id))
        if transaction is None:  # pragma: no cover
            raise SettlementError("escrow_release_transaction_missing")
        if commit:
            self.db.commit()
        logger.info(
            "settlement player_escrow key=%s escrow=%s amount=%d created=%s",
            request.settlement_key,
            request.escrow_id,
            request.amount,
            created,
        )
        return SettlementResult(
            settlement_key=request.settlement_key,
            transaction=transaction,
            result=PostingResult(
                transaction=transaction,
                entries=tuple(
                    economy_repo.list_entries(self.db, transaction_id=int(transaction.id))
                ),
                created=created,
            ),
            created=created,
        )

    def settle(self, request: SettlementRequest, *, commit: bool = False) -> SettlementResult:
        """执行结算。

        `commit=False`（默认）：把提交权交给业务服务——`WorkOrderService.settle()` 把
        "奖励审计行 + 订单状态 + 结算交易"放在同一事务里提交（E13/E14/E15）；
        独立调用方（CLI/运维脚本/测试）传 `commit=True` 即可自持事务。
        """
        if not request.settlement_key:
            raise SettlementError("settlement_key_required", http_status=422)
        if request.funding_mode is FundingMode.player_escrow:
            # 玩家之间的钱：**放款**而不是发行（E8）——Supply 不变（E7）
            return self._settle_from_escrow(request, commit=commit)
        if request.funding_mode is not FundingMode.system_mint:
            # npc_treasury 在 M1.8 接入；现在明确拒绝，而不是"退化成 mint"
            # （那会凭空印钱，E8）。
            raise SettlementError(
                f"funding_mode_not_supported:{request.funding_mode.value}", http_status=409
            )
        try:
            posting = MonetaryAuthority(self.db).mint(
                actor=request.beneficiary,
                amount=request.amount,
                reason=request.reason,
                reference_type=request.reference_type,
                reference_id=request.reference_id,
                idempotency_key=request.settlement_key,
                currency=request.currency,
                metadata={"settlement_key": request.settlement_key, **request.metadata},
                commit=False,
            )
        except IntegrityError:  # pragma: no cover - 账本层已兜底（唯一索引 + 复用赢家）
            self.db.rollback()
            raise SettlementError("settlement_conflict") from None
        created = bool(posting.created)
        if commit:
            self.db.commit()
        logger.info(
            "settlement %s key=%s amount=%d created=%s",
            request.reference_type,
            request.settlement_key,
            request.amount,
            created,
        )
        return SettlementResult(
            settlement_key=request.settlement_key,
            transaction=posting.transaction,
            result=posting,
            created=created,
        )
