#!/usr/bin/env python3
"""经济观测（M1.9）—— 管理员视角的 supply / 发行 / 成本 / 托管 / 一致性巡检。

用法：

    make economy-stats                       # 经济快照（人类可读）
    make economy-stats STATS_ARGS="--json"   # 机器可读（CI/面板）
    make economy-check                       # 一致性巡检（只读；drift/异常退出码 1）
    make economy-policy-reload               # 在线刷新政策（重读 .env/EIDOLON_ECONOMY_*）

口径（docs/m1-economy-design.md §31）：
- **只读**：本 CLI 不产生任何交易（观测不能改钱）；
- `supply = minted − burned`；`circulating = supply − treasury − escrow`；
- `unpaid`（算力欠费）单列，不当免费；NPC 注入与上限单列（发行侧）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.economy.policy import reload_policy  # noqa: E402
from app.services.economy.stats import EconomyStatsService  # noqa: E402


def _dump(value) -> dict:
    if is_dataclass(value):
        return {key: _dump(item) for key, item in asdict(value).items()}
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="经济观测（M1.9；只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    parser.add_argument("--check", action="store_true", help="一致性巡检（drift/异常 → 退出码 1）")
    parser.add_argument("--policy-reload", action="store_true", help="在线刷新政策快照")
    args = parser.parse_args(argv)

    if args.policy_reload:
        policy = reload_policy()
        print(
            f"policy reloaded: version={policy.version} starter={policy.starter_grant} "
            f"daily={policy.daily_reward} market_fee_bps={policy.market_fee_bps} "
            f"contract_fee_bps={policy.contract_fee_bps} "
            f"official_max={policy.official_max_reward} "
            f"npc_injection={policy.npc_budget_injection} npc_cap={policy.npc_budget_cap}"
        )
        return 0

    with SessionLocal() as db:
        service = EconomyStatsService(db)
        if args.check:
            report = service.consistency()
            print(report.summary())
            return 0 if report.ok else 1

        stats = service.snapshot()
        if args.json:
            print(json.dumps(_dump(stats), ensure_ascii=False, indent=2, default=str))
            return 0
        for line in stats.summary_lines():
            print(line)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
