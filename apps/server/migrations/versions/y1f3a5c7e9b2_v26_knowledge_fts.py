"""v26: K2 检索增强 —— knowledge_items 的 FTS5 全文索引

Revision ID: y1f3a5c7e9b2
Revises: x0e2f4a6c8d1
Create Date: 2026-09-10

依据 docs/talent-ecosystem-plan.md §3 K2 / vision §5：
- 建 FTS5 虚拟表 `knowledge_items_fts`（列 title/topic/content，rowid 映射
  knowledge_items.id），`tokenize='trigram'` —— trigram 的子串语义对中日韩友好
  （unicode61 会把整段中文当一个词，效果差），ASCII 默认大小写折叠；
- 内联回填全部存量行；
- 同步策略走**服务层**（不用 DB trigger）：写入入口已收敛在
  repositories/knowledge.py::create_knowledge_item；检索失败时回落旧
  token-overlap 路径（检索降级可接受，知识丢失不可接受）；
- 虚拟表及其影子表（_data/_idx/_content/_docsize/_config）不进 ORM metadata；
  为避免 alembic check 把它们当漂移，migrations/env.py 的 include_object
  按前缀排除（与既有 _RETAINED_LEGACY_TABLES 同一惯例）。

Verify: up/down/up + alembic check；回填后 FTS 行数 = knowledge_items 行数。
"""

import sqlalchemy as sa
from alembic import op

revision = "y1f3a5c7e9b2"
down_revision = "x0e2f4a6c8d1"
branch_labels = None
depends_on = None

_FTS_TABLE = "knowledge_items_fts"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"CREATE VIRTUAL TABLE {_FTS_TABLE} USING fts5("
            "title, topic, content, tokenize='trigram')"
        )
    )
    op.execute(
        sa.text(
            f"INSERT INTO {_FTS_TABLE} (rowid, title, topic, content)"
            " SELECT id, title, topic, content FROM knowledge_items"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP TABLE IF EXISTS {_FTS_TABLE}"))
