#!/usr/bin/env python3
"""Dev Data Cleanup **Plan** —— 只读清理规划器（docs/dev-data-cleanup.md）。

定位：把"清理哪家开发库公司"设计成显式、可审、**只读**的规划流程。

- 目标公司**必须显式传入**（`--company 1 3`）；不给就报错退出（拒绝"顺手全库清"）。
- 对每个目标重扫现状（复用 `dev_inventory` 的判断，不写第二套），并给出：
  · **dependency impact** —— 删除该公司会连带消失的行（按表计数，含 0）；
  · **external resource impact** —— 数据库外、删除行**不会**自动消失的资源；
  · `suspected_probe` 只折叠成 **probe hints**（提示），永不自动成为目标。
- 没有 --delete / --cleanup / --fix / --apply / --force / --auto-remove-probes。
- 结尾固定声明 `NO DATA HAS BEEN MODIFIED.`（文本与 JSON 都是）。

用法：

    make dev-cleanup-plan COMPANY_IDS="1 3"
    python scripts/dev_cleanup_plan.py --company 1 3
    python scripts/dev_cleanup_plan.py --company 1 3 --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

# 复用 dev_inventory 的只读引擎、公司盘点与探针判定 —— 两套判断迟早分家。
from dev_inventory import (
    _as_datetime,
    _resolve_database_url,
    build_engine,
    company_counts,
    slot_state,
    suspected_probe,
)

NO_DATA_MODIFIED = "NO DATA HAS BEEN MODIFIED."

# ---------------------------------------------------------------------------
# 依赖影响：删除该公司会连带消失的行。按表名给归属 SQL（company_id 直属或经 JOIN）。
# 每一条都会 try/except：表不存在或列改名时输出 error 而不是让规划器崩掉。
# ---------------------------------------------------------------------------

# 直属（company_id 就在表上）
_DIRECT = {
    "departments": "SELECT COUNT(*) FROM departments WHERE company_id = :cid",
    "position_slots": "SELECT COUNT(*) FROM position_slots WHERE company_id = :cid",
    "projects": "SELECT COUNT(*) FROM projects WHERE company_id = :cid",
    "drive_nodes": "SELECT COUNT(*) FROM drive_nodes WHERE company_id = :cid",
    "events": "SELECT COUNT(*) FROM events WHERE company_id = :cid",
    "git_connections": "SELECT COUNT(*) FROM git_connections WHERE company_id = :cid",
    "providers": "SELECT COUNT(*) FROM providers WHERE company_id = :cid",
    "company_memberships": (
        "SELECT COUNT(*) FROM company_memberships WHERE company_id = :cid"
    ),
    "user_audit_events": (
        "SELECT COUNT(*) FROM user_audit_events WHERE company_id = :cid"
    ),
}

# 经员工归属（employee 属于公司）
_EMPLOYEE_OWNED = {
    "employments": (
        "SELECT COUNT(*) FROM employments a JOIN employees e ON e.id = a.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "employee_brains": (
        "SELECT COUNT(*) FROM employee_brains b JOIN employees e ON e.id = b.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "employee_packages": (
        "SELECT COUNT(*) FROM employee_packages p JOIN employees e ON e.id = p.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "model_bindings": (
        "SELECT COUNT(*) FROM model_bindings b JOIN employees e ON e.id = b.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "resource_accounts": (
        "SELECT COUNT(*) FROM resource_accounts a JOIN employees e ON e.id = a.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "resource_assets": (
        "SELECT COUNT(*) FROM resource_assets a JOIN employees e ON e.id = a.owner_employee_id"
        " WHERE e.company_id = :cid"
    ),
    "runtime_instances": (
        "SELECT COUNT(*) FROM runtime_instances r JOIN employees e ON e.id = r.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "skills": (
        "SELECT COUNT(*) FROM skills s JOIN employees e ON e.id = s.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "skill_usages": (
        "SELECT COUNT(*) FROM skill_usages u JOIN employees e ON e.id = u.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "learning_records": (
        "SELECT COUNT(*) FROM learning_records r JOIN employees e ON e.id = r.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "learning_priorities": (
        "SELECT COUNT(*) FROM learning_priorities p JOIN employees e ON e.id = p.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "memory_entries": (
        "SELECT COUNT(*) FROM memory_entries m JOIN employees e ON e.id = m.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "provisioning_jobs": (
        "SELECT COUNT(*) FROM provisioning_jobs j JOIN employees e ON e.id = j.employee_id"
        " WHERE e.company_id = :cid"
    ),
    "drive_collaborators": (
        "SELECT COUNT(*) FROM drive_collaborators c JOIN employees e ON e.id = c.employee_id"
        " WHERE e.company_id = :cid"
    ),
}

# 经项目归属（project 属于公司）
_PROJECT_OWNED = {
    "milestones": (
        "SELECT COUNT(*) FROM milestones m JOIN projects p ON p.id = m.project_id"
        " WHERE p.company_id = :cid"
    ),
    "tasks": (
        "SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id"
        " WHERE p.company_id = :cid"
    ),
    "artifacts": (
        "SELECT COUNT(*) FROM artifacts a JOIN projects p ON p.id = a.project_id"
        " WHERE p.company_id = :cid"
    ),
    "messages": (
        "SELECT COUNT(*) FROM messages m JOIN projects p ON p.id = m.project_id"
        " WHERE p.company_id = :cid"
    ),
    "work_sessions": (
        "SELECT COUNT(*) FROM work_sessions w JOIN tasks t ON t.id = w.task_id"
        " JOIN projects p ON p.id = t.project_id WHERE p.company_id = :cid"
    ),
    "task_dependencies": (
        "SELECT COUNT(*) FROM task_dependencies d JOIN tasks t ON t.id = d.task_id"
        " JOIN projects p ON p.id = t.project_id WHERE p.company_id = :cid"
    ),
    "project_phases": (
        "SELECT COUNT(*) FROM project_phases ph JOIN projects p ON p.id = ph.project_id"
        " WHERE p.company_id = :cid"
    ),
    "project_requirements": (
        "SELECT COUNT(*) FROM project_requirements r JOIN projects p ON p.id = r.project_id"
        " WHERE p.company_id = :cid"
    ),
    "baselines": (
        "SELECT COUNT(*) FROM baselines b JOIN projects p ON p.id = b.project_id"
        " WHERE p.company_id = :cid"
    ),
    "change_requests": (
        "SELECT COUNT(*) FROM change_requests c JOIN projects p ON p.id = c.project_id"
        " WHERE p.company_id = :cid"
    ),
    "delivery_packages": (
        "SELECT COUNT(*) FROM delivery_packages d JOIN projects p ON p.id = d.project_id"
        " WHERE p.company_id = :cid"
    ),
    "review_meetings": (
        "SELECT COUNT(*) FROM review_meetings m JOIN projects p ON p.id = m.project_id"
        " WHERE p.company_id = :cid"
    ),
    "document_artifacts": (
        "SELECT COUNT(*) FROM document_artifacts d JOIN projects p ON p.id = d.project_id"
        " WHERE p.company_id = :cid"
    ),
}

# 经部门归属（部门属于公司）：v0.4 遗留 positions 挂在部门下
_EXTRA = {
    "position_definitions": (
        "SELECT COUNT(*) FROM position_definitions WHERE company_id = :cid"
    ),
    "legacy_positions": (
        "SELECT COUNT(*) FROM positions p JOIN departments d ON d.id = p.department_id"
        " WHERE d.company_id = :cid"
    ),
    "position_definition_packages": (
        "SELECT COUNT(*) FROM position_definition_packages pk"
        " JOIN position_definitions d ON d.id = pk.position_definition_id"
        " WHERE d.company_id = :cid"
    ),
}

_ALL_DEP = {**_DIRECT, **_EMPLOYEE_OWNED, **_PROJECT_OWNED, **_EXTRA}

# 数据库行删除后不会自动消失的外部资源 —— 只盘点提醒人工，不回收。
_EXTERNAL_ASSET_SQL = {
    "resource_accounts_with_external_id": (
        "SELECT COUNT(*) FROM resource_accounts a JOIN employees e ON e.id = a.employee_id"
        " WHERE e.company_id = :cid AND a.external_account_id IS NOT NULL"
        "   AND a.external_account_id <> ''"
    ),
    "resource_assets_with_external_id": (
        "SELECT COUNT(*) FROM resource_assets a JOIN employees e ON e.id = a.owner_employee_id"
        " WHERE e.company_id = :cid AND a.external_id IS NOT NULL AND a.external_id <> ''"
    ),
    "git_connections": ("SELECT COUNT(*) FROM git_connections WHERE company_id = :cid"),
}


def _count(db: Session, sql: str, cid: int) -> dict[str, Any]:
    """一条计数：要么给出数字，要么把原因写进结果（规划器不因单表失败而崩）。"""
    try:
        return {"count": int(db.execute(sa.text(sql), {"cid": cid}).scalar() or 0)}
    except sa_exc.SQLAlchemyError as exc:
        return {"count": 0, "error": str(exc).splitlines()[0][:140]}


def dependency_impact(db: Session, company_id: int) -> dict[str, Any]:
    """按归属类别列出**会随公司删除而消失**的行（只读估计，不是删除动作）。"""
    out: dict[str, dict[str, Any]] = {}
    for group, table_sql in (
        ("direct", _DIRECT),
        ("employee_owned", _EMPLOYEE_OWNED),
        ("project_owned", _PROJECT_OWNED),
        ("misc", _EXTRA),
    ):
        rows = {name: _count(db, sql, company_id) for name, sql in table_sql.items()}
        out[group] = rows
    return out


def employee_inventory(db: Session, company_id: int) -> dict[str, Any]:
    """员工侧现状：总数 + lifecycle 分布（规划要能看出"这家公司有没有真人"）。"""
    try:
        rows = db.execute(
            sa.text(
                "SELECT lifecycle_status, COUNT(*) FROM employees"
                " WHERE company_id = :cid GROUP BY lifecycle_status"
            ),
            {"cid": company_id},
        ).all()
        return {
            "total": sum(int(row[1]) for row in rows),
            "by_lifecycle": {str(row[0]): int(row[1]) for row in rows},
        }
    except sa_exc.SQLAlchemyError as exc:  # pragma: no cover
        return {"total": 0, "error": str(exc).splitlines()[0][:140]}


def external_resource_impact(db: Session, company_id: int) -> dict[str, Any]:
    """数据库外、删除行不会自动消失的资源清单（只盘点）。

    不主动连 Docker daemon / 外部 API：规划器是只读工具，这里只报
    "哪些行**指向**外部资源"，实际回收是人工步骤。
    """
    from app.core.config import settings
    from app.models.organization import Employee

    external = {
        name: _count(db, sql, company_id) for name, sql in _EXTERNAL_ASSET_SQL.items()
    }

    # 磁盘目录：workspace 按 slug 命名、员工数据目录按 id 命名（与 dev_inventory 同键）。
    def _dirs(root: Path, names: list[str]) -> list[str]:
        if not root.exists():
            return []
        existing = sorted(entry.name for entry in root.iterdir() if entry.is_dir())
        return [name for name in names if name in existing]

    employees = db.scalars(
        sa.select(Employee).where(Employee.company_id == company_id)
    ).all()
    workspaces_root = Path(getattr(settings, "workspace_root", "./data/workspaces"))
    if not workspaces_root.is_absolute():
        workspaces_root = (SERVER_ROOT / workspaces_root).resolve()
    data_root = Path(getattr(settings, "data_root", "./data"))
    if not data_root.is_absolute():
        data_root = (SERVER_ROOT / data_root).resolve()

    external["workspace_dirs_on_disk"] = _dirs(
        workspaces_root, [str(employee.slug) for employee in employees]
    )
    external["employee_data_dirs_on_disk"] = _dirs(
        data_root / "employees", [str(employee.id) for employee in employees]
    )
    external["workspace_root"] = str(workspaces_root)
    external["note"] = (
        "磁盘目录与外部平台对象不会被数据库删除带走；回收是人工步骤，本工具只列清单。"
    )
    return external


def collect(db: Session, company_ids: list[int]) -> dict[str, Any]:
    """组装只读规划报告。全程不写。"""
    companies = list(
        db.execute(
            sa.text("SELECT id, name, slug, created_at FROM companies ORDER BY id")
        )
    )
    by_id = {int(row[0]): row for row in companies}
    missing = [cid for cid in company_ids if cid not in by_id]
    if missing:
        raise ValueError(
            f"公司 id 不存在：{sorted(missing)}。可用：{sorted(by_id)} —— 拒绝静默忽略"
        )

    sections: list[dict[str, Any]] = []
    for company_id in company_ids:
        company_id, name, slug, created = by_id[company_id]
        resources = company_counts(db, company_id)
        slots = slot_state(db, company_id)
        suspicion = suspected_probe({"slug": slug, "name": name}, resources, created)
        employees = employee_inventory(db, company_id)
        external = external_resource_impact(db, company_id)
        sections.append(
            {
                "company_id": int(company_id),
                "company_name": name,
                "company_slug": slug,
                "created_at": _as_datetime(created).isoformat()
                if _as_datetime(created)
                else None,
                # 用户验收要求的分类顺序，逐项清点：
                "inventory": {
                    "employees": employees,
                    "employments": resources.get("employment_count", 0),
                    "position_slots": slots["total"],
                    "slot_administrative": slots["administrative"],
                    "projects": resources.get("project_count", 0),
                    "documents": resources.get("document_count", 0),
                    "runtime_instances": resources.get("runtime_count", 0),
                    "git_connections": resources.get("git_connection_count", 0),
                    "tutorial_progress": resources.get("tutorial_progress_count", 0),
                    "external_resources": external,
                },
                "dependency_impact": dependency_impact(db, company_id),
                # probe 只作提示 —— 目标永远来自 COMPANY_IDS
                "probe_hints": {
                    "suspected_probe": suspicion["suspected_probe"],
                    "confidence": suspicion["confidence"],
                    "reasons": suspicion["reasons"],
                    "counter_evidence": suspicion["counter_evidence"],
                    "rule": suspicion["rule"],
                    "note": (
                        "informational only: hints never auto-target a company; "
                        "targets come exclusively from COMPANY_IDS."
                    ),
                },
            }
        )

    return {
        "tool": "eidolon-dev-cleanup-plan",
        "purpose": "read-only cleanup planning; no mutation of any kind",
        "write_policy": "none (sqlite opened with mode=ro)",
        "targets_from": (
            "explicit COMPANY_IDS only; suspected_probe is a hint and never auto-targets"
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "companies": sections,
        "no_data_modified": NO_DATA_MODIFIED,
    }


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    push = lines.append
    push(f"Eidolon 开发库清理计划（只读规划，不是清理执行） · {report['generated_at']}")
    push(f"写入策略：{report['write_policy']}")
    push(f"目标来源：{report['targets_from']}")
    push("")
    for section in report["companies"]:
        inv = section["inventory"]
        probe = section["probe_hints"]
        marker = "  ⚠ 探针特征（仅提示）" if probe["suspected_probe"] else ""
        push(
            f"Target Company: {section['company_name']}  (id={section['company_id']}, "
            f"{section['company_slug']}){marker}"
        )
        push(f"  created_at            {section['created_at']}")
        emp = inv["employees"]
        push(
            f"  Employees             {emp['total']}"
            f"  by_lifecycle={_fmt_map(emp.get('by_lifecycle', {}))}"
        )
        push(f"  Employments           {inv['employments']}")
        push(
            f"  Position Slots        {inv['position_slots']}"
            f"  administrative={_fmt_map(inv['slot_administrative'])}"
        )
        push(f"  Projects              {inv['projects']}")
        push(f"  Documents             {inv['documents']}")
        push(f"  Runtime Instances     {inv['runtime_instances']}")
        push(f"  Git Connections       {inv['git_connections']}")
        push(f"  Tutorial Progress     {inv['tutorial_progress']}")

        external = inv["external_resources"]
        push("  External Resources（数据库外，删除行不会自动回收）")
        for key, value in external.items():
            if key.endswith(("_root", "note")):
                continue
            if isinstance(value, dict):
                push(f"    {key:38s} {value.get('count', 0)}")
                if value.get("error"):
                    push(f"    {key:38s} 无法检查：{value['error']}")
            elif isinstance(value, list):
                push(
                    f"    {key:38s} {len(value)} 个目录"
                    + (f"：{', '.join(value[:5])}" if value else "")
                )
        if external.get("workspace_root"):
            push(f"    workspace_root        {external['workspace_root']}")
        push(f"    note: {external.get('note', '')}")

        push("  Dependency Impact（若删除该公司会连带消失的行，只读估计）")
        for group, table_sql in section["dependency_impact"].items():
            counts = {
                name: item["count"]
                for name, item in table_sql.items()
                if "error" not in item
            }
            errored = [name for name, item in table_sql.items() if "error" in item]
            push(f"    {group:18s} {_fmt_map(counts)}")
            if errored:
                push(f"    {group:18s} 无法检查：{', '.join(errored)}")
        push("  probe_hints（仅提示，不自动成为清理目标）")
        push(
            f"    suspected={probe['suspected_probe']} confidence={probe['confidence']}"
        )
        for reason in probe["reasons"]:
            push(f"    - 理由：{reason}")
        for item in probe["counter_evidence"]:
            push(f"    + 反证据：{item}")
        push(f"    note: {probe['note']}")
        push("")
    push(report["no_data_modified"])
    return "\n".join(lines)


def _fmt_map(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "-"
    return " ".join(f"{key}={value}" for key, value in sorted(mapping.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "只读清理规划器：显式公司 + 依赖/外部资源影响。"
            "不删除任何数据 —— 清理执行在后续独立阶段。"
        )
    )
    parser.add_argument(
        "--company",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="目标公司 id（必填，可多个：--company 1 3）",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--db", default=None, help="数据库 URL，默认取 EIDOLON_DATABASE_URL"
    )
    args = parser.parse_args(argv)

    from app.core.config import settings

    url = _resolve_database_url(args.db or settings.database_url)
    engine = build_engine(url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as db:
        try:
            report = collect(db, sorted(set(args.company)))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        report["database"] = url.replace(str(Path.home()), "~")
        assert not db.new and not db.dirty, "规划器产生了待写入对象 —— 越界"
        db.rollback()

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
