"""M1 经济域（Economy / Finance / Contract）—— 契约与政策层。

docs/m1-economy-design.md：钱不能凭空从普通业务代码产生；所有资金变化经 Ledger；
Wallet 不是账务真相；普通玩家不能 Mint；玩家之间交易与 Escrow 不改变 Total Supply。

- `contracts`：金额/主体/账户引用/过账腿与守恒校验、供给与余额派生公式、状态机迁移表（M1.0 冻结）；
- `policy`：金额/费用/比例的**配置化**封装（`EconomicPolicy` + 版本）。

M1.1 起在此包下实现 LedgerService / AccountService，以及后续 MonetaryAuthority / RewardService /
WorkOrder / Contract / Escrow / Settlement —— 契约先行，
实现按 m1-implementation-plan.md 逐阶段落地。
"""

from app.economy.contracts import (
    MAX_AMOUNT,
    REQUIRES_FUNDS_KINDS,
    SYSTEM_ACCOUNT_KINDS,
    AccountRef,
    EconomicActor,
    EconomyContractError,
    LegSpec,
    PostingLeg,
    StateMachine,
    assert_balanced_totals,
    assert_transition,
    available_balance,
    balance_delta,
    balance_from_totals,
    can_transition,
    circulating_supply,
    is_balanced,
    normal_side,
    parse_currency,
    posting_totals,
    requires_funds,
    split_fee,
    supply_effect,
    total_supply,
    validate_amount,
    validate_posting,
)
from app.economy.policy import ECONOMIC_POLICY_VERSION, EconomicPolicy, economic_policy

__all__ = [
    "ECONOMIC_POLICY_VERSION",
    "MAX_AMOUNT",
    "REQUIRES_FUNDS_KINDS",
    "SYSTEM_ACCOUNT_KINDS",
    "AccountRef",
    "EconomicActor",
    "EconomicPolicy",
    "EconomyContractError",
    "LegSpec",
    "PostingLeg",
    "StateMachine",
    "assert_balanced_totals",
    "assert_transition",
    "available_balance",
    "balance_delta",
    "balance_from_totals",
    "can_transition",
    "circulating_supply",
    "economic_policy",
    "is_balanced",
    "normal_side",
    "parse_currency",
    "posting_totals",
    "requires_funds",
    "split_fee",
    "supply_effect",
    "total_supply",
    "validate_amount",
    "validate_posting",
]
