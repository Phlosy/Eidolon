"""v0.2 persistent workforce: providers, model_bindings, employee_brains,
runtime_instances, runtime_images, secrets; artifacts/work_sessions columns.

Revision ID: c7f2a9d3e812
Revises: b6a02065c420
Create Date: 2026-09-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f2a9d3e812'
down_revision: Union[str, Sequence[str], None] = 'b6a02065c420'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('providers',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('provider_type', sa.String(length=50), nullable=False),
    sa.Column('base_url', sa.String(length=500), nullable=True),
    sa.Column('scope', sa.String(length=50), nullable=False),
    sa.Column('owner_employee_id', sa.Integer(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('credential_ref', sa.String(length=200), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_employee_id'], ['employees.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_providers_owner_employee_id'), 'providers', ['owner_employee_id'], unique=False)
    op.create_table('model_bindings',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('provider_id', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_bindings_employee_id'), 'model_bindings', ['employee_id'], unique=False)
    op.create_table('employee_brains',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('personality', sa.Text(), nullable=False),
    sa.Column('goals', sa.Text(), nullable=False),
    sa.Column('interests', sa.JSON(), nullable=False),
    sa.Column('learning_policy', sa.JSON(), nullable=False),
    sa.Column('memory_policy', sa.JSON(), nullable=False),
    sa.Column('curiosity', sa.Float(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employee_id')
    )
    op.create_index(op.f('ix_employee_brains_employee_id'), 'employee_brains', ['employee_id'], unique=True)
    op.create_table('runtime_instances',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('runtime_type', sa.String(length=50), nullable=False),
    sa.Column('deployment_mode', sa.String(length=50), nullable=False),
    sa.Column('container_id', sa.String(length=100), nullable=True),
    sa.Column('container_name', sa.String(length=200), nullable=True),
    sa.Column('image', sa.String(length=300), nullable=False),
    sa.Column('image_tag', sa.String(length=100), nullable=False),
    sa.Column('image_digest', sa.String(length=300), nullable=True),
    sa.Column('runtime_version', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('health_status', sa.String(length=50), nullable=False),
    sa.Column('internal_host', sa.String(length=200), nullable=True),
    sa.Column('internal_port', sa.Integer(), nullable=True),
    sa.Column('workspace_path', sa.String(length=500), nullable=False),
    sa.Column('data_path', sa.String(length=500), nullable=False),
    sa.Column('model_binding_id', sa.Integer(), nullable=True),
    sa.Column('cpu_limit', sa.Float(), nullable=False),
    sa.Column('memory_limit_mb', sa.Integer(), nullable=False),
    sa.Column('restart_policy', sa.String(length=50), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('last_healthcheck_at', sa.DateTime(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('stopped_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['model_binding_id'], ['model_bindings.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employee_id')
    )
    op.create_index(op.f('ix_runtime_instances_employee_id'), 'runtime_instances', ['employee_id'], unique=True)
    op.create_index(op.f('ix_runtime_instances_status'), 'runtime_instances', ['status'], unique=False)
    op.create_table('runtime_images',
    sa.Column('runtime_type', sa.String(length=50), nullable=False),
    sa.Column('repository', sa.String(length=300), nullable=False),
    sa.Column('tag', sa.String(length=100), nullable=False),
    sa.Column('digest', sa.String(length=300), nullable=True),
    sa.Column('installed_version', sa.String(length=100), nullable=True),
    sa.Column('latest_version', sa.String(length=100), nullable=True),
    sa.Column('latest_digest', sa.String(length=300), nullable=True),
    sa.Column('channel', sa.String(length=50), nullable=False),
    sa.Column('update_available', sa.Boolean(), nullable=False),
    sa.Column('compatibility_status', sa.String(length=50), nullable=False),
    sa.Column('update_status', sa.String(length=50), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runtime_type')
    )
    op.create_table('secrets',
    sa.Column('ref', sa.String(length=200), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ref')
    )
    op.create_index(op.f('ix_secrets_ref'), 'secrets', ['ref'], unique=True)
    with op.batch_alter_table('artifacts') as batch_op:
        batch_op.add_column(sa.Column('sha256', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('work_session_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_artifacts_work_session', 'work_sessions', ['work_session_id'], ['id'])
    with op.batch_alter_table('work_sessions') as batch_op:
        batch_op.add_column(sa.Column('runtime_instance_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('provider_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('model', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_work_sessions_runtime_instance', 'runtime_instances', ['runtime_instance_id'], ['id'])
        batch_op.create_foreign_key('fk_work_sessions_provider', 'providers', ['provider_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_sessions') as batch_op:
        batch_op.drop_constraint('fk_work_sessions_provider', type_='foreignkey')
        batch_op.drop_constraint('fk_work_sessions_runtime_instance', type_='foreignkey')
        batch_op.drop_column('error')
        batch_op.drop_column('model')
        batch_op.drop_column('provider_id')
        batch_op.drop_column('runtime_instance_id')
    with op.batch_alter_table('artifacts') as batch_op:
        batch_op.drop_constraint('fk_artifacts_work_session', type_='foreignkey')
        batch_op.drop_column('work_session_id')
        batch_op.drop_column('sha256')
    op.drop_index(op.f('ix_secrets_ref'), table_name='secrets')
    op.drop_table('secrets')
    op.drop_table('runtime_images')
    op.drop_index(op.f('ix_runtime_instances_status'), table_name='runtime_instances')
    op.drop_index(op.f('ix_runtime_instances_employee_id'), table_name='runtime_instances')
    op.drop_table('runtime_instances')
    op.drop_index(op.f('ix_employee_brains_employee_id'), table_name='employee_brains')
    op.drop_table('employee_brains')
    op.drop_index(op.f('ix_model_bindings_employee_id'), table_name='model_bindings')
    op.drop_table('model_bindings')
    op.drop_index(op.f('ix_providers_owner_employee_id'), table_name='providers')
    op.drop_table('providers')
