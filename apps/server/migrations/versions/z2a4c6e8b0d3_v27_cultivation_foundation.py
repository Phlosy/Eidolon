"""v27: T1.0 培养子系统 —— 遗留 employee_id 放开 NOT NULL + 三张培养表

Revision ID: z2a4c6e8b0d3
Revises: y1f3a5c7e9b2
Create Date: 2026-09-10

依据 docs/cultivation-system-design.md §2 D1/D2、docs/person-core-migration.md D3/D4.1。

a) 放开遗留 NOT NULL（D1）：person 域各表的 employee_id 全部 nullable 化
   （learning_sessions 的 company_id 一并放开）——无 employee 行的 person（培养期角色）
   从此可以拥有记忆/技能/学习/能力数据。逐表处理方式：

   全部 11 张表统一走 `batch_alter_table` + `alter_column(nullable=True)`。
   实测探针（/tmp/batch_probe.db，alembic 现行版本）：匿名 FK 与匿名内联 UNIQUE
   （employee_brains/runtime_instances 的 UNIQUE(employee_id)）在重建中**不再**炸
   「Constraint must have a name」——position.py:160-186 记录的老教训针对的是
   给既有表**加** FK 的场景，纯放开 nullable 的 copy 重建会原样带上既有约束与索引
   （uq_learning_priority / uq_employee_competency 两个命名唯一约束、
   uq_learning_priority_person / uq_employee_brains_person / uq_runtime_instances_person /
   uq_learning_session_dupe 等部分唯一索引实测全部保留）。FK 定义本身不动。
   若未来 alembic 升级后行为变化，alembic check 会当场报漂移。

b) 三张新表（D2）：
   - character_profiles：person_id unique（无 FK，D3）、identity_id unique
     （`CH-` + 12 位 Crockford base32，生成规则见 repositories/cultivation.py）、
     origin（issued/trained/blank）、owner_company_id nullable、lifecycle
     （cultivating/ready；listed/hired 属 T2 预留）；
   - training_programs：person_id index、template（academic/vocational/self_taught/
     空串=自由养成）、current_stage、resource_used JSON（资源消耗累计）、rng_seed
     （确定性来源，创建时落库）、status（active/completed/abandoned）；
   - education_events：person_id index、program_id nullable（无 FK）、kind
     （course/exam/fortune/project/internship/competition）、topic、outcome JSON、
     evidence_id nullable（回链 competency_evidence，不加 FK）、occurred_at。
   三表主键/时间戳沿用 TimestampMixin 惯例（id + created_at + updated_at）。

Verify: up/down/up + alembic check；存量行无损；部分唯一索引重建后仍在。
"""

import sqlalchemy as sa
from alembic import op

revision = "z2a4c6e8b0d3"
down_revision = "y1f3a5c7e9b2"
branch_labels = None
depends_on = None

# (表, 要放开的列)
_RELAXED_COLUMNS = (
    ("employee_brains", ("employee_id",)),
    ("runtime_instances", ("employee_id",)),
    ("memory_entries", ("employee_id",)),
    ("skills", ("employee_id",)),
    ("learning_records", ("employee_id",)),
    ("skill_usages", ("employee_id",)),
    ("learning_priorities", ("employee_id",)),
    ("learning_sessions", ("employee_id", "company_id")),
    ("employee_competencies", ("employee_id",)),
    ("competency_evidence", ("employee_id",)),
    ("assessment_runs", ("employee_id",)),
)


def _set_nullable(nullable: bool) -> None:
    for table, columns in _RELAXED_COLUMNS:
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, existing_type=sa.Integer(), nullable=nullable)


def upgrade() -> None:
    _set_nullable(True)

    op.create_table(
        "character_profiles",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("owner_company_id", sa.Integer(), nullable=True),
        sa.Column("lifecycle", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id"),
        sa.UniqueConstraint("identity_id"),
    )
    op.create_table(
        "training_programs",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=30), nullable=False),
        sa.Column("current_stage", sa.Integer(), nullable=False),
        sa.Column("resource_used", sa.JSON(), nullable=False),
        sa.Column("rng_seed", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_programs_person_id"), "training_programs", ["person_id"])
    op.create_table(
        "education_events",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_education_events_person_id"), "education_events", ["person_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_education_events_person_id"), table_name="education_events")
    op.drop_table("education_events")
    op.drop_index(op.f("ix_training_programs_person_id"), table_name="training_programs")
    op.drop_table("training_programs")
    op.drop_table("character_profiles")
    _set_nullable(False)
