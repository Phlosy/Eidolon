"""EconomyStatsService —— 经济观测（M1.9，设计 §31；**只读**）。

玩家看得懂"我的钱"（`/economy/*` 的读面），管理员看得懂"经济的钱"（本模块 + CLI）：

```
supply:   minted / burned / supply / treasury / escrow / circulating
rewards:  已过账奖励按类型汇总（谁拿了多少）
official: 官方未结算承诺额（预算占用）
compute:  paid / unpaid（欠费看得见）
npc:      注入累计 / 上限（发行侧）
escrow:   各状态计数 + 仍锁定的金额
contracts / work_orders: 各状态计数
```

**只读纪律**：本模块不产生任何交易（运维观测不能改钱）。
一致性巡检（`consistency()`）复用 M1.1 的投影对账，并补三条"状态与资金应当一致"的检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.economy.policy import EconomicPolicy, economic_policy
from app.models.economy import Contract, Escrow, WorkOrder
from app.models.enums import (
    ContractStatus,
    EscrowStatus,
    LedgerAccountKind,
    WorkOrderStatus,
)
from app.repositories import economy as economy_repo
from app.services.economy.ledger import LedgerService, SupplySnapshot
from app.services.economy.projection import ProjectionCheck, verify_wallet_projection


@dataclass(frozen=True)
class EconomyStats:
    """一次经济快照（管理员视角；金额一律整数最小单位）。"""

    currency: str
    policy_version: str
    supply: SupplySnapshot
    rewards_by_type: dict[str, int]
    official_outstanding: int
    compute: dict[str, int]
    npc: dict[str, int]
    escrow_status: dict[str, int]
    escrow_locked: int
    contract_status: dict[str, int]
    work_order_status: dict[str, int]
    accounts_total: int = 0
    metadata: dict = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        supply = self.supply
        lines = [
            f"policy={self.policy_version} currency={self.currency}",
            (
                f"supply: minted={supply.minted} burned={supply.burned} supply={supply.supply} "
                f"treasury={supply.treasury_balance} escrow={supply.escrow_balance} "
                f"circulating={supply.circulating}"
            ),
            f"accounts={self.accounts_total}",
            f"official outstanding={self.official_outstanding}",
            f"compute: paid={self.compute.get('paid', 0)} unpaid={self.compute.get('unpaid', 0)}",
            (
                f"npc: injected={self.npc.get('injected_total', 0)}/"
                f"{self.npc.get('cap_total', 0)} profiles={self.npc.get('profiles', 0)}"
            ),
            f"escrow: locked={self.escrow_locked} "
            f"status={dict(sorted(self.escrow_status.items()))}",
            f"contracts: {dict(sorted(self.contract_status.items()))}",
            f"work_orders: {dict(sorted(self.work_order_status.items()))}",
        ]
        if self.rewards_by_type:
            lines.append(f"rewards: {dict(sorted(self.rewards_by_type.items()))}")
        return lines


@dataclass(frozen=True)
class ConsistencyIssue:
    """一处"应当一致但没有"的地方（只报告，不修）。"""

    kind: str
    detail: str

    def describe(self) -> str:
        return f"[{self.kind}] {self.detail}"


@dataclass(frozen=True)
class ConsistencyReport:
    ok: bool
    projection: ProjectionCheck
    issues: tuple[ConsistencyIssue, ...]

    def summary(self) -> str:
        if self.ok:
            return f"OK ({self.projection.accounts_checked} accounts reconciled)"
        lines = [self.projection.summary()] if not self.projection.ok else []
        lines.extend(issue.describe() for issue in self.issues)
        return "\n".join(lines)


class EconomyStatsService:
    """经济观测与一致性巡检（**只读**）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()

    # ---------------------------------------------------------------- 快照

    def snapshot(self) -> EconomyStats:
        ledger = LedgerService(self.db)
        escrow_status = economy_repo.count_by_status(self.db, Escrow)
        return EconomyStats(
            currency="CREDIT",
            policy_version=self.policy.version,
            supply=ledger.supply(),
            rewards_by_type=economy_repo.reward_grants_by_type(self.db),
            official_outstanding=economy_repo.outstanding_official_commitment(self.db),
            compute=economy_repo.compute_usage_totals_all(self.db),
            npc=economy_repo.npc_budget_totals(self.db),
            escrow_status=escrow_status,
            escrow_locked=economy_repo.sum_locked_by_status(
                self.db, Escrow, status=EscrowStatus.funded.value, column=Escrow.amount
            ),
            contract_status=economy_repo.count_by_status(self.db, Contract),
            work_order_status=economy_repo.count_by_status(self.db, WorkOrder),
            accounts_total=len(
                economy_repo.list_accounts_by_kind(
                    self.db, kinds=tuple(kind.value for kind in LedgerAccountKind)
                )
            ),
        )

    # ---------------------------------------------------------------- 一致性

    def consistency(self) -> ConsistencyReport:
        """巡检：投影对账 + "托管该归零却还有钱" + "余额不该为负" + "注入不该超上限"。"""
        ledger_check = verify_wallet_projection(self.db)
        issues: list[ConsistencyIssue] = []

        # 1) 终态业务（已结算/取消/失败/过期的合同或订单）不该还有钱锁在托管账户里（E25）
        terminal_contract_ids = {
            int(row.id)
            for row in economy_repo.list_contracts(
                self.db,
                statuses=(
                    ContractStatus.settled.value,
                    ContractStatus.cancelled.value,
                    ContractStatus.expired.value,
                    ContractStatus.failed.value,
                ),
            )
        }
        terminal_order_ids = {
            int(row.id)
            for row in economy_repo.list_work_orders(
                self.db,
                statuses=(
                    WorkOrderStatus.settled.value,
                    WorkOrderStatus.cancelled.value,
                    WorkOrderStatus.expired.value,
                ),
            )
        }
        balances = dict(economy_repo.count_escrow_accounts_with_balance(self.db))
        for escrow in economy_repo.list_escrows(self.db):
            account_id = int(escrow.escrow_account_id or 0)
            balance = balances.get(account_id, 0)
            if balance <= 0:
                continue
            if escrow.contract_id in terminal_contract_ids or (
                escrow.work_order_id in terminal_order_ids
            ):
                issues.append(
                    ConsistencyIssue(
                        kind="escrow_not_zeroed",
                        detail=(
                            f"escrow={int(escrow.id)} status={escrow.status} "
                            f"balance={balance}（业务已终态，托管应当归零，E25）"
                        ),
                    )
                )

        # 2) 普通钱包不该出现负可花余额（E24/守卫兜底；SQLite 下理论上不可能）
        for row in economy_repo.list_projections(self.db):
            account = economy_repo.get_account(self.db, int(row.account_id))
            if account is None or account.kind not in ("actor", "escrow"):
                continue
            if int(row.available_balance) < 0:
                issues.append(
                    ConsistencyIssue(
                        kind="negative_available",
                        detail=(
                            f"account={int(row.account_id)} kind={account.kind} "
                            f"available={int(row.available_balance)}（余额不可为负，E24）"
                        ),
                    )
                )

        # 3) NPC 累计注入不该超过上限（封顶是发行侧的硬约束）
        for profile in economy_repo.list_npc_profiles(self.db):
            if int(profile.budget_injected_total) > int(profile.budget_cap):
                issues.append(
                    ConsistencyIssue(
                        kind="npc_budget_over_cap",
                        detail=(
                            f"participant={int(profile.participant_id)} injected="
                            f"{int(profile.budget_injected_total)} > "
                            f"cap={int(profile.budget_cap)}"
                        ),
                    )
                )

        return ConsistencyReport(
            ok=ledger_check.ok and not issues,
            projection=ledger_check,
            issues=tuple(issues),
        )


__all__ = [
    "ConsistencyIssue",
    "ConsistencyReport",
    "EconomyStats",
    "EconomyStatsService",
]
