"""v11.skill_usages

Creates the benchmark table for candidate skills (docs/employee-brain-behavior-policy.md §10)
and adds ``learning_priorities.source`` (§13.1).

``success`` is the objective fact taken from ``_finalize``; ``outcome`` is a *human* judgement
(useful / not_useful / NULL = not rated yet). They are deliberately independent columns: a
successful task never auto-marks ``outcome`` (§10.2), and personality can only ever change how
often a candidate skill gets tried, never either verdict.

``uq_skill_usage(task_id, skill_id)`` keeps re-dispatch idempotent.
``source`` distinguishes failure-driven priorities (score >= FAILURE_PRIORITY_SCORE) from
behavior-derived extension items (score < it) so the two can never be conflated.

Verify: ``upgrade head`` → ``downgrade :base`` → ``upgrade head`` is idempotent and
``alembic check`` reports no pending update on both SQLite and PostgreSQL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j5e8a1b4c730"
down_revision = "i4d7f0a3b629"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_skill_usages()
    _add_priority_source()


def _create_skill_usages() -> None:
    bind = op.get_bind()
    if "skill_usages" in sa.inspect(bind).get_table_names():
        return  # legacy DB that already materialised this table via create_all
    op.create_table(
        "skill_usages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("work_session_id", sa.Integer(), nullable=True),
        sa.Column("skill_validation_status", sa.String(length=50), nullable=False),
        sa.Column("selection_reason", sa.String(length=100), nullable=False),
        sa.Column("policy_version", sa.String(length=50), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("outcome_source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["work_session_id"], ["work_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_skill_usage"),
    )
    for column in ("employee_id", "skill_id", "task_id"):
        op.create_index(f"ix_skill_usages_{column}", "skill_usages", [column], unique=False)


def _add_priority_source() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("learning_priorities")}
    if "source" in columns:
        return
    op.add_column(
        "learning_priorities",
        sa.Column("source", sa.String(length=50), nullable=False, server_default=""),
    )
    # 历史行都是失败驱动的（当时只有 record_failure 会写）
    op.execute("UPDATE learning_priorities SET source = 'failure' WHERE source = ''")


def downgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "learning_priorities" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("learning_priorities")}
        if "source" in columns:
            op.drop_column("learning_priorities", "source")
    if "skill_usages" not in tables:
        return
    for index in sa.inspect(bind).get_indexes("skill_usages"):
        op.drop_index(index["name"], table_name="skill_usages")
    op.drop_table("skill_usages")
