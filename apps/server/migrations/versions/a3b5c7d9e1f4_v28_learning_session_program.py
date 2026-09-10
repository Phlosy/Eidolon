"""v28: T1.1 培养引擎前置 —— learning_sessions.program_id

Revision ID: a3b5c7d9e1f4
Revises: z2a4c6e8b0d3
Create Date: 2026-09-10

依据 docs/cultivation-system-design.md §2 D3：培养期的学习会话挂在培养实例上
（training_programs）。加 `program_id` nullable 整型列（不加 FK，D3 纪律）+ 普通索引。
存量行无回填语义（员工学习会话不属于任何培养实例，保持 NULL）。
"""

import sqlalchemy as sa
from alembic import op

revision = "a3b5c7d9e1f4"
down_revision = "z2a4c6e8b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("learning_sessions", sa.Column("program_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_learning_sessions_program_id"), "learning_sessions", ["program_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_learning_sessions_program_id"), table_name="learning_sessions")
    op.drop_column("learning_sessions", "program_id")
