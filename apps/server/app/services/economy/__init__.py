"""M1 经济域服务层（M1.1 起）：账户 / 账本 / 投影 / 发行。

- `accounts.AccountService`：开户、状态、查询（**不含余额修改**）；
- `ledger.LedgerService`：唯一 Posting Core（`post()`）+ transfer/escrow 便利原语 + 供给快照；
- `monetary.MonetaryAuthority`：唯一 mint/burn/treasury_transfer（内部能力，无 HTTP 端点）；
- `projection`：`rebuild_wallet_projection` / `verify_wallet_projection`；
- `balances`：余额的唯一计算口径（post 的增量、重建、对账共用）。

契约层在 `app.economy`（枚举/腿蓝图/守恒/政策），本包只做编排与持久化协作。
"""

from app.services.economy.accounts import AccountError, AccountService
from app.services.economy.balances import DerivedWallet, derive_wallets
from app.services.economy.evaluations import (
    EvaluationError,
    EvaluationOutcome,
    EvaluationService,
)
from app.services.economy.ledger import (
    AUTHORITY_KINDS,
    AuthorityRequired,
    IdempotencyConflict,
    InsufficientFunds,
    LedgerError,
    LedgerService,
    Posting,
    PostingEntry,
    PostingRejected,
    PostingResult,
    SupplySnapshot,
    blueprint_entries,
)
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import (
    ProjectionCheck,
    ProjectionDrift,
    rebuild_wallet_projection,
    verify_wallet_projection,
)
from app.services.economy.rewards import (
    ACHIEVEMENT_CODES,
    OFFICIAL_KINDS,
    SELF_SERVICE_KINDS,
    RewardError,
    RewardEvaluation,
    RewardService,
)
from app.services.economy.settlement import (
    SettlementError,
    SettlementRequest,
    SettlementResult,
    SettlementService,
)
from app.services.economy.work_orders import (
    OFFICIAL_REWARD_TYPES,
    PublishResult,
    WorkOrderError,
    WorkOrderService,
)

__all__ = [
    "AUTHORITY_KINDS",
    "AccountError",
    "AccountService",
    "AuthorityRequired",
    "DerivedWallet",
    "EvaluationError",
    "EvaluationOutcome",
    "EvaluationService",
    "OFFICIAL_KINDS",
    "OFFICIAL_REWARD_TYPES",
    "PublishResult",
    "SettlementError",
    "SettlementRequest",
    "SettlementResult",
    "SettlementService",
    "WorkOrderError",
    "WorkOrderService",
    "IdempotencyConflict",
    "InsufficientFunds",
    "LedgerError",
    "LedgerService",
    "MonetaryAuthority",
    "Posting",
    "PostingEntry",
    "PostingRejected",
    "PostingResult",
    "ACHIEVEMENT_CODES",
    "ProjectionCheck",
    "ProjectionDrift",
    "RewardError",
    "RewardEvaluation",
    "RewardService",
    "SELF_SERVICE_KINDS",
    "SupplySnapshot",
    "blueprint_entries",
    "derive_wallets",
    "rebuild_wallet_projection",
    "verify_wallet_projection",
]
