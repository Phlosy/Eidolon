#!/usr/bin/env python3
"""发行人才（T2.4）—— 本地模拟市场的官方供给入口（CLI，不做 UI）。

用法：

    make market-issue ISSUE_ARGS="--tier rare --count 2"
    python scripts/issue_talent.py --tier fine --count 3
    python scripts/issue_talent.py --tier normal --count 1 --no-list      # 只造人，不挂牌
    python scripts/issue_talent.py --tier rare --seed demo --template academic

口径（docs/t2-talent-market-design.md §7 D11）：
- 走**真实培养链**（advance_program 跑满 + 阶段评估）产出证据；档位只影响采样参数；
- 发行角色 `owner_company_id=NULL`（在市场），评估快照用本部署默认公司作历史上下文；
- 挂牌方 = `MarketParticipant(kind=system_issuer)`；不产生任何货币/交易语义（M1 边界）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许 `python scripts/issue_talent.py` 直接跑（与 dev_inventory.py 同惯例）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.repositories import organization as org_repo  # noqa: E402
from app.talent.market.issuer import TIERS, IssuerService  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发行本地市场人才（真实培养链，非数值生成器）")
    parser.add_argument("--tier", choices=sorted(TIERS), default="normal")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--template", default=None, help="覆盖档位默认模板（测试/调试用）")
    parser.add_argument("--seed", default=None, help="确定性种子（同种子同角色）")
    parser.add_argument("--name", default=None, help="仅 count=1 时指定姓名")
    parser.add_argument("--no-list", action="store_true", help="只产出角色，不投放市场")
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("--count 必须 >= 1")

    with SessionLocal() as db:
        company = org_repo.get_default_company(db)
        if company is None:
            print("ERROR: 没有公司（先 make dev-seed-user）", file=sys.stderr)
            return 2
        service = IssuerService()
        for index in range(args.count):
            issued = service.issue(
                db,
                tier=args.tier,
                name=args.name if args.count == 1 else None,
                owner_context_company_id=int(company.id),
                template=args.template,
                seed=f"{args.seed}:{index}" if args.seed else None,
                list_on_market=not args.no_list,
            )
            listing = f"listing={issued.listing_id}" if issued.listing_id else "未挂牌"
            print(
                f"issued [{issued.tier}] {issued.identity_id} "
                f"person={issued.person_id} templates={','.join(issued.templates)} "
                f"evidence={issued.evidence_count} mean_signal={issued.mean_signal} {listing}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
