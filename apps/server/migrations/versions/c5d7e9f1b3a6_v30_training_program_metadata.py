"""v30: T2.4 发行方 —— training_programs.metadata_json（培养参数）

Revision ID: c5d7e9f1b3a6
Revises: b4c6d8e0f2a3
Create Date: 2026-09-10

依据 docs/t2-talent-market-design.md §7 D11（发行走真实培养，档位只影响参数与概率分布）。

- `training_programs.metadata_json`：培养参数（当前只有发行方档位
  `{"issuer": {"tier": ..., "signal_bonus": ..., "fortune_weight": ..., "intensity_bonus": ...}}`）。
  刻意**不塞进 `resource_used`**（那是资源台账），也不新建参数表（一对一 JSON 足够）。
- 非空 + server_default `'{}'`：存量行无损（模型两侧写同一字面量，alembic check 不报漂移）。

Verify: up/down/up + `alembic check`；存量 program 行 metadata_json = '{}'。
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d7e9f1b3a6"
down_revision = "b4c6d8e0f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "training_programs",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    with op.batch_alter_table("training_programs") as batch:
        batch.drop_column("metadata_json")
