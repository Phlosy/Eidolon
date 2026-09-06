"""v10.employee_brain_traits

Adds the authoritative personality store and migrates legacy rows explicitly:

* ``employee_brains.traits`` (nullable JSON) holds ``{"schema_version": 1, ...}`` and is the
  only source ``app.brain`` reads (docs/employee-brain-behavior-policy.md §4, 决策 1).
* rows whose ``traits`` is still NULL are backfilled from the legacy ``curiosity`` mirror in a
  **time-bounded transaction** (§14.2). ``curiosity`` is kept as a compatibility mirror, so an
  unfinished backfill changes no behavior: ``BrainTraits.from_brain`` falls back to the mirror.
* ``ix_employee_brains_curiosity_legacy`` is a **partial** index covering exactly those legacy
  rows (``traits IS NULL``) — it fades as the backfill converges instead of indexing the table.

The model declares the same ``sqlite_where`` / ``postgresql_where`` predicate so
``alembic check`` sees no drift on either dialect. Downgrade drops index + column; the mirror
column was never removed, so reverting loses only derived trait data.

Verify: ``upgrade head`` → ``downgrade :base`` → ``upgrade head`` is idempotent and
``alembic check`` reports no pending update on both SQLite and PostgreSQL.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

revision = "i4d7f0a3b629"
down_revision = "h3c6e9f2a518"
branch_labels = None
depends_on = None

# §14.2：回填是独立短时事务，不随 migration 无限持有写锁。
BACKFILL_TIME_BUDGET_SECONDS = 25.0
BACKFILL_BATCH_SIZE = 500
TRAITS_SCHEMA_VERSION = 1

BRAINS = sa.table(
    "employee_brains",
    sa.column("employee_id", sa.Integer),
    sa.column("curiosity", sa.Float),
    sa.column("traits", sa.JSON()),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("employee_brains")}
    indexes = {index["name"] for index in inspector.get_indexes("employee_brains")}

    if "traits" not in columns:
        op.add_column("employee_brains", sa.Column("traits", sa.JSON(), nullable=True))

    _backfill_traits()

    if "ix_employee_brains_curiosity_legacy" not in indexes:
        op.create_index(
            "ix_employee_brains_curiosity_legacy",
            "employee_brains",
            ["curiosity"],
            unique=False,
            sqlite_where=sa.text("traits IS NULL"),
            postgresql_where=sa.text("traits IS NULL"),
        )


def _backfill_traits() -> None:
    """Time-bounded backfill; commits per batch and never holds the write lock indefinitely."""
    started = time.monotonic()
    migrated = 0
    while True:
        pending = sa.select(BRAINS.c.employee_id, BRAINS.c.curiosity).where(
            BRAINS.c.traits.is_(None)
        )
        pending = pending.order_by(BRAINS.c.employee_id).limit(BACKFILL_BATCH_SIZE)
        rows = op.get_bind().execute(pending).all()
        if not rows:
            break
        for employee_id, curiosity in rows:
            value = 0.5 if curiosity is None else min(1.0, max(0.0, float(curiosity)))
            op.get_bind().execute(
                sa.update(BRAINS)
                .where(BRAINS.c.employee_id == employee_id)
                .values(traits={"schema_version": TRAITS_SCHEMA_VERSION, "curiosity": value})
            )
            migrated += 1
        _commit_batch()
        if time.monotonic() - started > BACKFILL_TIME_BUDGET_SECONDS:
            left = (
                op.get_bind()
                .execute(
                    sa.select(sa.func.count()).select_from(BRAINS).where(BRAINS.c.traits.is_(None))
                )
                .scalar()
            )
            print(
                f"[v10] WARN traits 回填超时（已迁移 {migrated} 行，剩余 {left} 行仍读 legacy "
                f"curiosity 镜像列，行为不变）——可重跑本 revision 或后续补齐"
            )
            return
    if migrated:
        print(f"[v10] traits 回填完成：{migrated} 行")


def _commit_batch() -> None:
    # Alembic 的同步上下文把事务存在 connection 上（context._transaction 是私有 API），
    # 所以直接提交 bind：已完成批次落库，超时中断时不丢已迁移数据。
    op.get_bind().commit()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("employee_brains")}
    columns = {column["name"] for column in inspector.get_columns("employee_brains")}
    if "ix_employee_brains_curiosity_legacy" in indexes:
        op.drop_index("ix_employee_brains_curiosity_legacy", table_name="employee_brains")
    if "traits" in columns:
        op.drop_column("employee_brains", "traits")
