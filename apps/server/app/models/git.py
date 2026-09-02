"""Git domain (v0.3 phase 2): GitConnection for external platforms.

A GitConnection describes an EXTERNAL self-hosted git platform (GitLab /
self-hosted Gitea / GitHub Enterprise / custom) that Eidolon connects to —
Eidolon never installs or manages these. Plaintext tokens are NEVER stored
here — only a ``credential_ref`` into the secret store, same as Provider.
"""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import GitPlatformType


class GitConnection(TimestampMixin, Base):
    __tablename__ = "git_connections"

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    platform_type: Mapped[str] = mapped_column(String(50), default=GitPlatformType.custom.value)
    base_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(default=True)
    # Reference into the secret store (e.g. "local:<uuid4>"), NOT the token itself.
    credential_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
