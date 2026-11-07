"""v2-0003: work_missions / work_projects / work_tasks / work_artifacts — projections
of work.* facts (R3-c). Rebuild: `python -m eidolon rebuild work_items`.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "work_missions",
        sa.Column("mission_id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("success_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=True),
        sa.Column("deadline", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decision_id", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_work_missions_company", "work_missions", ["company_id", "status"])

    op.create_table(
        "work_projects",
        sa.Column("project_id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("mission_id", UUID, nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("manager", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("plan_decision_id", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_work_projects_company", "work_projects", ["company_id", "status"])

    op.create_table(
        "work_tasks",
        sa.Column("task_id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("role", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assignee", sa.String(200), nullable=True),
        sa.Column("depends_on", postgresql.JSONB(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("rework_count", sa.Integer(), nullable=False),
        sa.Column("current_run_id", UUID, nullable=True),
        sa.Column("reviews", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_work_tasks_project", "work_tasks", ["project_id", "status"])
    op.create_index("ix_work_tasks_company_status", "work_tasks", ["company_id", "status"])
    op.create_index("ix_work_tasks_assignee", "work_tasks", ["company_id", "assignee"])

    op.create_table(
        "work_artifacts",
        sa.Column("artifact_id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("blob_ref", sa.String(80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("uses", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_work_artifacts_task", "work_artifacts", ["task_id"])
    op.create_index("ix_work_artifacts_project", "work_artifacts", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_work_artifacts_project", table_name="work_artifacts")
    op.drop_index("ix_work_artifacts_task", table_name="work_artifacts")
    op.drop_table("work_artifacts")
    op.drop_index("ix_work_tasks_assignee", table_name="work_tasks")
    op.drop_index("ix_work_tasks_company_status", table_name="work_tasks")
    op.drop_index("ix_work_tasks_project", table_name="work_tasks")
    op.drop_table("work_tasks")
    op.drop_index("ix_work_projects_company", table_name="work_projects")
    op.drop_table("work_projects")
    op.drop_index("ix_work_missions_company", table_name="work_missions")
    op.drop_table("work_missions")
