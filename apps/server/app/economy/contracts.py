"""M1 经济域契约（M1.0 Economic Domain Contract Freeze）。

docs/m1-economy-design.md 的代码化：金额/主体/账户引用/过账腿与守恒校验、供给公式、
状态机迁移表。**本模块是纯契约层**——不碰 Session、不建表、不发事件（M1.1 起实现）。

冻结纪律（设计 §38，E1–E25）：
- 金额一律**整数最小单位**（E21）；货币必须显式（E22）；
- posted 交易必须**复式守恒** Σdebit = Σcredit（E3）；单边账在结构上不可表达；
- mint/burn 是唯一改变 Total Supply 的腿组合（E4/E5/E6/E7）；
- 状态迁移只允许文档冻结的表内迁移（非法迁移一律抛错，不"顺手放行"）。

改动本模块 = 改经济契约：必须走设计文档评审，并同步 tests/test_m1_economy_contract.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import (
    ContractStatus,
    Currency,
    EconomicActorKind,
    EscrowStatus,
    LedgerAccountKind,
    LedgerEntryDirection,
    RewardStatus,
    SettlementStatus,
    SystemAccountKind,
    TransactionKind,
    WorkOrderStatus,
)

#: 单笔金额上限（整数；防溢出与异常值。政策金额远低于此）
MAX_AMOUNT = 10**12


class EconomyContractError(ValueError):
    """经济契约被违反（金额非法 / 账不平 / 非法状态迁移 / 币种混用）。"""


# ---------------------------------------------------------------------------
# 金额与货币
# ---------------------------------------------------------------------------


def validate_amount(amount: object) -> int:
    """金额必须是**正整数整数**（E21）。

    `bool` 在 Python 里是 int 的子类 —— 显式拒绝（True/False 不是金额）。
    float 一律拒绝（哪怕 1.0）：金额精度必须由整数最小单位表达。
    """
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise EconomyContractError(
            f"amount must be an int (minor units), got {type(amount).__name__}"
        )
    if amount <= 0:
        raise EconomyContractError(f"amount must be > 0, got {amount}")
    if amount > MAX_AMOUNT:
        raise EconomyContractError(f"amount exceeds MAX_AMOUNT ({MAX_AMOUNT}), got {amount}")
    return amount


def parse_currency(value: object) -> Currency:
    """把外部输入解析成 Currency（显式币种，E22）。"""
    try:
        return Currency(str(value))
    except ValueError as exc:  # pragma: no cover - 防御：未知币种必须报错而不是回落
        raise EconomyContractError(f"unknown currency: {value!r}") from exc


# ---------------------------------------------------------------------------
# 主体与账户引用
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EconomicActor:
    """经济主体（设计 §9）：kind + ref。

    - `company` → ref = companies.id
    - `npc_company` → ref = market_participants.id（kind=npc_company；不进 companies）
    - `user` → ref = users.id
    - `system` → ref = SystemAccountKind（发行/财政/销毁/Escrow 托管）
    """

    kind: EconomicActorKind
    ref: int | SystemAccountKind

    def __post_init__(self) -> None:
        if self.kind is EconomicActorKind.system:
            if not isinstance(self.ref, SystemAccountKind):
                raise EconomyContractError("system actor ref must be a SystemAccountKind")
        elif not isinstance(self.ref, int) or isinstance(self.ref, bool) or self.ref <= 0:
            raise EconomyContractError(f"{self.kind.value} actor ref must be a positive int id")

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind.value, self.ref if isinstance(self.ref, str) else str(self.ref))

    @classmethod
    def system(cls, account: SystemAccountKind) -> EconomicActor:
        return cls(EconomicActorKind.system, account)

    @classmethod
    def company(cls, company_id: int) -> EconomicActor:
        return cls(EconomicActorKind.company, int(company_id))

    @classmethod
    def npc_company(cls, participant_id: int) -> EconomicActor:
        return cls(EconomicActorKind.npc_company, int(participant_id))


#: 系统账户类型（发行/财政/销毁 + Escrow 托管）：归 system actor，**不做资金充足性校验**
SYSTEM_ACCOUNT_KINDS = frozenset(
    {
        LedgerAccountKind.issuance,
        LedgerAccountKind.treasury,
        LedgerAccountKind.burn,
        LedgerAccountKind.escrow,
    }
)

#: 需要资金充足性的账户类型（E24，设计 §10）："真实持有资金"的账户 —— debit 时 available >= amount。
#: 系统账务侧（issuance/treasury/burn）刻意不含在内：否则 mint/burn 的第一腿在数字上无法成立。
REQUIRES_FUNDS_KINDS = frozenset({LedgerAccountKind.actor, LedgerAccountKind.escrow})


@dataclass(frozen=True)
class AccountRef:
    """一个记账账户的引用（设计 §10）：主体 × 货币 × 账户类型。"""

    actor: EconomicActor
    currency: Currency
    kind: LedgerAccountKind

    def __post_init__(self) -> None:
        # 系统账户只属于 system actor；system actor 也只能持有系统账户（发行/财政/销毁/Escrow 托管）
        if self.kind in SYSTEM_ACCOUNT_KINDS and self.actor.kind is not EconomicActorKind.system:
            raise EconomyContractError("system accounts must be owned by the system actor")
        if self.actor.kind is EconomicActorKind.system and self.kind not in SYSTEM_ACCOUNT_KINDS:
            raise EconomyContractError("system actor may only hold system accounts")

    @classmethod
    def company_actor(cls, company_id: int, currency: Currency = Currency.credit) -> AccountRef:
        return cls(EconomicActor.company(company_id), currency, LedgerAccountKind.actor)

    @classmethod
    def system_account(
        cls, kind: LedgerAccountKind, currency: Currency = Currency.credit
    ) -> AccountRef:
        return cls(EconomicActor.system(SystemAccountKind(kind.value)), currency, kind)


#: 各账户类型的正常余额方向（会计口径；余额符号解释用）
_NORMAL_SIDE: dict[LedgerAccountKind, LedgerEntryDirection] = {
    LedgerAccountKind.actor: LedgerEntryDirection.debit,
    LedgerAccountKind.escrow: LedgerEntryDirection.debit,
    LedgerAccountKind.treasury: LedgerEntryDirection.debit,
    LedgerAccountKind.burn: LedgerEntryDirection.debit,
    LedgerAccountKind.issuance: LedgerEntryDirection.credit,
}


def normal_side(kind: LedgerAccountKind) -> LedgerEntryDirection:
    return _NORMAL_SIDE[kind]


def requires_funds(kind: LedgerAccountKind) -> bool:
    """该账户类型是否受「余额不可为负」约束（E24；与 AccountKind 绑定，不全局硬编码）。"""
    return kind in REQUIRES_FUNDS_KINDS


def balance_delta(kind: LedgerAccountKind, direction: LedgerEntryDirection, amount: int) -> int:
    """**余额语义的唯一解释入口**（E26）：方向 × 正常余额方向 → 余额变化量。

    业务代码不得自己写 `if direction == DEBIT: balance += amount`（设计 §10）。
    调用方若要算余额，只能把 entries 依次交给本函数求和。
    """
    validate_amount(amount)
    return amount if direction is normal_side(kind) else -amount


def balance_from_totals(kind: LedgerAccountKind, *, debit_total: int, credit_total: int) -> int:
    """按正常余额方向把 (Σdebit, Σcredit) 折算成余额（聚合查询与重建共用）。"""
    if kind is LedgerAccountKind.issuance:
        return int(credit_total) - int(debit_total)
    return int(debit_total) - int(credit_total)


def assert_balanced_totals(*, debit_total: int, credit_total: int) -> None:
    """聚合口径的守恒校验（DB 层过账用；腿口径见 `validate_posting`）。"""
    if debit_total != credit_total:
        raise EconomyContractError(
            f"posting is not balanced: debits={debit_total} credits={credit_total}"
        )


# ---------------------------------------------------------------------------
# 过账腿、腿蓝图与守恒校验（E3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostingLeg:
    """过账腿：账户 + 方向 + 正整数金额。"""

    account: AccountRef
    direction: LedgerEntryDirection
    amount: int

    def __post_init__(self) -> None:
        validate_amount(self.amount)


@dataclass(frozen=True)
class LegSpec:
    """腿蓝图（哪一种"角色"被 debit / credit）—— 冻结每种交易的会计方向。"""

    role: str
    direction: LedgerEntryDirection


#: 每种交易类型的腿组合（**M1.0 冻结**；M1.1 的 LedgerService 只按蓝图构造腿）。
LEG_BLUEPRINTS: dict[TransactionKind, tuple[LegSpec, ...]] = {
    TransactionKind.mint: (
        LegSpec("beneficiary", LedgerEntryDirection.debit),
        LegSpec("issuance", LedgerEntryDirection.credit),
    ),
    TransactionKind.transfer: (
        LegSpec("payee", LedgerEntryDirection.debit),
        LegSpec("payer", LedgerEntryDirection.credit),
    ),
    TransactionKind.burn: (
        LegSpec("burn", LedgerEntryDirection.debit),
        LegSpec("payer", LedgerEntryDirection.credit),
    ),
    TransactionKind.treasury_transfer: (
        LegSpec("treasury", LedgerEntryDirection.debit),
        LegSpec("payer", LedgerEntryDirection.credit),
    ),
    TransactionKind.escrow_fund: (
        LegSpec("escrow", LedgerEntryDirection.debit),
        LegSpec("payer", LedgerEntryDirection.credit),
    ),
    TransactionKind.escrow_release: (
        LegSpec("payee", LedgerEntryDirection.debit),
        LegSpec("escrow", LedgerEntryDirection.credit),
    ),
    TransactionKind.escrow_refund: (
        LegSpec("payer", LedgerEntryDirection.debit),
        LegSpec("escrow", LedgerEntryDirection.credit),
    ),
}

#: 只有这些交易类型改变 Total Supply（E5；mint 增加、burn 减少，其余不变）
SUPPLY_CHANGING_KINDS = frozenset({TransactionKind.mint, TransactionKind.burn})
#: 这些交易类型与"玩家之间转移/Escrow"同属不变量：Supply 恒定（E6/E7）
SUPPLY_NEUTRAL_KINDS = frozenset(
    {
        TransactionKind.transfer,
        TransactionKind.treasury_transfer,
        TransactionKind.escrow_fund,
        TransactionKind.escrow_release,
        TransactionKind.escrow_refund,
    }
)


def posting_totals(legs: list[PostingLeg] | tuple[PostingLeg, ...]) -> tuple[int, int]:
    """(Σdebit, Σcredit)。"""
    debits = sum(leg.amount for leg in legs if leg.direction is LedgerEntryDirection.debit)
    credits = sum(leg.amount for leg in legs if leg.direction is LedgerEntryDirection.credit)
    return debits, credits


def validate_posting(legs: list[PostingLeg] | tuple[PostingLeg, ...]) -> None:
    """过账校验（E3 + 结构性规则）：≥2 条腿、单币种、Σdebit == Σcredit、金额为正。

    非法交易在这里就被拦下 —— "单边账"没有任何构造路径（设计 §12）。
    """
    if len(legs) < 2:
        raise EconomyContractError("a posting needs at least two legs (double-entry)")
    currencies = {leg.account.currency for leg in legs}
    if len(currencies) != 1:
        raise EconomyContractError(
            f"a posting cannot mix currencies: {sorted(c.value for c in currencies)}"
        )
    debits, credits = posting_totals(legs)
    if debits != credits:
        raise EconomyContractError(f"posting is not balanced: debits={debits} credits={credits}")


def is_balanced(legs: list[PostingLeg] | tuple[PostingLeg, ...]) -> bool:
    try:
        validate_posting(legs)
    except EconomyContractError:
        return False
    return True


# ---------------------------------------------------------------------------
# 供给与余额（派生公式，M1.1 实现；此处冻结语义）
# ---------------------------------------------------------------------------


def total_supply(*, issuance_credit_total: int, burn_debit_total: int) -> int:
    """Total Supply = ΣISSUANCE 的 credit − ΣBURN 的 debit（设计 §4）。"""
    supply = int(issuance_credit_total) - int(burn_debit_total)
    if supply < 0:
        raise EconomyContractError("total supply cannot be negative (burn exceeds issuance)")
    return supply


def circulating_supply(*, total: int, treasury_balance: int, escrow_balance: int) -> int:
    """流通量 = Total Supply − Treasury − Escrow 中未释放资金（设计 §4）。

    两者都是"已发行但不在玩家手上"的部分，必须非负。
    """
    circulating = int(total) - int(treasury_balance) - int(escrow_balance)
    if circulating < 0:
        raise EconomyContractError(
            "circulating supply cannot be negative (treasury+escrow exceed total supply)"
        )
    return circulating


def available_balance(*, balance: int, reserved: int) -> int:
    """可用余额 = 余额 − 已锁定（Escrow 出资）。**不得为负**（E24）。"""
    if reserved < 0 or balance < 0:
        raise EconomyContractError("balance and reserved must be non-negative")
    available = int(balance) - int(reserved)
    if available < 0:
        raise EconomyContractError(f"available balance would be negative: {balance} - {reserved}")
    return available


def supply_effect(kind: TransactionKind, amount: int) -> int:
    """该交易对 Total Supply 的影响（E5/E6/E7 的纯函数表达）。"""
    validate_amount(amount)
    if kind is TransactionKind.mint:
        return amount
    if kind is TransactionKind.burn:
        return -amount
    return 0


# ---------------------------------------------------------------------------
# 状态机（设计 §37，**冻结**；非法迁移抛错）
# ---------------------------------------------------------------------------


class StateMachine(StrEnum):
    reward = "reward"
    work_order = "work_order"
    contract = "contract"
    escrow = "escrow"
    settlement = "settlement"


_REWARD = RewardStatus
_WORK_ORDER = WorkOrderStatus
_CONTRACT = ContractStatus
_ESCROW = EscrowStatus
_SETTLEMENT = SettlementStatus

#: 允许的迁移（键 = 当前状态，值 = 可去状态集合）
TRANSITIONS: dict[StateMachine, dict[str, frozenset[str]]] = {
    StateMachine.reward: {
        _REWARD.eligible.value: frozenset({_REWARD.claimed.value, _REWARD.void.value}),
        _REWARD.claimed.value: frozenset({_REWARD.posted.value, _REWARD.void.value}),
        _REWARD.posted.value: frozenset(),  # 已过账：不可再变（纠错走 reversal，E17）
        _REWARD.void.value: frozenset(),
    },
    StateMachine.work_order: {
        _WORK_ORDER.draft.value: frozenset({_WORK_ORDER.open.value, _WORK_ORDER.cancelled.value}),
        _WORK_ORDER.open.value: frozenset(
            {
                _WORK_ORDER.accepted.value,
                _WORK_ORDER.cancelled.value,
                _WORK_ORDER.expired.value,
            }
        ),
        _WORK_ORDER.accepted.value: frozenset(
            {_WORK_ORDER.in_progress.value, _WORK_ORDER.cancelled.value, _WORK_ORDER.disputed.value}
        ),
        _WORK_ORDER.in_progress.value: frozenset(
            {_WORK_ORDER.submitted.value, _WORK_ORDER.disputed.value}
        ),
        _WORK_ORDER.submitted.value: frozenset({_WORK_ORDER.reviewing.value}),
        _WORK_ORDER.reviewing.value: frozenset(
            {_WORK_ORDER.approved.value, _WORK_ORDER.rejected.value, _WORK_ORDER.disputed.value}
        ),
        _WORK_ORDER.approved.value: frozenset({_WORK_ORDER.settled.value}),
        _WORK_ORDER.rejected.value: frozenset(
            {_WORK_ORDER.in_progress.value, _WORK_ORDER.cancelled.value}
        ),
        _WORK_ORDER.settled.value: frozenset(),
        _WORK_ORDER.cancelled.value: frozenset(),
        _WORK_ORDER.expired.value: frozenset(),
        _WORK_ORDER.disputed.value: frozenset(
            {_WORK_ORDER.settled.value, _WORK_ORDER.cancelled.value}
        ),
    },
    StateMachine.contract: {
        _CONTRACT.draft.value: frozenset(
            {_CONTRACT.pending_acceptance.value, _CONTRACT.cancelled.value}
        ),
        _CONTRACT.pending_acceptance.value: frozenset(
            {_CONTRACT.active.value, _CONTRACT.cancelled.value, _CONTRACT.expired.value}
        ),
        _CONTRACT.active.value: frozenset({_CONTRACT.funded.value, _CONTRACT.cancelled.value}),
        _CONTRACT.funded.value: frozenset(
            {_CONTRACT.fulfilled.value, _CONTRACT.failed.value, _CONTRACT.disputed.value}
        ),
        _CONTRACT.fulfilled.value: frozenset({_CONTRACT.settling.value}),
        _CONTRACT.settling.value: frozenset({_CONTRACT.settled.value, _CONTRACT.failed.value}),
        _CONTRACT.settled.value: frozenset(),
        _CONTRACT.cancelled.value: frozenset(),
        _CONTRACT.expired.value: frozenset(),
        _CONTRACT.failed.value: frozenset({_CONTRACT.settling.value}),
        _CONTRACT.disputed.value: frozenset({_CONTRACT.settled.value, _CONTRACT.failed.value}),
    },
    StateMachine.escrow: {
        _ESCROW.unfunded.value: frozenset({_ESCROW.funded.value, _ESCROW.expired.value}),
        _ESCROW.funded.value: frozenset(
            {_ESCROW.released.value, _ESCROW.refunded.value, _ESCROW.expired.value}
        ),
        _ESCROW.released.value: frozenset(),
        _ESCROW.refunded.value: frozenset(),
        _ESCROW.expired.value: frozenset(),
    },
    StateMachine.settlement: {
        _SETTLEMENT.pending.value: frozenset(
            {_SETTLEMENT.processing.value, _SETTLEMENT.failed.value}
        ),
        _SETTLEMENT.processing.value: frozenset(
            {_SETTLEMENT.completed.value, _SETTLEMENT.failed.value}
        ),
        _SETTLEMENT.completed.value: frozenset(),  # 终态：同 key 重放直接返回既有结果（E12）
        _SETTLEMENT.failed.value: frozenset({_SETTLEMENT.processing.value}),
    },
}


def can_transition(machine: StateMachine, current: str, target: str) -> bool:
    """是否允许 current → target（同状态视为"无迁移"= False）。"""
    allowed = TRANSITIONS[machine]
    if current not in allowed:
        raise EconomyContractError(f"unknown {machine.value} state: {current!r}")
    return target in allowed[current]


def assert_transition(machine: StateMachine, current: str, target: str) -> None:
    if not can_transition(machine, current, target):
        raise EconomyContractError(
            f"illegal {machine.value} transition: {current} → {target}"
            "（状态机冻结于 docs/m1-economy-design.md §37）"
        )


# ---------------------------------------------------------------------------
# 手续费拆分（设计 §7/§30）
# ---------------------------------------------------------------------------


def split_fee(amount: int, *, treasury_ratio: float, burn_ratio: float) -> tuple[int, int]:
    """把手续费按比例拆成 (treasury, burn)。

    整数分配必须**守恒**（treasury + burn == amount）：余数给 Treasury
    （Burn 是永久销毁，宁可少烧不可凭空多烧）。
    """
    validate_amount(amount)
    if not 0.0 <= burn_ratio <= 1.0 or not 0.0 <= treasury_ratio <= 1.0:
        raise EconomyContractError("fee ratios must be within [0, 1]")
    if abs((treasury_ratio + burn_ratio) - 1.0) > 1e-9:
        raise EconomyContractError("treasury_ratio + burn_ratio must equal 1")
    burn = int(amount * burn_ratio)
    return amount - burn, burn
