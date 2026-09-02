"""v0.3 phase 2: git_connections table (external git platform connections).

Revision ID: f4a8c2d6e103
Revises: e1b3c5d7f902
Create Date: 2026-09-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8c2d6e103'
down_revision: Union[str, Sequence[str], None] = 'e1b3c5d7f902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('git_connections',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('platform_type', sa.String(length=50), nullable=False),
    sa.Column('base_url', sa.String(length=500), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('credential_ref', sa.String(length=200), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('git_connections')
