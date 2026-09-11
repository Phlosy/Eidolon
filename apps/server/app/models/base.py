from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(moment: datetime | None) -> datetime | None:
    """把数据库读回的时间按 UTC 归一（SQLite `DateTime` 是 naive，`utcnow()` 是 aware）。

    领域代码比较时间前必须过这一层，否则会抛
    `TypeError: can't compare offset-naive and offset-aware datetimes`（实测踩到过）。
    """
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
