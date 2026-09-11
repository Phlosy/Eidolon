"""公司经营成本（M1.5，设计 §7/§25/§26）—— 首批三项 **Sink** 的统一入口。

| 成本 | 去向 | 计量 | 触发点 |
| --- | --- | --- | --- |
| 算力（Agent 运行时） | Treasury（全额） | units × `compute_credit_per_unit` | `_finalize()` |
| 培养（T1 培养资源消耗） | Treasury（全额） | sessions × 培养单价 | `cultivation.completed` |
| 市场手续费（挂牌/结算） | 按 treasury/burn 比例拆 | `market_fee_bps` 基点 × 金额 | 玩家订单发布 |

（口径细节见 §26：1 compute unit = 1 分钟 Agent 运行时；v1 不映射真实 token 价格。）

**纪律**：
- 全部经 `MonetaryAuthority.treasury_transfer` / `burn` → `LedgerService.post()`（E1/E27）；
- 成本**永不 mint**（Sink 只回收，不发行）；手续费的 burn 腿按设计减少 Total Supply（E5）；
- **余额不足的语义 = 欠费**（`unpaid`）：计量照记（事实），扣款尽力而为，**绝不产生负余额**（E24）。
  v1 不做"停服/催收"策略（M1.9 决策）；`unpaid` 在报表里单独显示，而不是当成免费；
- 幂等：每个成本项都有稳定 `idempotency_key`（`compute:<session>` / `training:<profile>` /
  `fee:<order>`），重放不会重复扣款（E12 同族）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor, split_fee
from app.economy.policy import EconomicPolicy, economic_policy
from app.models.base import utcnow
from app.models.economy import ComputeUsage
from app.models.enums import Currency, EconomicActorKind, EconomicCategory
from app.repositories import economy as economy_repo
from app.services.economy.ledger import InsufficientFunds, PostingRejected
from app.services.economy.monetary import MonetaryAuthority

logger = get_logger(__name__)

#: 算力计量口径：1 compute unit = 1 分钟 Agent 运行时长（§26：先做内部 Compute Unit）
SECONDS_PER_COMPUTE_UNIT = 60

PAID = "paid"
UNPAID = "unpaid"


class CostError(RuntimeError):
    """成本领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class CostCharge:
    """一次 Sink 扣款的结果。"""

    category: EconomicCategory
    amount: int
    treasury_amount: int
    burn_amount: int
    treasury_transaction_id: int | None
    burn_transaction_id: int | None
    paid: bool
    reason: str = ""
    created: bool = True


@dataclass(frozen=True)
class ComputeCharge:
    """一次算力计量 + 扣款的结果。"""

    usage: ComputeUsage
    charge: CostCharge
    created: bool
    amount: int


class CompanyCostService:
    """公司经营成本的统一扣款原语（Sink 侧）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()

    def charge(
        self,
        *,
        actor: EconomicActor,
        amount: int,
        category: EconomicCategory,
        reason: str,
        reference_type: str,
        reference_id: str,
        idempotency_key: str,
        burn_amount: int = 0,
        metadata: dict | None = None,
        commit: bool = False,
    ) -> CostCharge:
        """扣一笔成本：`treasury_amount = amount − burn_amount` → Treasury，其余 → Burn。

        - 金额必须为正整数（`amount <= 0` 时直接返回"零成本"，不产生交易）；
        - `burn_amount` 由调用方按政策拆分（手续费 `split_fee`），且 `0 <= burn <= amount`；
        - **余额不足 ⇒ `paid=False, reason="insufficient_funds"`**：不抛异常、不留半笔账
          （账本层 CAS 已保证失败即回滚该笔交易）；调用方据此记录欠费（`unpaid`）。
        """
        if amount < 0:
            raise CostError("amount_must_not_be_negative", http_status=422)
        if burn_amount < 0 or burn_amount > amount:
            raise CostError("burn_amount_out_of_range", http_status=422)
        if amount == 0:
            return CostCharge(
                category=category,
                amount=0,
                treasury_amount=0,
                burn_amount=0,
                treasury_transaction_id=None,
                burn_transaction_id=None,
                paid=True,
                reason="zero_amount",
                created=False,
            )
        if actor.kind is EconomicActorKind.system:  # pragma: no cover - 防御：系统主体不交成本
            raise CostError("system_actor_cannot_pay_cost", http_status=422)

        treasury_amount = amount - burn_amount
        authority = MonetaryAuthority(self.db)
        treasury_transaction_id: int | None = None
        burn_transaction_id: int | None = None
        created = True
        try:
            # **SAVEPOINT**：成本是"尽力而为"的子操作 —— 失败只能回滚它自己，
            # 绝不能把调用方已经写在同一个事务里的东西（订单、托管、计量行）一起带走，
            # 也不能留下"账本有腿、投影没动"的半笔账（实测踩到过：失败后残留的
            # 未提交腿被调用方的 commit 一起提交，复式守恒被破坏）。
            with self.db.begin_nested():
                if treasury_amount > 0:
                    posting = authority.treasury_transfer(
                        actor=actor,
                        amount=treasury_amount,
                        reason=reason,
                        reference_type=reference_type,
                        reference_id=reference_id,
                        idempotency_key=f"{idempotency_key}:treasury",
                        category=category,
                        metadata=dict(metadata or {}),
                        commit=False,
                    )
                    treasury_transaction_id = int(posting.transaction.id)
                    created = created and bool(posting.created)
                if burn_amount > 0:
                    posting = authority.burn(
                        actor=actor,
                        amount=burn_amount,
                        reason=reason,
                        reference_type=reference_type,
                        reference_id=reference_id,
                        idempotency_key=f"{idempotency_key}:burn",
                        category=category,
                        metadata=dict(metadata or {}),
                        commit=False,
                    )
                    burn_transaction_id = int(posting.transaction.id)
                    created = created and bool(posting.created)
        except (InsufficientFunds, PostingRejected) as exc:
            # 余额不足/账户不可用：savepoint 已回滚这笔成本；外层事务继续
            # （计量事实照记，扣款记欠费）—— 交给调用方决定怎么表达
            reason_code = getattr(exc, "reason", "insufficient_funds")
            logger.info(
                "cost unpaid category=%s amount=%d reason=%s ref=%s",
                category.value,
                amount,
                reason_code,
                reference_id,
            )
            return CostCharge(
                category=category,
                amount=amount,
                treasury_amount=0,
                burn_amount=0,
                treasury_transaction_id=None,
                burn_transaction_id=None,
                paid=False,
                reason=reason_code,
                created=False,
            )

        if commit:
            self.db.commit()
        return CostCharge(
            category=category,
            amount=amount,
            treasury_amount=treasury_amount,
            burn_amount=burn_amount,
            treasury_transaction_id=treasury_transaction_id,
            burn_transaction_id=burn_transaction_id,
            paid=True,
            created=created,
        )


class ComputeCostService:
    """算力计量 + 计价 + 扣款（§26：最重要的持续型 Sink）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()
        self.costs = CompanyCostService(db, policy=self.policy)

    # ---------------------------------------------------------------- 计量入口

    def record_usage(
        self,
        *,
        company_id: int,
        units: int,
        employee_id: int | None = None,
        work_session_id: int | None = None,
        runtime_instance_id: int | None = None,
        provider_id: int | None = None,
        model: str = "",
        tokens: int = 0,
        duration_seconds: float | None = None,
        idempotency_key: str,
        occurred_at: datetime | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> ComputeCharge:
        """记录一次算力消耗并尽力扣款（幂等：同 key 返回既有记录，不再扣一次）。"""
        existing = economy_repo.find_compute_usage_by_key(self.db, idempotency_key)
        if existing is not None:
            return ComputeCharge(
                usage=existing,
                charge=self._charge_view(existing),
                created=False,
                amount=int(existing.amount),
            )

        resolved_units = int(units)
        if resolved_units <= 0:
            raise CostError("compute_units_must_be_positive", http_status=422)
        unit_price = int(self.policy.compute_credit_per_unit)
        amount = resolved_units * unit_price

        try:
            usage = economy_repo.insert_compute_usage(
                self.db,
                company_id=int(company_id),
                employee_id=employee_id,
                work_session_id=work_session_id,
                runtime_instance_id=runtime_instance_id,
                provider_id=provider_id,
                model=model,
                units=resolved_units,
                unit_price=unit_price,
                amount=amount,
                currency=Currency.credit.value,
                tokens=int(tokens),
                duration_seconds=duration_seconds,
                status=UNPAID,  # 先记欠费；扣款成功再置 paid（钱与状态同一事务）
                unpaid_reason="pending_charge",
                idempotency_key=idempotency_key,
                occurred_at=occurred_at or utcnow(),
                metadata_json=dict(metadata or {}),
            )
        except IntegrityError:
            # 并发同 key：唯一约束裁定赢家，输家回滚后复用（不重复扣款）
            self.db.rollback()
            winner = economy_repo.find_compute_usage_by_key(self.db, idempotency_key)
            if winner is None:  # pragma: no cover
                raise
            return ComputeCharge(
                usage=winner,
                charge=self._charge_view(winner),
                created=False,
                amount=int(winner.amount),
            )

        charge = self.costs.charge(
            actor=EconomicActor.company(int(company_id)),
            amount=amount,
            category=EconomicCategory.compute,
            reason=f"compute:{model or 'runtime'}",
            reference_type="compute_usage",
            reference_id=str(int(usage.id)),
            idempotency_key=f"compute:{idempotency_key}",
            metadata={
                "units": resolved_units,
                "unit_price": unit_price,
                "work_session_id": work_session_id,
                "model": model,
            },
            commit=False,
        )
        usage.status = PAID if charge.paid else UNPAID
        usage.unpaid_reason = "" if charge.paid else charge.reason
        usage.treasury_transaction_id = charge.treasury_transaction_id
        usage.burn_transaction_id = charge.burn_transaction_id
        self.db.flush()
        if commit:
            self.db.commit()
        return ComputeCharge(usage=usage, charge=charge, created=True, amount=amount)

    def record_for_session(
        self,
        work_session,
        *,
        company_id: int | None = None,
        model: str = "",
        tokens: int = 0,
        commit: bool = False,
    ) -> ComputeCharge | None:
        """按 WorkSession 计量（幂等键 = `work_session:<id>`）。

        会话没有公司归属或时长为空时返回 `None`（不是错误：计量只对"可归属的事实"发生）。
        """
        resolved_company = company_id
        if resolved_company is None:  # pragma: no cover - 调用方已解析
            return None
        duration = _duration_seconds(getattr(work_session, "cost", None))
        units = max(1, math.ceil((duration or 0) / SECONDS_PER_COMPUTE_UNIT))
        return self.record_usage(
            company_id=int(resolved_company),
            units=units,
            employee_id=getattr(work_session, "employee_id", None),
            work_session_id=int(getattr(work_session, "id", 0)) or None,
            runtime_instance_id=getattr(work_session, "runtime_instance_id", None),
            provider_id=getattr(work_session, "provider_id", None),
            model=model,
            tokens=int(tokens),
            duration_seconds=duration,
            idempotency_key=f"work_session:{int(getattr(work_session, 'id', 0))}",
            metadata={"runtime_type": getattr(work_session, "runtime_type", "")},
            commit=commit,
        )

    # ---------------------------------------------------------------- 读面

    def totals(self, *, company_id: int) -> dict[str, int]:
        return economy_repo.compute_usage_totals(self.db, company_id=company_id)

    # ---------------------------------------------------------------- 内部

    def _charge_view(self, usage: ComputeUsage) -> CostCharge:
        paid = usage.status == PAID
        return CostCharge(
            category=EconomicCategory.compute,
            amount=int(usage.amount),
            treasury_amount=int(usage.amount) if paid else 0,
            burn_amount=0,
            treasury_transaction_id=usage.treasury_transaction_id,
            burn_transaction_id=usage.burn_transaction_id,
            paid=paid,
            reason=usage.unpaid_reason,
            created=False,
        )


@dataclass(frozen=True)
class FeeQuote:
    """一笔手续费的报价（整数守恒：`treasury + burn == fee`）。"""

    gross: int
    fee: int
    net: int
    treasury: int
    burn: int
    bps: int


class FeeService:
    """市场/合同手续费（§7：按 `treasury_ratio` / `burn_ratio` 拆分）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()
        self.costs = CompanyCostService(db, policy=self.policy)

    def quote(self, amount: int) -> FeeQuote:
        """报价：`fee = amount × bps / 10000`（向下取整），拆分用 M1.0 冻结的 `split_fee`。"""
        if amount < 0:
            raise CostError("amount_must_not_be_negative", http_status=422)
        fee = self.policy.fee_for(amount) if amount > 0 else 0
        treasury, burn = self.policy.split_fee(fee) if fee > 0 else (0, 0)
        assert treasury + burn == fee, "fee split must be conserving"
        return FeeQuote(
            gross=int(amount),
            fee=fee,
            net=int(amount) - fee,
            treasury=treasury,
            burn=burn,
            bps=self.policy.market_fee_bps,
        )

    def charge_listing_fee(
        self,
        *,
        actor: EconomicActor,
        gross: int,
        reference_type: str,
        reference_id: str,
        idempotency_key: str,
        commit: bool = False,
    ) -> tuple[FeeQuote, CostCharge]:
        """挂牌手续费（发布玩家订单时收取）：treasury + burn 两条腿，金额守恒。"""
        quote = self.quote(gross)
        charge = self.costs.charge(
            actor=actor,
            amount=quote.fee,
            burn_amount=quote.burn,
            category=EconomicCategory.market_fee,
            reason="market_fee:listing",
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            metadata={"gross": quote.gross, "bps": quote.bps},
            commit=commit,
        )
        return quote, charge

    def charge_contract_fee(
        self,
        *,
        actor: EconomicActor,
        gross: int,
        reference_type: str,
        reference_id: str,
        idempotency_key: str,
        commit: bool = False,
    ) -> tuple[FeeQuote, CostCharge]:
        """合同手续费（M1.6 的 Contract 结算调用；M1.5 先把通道就位）。"""
        quote = self.quote(gross)
        charge = self.costs.charge(
            actor=actor,
            amount=quote.fee,
            burn_amount=quote.burn,
            category=EconomicCategory.contract_fee,
            reason="contract_fee",
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            metadata={"gross": quote.gross, "bps": quote.bps},
            commit=commit,
        )
        return quote, charge

    def split(self, amount: int) -> tuple[int, int]:
        """纯函数包装（测试/调用方需要单独看拆分时用）。"""
        return split_fee(
            amount,
            treasury_ratio=self.policy.fee_treasury_ratio,
            burn_ratio=self.policy.fee_burn_ratio,
        )


def _duration_seconds(cost: object) -> float | None:
    if not isinstance(cost, dict):
        return None
    raw = cost.get("duration_sec")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):  # pragma: no cover - 脏数据不该炸掉计量
        return None


def compute_units_for_duration(duration_seconds: float | None) -> int:
    """时长 → compute units（至少 1：跑过就是消耗）。"""
    return max(1, math.ceil((duration_seconds or 0) / SECONDS_PER_COMPUTE_UNIT))
