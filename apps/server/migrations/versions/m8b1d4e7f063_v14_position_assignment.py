"""v14.employments 成为 PositionAssignment 的时间轴

拍板结论（docs/workforce-domain-refactor.md §11 / docs/position-system.md §2.4）：
**物理表继续叫 `employments`，领域实体叫 `PositionAssignment`**。所以本迁移
不做 `rename_table`（SQLite 改名会牵扯引用它的 FK 子句，是本次设计里风险最高、
收益最低的一步），只做四件事：

1. `ADD COLUMN`：`position_slot_id` / `assignment_type` / `is_primary` / `assigned_by`
   / `reason` / `position_title_snapshot`。既有列与既有行**一条都不动**
   （`id`、`employee_id`、`department_id`、`position_id`、`manager_employee_id`、
   `joined_at`、`effective_from/to`、`metadata_json` 原值保留）。
2. 回填：老任职一律视为 `primary`（v0.4 语义里确实只有主职）；
   `slot` 用 v13 存在 `position_slots.metadata_json.__position_id` 的对应关系还原，
   **不在这里重跑一遍映射规则**（两处实现迟早漂移）；`position_title_snapshot`
   从旧 `positions.title` 取快照，让定义将来改名也不会篡改历史读感。
3. **先清洗、再建索引**：部分唯一索引要求"同一员工同时只有一条生效 PRIMARY"
   "同一个坑同时只有一条生效 PRIMARY"。存量若有重叠（真实数据里会出现：
   转岗脚本中断留下两条 `effective_to IS NULL`），保留 `effective_from` 最新的一条，
   其余关窗并置 `superseded`，在 `metadata_json` 留下 `normalized` 痕迹，
   并把清洗条数打到迁移输出 —— **不静默改写历史**。
4. 建索引：普通索引两条 + 部分唯一索引两条（`WHERE effective_to IS NULL`）。
   部分唯一索引只约束 PRIMARY，因此 `acting` / `secondary` 将来天然可用，不需再改表。

Verify: `upgrade head` → `downgrade -4` → `upgrade head`；且 `tests/test_position_history.py`
在真实带数据的库上断言员工/资源/历史行数与 id 集合逐项不变。
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "m8b1d4e7f063"
down_revision = "l7a0c3d6e952"
branch_labels = None
depends_on = None

NEW_COLUMNS = {
    "position_slot_id": sa.Column("position_slot_id", sa.Integer(), nullable=True),  # 无 FK：见注释
    "assignment_type": sa.Column(
        "assignment_type", sa.String(length=20), nullable=False, server_default=sa.text("'primary'")
    ),
    "is_primary": sa.Column(
        "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("1")
    ),
    "assigned_by": sa.Column("assigned_by", sa.Integer(), nullable=True),
    "reason": sa.Column(
        "reason", sa.String(length=500), nullable=False, server_default=sa.text("''")
    ),
    "position_title_snapshot": sa.Column(
        "position_title_snapshot",
        sa.String(length=200),
        nullable=False,
        server_default=sa.text("''"),
    ),
}
PARTIAL_INDEXES = {
    # 一个坑同时最多一人（只约束主职 ⇒ 兼任/代理不需要再改表）
    "uq_employment_slot_primary": (
        "position_slot_id",
        "position_slot_id IS NOT NULL AND effective_to IS NULL AND assignment_type = 'primary'",
    ),
    # 一人同时只有一个主职
    "uq_employment_employee_primary": (
        "employee_id",
        "effective_to IS NULL AND assignment_type = 'primary' AND is_primary = 1",
    ),
}


def _as_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "employments" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("employments")}
    missing = [name for name in NEW_COLUMNS if name not in existing]
    if missing:
        # 原生 ADD COLUMN 即可：新列都可空或带库级默认值，不需要 batch 重建历史表
        # （见 app/models/position.py 里 position_slot_id 的注释：FK 在 SQLite 上从不生效，
        #  而 batch 重建会因匿名外键约束报 "Constraint must have a name"）。
        for name in missing:
            op.add_column("employments", NEW_COLUMNS[name])

    backfilled = _backfill(bind)
    normalized = _normalize(bind)
    _create_indexes(bind)
    print(f"[v14] employments → PositionAssignment：回填 {backfilled} 行")
    print(
        f"[v14] 重叠主职清洗：employee 轴 {normalized['employee']} 条、"
        f"slot 轴 {normalized['slot']} 条（均在 metadata_json.normalized 留痕）"
    )


def _backfill(bind) -> int:
    slot_of_position: dict[int, int] = {}
    if "position_slots" in set(sa.inspect(bind).get_table_names()):
        for row in bind.execute(sa.text("SELECT id, metadata_json FROM position_slots")):
            marker = _as_dict(row.metadata_json).get("__position_id")
            if isinstance(marker, int):
                slot_of_position[marker] = row.id

    titles = (
        {row.id: row.title for row in bind.execute(sa.text("SELECT id, title FROM positions"))}
        if "positions" in set(sa.inspect(bind).get_table_names())
        else {}
    )

    pending = bind.execute(
        sa.text(
            "SELECT id, position_id, metadata_json FROM employments "
            "WHERE position_slot_id IS NULL OR position_title_snapshot = '' "
            "OR reason = '' OR assignment_type IS NULL"
        )
    ).all()
    for row in pending:
        metadata = _as_dict(row.metadata_json)
        bind.execute(
            sa.text(
                "UPDATE employments SET position_slot_id = :slot, assignment_type = 'primary', "
                "is_primary = 1, reason = :reason, position_title_snapshot = :title "
                "WHERE id = :id"
            ),
            {
                "slot": slot_of_position.get(row.position_id),
                "reason": str(metadata.get("kind") or ""),
                "title": titles.get(row.position_id or 0) or "",
                "id": row.id,
            },
        )
    return len(pending)


def _normalize(bind) -> dict[str, int]:
    """关闭重复的生效主职：保留 `effective_from` 最新（同值取 id 最大）的一条。

    决策是确定性的，且被关窗的行**不删**，只 `effective_to` + `superseded` + 留痕。
    """
    counts = {"employee": 0, "slot": 0}
    for axis, column in (("employee", "employee_id"), ("slot", "position_slot_id")):
        where = "employee_id IS NOT NULL" if axis == "employee" else "position_slot_id IS NOT NULL"
        rows = bind.execute(
            sa.text(
                f"SELECT id, {column} AS bucket, effective_from, metadata_json FROM employments "
                "WHERE effective_to IS NULL AND assignment_type = 'primary' "
                f"AND {where} ORDER BY {column}, effective_from DESC, id DESC"
            )
        ).all()
        seen: set[tuple] = set()
        for row in rows:
            if row.bucket in seen:
                keep = bind.execute(
                    sa.text(
                        "SELECT id, effective_from FROM employments WHERE effective_to IS NULL "
                        "AND assignment_type = 'primary' AND "
                        + (
                            "employee_id = :bucket"
                            if axis == "employee"
                            else "position_slot_id = :bucket"
                        )
                        + " ORDER BY effective_from DESC, id DESC LIMIT 1"
                    ),
                    {"bucket": row.bucket},
                ).one()
                metadata = _as_dict(row.metadata_json)
                metadata["normalized"] = {
                    "by": "v14",
                    "axis": axis,
                    "kept_assignment_id": keep.id,
                    "reason": "duplicate active primary assignment",
                }
                bind.execute(
                    sa.text(
                        "UPDATE employments SET effective_to = :close_at, "
                        "employment_status = 'superseded', metadata_json = :metadata "
                        "WHERE id = :id"
                    ),
                    {
                        "close_at": keep.effective_from,
                        "metadata": json.dumps(metadata, ensure_ascii=False),
                        "id": row.id,
                    },
                )
                counts[axis] += 1
            else:
                seen.add(row.bucket)
    return counts


def _create_indexes(bind) -> None:
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("employments")}
    if "ix_employments_effective_to" not in existing:
        op.create_index("ix_employments_effective_to", "employments", ["effective_to"])
    if "ix_employments_position_slot_id" not in existing:
        op.create_index("ix_employments_position_slot_id", "employments", ["position_slot_id"])
    # 直接在 alembic 的连接上执行：迁移已经跑在一个事务里，
    # 这里再 `bind.begin()` 会撞上 "already initialized a Transaction"。
    for name, (column, predicate) in PARTIAL_INDEXES.items():
        exists = bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name = :name"),
            {"name": name},
        ).first()
        if exists is None:
            bind.execute(
                sa.text(f"CREATE UNIQUE INDEX {name} ON employments ({column}) WHERE {predicate}")
            )


def downgrade() -> None:
    bind = op.get_bind()
    if "employments" not in set(sa.inspect(bind).get_table_names()):
        return
    for name in PARTIAL_INDEXES:
        bind.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("employments")}
    for name in ("ix_employments_position_slot_id", "ix_employments_effective_to"):
        if name in existing:
            op.drop_index(name, table_name="employments")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("employments")}
    present = [name for name in NEW_COLUMNS if name in columns]
    if present:
        # 逆序删除，保持与新增相反的顺序（SQLite 3.35+ / PG 都支持 DROP COLUMN）
        for name in reversed(present):
            op.drop_column("employments", name)
