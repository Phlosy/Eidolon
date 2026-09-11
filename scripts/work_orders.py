#!/usr/bin/env python3
"""官方工作市场管理面（M1.3）—— 发布 / 验收 / 结算 / 过期（**内部能力，不进玩家 router**）。

设计 §32 的三层边界：玩家面只有 `GET /work-orders`、`POST /work-orders/{id}/accept|submit`；
**发布、验收、结算**属系统/管理面 —— v1 无 admin 角色体系，因此落在这里（CLI），
而不是暴露一个玩家可调的 `/evaluate`。

用法：

    make work-order-publish WO_ARGS='--title "写文档" --reward 5000 --require readme'
    make work-order-list
    make work-order-evaluate WO_ARGS='--order 1 --verdict approved --score 95 --bonus early_delivery=1000'
    make work-order-expire

口径（docs/m1-economy-design.md §18/§20）：
- **预算内发行**：单笔 ≤ `official_max_reward`，未结算承诺额 ≤ `official_outstanding_budget`；
- **auto 验收**：确定性规则（提交非空 + 满足必交项），**不给 bonus**；
- **manual 验收**：运维给 verdict/score/bonus（bonus 必须是非负整数）；
- 奖励 = base + Σbonus，发放只经 `SettlementService → MonetaryAuthority → Ledger`。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.database import SessionLocal  # noqa: E402
from app.economy.policy import economic_policy  # noqa: E402
from app.models.enums import (  # noqa: E402
    EvaluationMode,
    EvaluationVerdict,
    WorkOrderKind,
    WorkOrderStatus,
)
from app.repositories import economy as economy_repo  # noqa: E402
from app.services.economy.work_orders import WorkOrderError, WorkOrderService  # noqa: E402

_VERDICTS = {v.value: v for v in EvaluationVerdict}


def _parse_bonuses(raw: list[str] | None) -> dict[str, int]:
    bonuses: dict[str, int] = {}
    for item in raw or []:
        if "=" not in item:
            raise SystemExit(f"bonus 需要 name=amount 形式：{item}")
        name, _, amount = item.partition("=")
        try:
            bonuses[name.strip()] = int(amount)
        except ValueError as exc:  # pragma: no cover - argparse 层已挡住
            raise SystemExit(f"bonus 金额必须是整数：{item}") from exc
    return bonuses


def _print_order(order) -> None:
    print(
        f"#{order.id} {order.code} [{order.status}] {order.kind} "
        f"reward={order.reward_amount} assignee={order.assignee_actor_kind or '-'}:"
        f"{order.assignee_actor_ref or '-'} deadline={order.deadline_at or '-'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="官方工作市场管理面（M1.3）")
    sub = parser.add_subparsers(dest="command")

    publish = sub.add_parser("publish", help="发布官方订单（预算内发行）")
    publish.add_argument("--title", required=True)
    publish.add_argument("--reward", type=int, required=True)
    publish.add_argument(
        "--kind",
        choices=[k.value.lower() for k in WorkOrderKind],
        default=WorkOrderKind.official_bounty.value.lower(),
    )
    publish.add_argument("--description", default="")
    publish.add_argument("--code", default=None)
    publish.add_argument(
        "--require", action="append", default=[], help="必交交付物 key（可多次，auto 验收用）"
    )
    publish.add_argument("--evaluation", choices=["auto", "manual"], default="auto")
    publish.add_argument("--deadline-in-hours", type=int, default=None)

    listing = sub.add_parser("list", help="列出订单")
    listing.add_argument("--status", default=None)
    listing.add_argument("--limit", type=int, default=20)

    evaluate = sub.add_parser("evaluate", help="验收（manual/auto）")
    evaluate.add_argument("--order", type=int, required=True)
    evaluate.add_argument("--verdict", choices=sorted(_VERDICTS), default=None)
    evaluate.add_argument("--mode", choices=["auto", "manual"], default=None)
    evaluate.add_argument("--score", type=int, default=None)
    evaluate.add_argument("--bonus", action="append", default=[], help="name=amount（可多次）")
    evaluate.add_argument("--notes", default="")

    settle = sub.add_parser("settle", help="结算（幂等）")
    settle.add_argument("--order", type=int, required=True)

    sub.add_parser("expire", help="把过期订单推进到 EXPIRED/CANCELLED")

    supply = sub.add_parser("supply", help="打印供给快照（核对 minted/burned）")
    supply.add_argument("--unused", action="store_true")

    args = parser.parse_args(argv)
    command = args.command or "list"

    with SessionLocal() as db:
        service = WorkOrderService(db)
        if command == "publish":
            policy = economic_policy()
            deadline = (
                datetime.now(UTC) + timedelta(hours=args.deadline_in_hours)
                if args.deadline_in_hours
                else None
            )
            try:
                result = service.publish_official(
                    title=args.title,
                    reward_amount=args.reward,
                    kind=WorkOrderKind(args.kind.upper()),
                    code=args.code,
                    description=args.description,
                    deliverables={"required_keys": args.require},
                    evaluation_mode=EvaluationMode(args.evaluation),
                    deadline_at=deadline,
                )
            except WorkOrderError as exc:
                print(f"发布被拒绝：{exc.reason}", file=sys.stderr)
                return 1
            print(
                f"{'created' if result.created else 'existing'}: "
                f"#{result.order.id} {result.order.code} reward={result.order.reward_amount} "
                f"(max per order={policy.official_max_reward}, budget={policy.official_outstanding_budget})"
            )
            return 0

        if command == "list":
            statuses = (args.status.upper(),) if args.status else None
            for order in economy_repo.list_work_orders(db, statuses=statuses, limit=args.limit):
                _print_order(order)
            return 0

        if command == "evaluate":
            order = economy_repo.get_work_order(db, args.order)
            if order is None:
                print(f"订单不存在：{args.order}", file=sys.stderr)
                return 1
            try:
                order, evaluation = service.evaluate(
                    args.order,
                    verdict=_VERDICTS[args.verdict] if args.verdict else None,
                    mode=EvaluationMode(args.mode) if args.mode else None,
                    score=args.score,
                    bonuses=_parse_bonuses(args.bonus),
                    notes=args.notes,
                    evaluated_by=("system", 0),
                )
            except WorkOrderError as exc:
                print(f"验收被拒绝：{exc.reason}", file=sys.stderr)
                return 1
            except Exception as exc:  # noqa: BLE001 - CLI：把领域错误整句打出来
                print(f"验收失败：{exc}", file=sys.stderr)
                return 1
            print(
                f"verdict={evaluation.verdict} score={evaluation.score} "
                f"bonuses={evaluation.bonuses_json} order=#{order.id} status={order.status}"
            )
            if order.status == WorkOrderStatus.settled.value:
                print(f"已结算：transaction={order.settlement_transaction_id}")
            return 0

        if command == "settle":
            try:
                order = service.settle(args.order)
            except WorkOrderError as exc:
                print(f"结算被拒绝：{exc.reason}", file=sys.stderr)
                return 1
            print(
                f"#{order.id} status={order.status} transaction={order.settlement_transaction_id}"
            )
            return 0

        if command == "expire":
            expired = service.expire_overdue()
            print(f"expired {expired} orders")
            return 0

        if command == "supply":
            from app.services.economy.ledger import LedgerService

            snapshot = LedgerService(db).supply()
            print(
                f"minted={snapshot.minted} burned={snapshot.burned} supply={snapshot.supply} "
                f"treasury={snapshot.treasury_balance} escrow={snapshot.escrow_balance} "
                f"circulating={snapshot.circulating}"
            )
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
