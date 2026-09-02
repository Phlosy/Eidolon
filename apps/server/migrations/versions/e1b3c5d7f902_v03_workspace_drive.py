"""v0.3 workspace drive: drive_nodes, drive_revisions, drive_collaborators.

The artifacts table stays in place (deprecated, no longer written); rows are
migrated to drive_nodes at application startup (services/drive_migration.py).

Revision ID: e1b3c5d7f902
Revises: c7f2a9d3e812
Create Date: 2026-09-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1b3c5d7f902'
down_revision: Union[str, Sequence[str], None] = 'c7f2a9d3e812'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('drive_nodes',
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=False),
    sa.Column('zone', sa.String(length=50), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('doc_type', sa.String(length=50), nullable=True),
    sa.Column('owner_employee_id', sa.Integer(), nullable=True),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('work_session_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['drive_nodes.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['work_session_id'], ['work_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_drive_nodes_owner_employee_id'), 'drive_nodes', ['owner_employee_id'], unique=False)
    op.create_index(op.f('ix_drive_nodes_parent_id'), 'drive_nodes', ['parent_id'], unique=False)
    op.create_index(op.f('ix_drive_nodes_path'), 'drive_nodes', ['path'], unique=True)
    op.create_index(op.f('ix_drive_nodes_project_id'), 'drive_nodes', ['project_id'], unique=False)
    op.create_index(op.f('ix_drive_nodes_zone'), 'drive_nodes', ['zone'], unique=False)
    op.create_table('drive_revisions',
    sa.Column('node_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('author_employee_id', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['author_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['node_id'], ['drive_nodes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_drive_revisions_node_id'), 'drive_revisions', ['node_id'], unique=False)
    op.create_table('drive_collaborators',
    sa.Column('node_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['node_id'], ['drive_nodes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_drive_collaborators_employee_id'), 'drive_collaborators', ['employee_id'], unique=False)
    op.create_index(op.f('ix_drive_collaborators_node_id'), 'drive_collaborators', ['node_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_drive_collaborators_node_id'), table_name='drive_collaborators')
    op.drop_index(op.f('ix_drive_collaborators_employee_id'), table_name='drive_collaborators')
    op.drop_table('drive_collaborators')
    op.drop_index(op.f('ix_drive_revisions_node_id'), table_name='drive_revisions')
    op.drop_table('drive_revisions')
    op.drop_index(op.f('ix_drive_nodes_zone'), table_name='drive_nodes')
    op.drop_index(op.f('ix_drive_nodes_project_id'), table_name='drive_nodes')
    op.drop_index(op.f('ix_drive_nodes_path'), table_name='drive_nodes')
    op.drop_index(op.f('ix_drive_nodes_parent_id'), table_name='drive_nodes')
    op.drop_index(op.f('ix_drive_nodes_owner_employee_id'), table_name='drive_nodes')
    op.drop_table('drive_nodes')
