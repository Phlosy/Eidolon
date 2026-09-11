#!/usr/bin/env python3
"""Agent 工具执行面的**调试口**（M2.3，用户拍板 §12）。

用法：

    python scripts/agent_tools.py list                     # 列出工具（含副作用/授权/自主等级）
    python scripts/agent_tools.py describe                 # 同上，附运行时可用性
    python scripts/agent_tools.py call \\
        --employee charlie --tool inspect_project --args '{"project_id": 1}'
    python scripts/agent_tools.py call --employee alice --tool create_task \\
        --args '{"project_id": 1, "title": "拆分登录模块"}'

这个脚本**不是**生产业务入口：

* 默认**关闭** —— 需要 `EIDOLON_AGENT_TOOL_CLI_ENABLED=true`（执行面会核对，见 T10）；
* 不在任何玩家 router 里（没有 `POST /api/v1/tools/*`，T3）；
* 它走的执行面与 Agent Runtime **完全相同**：Authority 校验、领域约束、自主等级门禁、
  审计一样都不少（T4）。它只解决"谁能发起"，不解决"可以绕过什么"；
* `--employee` 只用来**选定 actor**（debug 注入），参数里仍然不许出现身份字段（T5）。

退出码：0 = 成功；1 = 被拒绝或失败（拒绝原因打印在 stderr）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "server"))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.enums import ToolTransport
from app.repositories import organization as org_repo
from app.work import tool_executor as executor


def _print(payload, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _resolve_employee(db, identifier: str):
    employee = org_repo.get_employee_by_slug(db, identifier)
    if employee is None and identifier.isdigit():
        employee = org_repo.get_employee(db, int(identifier))
    if employee is None:
        raise SystemExit(f"employee not found: {identifier}")
    return employee


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent 工具执行面调试口（M2.3；默认关闭）"
    )
    parser.add_argument(
        "--json", action="store_true", help="输出 JSON（默认就是 JSON）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出工具清单")
    sub.add_parser("describe", help="工具清单 + 运行时可用性")

    call = sub.add_parser("call", help="调用一个工具（调试注入 actor）")
    call.add_argument(
        "--employee", required=True, help="actor：员工 slug 或 id（debug 注入）"
    )
    call.add_argument("--tool", required=True, help="工具名")
    call.add_argument("--args", default="{}", help="JSON 参数对象")

    args = parser.parse_args(argv)

    if args.command == "list":
        _print({"tools": executor.catalog()}, as_json=True)
        return 0
    if args.command == "describe":
        _print(executor.describe_tools(), as_json=True)
        return 0

    try:
        payload = json.loads(args.args)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--args must be a JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--args must be a JSON object")

    if not settings.agent_tool_cli_enabled:
        print(
            "debug CLI is disabled: set EIDOLON_AGENT_TOOL_CLI_ENABLED=true "
            "(the executor would refuse anyway — T10)",
            file=sys.stderr,
        )
        return 1

    with SessionLocal() as db:
        employee = _resolve_employee(db, args.employee)
        context = executor.context_for_employee(db, employee, origin="debug_cli")
        result = executor.execute_tool(
            db,
            name=args.tool,
            args=payload,
            context=context,
            transport=ToolTransport.debug_cli,
        )
    _print(result.as_dict(), as_json=True)
    if result.ok:
        return 0
    print(f"rejected: {result.reason}: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
