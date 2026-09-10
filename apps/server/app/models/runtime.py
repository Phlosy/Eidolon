"""Runtime domain (v0.2): EmployeeBrain / RuntimeInstance / RuntimeImage.

A RuntimeInstance is the persisted, per-employee execution environment
(One Employee = One Persistent Agent). It survives restarts and is driven
through the RuntimeInstanceManager; identity/memory/skills live outside it,
so replacing an instance never touches them.
"""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    DeploymentMode,
    HealthStatus,
    ImageCompatibility,
    ImageUpdateStatus,
    RuntimeInstanceStatus,
    RuntimeType,
)


class EmployeeBrain(TimestampMixin, Base):
    """Persistent personality/goals/learning configuration per employee.

    `traits` is the authoritative personality store (`{"schema_version": 1, ...}`) read only
    through `app.brain.BrainTraits`; `curiosity` survives as a legacy compatibility mirror
    (§14.1) and is partial-indexed for backfill scans. Behavior code must read neither — it
    reads the derived BehaviorPolicy.
    """

    __tablename__ = "employee_brains"
    __table_args__ = (
        Index(
            "ix_employee_brains_curiosity_legacy",
            "curiosity",
            unique=False,
            sqlite_where=text("traits IS NULL"),
            postgresql_where=text("traits IS NULL"),
        ),
        # R1.1（docs/person-core-migration.md D4 批次 1）：一人一脑的唯一性在 person 口径
        # 上的镜像。必须写进模型，否则 alembic check 报漂移。
        Index(
            "uq_employee_brains_person",
            "person_id",
            unique=True,
            sqlite_where=text("person_id IS NOT NULL"),
            postgresql_where=text("person_id IS NOT NULL"),
        ),
    )

    # deprecated（R1.1）：读口径已切到 person_id；列保留作兼容镜像，随表留存不删；
    # v27 起 nullable（培养路径的 person-only 行此列为 NULL）。
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, unique=True, index=True
    )
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personality: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    interests: Mapped[list] = mapped_column(JSON, default=list)
    learning_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    memory_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    curiosity: Mapped[float] = mapped_column(default=0.5)
    traits: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RuntimeInstance(TimestampMixin, Base):
    __tablename__ = "runtime_instances"
    __table_args__ = (
        # R1.4（docs/person-core-migration.md D4 批次 4）：一人一实例在 person 口径上的
        # 镜像（employee_id unique 的对应物）。必须写进模型，否则 alembic check 报漂移。
        Index(
            "uq_runtime_instances_person",
            "person_id",
            unique=True,
            sqlite_where=text("person_id IS NOT NULL"),
            postgresql_where=text("person_id IS NOT NULL"),
        ),
    )

    # deprecated（R1.4）：读口径已切到 person_id；列保留作兼容镜像，随表留存不删；
    # v27 起 nullable（培养路径的 person-only 行此列为 NULL）。
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, unique=True, index=True
    )
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_type: Mapped[str] = mapped_column(String(50), default=RuntimeType.mock.value)
    deployment_mode: Mapped[str] = mapped_column(String(50), default=DeploymentMode.mock.value)
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image: Mapped[str] = mapped_column(String(300), default="")
    image_tag: Mapped[str] = mapped_column(String(100), default="latest")
    image_digest: Mapped[str | None] = mapped_column(String(300), nullable=True)
    runtime_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default=RuntimeInstanceStatus.created.value, index=True
    )
    health_status: Mapped[str] = mapped_column(String(50), default=HealthStatus.unknown.value)
    internal_host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    internal_port: Mapped[int | None] = mapped_column(nullable=True)
    workspace_path: Mapped[str] = mapped_column(String(500), default="")
    data_path: Mapped[str] = mapped_column(String(500), default="")
    model_binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_bindings.id"), nullable=True
    )
    cpu_limit: Mapped[float] = mapped_column(default=2.0)
    memory_limit_mb: Mapped[int] = mapped_column(default=4096)
    restart_policy: Mapped[str] = mapped_column(String(50), default="unless-stopped")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_healthcheck_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeImage(TimestampMixin, Base):
    """Tracks a runtime image (per runtime_type) and its update state."""

    __tablename__ = "runtime_images"

    runtime_type: Mapped[str] = mapped_column(String(50), unique=True)
    repository: Mapped[str] = mapped_column(String(300))
    tag: Mapped[str] = mapped_column(String(100), default="latest")
    digest: Mapped[str | None] = mapped_column(String(300), nullable=True)
    installed_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latest_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latest_digest: Mapped[str | None] = mapped_column(String(300), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="stable")
    update_available: Mapped[bool] = mapped_column(default=False)
    compatibility_status: Mapped[str] = mapped_column(
        String(50), default=ImageCompatibility.unknown.value
    )
    update_status: Mapped[str] = mapped_column(String(50), default=ImageUpdateStatus.idle.value)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
