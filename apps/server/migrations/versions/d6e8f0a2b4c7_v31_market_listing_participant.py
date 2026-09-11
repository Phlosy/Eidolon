"""v31: T2.7c NPC 招募 —— market_listings.recruited_participant_id

Revision ID: d6e8f0a2b4c7
Revises: c5d7e9f1b3a6
Create Date: 2026-09-10

T2.7c：NPC 公司（`market_participants.kind=npc_company`，D8：**不进 `companies`**）
也要能"把人才买走"。玩家招募已有 `recruited_company_id` + `recruited_employee_id`；
NPC 没有公司行、也没有员工行，因此需要一个统一的所有者指针：

- `recruited_participant_id`：被哪个**市场参与者**招走（玩家公司参与者 / NPC / 发行方）。
  与既有两列并存：玩家路径两列都写（employee 归属 + 参与者归属），
  NPC 路径只写参与者 —— 不伪造 company/employee 行。

派生语义（`eligibility.market_state`）：凡存在 `status='closed'` 且
`recruited_company_id` 或 `recruited_participant_id` 非空的挂牌 ⇒ 该 person
已**被市场消化**（`unavailable`，不可再挂牌）。仅下架（delist）不算消化。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "d6e8f0a2b4c7"
down_revision = "c5d7e9f1b3a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_listings",
        sa.Column("recruited_participant_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("market_listings") as batch:
        batch.drop_column("recruited_participant_id")
