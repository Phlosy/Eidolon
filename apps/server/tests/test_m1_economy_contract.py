"""M1.0 经济契约与不变量测试（docs/m1-economy-design.md §38 E1–E25 / plan §4.M1.0）。

把经济契约钉成可执行规则：
- 枚举值冻结（改值 = 改契约）；
- 金额=正整数最小单位（E21）、货币显式（E22）；
- 复式守恒（E3）：随机腿集合下"只有平衡交易被接受"；
- 腿蓝图冻结（每种交易的借贷方向 = 设计 §12）；
- 供给公式（E5/E6/E7）与余额派生（E24）；
- 状态机迁移表冻结（非法迁移抛错）；
- 政策配置化 + 版本 + 拆分守恒（设计 §30）；
- 财务数据只准住经济模块（E1 锚点）、玩家 API 不出现 mint/burn（E23）、T2 不依赖 M1（E20 锚点）。
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from app.economy import (
    MAX_AMOUNT,
    REQUIRES_FUNDS_KINDS,
    AccountRef,
    EconomicActor,
    EconomyContractError,
    PostingLeg,
    StateMachine,
    assert_balanced_totals,
    assert_transition,
    available_balance,
    balance_delta,
    balance_from_totals,
    can_transition,
    circulating_supply,
    economic_policy,
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
from app.economy.contracts import (
    LEG_BLUEPRINTS,
    SUPPLY_CHANGING_KINDS,
    SUPPLY_NEUTRAL_KINDS,
    TRANSITIONS,
)
from app.models.enums import (
    ContractStatus,
    Currency,
    EconomicActorKind,
    EscrowStatus,
    EvaluationMode,
    EvaluationVerdict,
    FundingMode,
    LedgerAccountKind,
    LedgerEntryDirection,
    RewardStatus,
    RewardType,
    SettlementStatus,
    SystemAccountKind,
    TransactionKind,
    WorkOrderKind,
    WorkOrderStatus,
)

SERVER_ROOT = Path(__file__).resolve().parents[1]


# ---------------- 枚举值冻结 ----------------


def test_economy_enum_values_are_frozen():
    """值集是契约：改值会破坏已落库数据与政策解释力。"""
    assert {c.value for c in Currency} == {"CREDIT"}
    assert {k.value for k in EconomicActorKind} == {"system", "user", "company", "npc_company"}
    assert {k.value for k in SystemAccountKind} == {"issuance", "treasury", "burn", "escrow"}
    assert {k.value for k in LedgerAccountKind} == {
        "actor",
        "issuance",
        "treasury",
        "burn",
        "escrow",
    }
    assert {d.value for d in LedgerEntryDirection} == {"debit", "credit"}
    assert {k.value for k in TransactionKind} == {
        "mint",
        "transfer",
        "burn",
        "treasury_transfer",
        "escrow_fund",
        "escrow_release",
        "escrow_refund",
    }
    assert {t.value for t in RewardType} == {
        "STARTER_GRANT",
        "PROFILE_COMPLETION",
        "COMPANY_PROFILE_COMPLETION",
        "TUTORIAL_COMPLETION",
        "DAILY_LOGIN",
        "WEEKLY_ACTIVITY",
        "ACHIEVEMENT",
        "MILESTONE_REWARD",
        "OFFICIAL_BOUNTY",
        "OFFICIAL_CONTRACT",
        "RESEARCH_GRANT",
        "SYSTEM_PROCUREMENT",
        "EVENT_REWARD",
        "RECOVERY_GRANT",
    }
    assert {s.value for s in RewardStatus} == {"ELIGIBLE", "CLAIMED", "POSTED", "VOID"}
    assert {k.value for k in WorkOrderKind} == {
        "OFFICIAL_BOUNTY",
        "OFFICIAL_CONTRACT",
        "PLAYER_BOUNTY",
        "PLAYER_CONTRACT",
        "NPC_CONTRACT",
        "RESEARCH_GRANT",
        "SYSTEM_PROCUREMENT",
    }
    assert {m.value for m in FundingMode} == {"system_mint", "player_escrow", "npc_treasury"}
    assert {m.value for m in EvaluationMode} == {"auto", "manual", "none"}
    assert {v.value for v in EvaluationVerdict} == {"approved", "rejected", "revise"}
    assert {s.value for s in EscrowStatus} == {
        "UNFUNDED",
        "FUNDED",
        "RELEASED",
        "REFUNDED",
        "EXPIRED",
    }
    assert {s.value for s in SettlementStatus} == {
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    }
    assert {s.value for s in ContractStatus} == {
        "DRAFT",
        "PENDING_ACCEPTANCE",
        "ACTIVE",
        "FUNDED",
        "FULFILLED",
        "SETTLING",
        "SETTLED",
        "CANCELLED",
        "EXPIRED",
        "FAILED",
        "DISPUTED",
    }
    assert {s.value for s in WorkOrderStatus} == {
        "DRAFT",
        "OPEN",
        "ACCEPTED",
        "IN_PROGRESS",
        "SUBMITTED",
        "REVIEWING",
        "APPROVED",
        "REJECTED",
        "SETTLED",
        "CANCELLED",
        "EXPIRED",
        "DISPUTED",
    }


# ---------------- 金额与货币 ----------------


def test_amount_must_be_positive_integer():
    assert validate_amount(1) == 1
    assert validate_amount(MAX_AMOUNT) == MAX_AMOUNT
    for bad in (0, -1, -100):
        with pytest.raises(EconomyContractError):
            validate_amount(bad)
    for bad in (1.0, "100", None, [], {}, True, False, MAX_AMOUNT + 1):
        with pytest.raises(EconomyContractError):
            validate_amount(bad)
    # 明确：float 即使整数值也不是合法金额
    with pytest.raises(EconomyContractError):
        validate_amount(100.0)


def test_currency_is_explicit():
    assert parse_currency("CREDIT") is Currency.credit
    assert parse_currency(Currency.credit) is Currency.credit
    with pytest.raises(EconomyContractError):
        parse_currency("USD")
    with pytest.raises(EconomyContractError):
        parse_currency(None)


# ---------------- 主体与账户 ----------------


def test_economic_actor_kinds_and_refs():
    assert EconomicActor.company(7).key == ("company", "7")
    assert EconomicActor.npc_company(3).key == ("npc_company", "3")
    assert EconomicActor.system(SystemAccountKind.issuance).key == ("system", "issuance")
    with pytest.raises(EconomyContractError):
        EconomicActor(EconomicActorKind.company, 0)
    with pytest.raises(EconomyContractError):
        EconomicActor(EconomicActorKind.system, 1)  # system 必须指向 SystemAccountKind


def test_system_accounts_are_system_owned_only():
    for kind in (
        LedgerAccountKind.issuance,
        LedgerAccountKind.treasury,
        LedgerAccountKind.burn,
        LedgerAccountKind.escrow,
    ):
        assert AccountRef.system_account(kind).kind is kind
        assert normal_side(kind) in (LedgerEntryDirection.debit, LedgerEntryDirection.credit)
        # 公司不能持有系统账户
        with pytest.raises(EconomyContractError):
            AccountRef(EconomicActor.company(1), Currency.credit, kind)
    # system actor 也不能持有普通 actor 账户
    with pytest.raises(EconomyContractError):
        AccountRef(
            EconomicActor.system(SystemAccountKind.treasury),
            Currency.credit,
            LedgerAccountKind.actor,
        )


def test_balance_delta_is_the_single_interpretation_entry():
    """E26：余额变化只能由 `balance_delta` 解释（业务代码不得自己判断借贷方向）。"""
    debit, credit = LedgerEntryDirection.debit, LedgerEntryDirection.credit
    assert balance_delta(LedgerAccountKind.actor, debit, 100) == 100
    assert balance_delta(LedgerAccountKind.actor, credit, 100) == -100
    assert balance_delta(LedgerAccountKind.escrow, debit, 100) == 100
    assert balance_delta(LedgerAccountKind.issuance, credit, 100) == 100  # credit-normal
    assert balance_delta(LedgerAccountKind.issuance, debit, 100) == -100
    assert balance_delta(LedgerAccountKind.burn, debit, 7) == 7
    with pytest.raises(EconomyContractError):
        balance_delta(LedgerAccountKind.actor, debit, 0)

    # 聚合口径与逐腿口径必须一致
    legs = [debit, credit, credit]
    amounts = [500, 200, 150]
    total = sum(
        balance_delta(LedgerAccountKind.actor, direction, amount)
        for direction, amount in zip(legs, amounts, strict=True)
    )
    assert total == balance_from_totals(
        LedgerAccountKind.actor,
        debit_total=sum(a for d, a in zip(legs, amounts, strict=True) if d is debit),
        credit_total=sum(a for d, a in zip(legs, amounts, strict=True) if d is credit),
    )


def test_requires_funds_is_bound_to_account_kind():
    """E24：资金充足性只约束"真实持有资金"的账户 —— 系统账务侧不受限。"""
    assert requires_funds(LedgerAccountKind.actor) is True
    assert requires_funds(LedgerAccountKind.escrow) is True
    assert requires_funds(LedgerAccountKind.issuance) is False
    assert requires_funds(LedgerAccountKind.treasury) is False
    assert requires_funds(LedgerAccountKind.burn) is False
    assert REQUIRES_FUNDS_KINDS == {LedgerAccountKind.actor, LedgerAccountKind.escrow}
    assert REQUIRES_FUNDS_KINDS | {
        LedgerAccountKind.issuance,
        LedgerAccountKind.treasury,
        LedgerAccountKind.burn,
    } == set(LedgerAccountKind) | {LedgerAccountKind.escrow}


def test_assert_balanced_totals_rejects_imbalance():
    assert_balanced_totals(debit_total=100, credit_total=100)
    with pytest.raises(EconomyContractError, match="not balanced"):
        assert_balanced_totals(debit_total=100, credit_total=99)


def test_normal_side_is_frozen():
    assert normal_side(LedgerAccountKind.actor) is LedgerEntryDirection.debit
    assert normal_side(LedgerAccountKind.escrow) is LedgerEntryDirection.debit
    assert normal_side(LedgerAccountKind.treasury) is LedgerEntryDirection.debit
    assert normal_side(LedgerAccountKind.burn) is LedgerEntryDirection.debit
    assert normal_side(LedgerAccountKind.issuance) is LedgerEntryDirection.credit


# ---------------- 复式守恒（E3） ----------------


def _company_legs(*amounts: int, directions: tuple[LedgerEntryDirection, ...]) -> list[PostingLeg]:
    return [
        PostingLeg(AccountRef.company_actor(index + 1), direction, amount)
        for index, (amount, direction) in enumerate(zip(amounts, directions, strict=True))
    ]


def test_posting_requires_balance_and_two_legs():
    debit = LedgerEntryDirection.debit
    credit = LedgerEntryDirection.credit

    balanced = _company_legs(500, 500, directions=(debit, credit))
    validate_posting(balanced)
    assert is_balanced(balanced)
    assert posting_totals(balanced) == (500, 500)

    with pytest.raises(EconomyContractError, match="not balanced"):
        validate_posting(_company_legs(500, 400, directions=(debit, credit)))
    with pytest.raises(EconomyContractError, match="at least two legs"):
        validate_posting(_company_legs(500, directions=(debit,)))
    with pytest.raises(EconomyContractError):
        validate_posting([])


def test_posting_rejects_mixed_currencies_and_bad_amounts():
    debit = LedgerEntryDirection.debit
    credit = LedgerEntryDirection.credit
    legs = [
        PostingLeg(AccountRef.company_actor(1), debit, 100),
        PostingLeg(
            AccountRef(EconomicActor.company(2), Currency.credit, LedgerAccountKind.actor),
            credit,
            100,
        ),
    ]
    validate_posting(legs)
    with pytest.raises(EconomyContractError):
        PostingLeg(AccountRef.company_actor(1), debit, 0)
    with pytest.raises(EconomyContractError):
        PostingLeg(AccountRef.company_actor(1), credit, 1.5)  # type: ignore[arg-type]


def test_property_only_balanced_postings_are_accepted():
    """property-style：随机腿集合下，接受 ⟺ Σdebit == Σcredit（≥2 腿、单币种）。"""
    rng = random.Random(20260911)
    debit = LedgerEntryDirection.debit
    credit = LedgerEntryDirection.credit
    for _ in range(200):
        count = rng.randint(1, 6)
        amounts = [rng.randint(1, 10_000) for _ in range(count)]
        directions = [rng.choice([debit, credit]) for _ in range(count)]
        legs = _company_legs(*amounts, directions=tuple(directions))
        debits = sum(a for a, d in zip(amounts, directions, strict=True) if d is debit)
        credits = sum(a for a, d in zip(amounts, directions, strict=True) if d is credit)
        expected = count >= 2 and debits == credits
        assert is_balanced(legs) is expected, (amounts, directions)


# ---------------- 腿蓝图冻结（设计 §12） ----------------


def test_leg_blueprints_are_frozen_and_balanced():
    debit = LedgerEntryDirection.debit
    credit = LedgerEntryDirection.credit
    expected = {
        TransactionKind.mint: (("beneficiary", debit), ("issuance", credit)),
        TransactionKind.transfer: (("payee", debit), ("payer", credit)),
        TransactionKind.burn: (("burn", debit), ("payer", credit)),
        TransactionKind.treasury_transfer: (("treasury", debit), ("payer", credit)),
        TransactionKind.escrow_fund: (("escrow", debit), ("payer", credit)),
        TransactionKind.escrow_release: (("payee", debit), ("escrow", credit)),
        TransactionKind.escrow_refund: (("payer", debit), ("escrow", credit)),
    }
    assert set(LEG_BLUEPRINTS) == set(expected)
    for kind, specs in LEG_BLUEPRINTS.items():
        assert tuple((spec.role, spec.direction) for spec in specs) == expected[kind]
        debits = sum(1 for spec in specs if spec.direction is debit)
        credits = sum(1 for spec in specs if spec.direction is credit)
        assert debits == credits == 1, f"{kind.value} 必须是「一借一贷」等额两腿"
        assert len({spec.role for spec in specs}) == len(specs), "同一交易的腿角色不得重复"


def test_supply_changing_and_neutral_kinds_are_frozen():
    assert SUPPLY_CHANGING_KINDS == {TransactionKind.mint, TransactionKind.burn}
    assert SUPPLY_NEUTRAL_KINDS == {
        TransactionKind.transfer,
        TransactionKind.treasury_transfer,
        TransactionKind.escrow_fund,
        TransactionKind.escrow_release,
        TransactionKind.escrow_refund,
    }
    assert SUPPLY_CHANGING_KINDS | SUPPLY_NEUTRAL_KINDS == set(TransactionKind)


# ---------------- 供给与余额（E5/E6/E7/E24） ----------------


def test_supply_math():
    assert total_supply(issuance_credit_total=1_000, burn_debit_total=200) == 800
    with pytest.raises(EconomyContractError):
        total_supply(issuance_credit_total=100, burn_debit_total=200)  # 烧超过发行 = 数据损坏
    assert circulating_supply(total=800, treasury_balance=100, escrow_balance=50) == 650
    with pytest.raises(EconomyContractError):
        circulating_supply(total=100, treasury_balance=80, escrow_balance=30)

    assert supply_effect(TransactionKind.mint, 500) == 500
    assert supply_effect(TransactionKind.burn, 500) == -500
    for kind in sorted(SUPPLY_NEUTRAL_KINDS):
        assert supply_effect(kind, 500) == 0, kind
    with pytest.raises(EconomyContractError):
        supply_effect(TransactionKind.transfer, 0)


def test_available_balance_cannot_go_negative():
    assert available_balance(balance=1_000, reserved=400) == 600
    assert available_balance(balance=1_000, reserved=1_000) == 0
    with pytest.raises(EconomyContractError):
        available_balance(balance=1_000, reserved=1_001)
    with pytest.raises(EconomyContractError):
        available_balance(balance=-1, reserved=0)


def test_fee_split_is_conserving(  # 设计 §7/§30
):
    treasury, burn = split_fee(100, treasury_ratio=0.6, burn_ratio=0.4)
    assert (treasury, burn) == (60, 40)
    # 余数给 Treasury（Burn 少烧不多烧）
    treasury, burn = split_fee(7, treasury_ratio=0.5, burn_ratio=0.5)
    assert treasury + burn == 7 and burn == 3 and treasury == 4
    with pytest.raises(EconomyContractError):
        split_fee(100, treasury_ratio=0.6, burn_ratio=0.5)
    with pytest.raises(EconomyContractError):
        split_fee(0, treasury_ratio=1.0, burn_ratio=0.0)


# ---------------- 状态机（设计 §37） ----------------


def test_state_machine_transition_tables_are_frozen():
    assert set(TRANSITIONS) == {
        StateMachine.reward,
        StateMachine.work_order,
        StateMachine.contract,
        StateMachine.escrow,
        StateMachine.settlement,
    }
    # 终态不可离开
    for machine, terminal in (
        (StateMachine.reward, RewardStatus.posted.value),
        (StateMachine.work_order, WorkOrderStatus.settled.value),
        (StateMachine.contract, ContractStatus.settled.value),
        (StateMachine.escrow, EscrowStatus.released.value),
        (StateMachine.settlement, SettlementStatus.completed.value),
    ):
        assert TRANSITIONS[machine][terminal] == frozenset(), (machine, terminal)


def test_legal_and_illegal_transitions():
    assert can_transition(
        StateMachine.work_order, WorkOrderStatus.open.value, WorkOrderStatus.accepted.value
    )
    assert can_transition(
        StateMachine.contract, ContractStatus.funded.value, ContractStatus.fulfilled.value
    )
    assert can_transition(
        StateMachine.escrow, EscrowStatus.funded.value, EscrowStatus.refunded.value
    )
    # 非法：跳步 / 逆向 / 终态之后
    with pytest.raises(EconomyContractError, match="illegal"):
        assert_transition(
            StateMachine.work_order, WorkOrderStatus.open.value, WorkOrderStatus.settled.value
        )
    with pytest.raises(EconomyContractError, match="illegal"):
        assert_transition(
            StateMachine.settlement,
            SettlementStatus.completed.value,
            SettlementStatus.processing.value,
        )
    with pytest.raises(EconomyContractError, match="unknown"):
        assert_transition(StateMachine.reward, "NOT_A_STATE", RewardStatus.claimed.value)
    # 同状态不算迁移（调用方应自行幂等处理）
    assert not can_transition(
        StateMachine.escrow, EscrowStatus.funded.value, EscrowStatus.funded.value
    )


# ---------------- 政策（配置化 + 版本） ----------------


def test_policy_comes_from_settings_and_is_versioned():
    policy = economic_policy()
    assert policy.version
    assert policy.starter_grant > 0
    assert 0 < policy.market_fee_bps <= 10_000
    assert policy.fee_treasury_ratio + policy.fee_burn_ratio == pytest.approx(1.0)
    # 手续费与拆分走政策，不是散落常量
    assert policy.fee_for(10_000) == 10_000 * policy.market_fee_bps // 10_000
    treasury, burn = policy.split_fee(1_000)
    assert treasury + burn == 1_000
    # 新手/兜底收益必须显著小于官方任务（防"签到致富"）
    assert policy.starter_grant > policy.recovery_grant > policy.daily_reward


def test_policy_reloads_after_settings_change(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "economy_starter_grant", 12_345)
    economic_policy.cache_clear()
    try:
        assert economic_policy().starter_grant == 12_345
    finally:
        economic_policy.cache_clear()  # 还原缓存，避免污染其它用例


def test_policy_rejects_inconsistent_ratios():
    from app.economy.policy import EconomicPolicy

    with pytest.raises(EconomyContractError):
        EconomicPolicy(
            version="bad",
            starter_grant=1_000_000,
            profile_reward=1,
            company_profile_reward=1,
            tutorial_reward=10_000,
            achievement_reward=5_000,
            daily_reward=1,
            weekly_activity_reward=1,
            recovery_grant=1_000,
            recovery_threshold=2_000,
            recovery_cooldown_hours=1,
            official_reward_multiplier=1.0,
            market_fee_bps=500,
            fee_treasury_ratio=0.5,
            fee_burn_ratio=0.6,
            compute_credit_per_unit=1,
            official_max_reward=1_000,
            official_outstanding_budget=10_000,
            player_order_max_reward=100_000,
        )


# ---------------- 守卫（E1 / E23 / E20 锚点） ----------------


def _python_files(prefixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(SERVER_ROOT))
        if relative.startswith(prefixes):
            files.append((path, relative))
    return files


def test_money_fields_live_only_in_the_economy_module():
    """E1/E2 锚点：`balance` 之类财务字段只准出现在 `app/models/economy.py`（M1.1 建）。

    其它域的模型不得夹带余额/钱包字段 —— 资金真相只能在账本里（设计 §8/§11）。
    """
    forbidden = {
        "balance",
        "cached_balance",
        "available_balance",
        "reserved_balance",
        "wallet",
        "credits",
        "price",
        "escrow_id",
    }
    offenders: list[str] = []
    for path, relative in _python_files(("app/models/",)):
        if relative == "app/models/economy.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            for target in targets:
                if isinstance(target, ast.Name) and target.id in forbidden:
                    offenders.append(f"{relative}:{node.lineno} {target.id}")
    assert not offenders, "财务字段只准住在经济模型（E1/E2）：\n" + "\n".join(offenders)


def test_player_api_never_exposes_mint_or_burn():
    """E23 锚点：玩家/公司 router 不得 import 铸币/销毁/财政入口。"""
    forbidden = ("mint", "burn", "MonetaryAuthority", "treasury_transfer", "issue_currency")
    offenders: list[str] = []
    for path, relative in _python_files(("app/api/v1/",)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if any(token.lower() in name.lower() for token in forbidden):
                    offenders.append(f"{relative}:{node.lineno} {name}")
    assert not offenders, "玩家 API 不得直接触达发行/销毁（E23）：\n" + "\n".join(offenders)


def test_t2_talent_domain_does_not_depend_on_economy():
    """E20/T2 边界：T2（talent/市场/招募）不得 import M1 经济写入口 —— 方向只能是 M1 → T2。"""
    offenders: list[str] = []
    for path, relative in _python_files(("app/talent/",)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.economy"):
                        offenders.append(f"{relative}:{node.lineno} {alias.name}")
                continue
            if module and module.startswith("app.economy"):
                offenders.append(f"{relative}:{node.lineno} {module}")
    assert not offenders, "T2 不得依赖 M1（E20）：\n" + "\n".join(offenders)


def test_economy_contract_layer_is_dependency_free():
    """契约/政策层不 import 业务域（保持纯契约：M1.1 的服务才允许编排业务）。"""
    offenders: list[str] = []
    for path, relative in _python_files(("app/economy/",)):
        if relative.endswith(("contracts.py", "policy.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    if module.startswith(("app.talent", "app.services", "app.repositories")):
                        offenders.append(f"{relative}:{node.lineno} {module}")
    assert not offenders, "经济契约层必须是纯契约：\n" + "\n".join(offenders)
