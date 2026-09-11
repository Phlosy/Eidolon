#!/usr/bin/env python3
"""钱包投影对账 / 重建（M1.1c）—— 运维常规能力（E29/E15）。

用法：

    make economy-verify                     # **只读**对账：账本推导值 vs 投影缓存
    make economy-rebuild                    # 由账本重建投影（--apply 的快捷方式）
    python scripts/economy_wallets.py --verify
    python scripts/economy_wallets.py --supply
    python scripts/economy_wallets.py --rebuild --yes

口径（docs/m1-economy-design.md §11）：
- Ledger 是事实来源；`wallet_projection` 只是可重建缓存；
- 重建**绝不读旧投影**：清空 → 由 `ledger_accounts/transactions/entries` 重算 → 写回；
- 对账只报告 drift（不自动修）：`--rebuild` 是人的显式动作，需要 `--yes` 确认。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许 `python scripts/economy_wallets.py` 直接跑（与 dev_inventory.py 同惯例）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.services.economy.ledger import LedgerService  # noqa: E402
from app.services.economy.projection import (  # noqa: E402
    rebuild_wallet_projection,
    verify_wallet_projection,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="账本 / 钱包投影对账与重建（M1.1）")
    parser.add_argument("--verify", action="store_true", help="只读对账（默认动作）")
    parser.add_argument("--rebuild", action="store_true", help="由账本重建投影（破坏性）")
    parser.add_argument("--supply", action="store_true", help="打印货币供给快照")
    parser.add_argument("--overview", action="store_true", help="打印公司经营报表（收入/成本/分类）")
    parser.add_argument("--company", type=int, default=None, help="--overview 的公司 id（默认全部公司）")
    parser.add_argument("--compute", action="store_true", help="打印算力用量与欠费")
    parser.add_argument("--yes", action="store_true", help="确认重建（--rebuild 需要）")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.compute:
            from app.services.economy.costs import ComputeCostService

            totals = ComputeCostService(db).totals(company_id=int(args.company)) if args.company else {}
            if args.company:
                print(f"company={args.company} compute paid={totals.get('paid', 0)} unpaid={totals.get('unpaid', 0)}")
            else:
                from sqlalchemy import select

                from app.models.economy import ComputeUsage

                rows = db.execute(
                    select(ComputeUsage.company_id, ComputeUsage.status)
                    .distinct()
                ).all()
                print(f"compute_usage rows: {len(rows)} (company,status) pairs; 用 --company N 看汇总")
            return 0

        if args.overview:
            from sqlalchemy import select

            from app.models.organization import Company
            from app.repositories import economy as economy_repo
            from app.services.economy.costs import ComputeCostService

            company_ids = (
                [int(args.company)]
                if args.company
                else [int(row.id) for row in db.scalars(select(Company).order_by(Company.id))]
            )
            for company_id in company_ids:
                accounts = [
                    int(account.id)
                    for account in economy_repo.list_accounts_for_actor(
                        db, actor_kind="company", actor_ref=company_id, kinds=("actor",)
                    )
                ]
                totals = economy_repo.category_totals_for_accounts(db, account_ids=accounts)
                compute = ComputeCostService(db).totals(company_id=company_id)
                income = sum(debits for debits, _ in totals.values())
                expense = sum(credits for _, credits in totals.values())
                print(
                    f"company={company_id} income={income} expense={expense} "
                    f"net={income - expense} compute_paid={compute.get('paid', 0)} "
                    f"compute_unpaid={compute.get('unpaid', 0)}"
                )
                for category, (debits, credits) in sorted(
                    totals.items(), key=lambda item: -(item[1][0] + item[1][1])
                ):
                    print(
                        f"   {(category or 'unclassified'):<18} income={debits:<10} expense={credits}"
                    )
            return 0

        if args.supply:
            snapshot = LedgerService(db).supply()
            print(
                "supply: "
                f"minted={snapshot.minted} burned={snapshot.burned} "
                f"supply={snapshot.supply} treasury={snapshot.treasury_balance} "
                f"escrow={snapshot.escrow_balance} circulating={snapshot.circulating}"
            )
            return 0

        if args.rebuild:
            if not args.yes:
                print("refusing to rebuild without --yes（重建会清空并重算投影）", file=sys.stderr)
                return 2
            rows = rebuild_wallet_projection(db)
            print(f"rebuilt {rows} projection rows from the ledger")
            check = verify_wallet_projection(db)
            print(check.summary())
            return 0 if check.ok else 1

        check = verify_wallet_projection(db)
        print(check.summary())
        return 0 if check.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
