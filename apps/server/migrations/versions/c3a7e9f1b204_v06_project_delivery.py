"""v0.6 guided company and formal project delivery lifecycle.

Revision ID: c3a7e9f1b204
Revises: d8e2f4a6b105
Create Date: 2026-09-02 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c3a7e9f1b204"
down_revision: Union[str, Sequence[str], None] = "d8e2f4a6b105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("code", sa.String(50), nullable=True))
        batch_op.add_column(
            sa.Column("priority", sa.String(30), nullable=False, server_default="medium")
        )
        batch_op.add_column(
            sa.Column("customer", sa.String(200), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("background", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("objectives", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("technical_requirements", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("constraints", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("deliverables", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("review_configuration", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("participants", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column(
                "tutorial_accelerated", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    op.create_table(
        "project_requirements",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(30), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("design_refs", sa.JSON(), nullable=False),
        sa.Column("implementation_refs", sa.JSON(), nullable=False),
        sa.Column("test_refs", sa.JSON(), nullable=False),
        sa.Column("acceptance_refs", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint("project_id", "code"),
    )
    op.create_index("ix_project_requirements_project_id", "project_requirements", ["project_id"])

    op.create_table(
        "project_phases",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("phase_type", sa.String(60), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("gate_required", sa.Boolean(), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("baseline_id", sa.Integer(), nullable=True),
        sa.Column("owner_employee_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint("project_id", "phase_type"),
    )
    op.create_index("ix_project_phases_project_id", "project_phases", ["project_id"])
    op.create_index("ix_project_phases_phase_type", "project_phases", ["phase_type"])
    op.create_index("ix_project_phases_status", "project_phases", ["status"])

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("phase_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_tasks_phase_id", ["phase_id"])
        batch_op.create_foreign_key(
            "fk_tasks_phase_id_project_phases", "project_phases", ["phase_id"], ["id"]
        )

    op.create_table(
        "document_artifacts",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("change_request_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("document_type", sa.String(80), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("format", sa.String(30), nullable=False),
        sa.Column("version_major", sa.Integer(), nullable=False),
        sa.Column("version_minor", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(30), nullable=False),
        sa.Column("drive_node_id", sa.Integer(), nullable=False),
        sa.Column("drive_revision_id", sa.Integer(), nullable=True),
        sa.Column("author_employee_id", sa.Integer(), nullable=True),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("baseline_status", sa.String(30), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["drive_node_id"], ["drive_nodes.id"]),
        sa.ForeignKeyConstraint(["drive_revision_id"], ["drive_revisions.id"]),
        sa.ForeignKeyConstraint(["phase_id"], ["project_phases.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["document_artifacts.id"]),
    )
    op.create_index("ix_document_artifacts_project_id", "document_artifacts", ["project_id"])
    op.create_index("ix_document_artifacts_category", "document_artifacts", ["category"])
    op.create_index("ix_document_artifacts_document_type", "document_artifacts", ["document_type"])

    op.create_table(
        "review_meetings",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=False),
        sa.Column("source_phase_id", sa.Integer(), nullable=False),
        sa.Column("review_type", sa.String(60), nullable=False),
        sa.Column("subtype", sa.String(80), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("presenter_employee_id", sa.Integer(), nullable=True),
        sa.Column("participants", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(40), nullable=True),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column("action_items", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["phase_id"], ["project_phases.id"]),
        sa.ForeignKeyConstraint(["presenter_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_phase_id"], ["project_phases.id"]),
    )
    op.create_index("ix_review_meetings_project_id", "review_meetings", ["project_id"])
    op.create_index("ix_review_meetings_phase_id", "review_meetings", ["phase_id"])
    op.create_index("ix_review_meetings_review_type", "review_meetings", ["review_type"])
    op.create_index("ix_review_meetings_status", "review_meetings", ["status"])

    op.create_table(
        "review_packages",
        sa.Column("review_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("document_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("detailed_document_id", sa.Integer(), nullable=True),
        sa.Column("presentation_id", sa.Integer(), nullable=True),
        sa.Column("speaker_notes_id", sa.Integer(), nullable=True),
        sa.Column("agenda_id", sa.Integer(), nullable=True),
        sa.Column("traceability_document_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agenda_id"], ["document_artifacts.id"]),
        sa.ForeignKeyConstraint(["detailed_document_id"], ["document_artifacts.id"]),
        sa.ForeignKeyConstraint(["presentation_id"], ["document_artifacts.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["review_meetings.id"]),
        sa.ForeignKeyConstraint(["speaker_notes_id"], ["document_artifacts.id"]),
        sa.ForeignKeyConstraint(["traceability_document_id"], ["document_artifacts.id"]),
    )

    op.create_table(
        "baselines",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("baseline_type", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("document_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("supersedes_baseline_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["phase_id"], ["project_phases.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["review_meetings.id"]),
        sa.ForeignKeyConstraint(["supersedes_baseline_id"], ["baselines.id"]),
    )
    op.create_index("ix_baselines_project_id", "baselines", ["project_id"])
    op.create_index("ix_baselines_baseline_type", "baselines", ["baseline_type"])

    op.create_table(
        "change_requests",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("priority", sa.String(30), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(40), nullable=True),
        sa.Column("affected_requirements", sa.JSON(), nullable=False),
        sa.Column("affected_design", sa.JSON(), nullable=False),
        sa.Column("affected_tasks", sa.JSON(), nullable=False),
        sa.Column("affected_tests", sa.JSON(), nullable=False),
        sa.Column("impact_analysis", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint("project_id", "code"),
    )
    op.create_index("ix_change_requests_project_id", "change_requests", ["project_id"])
    op.create_index("ix_change_requests_status", "change_requests", ["status"])

    op.create_table(
        "delivery_packages",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("drive_node_id", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["drive_node_id"], ["drive_nodes.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index("ix_delivery_packages_project_id", "delivery_packages", ["project_id"])

    op.create_table(
        "tutorial_progress",
        sa.Column("company_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("current_step", sa.String(80), nullable=False),
        sa.Column("completed_steps", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )


def downgrade() -> None:
    op.drop_table("tutorial_progress")
    op.drop_index("ix_delivery_packages_project_id", table_name="delivery_packages")
    op.drop_table("delivery_packages")
    op.drop_index("ix_change_requests_status", table_name="change_requests")
    op.drop_index("ix_change_requests_project_id", table_name="change_requests")
    op.drop_table("change_requests")
    op.drop_index("ix_baselines_baseline_type", table_name="baselines")
    op.drop_index("ix_baselines_project_id", table_name="baselines")
    op.drop_table("baselines")
    op.drop_table("review_packages")
    op.drop_index("ix_review_meetings_status", table_name="review_meetings")
    op.drop_index("ix_review_meetings_review_type", table_name="review_meetings")
    op.drop_index("ix_review_meetings_phase_id", table_name="review_meetings")
    op.drop_index("ix_review_meetings_project_id", table_name="review_meetings")
    op.drop_table("review_meetings")
    op.drop_index("ix_document_artifacts_document_type", table_name="document_artifacts")
    op.drop_index("ix_document_artifacts_category", table_name="document_artifacts")
    op.drop_index("ix_document_artifacts_project_id", table_name="document_artifacts")
    op.drop_table("document_artifacts")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_phase_id_project_phases", type_="foreignkey")
        batch_op.drop_index("ix_tasks_phase_id")
        batch_op.drop_column("phase_id")
    op.drop_index("ix_project_phases_status", table_name="project_phases")
    op.drop_index("ix_project_phases_phase_type", table_name="project_phases")
    op.drop_index("ix_project_phases_project_id", table_name="project_phases")
    op.drop_table("project_phases")
    op.drop_index("ix_project_requirements_project_id", table_name="project_requirements")
    op.drop_table("project_requirements")
    with op.batch_alter_table("projects") as batch_op:
        for column in (
            "tutorial_accelerated",
            "participants",
            "review_configuration",
            "deliverables",
            "constraints",
            "technical_requirements",
            "objectives",
            "background",
            "customer",
            "priority",
            "code",
        ):
            batch_op.drop_column(column)
