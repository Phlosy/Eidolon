#!/usr/bin/env python3
"""Development Data Inventory —— 开发库**只读**清点。

定位（用户拍板）：这是 **Inventory / Audit 工具，不是 cleanup 工具**。
它不删、不改、不补坑，也不给任何"这是测试数据"的判决 —— 只按公司口径把事实列出来，
并对可疑数据**给出理由**。判定与清理是 P4/P5 稳定之后另一个独立设计的东西
（需要 dry-run、显式 company id、确认、备份、事务）。

为什么需要它：本轮已经被"跨公司混算"坑过一次（P4a 报的 available 7 其实是 11 个
探针公司的合计）。所以这个工具的每一条统计都**必须**挂在某个公司下面，
全局合计只出现在最后，并且明确标注"仅诊断用"。

三条实现纪律：

1. **不重写判断。** 人数分布来自 `position_service.roster()`（与 `/talent-roster`
   完全同一条派生路径），任职完整性来自 `position_service.integrity()`。
   工具里再写一份 `LEFT JOIN` 规则，就会出现"清点页与名册页说法不一致"。
2. **物理只读。** SQLite 用 `mode=ro` 打开（`creator=` 强制），连 INSERT 都会被驱动拒绝；
   非 SQLite 退回普通连接但**永不 commit**，并在结尾断言 session 未被弄脏。
3. **可疑 ≠ 判定。** `suspected_probe` 永远伴随 `reasons`，且输出里没有 `verdict` 字段。

用法：

    make dev-inventory
    python scripts/dev_inventory.py                      # 人看的
    python scripts/dev_inventory.py --format json        # 机器读的
    python scripts/dev_inventory.py --company 1 --company 3
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import Session, sessionmaker

# 脚本可以放在 scripts/ 下跑：把 apps/server 加进 import 路径，
# 并把相对 sqlite 路径锚定到 server 根目录（否则从仓库根跑会指向不存在的 ./data）。
REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

GLOBAL_NOTE = (
    "Global totals are diagnostic only; "
    "business status must be interpreted within a company boundary."
)

#: 探针命名模式。刻意宽松（宁可多给理由），因为判定权在人；
#: 覆盖 dev 库里实际出现过的那几类：behavior-e2e-*、p4probe*、lifecycle-probe-*、spotlight-*。
PROBE_PATTERNS = [
    r"probe",
    r"\btest\b",
    r"^test",
    r"e2e",
    r"fixture",
    r"tmp",
    r"sample",
    r"demo",
    r"spotlight",
    r"p4[a-d]?probe",
    r"^\d+$",
]

#: owner 账号证据。刻意**不**把"登录过 / 邮箱已验证"当人类证据：
#: 集成测试走的正是同一条注册+验证流程，那两项对探针账号同样为真 —— 拿它当反证据
#: 会把 p4probe / lifecycle-probe 这类一眼假的公司洗白。真正区分人与脚本的是：
#: 邮箱本身像不像自动化产物、以及有没有留下使用痕迹（项目/文档/事件量）。
OWNER_SQL = """
SELECT COUNT(*)                                            AS owners,
       SUM(CASE WHEN u.email LIKE 'deleted-%@deleted.invalid' THEN 1 ELSE 0 END) AS deleted,
       SUM(CASE WHEN u.email LIKE '%@example.%'
                  OR u.email LIKE '%test%'
                  OR u.email LIKE '%probe%'
                  OR u.email LIKE '%e2e%'
                  OR u.display_name LIKE '%probe%'
                  OR u.display_name LIKE '%Tester%' THEN 1 ELSE 0 END) AS automation_like,
       SUM(CASE WHEN (u.email LIKE '%@gmail.com' OR u.email LIKE '%@qq.com'
                        OR u.email LIKE '%@mail.qq.com' OR u.email LIKE '%@163.com'
                        OR u.email LIKE '%@outlook.com' OR u.email LIKE '%@icloud.com')
                  AND u.email NOT LIKE '%@example.%'
                  AND u.email NOT LIKE '%test%'
                  AND u.email NOT LIKE '%probe%'
                  AND u.email NOT LIKE '%e2e%'
                  AND u.display_name NOT LIKE '%probe%'
                  AND u.display_name NOT LIKE '%Tester%' THEN 1 ELSE 0 END) AS human_like
FROM company_memberships m
JOIN users u ON u.id = m.user_id
WHERE m.company_id = :company_id
"""

#: 与默认公司共享的人类邮箱（同一个人的第二家公司 ⇒ 强反证据）
SHARED_OWNER_SQL = """
SELECT COUNT(*)
FROM company_memberships a
JOIN users u ON u.id = a.user_id
JOIN company_memberships b ON b.user_id = a.user_id AND b.company_id = :default_company_id
WHERE a.company_id = :company_id
  AND a.company_id <> :default_company_id
  AND u.email NOT LIKE '%@example.%'
  AND u.email NOT LIKE '%test%'
"""


def _resolve_database_url(raw: str) -> str:
    """把相对 sqlite 路径锚定到 apps/server（与 uvicorn 的 cwd 一致）。"""
    prefix = "sqlite:///./"
    if raw.startswith(prefix):
        return f"sqlite:///{(SERVER_ROOT / raw[len(prefix) :]).resolve()}"
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        tail = raw[len("sqlite:///") :]
        if tail and not tail.startswith("/") and tail != ":memory:":
            return f"sqlite:///{(SERVER_ROOT / tail).resolve()}"
    return raw


def _sqlite_path(url: str) -> str | None:
    if not url.startswith("sqlite:///"):
        return None
    tail = url[len("sqlite:///") :]
    return None if tail == ":memory:" else tail


def build_engine(url: str):
    """只读引擎。SQLite 走 `mode=ro`，写操作在驱动层就会被拒。"""
    path = _sqlite_path(url)
    if path is not None:
        return sa.create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(
                f"file:{path}?mode=ro", uri=True, check_same_thread=False
            ),
        )
    # 非 SQLite（postgres）没有等价的一键只读，退化为"永不 commit"，
    # 由 `assert_session_clean()` 兜住；真正的只读靠部署账号权限。
    return sa.create_engine(url)


def _probe_like(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(pattern, lowered) for pattern in PROBE_PATTERNS)


def _scalar(db: Session, sql: str, **params) -> int:
    return int(db.execute(sa.text(sql), params).scalar() or 0)


def company_counts(db: Session, company_id: int) -> dict[str, int]:
    per_company = {
        "employee_count": """
            SELECT COUNT(*) FROM employees WHERE company_id = :company_id""",
        "active_employee_count": """
            SELECT COUNT(*) FROM employees
            WHERE company_id = :company_id AND lifecycle_status = 'active'""",
        "department_count": "SELECT COUNT(*) FROM departments WHERE company_id = :company_id",
        "position_definition_count": """
            SELECT COUNT(*) FROM position_definitions WHERE company_id = :company_id""",
        # 全局模板（company_id IS NULL）单独一列：混进上面那条就成"本公司的定义"了
        "position_definition_shared_count": """
            SELECT COUNT(*) FROM position_definitions WHERE company_id IS NULL""",
        "position_slot_count": "SELECT COUNT(*) FROM position_slots WHERE company_id = :company_id",
        "project_count": "SELECT COUNT(*) FROM projects WHERE company_id = :company_id",
        "drive_node_count": "SELECT COUNT(*) FROM drive_nodes WHERE company_id = :company_id",
        "document_count": """
            SELECT COUNT(*) FROM drive_nodes
            WHERE company_id = :company_id AND kind = 'document'""",
        "git_connection_count": """
            SELECT COUNT(*) FROM git_connections WHERE company_id = :company_id""",
        "tutorial_progress_count": """
            SELECT COUNT(*) FROM tutorial_progress WHERE company_id = :company_id""",
        "employment_count": """
            SELECT COUNT(*) FROM employments a
            JOIN employees e ON e.id = a.employee_id WHERE e.company_id = :company_id""",
        "active_employment_count": """
            SELECT COUNT(*) FROM employments a
            JOIN employees e ON e.id = a.employee_id
            WHERE e.company_id = :company_id AND a.effective_to IS NULL""",
        "runtime_count": """
            SELECT COUNT(*) FROM runtime_instances r
            JOIN employees e ON e.id = r.employee_id WHERE e.company_id = :company_id""",
        "resource_account_count": """
            SELECT COUNT(*) FROM resource_accounts r
            JOIN employees e ON e.id = r.employee_id WHERE e.company_id = :company_id""",
        "model_binding_count": """
            SELECT COUNT(*) FROM model_bindings b
            JOIN employees e ON e.id = b.employee_id WHERE e.company_id = :company_id""",
        "event_count": "SELECT COUNT(*) FROM events WHERE company_id = :company_id",
    }
    result = {
        key: _scalar(db, sql, company_id=company_id) for key, sql in per_company.items()
    }
    # v0.4 的 positions 挂在部门下，所以按部门归属
    result["legacy_position_count"] = _scalar(
        db,
        """
        SELECT COUNT(*) FROM positions p
        JOIN departments d ON d.id = p.department_id
        WHERE d.company_id = :company_id""",
        company_id=company_id,
    )
    # 全局资源没有公司列，单独报（否则会被误读成"这家公司只有 3 个 provider"）
    result["global_provider_count"] = _scalar(
        db, "SELECT COUNT(*) FROM resource_providers"
    )
    row = db.execute(sa.text(OWNER_SQL), {"company_id": company_id}).first()
    result["owner_accounts"] = int(row[0] or 0)
    result["owner_accounts_deleted"] = int(row[1] or 0)
    result["owner_accounts_automation_like"] = int(row[2] or 0)
    result["owner_accounts_human_like"] = int(row[3] or 0)
    default_company_id = db.scalar(
        sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")
    )
    result["owner_accounts_shared_with_default"] = (
        _scalar(
            db,
            SHARED_OWNER_SQL,
            company_id=company_id,
            default_company_id=int(default_company_id or 0),
        )
        if default_company_id is not None
        else 0
    )
    return result


def slot_state(db: Session, company_id: int) -> dict[str, int]:
    """坑的两个轴：行政态计数 + 派生占用态计数。占用态走仓库现成派生，不自己 JOIN。"""
    from app.models.enums import OccupancyStatus
    from app.repositories import position as position_repo

    slots = position_repo.list_slots(db, company_id)
    occupancy = position_repo.occupancy_map(db, slots)
    by_admin: dict[str, int] = {}
    for slot in slots:
        by_admin[slot.administrative_status] = (
            by_admin.get(slot.administrative_status, 0) + 1
        )
    return {
        "total": len(slots),
        "occupied": sum(
            1 for slot in slots if occupancy[slot.id] == OccupancyStatus.occupied
        ),
        "vacant": sum(
            1 for slot in slots if occupancy[slot.id] == OccupancyStatus.vacant
        ),
        "frozen": sum(
            1 for slot in slots if occupancy[slot.id] == OccupancyStatus.frozen
        ),
        "closed": sum(
            1 for slot in slots if occupancy[slot.id] == OccupancyStatus.closed
        ),
        "administrative": by_admin,
    }


def workforce_state(db: Session, company_id: int) -> dict[str, Any]:
    """人数分布 —— 直接调名册读面，保证与 `GET /talent-roster` 一模一样。

    这里刻意不用 `WorkforceStatusResolver.counts()`：那个是**全库**口径的便利函数，
    正是它让上一轮统计漏掉了公司边界。
    """
    from app.services import position_service

    entries = position_service.roster(db, company_id)
    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry["workforce_status"]] = (
            by_status.get(entry["workforce_status"], 0) + 1
        )
    return {
        "total": len(entries),
        "by_status": by_status,
        "on_roster": sum(
            1
            for entry in entries
            if entry["workforce_status"] in {"assigned", "available", "transferring"}
        ),
        "occupying_establishment": sum(
            1 for entry in entries if entry["occupies_establishment"]
        ),
    }


def assignment_integrity(db: Session, company_id: int) -> dict[str, Any]:
    """任职完整性 —— 复用 `position_service.integrity()`，一种判断都不重写。"""
    from app.services import position_service

    report = position_service.integrity(db, company_id)
    # VALID 不从 integrity 里造新数：名册每行已经带了同一个报告的逐人切片
    entries = position_service.roster(db, company_id)
    valid = sum(1 for entry in entries if not entry["integrity"])
    kinds = {item.kind for item in report["items"]}
    return {
        "entries_scanned": len(entries),
        "valid": valid,
        "problem_entries": len(entries) - valid,
        "counts": dict(report["counts"]),
        "kinds_seen": sorted(kinds),
        "read_only": report["read_only"],
        "source": "app.services.position_service.integrity + roster[].integrity",
    }


def suspected_probe(
    company: dict[str, Any], counts: dict[str, int], created_at: datetime | None
) -> dict[str, Any]:
    """给理由，也给反理由；单条弱证据不构成嫌疑。

    这条阈值是实测逼出来的：真实公司 `eidolon-studio` 一开始被单独一条
    "没有人类 owner 活动"标成了疑似探针 —— dev 库里它的账号确实没走过登录流程。
    "看起来像测试数据"这个直觉经常错（旧语义留下的合法历史长得和脏数据一模一样），
    所以宁可少标、把理由摊开给人看。
    """
    reasons: list[str] = []
    counter: list[str] = []
    label = f"{company['slug']} / {company['name']}"
    if _probe_like(label):
        reasons.append(f"name/slug 命中探针命名模式：{label}")
    if counts.get("owner_accounts", 0) == 0:
        reasons.append("没有任何 owner 账号（公司是被服务/脚本直接建出来的）")
    if counts.get("owner_accounts_deleted", 0) > 0:
        reasons.append(
            f"{counts['owner_accounts_deleted']} 个 owner 账号已注销"
            "（deleted-*.invalid：这家公司是账号删除流程留下的现场，不代表真实组织）"
        )
    if (
        counts.get("owner_accounts_automation_like", 0) > 0
        and counts.get("owner_accounts_shared_with_default", 0) == 0
    ):
        reasons.append(
            f"{counts['owner_accounts_automation_like']}/{counts['owner_accounts']} 个 owner 邮箱"
            "命中自动化命名（example/test/probe/e2e）"
        )
    if counts.get("owner_accounts_shared_with_default", 0) > 0:
        counter.append(
            f"{counts['owner_accounts_shared_with_default']} 个 owner 与主公司同一邮箱"
            "（同一个人的第二家公司，不是脚本）"
        )
    if counts["project_count"] == 0:
        reasons.append("没有任何项目")
    else:
        counter.append(f"{counts['project_count']} 个项目（真实工作痕迹）")
    if counts["event_count"] == 0:
        reasons.append("没有任何业务事件（events 表 0 行）")
    elif counts["event_count"] >= 50:
        counter.append(f"{counts['event_count']} 条业务事件")
    if counts["document_count"] > 0:
        counter.append(f"{counts['document_count']} 篇文档")
    if counts["employee_count"] == 0:
        reasons.append("没有员工")
    elif counts["active_employee_count"] == 0:
        reasons.append(
            f"{counts['employee_count']} 名员工无一处于 active（全停在入册途中）"
        )
    if counts["position_slot_count"] == 0 and counts["employee_count"] > 0:
        reasons.append("有员工却没有任何编制")
    # 阈值：**两条以上**正证据、且没有强反证据，才算嫌疑
    if counts.get("owner_accounts_human_like", 0) > 0:
        counter.append(
            f"{counts['owner_accounts_human_like']} 个 owner 是常见邮箱域名下的真人账号"
            "（gmail/qq/163/outlook/icloud，且命名不像脚本）"
        )
    strong_counter = [
        item
        for item in counter
        if "项目" in item or "文档" in item or "同一邮箱" in item or "真人账号" in item
    ]
    suspected = len(reasons) >= 2 and not strong_counter
    return {
        "suspected_probe": suspected,
        "confidence": (
            "none" if not reasons else ("low" if not suspected else "medium")
        ),
        "reasons": reasons,
        "counter_evidence": counter,
        "rule": "suspected = ≥2 条正证据 且 无强反证据（项目/文档/与主公司同一邮箱/真人邮箱）",
        # 没有 verdict / is_test / delete 字段是刻意的（见 tests/test_dev_inventory.py）
        "note": "理由清单不等于判定；是否算脏数据由人看，清理另有 cleanup workflow。",
    }


def _clustered_created(
    created_pairs: list[tuple[int, datetime]], window: timedelta
) -> set[int]:
    """同一时间窗里批量冒出来的公司 —— `created during integration test window` 的证据。"""
    hits: set[int] = set()
    ordered = sorted(created_pairs, key=lambda item: item[1])
    for index, (company_id, moment) in enumerate(ordered):
        neighbours = [
            other_id
            for other_id, other in ordered[index + 1 :]
            if other - moment <= window
        ]
        if len(neighbours) >= 2:
            hits.update([company_id, *neighbours])
    return hits


def orphan_candidates(db: Session) -> dict[str, Any]:
    """结构孤儿（只列，不修）。

    每条都是 `{count, samples}`：把样本数当计数是最容易骗到自己的一种报告写法 ——
    第一版就是 `LIMIT 5` 之后直接 `len()`，于是 11 条显示成"5 条"。
    """
    checks = {
        "runtime_without_employee": """
            SELECT r.id FROM runtime_instances r
            LEFT JOIN employees e ON e.id = r.employee_id
            WHERE e.id IS NULL""",
        "model_binding_without_employee": """
            SELECT b.id FROM model_bindings b
            LEFT JOIN employees e ON e.id = b.employee_id
            WHERE e.id IS NULL""",
        "resource_account_without_employee": """
            SELECT a.id FROM resource_accounts a
            LEFT JOIN employees e ON e.id = a.employee_id
            WHERE e.id IS NULL""",
        "employee_package_without_employee": """
            SELECT p.id FROM employee_packages p
            WHERE p.employee_id NOT IN (SELECT id FROM employees)""",
        "brain_without_employee": """
            SELECT b.id FROM employee_brains b
            WHERE b.employee_id NOT IN (SELECT id FROM employees)""",
        "employment_without_employee": """
            SELECT a.id FROM employments a
            WHERE a.employee_id NOT IN (SELECT id FROM employees)""",
        "assignment_referencing_missing_slot": """
            SELECT a.id FROM employments a
            WHERE a.position_slot_id IS NOT NULL
              AND a.position_slot_id NOT IN (SELECT id FROM position_slots)""",
        "slot_referencing_missing_definition": """
            SELECT s.id FROM position_slots s
            WHERE s.position_definition_id NOT IN (SELECT id FROM position_definitions)""",
        "slot_outside_company_of_department": """
            SELECT s.id FROM position_slots s
            JOIN departments d ON d.id = s.department_id
            WHERE s.company_id <> d.company_id""",
        "employee_without_department": """
            SELECT e.id FROM employees e
            WHERE e.department_id IS NOT NULL
              AND e.department_id NOT IN (SELECT id FROM departments)""",
        "employee_without_company": """
            SELECT e.id FROM employees e
            WHERE e.company_id NOT IN (SELECT id FROM companies)""",
        "department_without_company": """
            SELECT d.id FROM departments d
            WHERE d.company_id NOT IN (SELECT id FROM companies)""",
        "drive_node_without_company": """
            SELECT n.id FROM drive_nodes n
            WHERE n.company_id IS NOT NULL
              AND n.company_id NOT IN (SELECT id FROM companies)""",
        "drive_document_without_owner": """
            SELECT n.id FROM drive_nodes n
            WHERE n.kind = 'document' AND n.owner_employee_id IS NOT NULL
              AND n.owner_employee_id NOT IN (SELECT id FROM employees)""",
        "membership_without_company": """
            SELECT m.id FROM company_memberships m
            WHERE m.company_id NOT IN (SELECT id FROM companies)""",
        # 按拍板**保留原样**的一类：v0.4 遗留的无坑生效主职。列在这里是为了让它可见，
        # 不是为了被"修"—— 补编制是业务动作（P4 分配工作流），不是数据修补。
        "active_primary_without_slot": """
            SELECT a.id FROM employments a
            WHERE a.effective_to IS NULL AND a.assignment_type = 'primary'
              AND a.position_slot_id IS NULL""",
    }
    out: dict[str, Any] = {}
    for name, sql in checks.items():
        try:
            ids = [int(row[0]) for row in db.execute(sa.text(sql))]
            out[name] = {"count": len(ids), "samples": ids[:5]}
        except sa_exc.SQLAlchemyError as exc:
            # 表缺失/列名变了：把原因写进报告，而不是让清点工具在半夜把自己弄崩
            out[name] = {
                "count": 0,
                "samples": [],
                "error": str(exc).splitlines()[0][:140],
            }
    out.update(_workspace_orphans(db))
    out["_note"] = (
        "只列不修。`active_primary_without_slot` 是 11 条 v0.4 遗留合法历史，"
        "按拍板保持原样；补编制走分配工作流，不在工具里改数据。"
    )
    return out


def _abs(path: str | Path) -> Path:
    """相对路径按 apps/server 解析 —— uvicorn 的 cwd 就是它，脚本从仓库根跑也不能指错。"""
    candidate = Path(str(path))
    return candidate if candidate.is_absolute() else (SERVER_ROOT / candidate).resolve()


def _workspace_orphans(db: Session) -> dict[str, Any]:
    """工作区两个方向都查：人有记录但目录不在、目录在但没人。

    根目录从 settings 取并**锚定到 apps/server**（第一版直接拿相对路径 `./data/workspaces`
    去 `exists()`，从仓库根跑就永远"不存在"，于是把只读检查报成了失败）。
    磁盘侧只 `exists()` / `iterdir()`，不写不删。
    """
    from app.core.config import settings

    workspaces_root = _abs(getattr(settings, "workspace_root", "./data/workspaces"))
    employee_data_root = _abs(
        Path(getattr(settings, "data_root", "./data")) / "employees"
    )
    rows = list(
        db.execute(
            sa.text("SELECT id, slug, workspace_path, company_id FROM employees")
        )
    )
    slugs = {str(slug) for _id, slug, _path, _company in rows}
    # 员工数据目录是按 **id** 命名的（`data/employees/{id}/brain/...`），
    # 工作区目录是按 **slug** 命名的（`data/workspaces/{slug}`）。
    # 拿一套键去比另一套目录，就会把 24 个正常目录全报成孤儿（第一版正是这样）。
    ids = {str(employee_id) for employee_id, _slug, _path, _company in rows}

    missing = []
    for employee_id, slug, workspace_path, company_id in rows:
        target = _abs(workspace_path or workspaces_root / str(slug))
        if not target.exists():
            missing.append(
                {
                    "employee_id": int(employee_id),
                    "company_id": company_id,
                    "slug": slug,
                    "stored_path": workspace_path,
                    "resolved_path": str(target),
                }
            )

    def stray_dirs(root: Path, keys: set[str]) -> list[dict[str, Any]]:
        if not root.exists():
            return [{"error": f"根目录不存在：{root}"}]
        return [
            {"dir": entry.name}
            for entry in sorted(root.iterdir())
            if entry.is_dir() and entry.name not in keys
        ]

    return {
        "workspace_missing_on_disk": {
            "count": len(missing),
            "samples": missing[:5],
            "root": str(workspaces_root),
        },
        "workspace_dir_without_employee": {
            "count": len(stray_dirs(workspaces_root, slugs)),
            "samples": stray_dirs(workspaces_root, slugs)[:5],
            "root": str(workspaces_root),
            "keyed_by": "slug",
        },
        "employee_data_dir_without_employee": {
            "count": len(stray_dirs(employee_data_root, ids)),
            "samples": stray_dirs(employee_data_root, ids)[:5],
            "root": str(employee_data_root),
            "keyed_by": "employee_id",
        },
    }


def collect(
    db: Session, company_ids: list[int] | None, window_minutes: int
) -> dict[str, Any]:
    """组装报告。全程只读。"""
    companies = list(
        db.execute(
            sa.text("SELECT id, name, slug, created_at FROM companies ORDER BY id")
        )
    )
    rows = [row for row in companies if company_ids is None or row[0] in company_ids]

    created_pairs: list[tuple[int, datetime]] = []
    for row in companies:  # 聚类窗口用全量公司算，否则"批量造公司"这条证据永远不成立
        moment = _as_datetime(row[3])
        if moment is not None:
            created_pairs.append((int(row[0]), moment))
    clustered = _clustered_created(created_pairs, timedelta(minutes=window_minutes))

    sections: list[dict[str, Any]] = []
    for company_id, name, slug, created in rows:
        counts = company_counts(db, company_id)
        # owner 证据已经在 company_counts() 里一起取，避免两处 SQL 漂移
        roster = workforce_state(db, company_id)
        slots = slot_state(db, company_id)
        integrity = assignment_integrity(db, company_id)
        suspicion = suspected_probe(
            {"slug": slug, "name": name}, counts, _as_datetime(created)
        )
        if int(company_id) in clustered:
            suspicion["reasons"].append(
                f"创建时间与 ≥3 家其它公司落在同一 {window_minutes} 分钟窗口内（批量生成特征）"
            )
            # 批量时间窗是硬线索：它可以独立成立（一次集成测试就是会批量造公司）
            if len(suspicion["reasons"]) >= 2:
                suspicion["suspected_probe"] = True
                suspicion["confidence"] = "medium"
        sections.append(
            {
                "company_id": int(company_id),
                "company_name": name,
                "company_slug": slug,
                "created_at": _iso(created),
                "scope": "company",
                "talent_roster": roster,
                "assignment_integrity": integrity,
                "position_slots": slots,
                "resources": counts,
                "suspected_probe": suspicion,
            }
        )

    totals = {
        "company_count": len(sections),
        "employee_count": sum(
            section["talent_roster"]["total"] for section in sections
        ),
        "assigned": sum(
            section["talent_roster"]["by_status"].get("assigned", 0)
            for section in sections
        ),
        "available": sum(
            section["talent_roster"]["by_status"].get("available", 0)
            for section in sections
        ),
        "position_slot_count": sum(
            section["position_slots"]["total"] for section in sections
        ),
        "occupied_slot_count": sum(
            section["position_slots"]["occupied"] for section in sections
        ),
        "vacant_slot_count": sum(
            section["position_slots"]["vacant"] for section in sections
        ),
        "project_count": sum(
            section["resources"]["project_count"] for section in sections
        ),
        "integrity_problems": sum(
            sum(section["assignment_integrity"]["counts"].values())
            for section in sections
        ),
        "suspected_probe_companies": sum(
            1 for section in sections if section["suspected_probe"]["suspected_probe"]
        ),
    }
    # 逐公司相加，所以 totals 天然可核对；不一致说明有人往脚本里塞了全库查询
    recomputed = sum(section["talent_roster"]["total"] for section in sections)
    assert recomputed == totals["employee_count"], (
        "全局合计与逐公司之和不一致 —— 口径漏了"
    )

    return {
        "tool": "eidolon-dev-inventory",
        "purpose": "read-only inventory; not a cleanup tool",
        "write_policy": "none (sqlite opened with mode=ro)",
        "scope_rule": "every figure above is per-company; global totals are at the end",
        "global_note": GLOBAL_NOTE,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "companies": sections,
        "orphan_candidates": orphan_candidates(db),
        "global_totals": totals,
    }


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:19])
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    moment = _as_datetime(value)
    return moment.isoformat() if moment else None


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    push = lines.append
    push(f"Eidolon 开发库清点 · {report['generated_at']}")
    push(f"写入策略：{report['write_policy']}")
    push("")
    for section in report["companies"]:
        roster = section["talent_roster"]
        slots = section["position_slots"]
        integrity = section["assignment_integrity"]
        suspicion = section["suspected_probe"]
        marker = "  ⚠ 疑似探针" if suspicion["suspected_probe"] else ""
        push(
            f"Company: {section['company_name']}  (id={section['company_id']}, {section['company_slug']}){marker}"
        )
        push(f"  created_at            {section['created_at']}")
        push(
            f"  Talent Roster         {roster['total']}  → {_fmt_map(roster['by_status'])}"
        )
        push(
            f"    on_roster / 在编制   {roster['on_roster']} / {roster['occupying_establishment']}"
        )
        push(
            f"  Position Slots        {slots['total']}"
            f"  occupied={slots['occupied']} vacant={slots['vacant']}"
            f" frozen={slots['frozen']} closed={slots['closed']}"
        )
        push(
            "  Assignment Integrity  "
            f"{integrity['valid']} valid / {integrity['problem_entries']} problem"
            f" · {_fmt_map(integrity['counts'])}  (read_only={integrity['read_only']})"
        )
        resources = section["resources"]
        if resources.get("position_definition_shared_count"):
            push(
                f"  Shared definitions    {resources['position_definition_shared_count']}"
                "  (company_id IS NULL，全局模板，不计入本公司自有)"
            )
        push(
            f"  Resources             employees={resources['employee_count']}"
            f"(active {resources['active_employee_count']})"
            f" employments={resources['employment_count']}"
            f" definitions={resources['position_definition_count']}"
            f" projects={resources['project_count']}"
            f" runtimes={resources['runtime_count']}"
            f" documents={resources['document_count']}"
            f" git={resources['git_connection_count']}"
            f" tutorial={resources['tutorial_progress_count']}"
            f" events={resources['event_count']}"
        )
        push(
            f"  Departments           {resources['department_count']}"
            f" · legacy positions {resources['legacy_position_count']}"
        )
        push(
            f"  suspected_probe       {suspicion['suspected_probe']}"
            f" (confidence={suspicion['confidence']}, {suspicion['rule']})"
        )
        for reason in suspicion["reasons"]:
            push(f"    - 理由：{reason}")
        for item in suspicion["counter_evidence"]:
            push(f"    + 反证据：{item}")
        push("")
    orphans = report["orphan_candidates"]
    push("Orphan Candidates（只列，不修）")
    listed = 0
    checked = 0
    for key, value in orphans.items():
        if key.startswith("_"):
            continue
        checked += 1
        if value.get("count"):
            listed += 1
            push(f"  {key:36s} {value['count']} 条  样例={value['samples'][:3]}")
        else:
            push(f"  {key:36s} 0 条")
        for sample in value.get("samples", []):
            if isinstance(sample, dict) and "error" in sample:
                push(f"  {key:36s} 无法检查：{sample['error']}")
    push(f"  （共检查 {checked} 类；0 条只代表该类没查到问题，不代表做过修复）")
    push(f"  note: {orphans.get('_note', '')}")
    push("")
    push("GLOBAL TOTAL（跨公司相加）")
    for key, value in report["global_totals"].items():
        push(f"  {key:26s} {value}")
    push("")
    push(f"  {report['global_note']}")
    return "\n".join(lines)


def _fmt_map(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "-"
    return " ".join(f"{key}={value}" for key, value in sorted(mapping.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--db", default=None, help="数据库 URL，默认取 EIDOLON_DATABASE_URL"
    )
    parser.add_argument(
        "--company",
        action="append",
        type=int,
        default=None,
        help="只看指定公司（可重复）",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=30,
        help="判定「批量生成」的时间窗（默认 30 分钟）",
    )
    args = parser.parse_args(argv)

    from app.core.config import settings

    url = _resolve_database_url(args.db or settings.database_url)
    engine = build_engine(url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as db:
        report = collect(db, args.company, args.window_minutes)
        report["database"] = url.replace(str(Path.home()), "~")
        # 只读断言：跑到这里 session 必须是干净的（没有 new/dirty 对象）
        assert not db.new and not db.dirty, "清点工具产生了待写入对象 —— 越界"
        db.rollback()

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
