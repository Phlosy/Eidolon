"""v0.5 project schedules: owners and planned/actual timeline dates.

Revision ID: d8e2f4a6b105
Revises: a5b1c3d7e904
Create Date: 2026-09-02 15:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e2f4a6b105"
down_revision: Union[str, Sequence[str], None] = "a5b1c3d7e904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("planned_start_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("planned_end_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("milestones") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("planned_start_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("planned_end_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_milestones_owner_id_employees", "employees", ["owner_id"], ["id"]
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("planned_start_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("planned_end_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("actual_start_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("actual_end_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("actual_end_at")
        batch_op.drop_column("actual_start_at")
        batch_op.drop_column("planned_end_at")
        batch_op.drop_column("planned_start_at")

    with op.batch_alter_table("milestones") as batch_op:
        batch_op.drop_constraint("fk_milestones_owner_id_employees", type_="foreignkey")
        batch_op.drop_column("planned_end_at")
        batch_op.drop_column("planned_start_at")
        batch_op.drop_column("owner_id")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("planned_end_at")
        batch_op.drop_column("planned_start_at")
