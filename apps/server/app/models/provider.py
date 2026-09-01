"""Provider domain (v0.2): Provider / ModelBinding / Secret.

Providers hold model-provider connection metadata; plaintext API keys are NEVER
stored here — only a ``credential_ref`` into the secret store. Scope is enforced
at the repository/service level: company-scope providers are visible to all,
employee-scope providers only to their owner.
"""

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ProviderScope, ProviderType


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(200))
    provider_type: Mapped[str] = mapped_column(String(50), default=ProviderType.custom.value)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scope: Mapped[str] = mapped_column(String(50), default=ProviderScope.company.value)
    owner_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    # Reference into the secret store (e.g. "local:<uuid4>"), NOT the key itself.
    credential_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelBinding(TimestampMixin, Base):
    """Binds an employee to a provider+model. Primary binding is used; the
    position column reserves ordered fallback chains (data model only for now)."""

    __tablename__ = "model_bindings"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    model: Mapped[str] = mapped_column(String(200))
    is_primary: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)  # fallback order (reserved)


class Secret(TimestampMixin, Base):
    """Encrypted secret material. ``ref`` is the public handle ("local:<uuid4>")."""

    __tablename__ = "secrets"

    ref: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    ciphertext: Mapped[str] = mapped_column(Text)
