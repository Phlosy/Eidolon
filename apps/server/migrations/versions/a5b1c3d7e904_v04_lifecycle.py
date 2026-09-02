"""v0.4 employee lifecycle: 12 new tables + employees.lifecycle_status/username.

Data backfill (positions, employments, packages, resource accounts for the 5
seed employees) happens idempotently at application startup
(services/lifecycle.seed_lifecycle), per docs/design-v0.4-lifecycle.md §12.

Revision ID: a5b1c3d7e904
Revises: f4a8c2d6e103
Create Date: 2026-09-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5b1c3d7e904'
down_revision: Union[str, Sequence[str], None] = 'f4a8c2d6e103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'employees',
        sa.Column('lifecycle_status', sa.String(length=50), server_default='active', nullable=False),
    )
    op.add_column('employees', sa.Column('username', sa.String(length=100), nullable=True))

    op.create_table('positions',
    sa.Column('department_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('level', sa.String(length=50), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_positions_department_id'), 'positions', ['department_id'], unique=False)
    op.create_table('entitlements',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('type', sa.String(length=50), nullable=False),
    sa.Column('resource_type', sa.String(length=50), nullable=False),
    sa.Column('description', sa.String(length=2000), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('resource_providers',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('type', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('capabilities', sa.JSON(), nullable=False),
    sa.Column('connection', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('access_packages',
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=2000), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=True),
    sa.Column('built_in', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('employments',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.Integer(), nullable=True),
    sa.Column('position_id', sa.Integer(), nullable=True),
    sa.Column('manager_employee_id', sa.Integer(), nullable=True),
    sa.Column('employment_status', sa.String(length=50), nullable=False),
    sa.Column('joined_at', sa.DateTime(), nullable=False),
    sa.Column('effective_from', sa.DateTime(), nullable=False),
    sa.Column('effective_to', sa.DateTime(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['manager_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['position_id'], ['positions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_employments_employee_id'), 'employments', ['employee_id'], unique=False)
    op.create_table('access_package_items',
    sa.Column('package_id', sa.Integer(), nullable=False),
    sa.Column('entitlement_id', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['entitlement_id'], ['entitlements.id'], ),
    sa.ForeignKeyConstraint(['package_id'], ['access_packages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_access_package_items_entitlement_id'), 'access_package_items', ['entitlement_id'], unique=False)
    op.create_index(op.f('ix_access_package_items_package_id'), 'access_package_items', ['package_id'], unique=False)
    op.create_table('employee_packages',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('package_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['package_id'], ['access_packages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_employee_packages_employee_id'), 'employee_packages', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_packages_package_id'), 'employee_packages', ['package_id'], unique=False)
    op.create_table('provisioning_jobs',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('total_steps', sa.Integer(), nullable=False),
    sa.Column('done_steps', sa.Integer(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_provisioning_jobs_employee_id'), 'provisioning_jobs', ['employee_id'], unique=False)
    op.create_table('resource_accounts',
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('resource_type', sa.String(length=50), nullable=False),
    sa.Column('provider_id', sa.Integer(), nullable=True),
    sa.Column('external_account_id', sa.String(length=200), nullable=True),
    sa.Column('username', sa.String(length=200), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('provisioning_state', sa.String(length=50), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['provider_id'], ['resource_providers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resource_accounts_employee_id'), 'resource_accounts', ['employee_id'], unique=False)
    op.create_index(op.f('ix_resource_accounts_status'), 'resource_accounts', ['status'], unique=False)
    op.create_table('resource_assets',
    sa.Column('resource_type', sa.String(length=50), nullable=False),
    sa.Column('external_id', sa.String(length=500), nullable=True),
    sa.Column('owner_employee_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('provider_key', sa.String(length=100), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resource_assets_owner_employee_id'), 'resource_assets', ['owner_employee_id'], unique=False)
    op.create_table('provisioning_steps',
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('resource_type', sa.String(length=50), nullable=False),
    sa.Column('provider_key', sa.String(length=100), nullable=False),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('entitlement_id', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['entitlement_id'], ['entitlements.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['provisioning_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_provisioning_steps_job_id'), 'provisioning_steps', ['job_id'], unique=False)
    op.create_table('audit_logs',
    sa.Column('actor', sa.String(length=100), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('before_json', sa.JSON(), nullable=True),
    sa.Column('after_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_employee_id'), 'audit_logs', ['employee_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_audit_logs_employee_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_provisioning_steps_job_id'), table_name='provisioning_steps')
    op.drop_table('provisioning_steps')
    op.drop_index(op.f('ix_resource_assets_owner_employee_id'), table_name='resource_assets')
    op.drop_table('resource_assets')
    op.drop_index(op.f('ix_resource_accounts_status'), table_name='resource_accounts')
    op.drop_index(op.f('ix_resource_accounts_employee_id'), table_name='resource_accounts')
    op.drop_table('resource_accounts')
    op.drop_index(op.f('ix_provisioning_jobs_employee_id'), table_name='provisioning_jobs')
    op.drop_table('provisioning_jobs')
    op.drop_index(op.f('ix_employee_packages_package_id'), table_name='employee_packages')
    op.drop_index(op.f('ix_employee_packages_employee_id'), table_name='employee_packages')
    op.drop_table('employee_packages')
    op.drop_index(op.f('ix_access_package_items_package_id'), table_name='access_package_items')
    op.drop_index(op.f('ix_access_package_items_entitlement_id'), table_name='access_package_items')
    op.drop_table('access_package_items')
    op.drop_index(op.f('ix_employments_employee_id'), table_name='employments')
    op.drop_table('employments')
    op.drop_table('access_packages')
    op.drop_table('resource_providers')
    op.drop_table('entitlements')
    op.drop_index(op.f('ix_positions_department_id'), table_name='positions')
    op.drop_table('positions')
    op.drop_column('employees', 'username')
    op.drop_column('employees', 'lifecycle_status')
