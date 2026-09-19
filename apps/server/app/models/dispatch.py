"""Observed dispatch notification cycles, never an authority for readiness."""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DispatchNotice(TimestampMixin, Base):
    __tablename__ = "dispatch_notices"
    __table_args__ = (
        Index(
            "uq_dispatch_notice_open",
            "scope_type",
            "scope_id",
            "event_type",
            unique=True,
            sqlite_where=text("closed_at IS NULL"),
            postgresql_where=text("closed_at IS NULL"),
        ),
        Index(
            "ix_dispatch_notice_pending",
            "id",
            sqlite_where=text("closed_at IS NULL AND broadcast_at IS NULL"),
            postgresql_where=text("closed_at IS NULL AND broadcast_at IS NULL"),
        ),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100))
    assignee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # An attempted offer to the in-memory bus, not a consumer completion receipt.
    broadcast_at: Mapped[datetime | None] = mapped_column(nullable=True)
