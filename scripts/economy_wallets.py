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
    parser.add_argument("--yes", action="store_true", help="确认重建（--rebuild 需要）")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
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
