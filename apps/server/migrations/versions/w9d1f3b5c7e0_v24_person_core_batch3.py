"""v24: PersonCore R1.3 批次 3 —— 能力度量域 3 表挂 person_id

Revision ID: w9d1f3b5c7e0
Revises: v8c0e2a4b6d9
Create Date: 2026-09-10

依据 docs/person-core-migration.md §3 D4 三步法的第一步（加列回填）：
- employee_competencies / competency_evidence / assessment_runs 各加 `person_id`
  nullable 整型列（不加 FK，D3 纪律），按 employees 连接内联回填；
- 索引口径（服务层完整性 + 部分唯一索引惯例）：
  · employee_competencies 既有 uq(employee_id, competency_definition_id)
    （uq_employee_competency）⇒ person 侧镜像部分唯一索引
    uq_employee_competency_person（WHERE person_id IS NOT NULL）；
  · competency_evidence / assessment_runs 只加普通索引 ix_<table>_person_id
    （既有复合索引 ix_competency_evidence_* 维持原样，不镜像）；
- assessment_runs.company_id 是公司上下文快照，与人称切换无关，不动。

兼容期 employee_id 列保留不删（deprecated 镜像）。事件消费者的证据/考核写入
经 normalize.upsert_evidence 与 assessment 聚合器 —— 双写封在那两处。

Verify: up/down/up + alembic check；回填后 3 表 person_id 非空率 100%。
"""

import sqlalchemy as sa
from alembic import op

revision = "w9d1f3b5c7e0"
down_revision = "v8c0e2a4b6d9"
branch_labels = None
depends_on = None

_TABLES = ("employee_competencies", "competency_evidence", "assessment_runs")

_BACKFILL_SQL = (
    "UPDATE {table} SET person_id = ("
    "SELECT person_id FROM employees e WHERE e.id = {table}.employee_id"
    ") WHERE person_id IS NULL"
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("person_id", sa.Integer(), nullable=True))
        op.get_bind().execute(sa.text(_BACKFILL_SQL.format(table=table)))

    op.create_index(
        op.f("ix_competency_evidence_person_id"), "competency_evidence", ["person_id"]
    )
    op.create_index(op.f("ix_assessment_runs_person_id"), "assessment_runs", ["person_id"])
    op.create_index(
        "uq_employee_competency_person",
        "employee_competencies",
        ["person_id", "competency_definition_id"],
        unique=True,
        sqlite_where=sa.text("person_id IS NOT NULL"),
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_employee_competency_person", table_name="employee_competencies")
    op.drop_index(op.f("ix_assessment_runs_person_id"), table_name="assessment_runs")
    op.drop_index(op.f("ix_competency_evidence_person_id"), table_name="competency_evidence")
    for table in reversed(_TABLES):
        op.drop_column(table, "person_id")
