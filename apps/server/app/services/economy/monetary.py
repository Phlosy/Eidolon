"""MonetaryAuthority —— 唯一拥有发行/销毁权限的主体（M1.1b，设计 §8，E4/E5/E23）。

```
MonetaryAuthority
  ├── mint(actor, amount, reason, reference)      → 发行（Starter / Reward / 官方结算）
  ├── burn(actor, amount, reason, reference)      → 销毁（Sink 的 Burn 部分）
  └── treasury_transfer(actor, amount, reference) → 流入 Treasury（Sink 的财政部分）
```

- 本类是**内部能力**：没有 router、没有 HTTP 端点（E23 三层边界）；玩家/公司路径永远
  只能 `LedgerService.transfer`（不改变 Total Supply）；
- 令牌来自 `authority.py`，只被本模块获取：`mint` / `burn` / `treasury_transfer` 的腿
  在 Posting Core 里额外校验令牌，所以"绕过 MonetaryAuthority 铸币"在调用点就会失败；
- M1.2 起由 `RewardService` 等经此铸币；M1.1 只提供能力与测试锚点。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.economy.contracts import EconomicActor
from app.models.economy import LedgerAccount
from app.models.enums import Currency, LedgerAccountKind, SystemAccountKind, TransactionKind
from app.services.economy.authority import AUTHORITY_TOKEN
from app.services.economy.ledger import (
    LedgerService,
    Posting,
    PostingResult,
    SupplySnapshot,
    blueprint_entries,
)


class MonetaryAuthority:
    """发行/销毁/财政划转（唯一铸币入口）。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.ledger = LedgerService(db)

    def ensure_system_accounts(self) -> dict[LedgerAccountKind, LedgerAccount]:
        """系统账户 bootstrap（幂等）—— 发行/销毁/财政必须存在才能记账。"""
        return self.ledger.accounts.ensure_system_accounts()

    # ---------------------------------------------------------------- 发行

    def mint(
        self,
        *,
        actor: EconomicActor,
        amount: int,
        reason: str,
        reference_type: str = "",
        reference_id: str = "",
        idempotency_key: str | None = None,
        currency: Currency = Currency.credit,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> PostingResult:
        """发行（Total Supply 增加，E5 反向：只有本方法能增加）。

        `reason` + `reference` 必填：官方发行必须可审计（E9/E16）。
        """
        if not reason:
            raise ValueError("mint requires a reason（E9：发行必须可审计）")
        issuance = self.ensure_system_accounts()[LedgerAccountKind.issuance]
        beneficiary = self.ledger.accounts.ensure_account(actor, currency=currency)
        posting = Posting(
            transaction_type=TransactionKind.mint,
            entries=blueprint_entries(
                TransactionKind.mint,
                {
                    "beneficiary": (int(beneficiary.id), amount),
                    "issuance": (int(issuance.id), amount),
                },
            ),
            currency=currency,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            initiated_by=EconomicActor.system(SystemAccountKind.issuance),
            metadata=dict(metadata or {}),
        )
        return self.ledger.post(posting, authority=AUTHORITY_TOKEN, commit=commit)

    # ---------------------------------------------------------------- 回收

    def burn(
        self,
        *,
        actor: EconomicActor,
        amount: int,
        reason: str,
        reference_type: str = "",
        reference_id: str = "",
        idempotency_key: str | None = None,
        currency: Currency = Currency.credit,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> PostingResult:
        """销毁（Total Supply 减少；payer 必须是真实持有资金的账户，受 CAS 约束）。"""
        if not reason:
            raise ValueError("burn requires a reason（E9）")
        burn_account = self.ensure_system_accounts()[LedgerAccountKind.burn]
        payer = self.ledger.accounts.ensure_account(actor, currency=currency)
        posting = Posting(
            transaction_type=TransactionKind.burn,
            entries=blueprint_entries(
                TransactionKind.burn,
                {
                    "burn": (int(burn_account.id), amount),
                    "payer": (int(payer.id), amount),
                },
            ),
            currency=currency,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            initiated_by=EconomicActor.system(SystemAccountKind.burn),
            metadata=dict(metadata or {}),
        )
        return self.ledger.post(posting, authority=AUTHORITY_TOKEN, commit=commit)

    def treasury_transfer(
        self,
        *,
        actor: EconomicActor,
        amount: int,
        reason: str,
        reference_type: str = "",
        reference_id: str = "",
        idempotency_key: str | None = None,
        currency: Currency = Currency.credit,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> PostingResult:
        """财政划转（手续费等的财政部分；Total Supply 不变，资金进入 Treasury）。"""
        if not reason:
            raise ValueError("treasury_transfer requires a reason（E9）")
        treasury = self.ensure_system_accounts()[LedgerAccountKind.treasury]
        payer = self.ledger.accounts.ensure_account(actor, currency=currency)
        posting = Posting(
            transaction_type=TransactionKind.treasury_transfer,
            entries=blueprint_entries(
                TransactionKind.treasury_transfer,
                {
                    "treasury": (int(treasury.id), amount),
                    "payer": (int(payer.id), amount),
                },
            ),
            currency=currency,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            initiated_by=EconomicActor.system(SystemAccountKind.treasury),
            metadata=dict(metadata or {}),
        )
        return self.ledger.post(posting, authority=AUTHORITY_TOKEN, commit=commit)

    # ---------------------------------------------------------------- 观测

    def supply(self) -> SupplySnapshot:
        return self.ledger.supply()
