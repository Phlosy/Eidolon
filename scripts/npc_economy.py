#!/usr/bin/env python3
"""NPC 经济（M1.8）—— 预算注入 / 状态 / 跑一轮（**系统/管理面 CLI**，不进玩家 router）。

设计 §29：NPC 的钱包是 `npc_company` 经济 actor；**预算注入是唯一允许的发行入口**
（经 MonetaryAuthority，计入发行统计）；NPC 出手是 deterministic 规则
（`available >= price and price <= max_price and fit >= threshold`），成交只是**转移**（不 mint）。

用法：

    make npc-economy-status
    make npc-economy-inject   NPC_ARGS='--npc xinghai --amount 50000'
    make npc-economy-run      NPC_ARGS='--dry-run'          # 先看会买谁
    make npc-economy-run      NPC_ARGS='--npc mercury'

口径：`--company-context` 只用于 fit 的职位/口径上下文（NPC 没有公司）；
钱和参与方始终是 NPC 自己。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.repositories import organization as org_repo  # noqa: E402
from app.services.economy.npc_economy import NpcEconomyError, NpcEconomyService  # noqa: E402
from app.talent.market import npc as npc_market  # noqa: E402


def _context_company_id(db) -> int:
    company = org_repo.get_default_company(db)
    if company is None:
        raise SystemExit("没有默认公司（dev 库需要先 dev-seed-user）")
    return int(company.id)


def _participant_id(db, npc_key: str) -> int:
    service = npc_market.NpcMarketService()
    service.ensure_participants(db, company_context_id=_context_company_id(db))
    participant = service._participant_by_key(db, npc_key)  # noqa: SLF001 - CLI 内部
    if participant is None:
        raise SystemExit(f"unknown npc key: {npc_key}")
    return int(participant.id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NPC 经济（M1.8）")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="NPC 余额/累计注入/参数")
    status.add_argument("--unused", action="store_true")

    inject = sub.add_parser("inject", help="注入预算（mint；受 budget_cap 封顶）")
    inject.add_argument("--npc", required=True, help="NPC key（xinghai / mercury …）")
    inject.add_argument("--amount", type=int, default=None, help="注入额度（默认取政策值）")

    run = sub.add_parser("run", help="跑一轮 NPC 活动（在预算内成交）")
    run.add_argument("--npc", default=None, help="只跑某个 NPC")
    run.add_argument("--dry-run", action="store_true", help="只判定不成交（不动钱）")
    run.add_argument("--no-inject", action="store_true", help="不自动补预算")

    args = parser.parse_args(argv)
    command = args.command or "status"

    with SessionLocal() as db:
        context_id = _context_company_id(db)
        service = NpcEconomyService(db)

        if command == "status":
            for row in service.status(company_context_id=context_id):
                print(
                    f"{row['npc_key']:<10} {row['npc_name']:<12} available={row['available']:<8} "
                    f"injected={row['injected_total']}/{row['budget_cap']} "
                    f"max_price={row['max_price']} fit>={row['fit_threshold_bps'] / 10000:.2f} "
                    f"deals/round={row['deals_per_round']}"
                )
                summary = service.income_summary(row["participant_id"])
                if summary:
                    print(f"           收入/支出（net by category）: {summary}")
            return 0

        if command == "inject":
            try:
                participant_id = _participant_id(db, args.npc)
            except KeyError as exc:
                raise SystemExit(f"unknown npc key: {args.npc}") from exc
            granted, total = service.inject_budget(participant_id, amount=args.amount)
            print(f"injected={granted} total={total} available={service.available_budget(participant_id)}")
            return 0

        if command == "run":
            try:
                outcomes = service.run_round(
                    company_context_id=context_id,
                    only_npc_key=args.npc,
                    inject=not args.no_inject,
                    dry_run=args.dry_run,
                )
            except NpcEconomyError as exc:
                print(f"NPC 活动失败：{exc.reason}", file=sys.stderr)
                return 1
            for outcome in outcomes:
                print(
                    f"{outcome.npc_name}: considered={outcome.considered} "
                    f"injected={outcome.budget_injected} available={outcome.available} "
                    f"purchased={len(outcome.purchased)}"
                )
                for purchase in outcome.purchased:
                    suffix = "" if purchase.transaction_id is None else f" tx={purchase.transaction_id}"
                    print(
                        f"   {'(dry-run) ' if outcome.dry_run else ''}listing={purchase.listing_id} "
                        f"person={purchase.person_id} price={purchase.price} "
                        f"seller={purchase.sell_to_company_id or 'system'}{suffix}"
                    )
                for decision in outcome.skipped[:3]:
                    print(f"   skip price={decision.price} reason={decision.reason}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
