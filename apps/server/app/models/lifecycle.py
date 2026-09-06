"""Lifecycle domain (v0.4): positions, employment history, resource providers /
accounts / assets, entitlements, access packages, provisioning jobs / steps,
audit logs. See docs/design-v0.4-lifecycle.md §2.

Employment history is append-only: a transfer closes the old row with
``effective_to`` and opens a new one; rows are never overwritten or deleted.
"""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import (
    EntitlementType,
    PackageSource,
    ProvisioningJobStatus,
    ResourceAccountStatus,
)
from app.models.position import PositionAssignment

# 名字别名（不是第二个 mapper）：调用点可以逐段改读 PositionAssignment，
# 而表始终只有一个实体映射 —— 存在两个 Employment 类比存在两个位置概念更糟。
Employment = PositionAssignment


class Position(TimestampMixin, Base):
    __tablename__ = "positions"

    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(50), default="")


# `Employment` 已于 v0.7 领域重构中演进为 **PositionAssignment**
# （表名保留 `employments`，见 docs/position-system.md §2.4 拍板记录）。
# 下面的名字别名不是第二个 mapper，只是给尚未迁移的调用点一个过渡，P15 会清除。


class ResourceProvider(TimestampMixin, Base):
    """A system entitlements are provisioned into (builtin gitea, builtin docs,
    local workspace, external git connections)."""

    __tablename__ = "resource_providers"

    key: Mapped[str] = mapped_column(String(100), unique=True)  # e.g. "git:gitea"
    type: Mapped[str] = mapped_column(String(50))  # workspace | docs | git
    name: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    connection: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="active")


class ResourceAccount(TimestampMixin, Base):
    """The employee's account in one resource provider."""

    __tablename__ = "resource_accounts"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource_providers.id"), nullable=True
    )
    external_account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str] = mapped_column(String(200), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(
        String(50), default=ResourceAccountStatus.pending.value, index=True
    )
    provisioning_state: Mapped[str] = mapped_column(String(50), default="pending")
    last_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Entitlement(TimestampMixin, Base):
    __tablename__ = "entitlements"

    key: Mapped[str] = mapped_column(String(100), unique=True)  # e.g. "git:engineering-team"
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50), default=EntitlementType.permission.value)
    resource_type: Mapped[str] = mapped_column(String(50))  # workspace | docs | git
    description: Mapped[str] = mapped_column(String(2000), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class AccessPackage(TimestampMixin, Base):
    __tablename__ = "access_packages"

    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000), default="")
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)  # nullable → generic
    built_in: Mapped[bool] = mapped_column(default=False)


class AccessPackageItem(Base):
    __tablename__ = "access_package_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("access_packages.id"), index=True)
    entitlement_id: Mapped[int] = mapped_column(ForeignKey("entitlements.id"), index=True)


class EmployeePackage(TimestampMixin, Base):
    __tablename__ = "employee_packages"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("access_packages.id"), index=True)
    source: Mapped[str] = mapped_column(String(50), default=PackageSource.manual.value)
    assigned_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProvisioningJob(TimestampMixin, Base):
    __tablename__ = "provisioning_jobs"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # ProvisioningJobKind
    status: Mapped[str] = mapped_column(String(50), default=ProvisioningJobStatus.pending.value)
    total_steps: Mapped[int] = mapped_column(default=0)
    done_steps: Mapped[int] = mapped_column(default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Extra plan context (transfer diff, offboard asset target) so retries can
    # re-execute without the original request payload.
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ProvisioningStep(Base):
    __tablename__ = "provisioning_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("provisioning_jobs.id"), index=True)
    seq: Mapped[int] = mapped_column()
    resource_type: Mapped[str] = mapped_column(String(50))
    provider_key: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))  # provision|grant|revoke|suspend|...
    description: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(50), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    entitlement_id: Mapped[int | None] = mapped_column(ForeignKey("entitlements.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ResourceAsset(TimestampMixin, Base):
    """A trackable asset (repository / document / folder / workspace) with an
    owner; offboarding rewires ownership (v1 default: employee → department,
    recorded in metadata_json.department_id)."""

    __tablename__ = "resource_assets"

    resource_type: Mapped[str] = mapped_column(
        String(50)
    )  # repository|document|folder|workspace|artifact
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    owner_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    provider_key: Mapped[str] = mapped_column(String(100), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(
        String(100), default="user"
    )  # nullable per spec; "user" default
    action: Mapped[str] = mapped_column(String(100), index=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


__all__ = [
    "AccessPackage",
    "AccessPackageItem",
    "AuditLog",
    "EmployeePackage",
    "Employment",
    "Entitlement",
    "Position",
    "ProvisioningJob",
    "ProvisioningStep",
    "ResourceAccount",
    "ResourceAsset",
    "ResourceProvider",
]
