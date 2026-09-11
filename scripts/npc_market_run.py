#!/usr/bin/env python3
"""NPC 市场活动一轮（T2.7c）—— 本地模拟市场里的"其他公司"出手。

    make market-npc NPC_ARGS="--dry-run"          # 只看会买谁
    make market-npc NPC_ARGS="--npc xinghai"      # 只跑某一家
    python scripts/npc_market_run.py --dry-run

口径（docs/t2-talent-market-design.md §10e / plan §4.8）：
- NPC 不进 `companies`、不建 Employee；只关闭挂牌并写 `recruited_participant_id`；
- 选拔复用同一个 Fit 引擎；阈值是"分数 + 置信度"并列，Unknown 只落选、不被当成最差；
- 节奏由调用方决定（本脚本 = 手动跑一轮；调度器属后续）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.repositories import organization as org_repo  # noqa: E402
from app.talent.market.npc import NPC_SPECS, NpcMarketService  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑一轮 NPC 市场活动（发现 → Fit → 成交）")
    parser.add_argument("--npc", default=None, help=f"只跑某一家：{', '.join(s.key for s in NPC_SPECS)}")
    parser.add_argument("--dry-run", action="store_true", help="只报告会买谁，不写库")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        company = org_repo.get_default_company(db)
        if company is None:
            print("ERROR: 没有公司（先 make dev-seed-user）", file=sys.stderr)
            return 2
        reports = NpcMarketService().run_once(
            db,
            company_context_id=int(company.id),
            only_npc_key=args.npc,
            dry_run=args.dry_run,
        )
        for report in reports:
            head = "DRY-RUN" if report.dry_run else "RUN"
            print(
                f"[{head}] {report.npc_name}: 考虑 {report.considered} 人，"
                f"未达标 {report.skipped_below_threshold} 人，成交 {len(report.acquired)} 人"
            )
            for item in report.acquired:
                print(
                    f"    → {item.identity_id} person={item.person_id} "
                    f"listing={item.listing_id} score={item.fit_score} conf={item.fit_confidence}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
