"""Event log (activity feed source). See docs/architecture.md §3.2/§7."""

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    type: Mapped[str] = mapped_column(String(100), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    # deprecated（R1.4）：行为主体读口径已切到 actor_person_id；列保留作兼容镜像。
    actor_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    actor_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
