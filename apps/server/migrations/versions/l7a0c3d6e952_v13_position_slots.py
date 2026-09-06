"""v13.position_slots

编制层（docs/position-system.md §2.3）：`positions` 只是"部门里的一个头衔"，
它无法回答"Engineering 的 Software Engineer 有 3 个坑、空 1 个"。本迁移建立
`position_slots` 并把每个既有 `positions` 行收编成 1 号编制。

两个刻意的取舍：

1. **占用态不入库**。`administrative_status` 只存 `planned/active/frozen/closed`；
   `VACANT` / `OCCUPIED` 由"有无生效 PRIMARY 任职"派生（ADR-2）。因此本迁移把
   "当前有人在任"的坑标成 `active`（行政态），空缺与否留给派生函数，绝不写 `vacant`。
2. `manager_slot_id` 用"在任者的上级"回填，**回填不出来就留 NULL**，不猜组织树。
   旧的 `position_assignments.manager_employee_id`（人级汇报）原样保留，两条信息不互相覆盖。

定义↔旧 position 的对应关系靠 v12 写在 `career_path_metadata.__dept_id` 的回填标记还原，
在 Python 侧解析 JSON（不用 `json_extract`，以免把迁移焊死在 SQLite 上）。

Verify: `upgrade head` → `downgrade -3` → `upgrade head` 幂等；`alembic check` 无漂移。
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.models.base import utcnow

revision = "l7a0c3d6e952"
down_revision = "k6f9b2c5d841"
branch_labels = None
depends_on = None

slots = sa.table(
    "position_slots",
    sa.column("id"),
    sa.column("company_id"),
    sa.column("department_id"),
    sa.column("position_definition_id"),
    sa.column("slot_code"),
    sa.column("headcount_index"),
    sa.column("administrative_status"),
    sa.column("manager_slot_id"),
    sa.column("metadata_json"),
    sa.column("closed_at"),
    sa.column("created_at"),
    sa.column("updated_at"),
)


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
    if "position_slots" not in set(inspector.get_table_names()):
        op.create_table(
            "position_slots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column(
                "department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False
            ),
            sa.Column(
                "position_definition_id",
                sa.Integer(),
                sa.ForeignKey("position_definitions.id"),
                nullable=False,
            ),
            sa.Column("slot_code", sa.String(length=100), nullable=False),
            sa.Column("headcount_index", sa.Integer(), nullable=False),
            sa.Column("administrative_status", sa.String(length=20), nullable=False),
            sa.Column(
                "manager_slot_id", sa.Integer(), sa.ForeignKey("position_slots.id"), nullable=True
            ),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "department_id",
                "position_definition_id",
                "headcount_index",
                name="uq_position_slot_index",
            ),
        )
        for name, columns in (
            ("ix_position_slots_company_id", ["company_id"]),
            ("ix_position_slots_department_id", ["department_id"]),
            ("ix_position_slots_position_definition_id", ["position_definition_id"]),
        ):
            op.create_index(name, "position_slots", columns)

    if "positions" not in set(sa.inspect(bind).get_table_names()):
        return

    # (company, dept, title) → definition.id：用 v12 留在 metadata 里的回填标记还原，
    # 而不是重跑一遍 slug 规则 —— 否则两处实现会悄悄漂移。
    definitions: dict[tuple[int | None, int, str], int] = {}
    definition_codes: dict[int, str] = {}
    for row in bind.execute(
        sa.text("SELECT id, company_id, name, code, career_path_metadata FROM position_definitions")
    ):
        dept_id = _as_dict(row.career_path_metadata).get("__dept_id")
        if isinstance(dept_id, int):
            definitions[(row.company_id, dept_id, row.name)] = row.id
            definitions[(row.company_id, dept_id, row.code)] = row.id  # code 也允许被引用
            definition_codes[row.id] = row.code

    occupied = {
        row.position_id
        for row in bind.execute(
            sa.text(
                "SELECT position_id FROM employments "
                "WHERE effective_to IS NULL AND position_id IS NOT NULL"
            )
        )
    }
    now = utcnow()
    created: set[tuple[int, int, int]] = {
        (row.department_id, row.position_definition_id, row.headcount_index)
        for row in bind.execute(
            sa.text(
                "SELECT department_id, position_definition_id, headcount_index FROM position_slots"
            )
        )
    }
    counters: dict[tuple[int, int], int] = {}
    position_to_slot: dict[int, int] = {}

    source = bind.execute(
        sa.text(
            "SELECT p.id AS position_id, dd.company_id AS company_id, dd.id AS department_id, "
            "dd.slug AS dept_slug, p.title AS title FROM positions p "
            "JOIN departments dd ON dd.id = p.department_id ORDER BY p.id"
        )
    )
    for row in source:
        definition_id = definitions.get((row.company_id, row.department_id, row.title))
        if definition_id is None:
            continue  # v12 没建过这条（例如中途被删的 position）：不猜，留下一次处理
        key = (row.department_id, definition_id)
        counters[key] = counters.get(key, 0) + 1
        index = counters[key]
        while (row.department_id, definition_id, index) in created:
            counters[key] += 1
            index = counters[key]
        created.add((row.department_id, definition_id, index))
        # 人读编码：ENG-SOFTWARE_ENGINEER-1（§6 的 #1 #2 #3 在 UI 上要有可辨识的坑号）
        code = f"{(row.dept_slug or 'dept').upper()}-{definition_codes.get(definition_id, 'POS').upper()}-{index}"
        result = bind.execute(
            slots.insert().values(
                company_id=row.company_id,
                department_id=row.department_id,
                position_definition_id=definition_id,
                slot_code=code[:100],
                headcount_index=index,
                administrative_status="active" if row.position_id in occupied else "planned",
                metadata_json=json.dumps({"__backfill": "v13", "__position_id": row.position_id}),
                created_at=now,
                updated_at=now,
            )
        )
        result.close()
        # 同 v12：通用 sa.table() 构造拿不到 inserted_primary_key，
        # 用 uq_position_slot_index 的三元组回读，跨方言确定。
        position_to_slot[row.position_id] = int(
            bind.execute(
                sa.text(
                    "SELECT id FROM position_slots WHERE department_id = :d"
                    " AND position_definition_id = :p AND headcount_index = :i"
                ),
                {"d": row.department_id, "p": definition_id, "i": index},
            ).scalar_one()
        )

    _link_managers(bind, position_to_slot)


def _link_managers(bind, position_to_slot: dict[int, int]) -> None:
    """把"在任者的上级"翻译成组织线；上级没有在任坑则留 NULL（不猜）。"""
    incumbent_slot: dict[int, int] = {}
    for row in bind.execute(
        sa.text(
            "SELECT employee_id, position_id FROM employments "
            "WHERE effective_to IS NULL AND position_id IS NOT NULL"
        )
    ):
        if row.position_id in position_to_slot:
            incumbent_slot[row.employee_id] = position_to_slot[row.position_id]

    for row in bind.execute(
        sa.text(
            "SELECT employee_id, position_id, manager_employee_id FROM employments "
            "WHERE effective_to IS NULL AND manager_employee_id IS NOT NULL"
        )
    ):
        slot_id = position_to_slot.get(row.position_id)
        manager_slot = incumbent_slot.get(row.manager_employee_id)
        if slot_id is None or manager_slot is None or manager_slot == slot_id:
            continue
        bind.execute(
            sa.text(
                "UPDATE position_slots SET manager_slot_id = :manager WHERE id = :slot "
                "AND manager_slot_id IS NULL"
            ),
            {"manager": manager_slot, "slot": slot_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "position_slots" in set(sa.inspect(bind).get_table_names()):
        for name in (
            "ix_position_slots_position_definition_id",
            "ix_position_slots_department_id",
            "ix_position_slots_company_id",
        ):
            op.drop_index(name, table_name="position_slots")
        op.drop_table("position_slots")
