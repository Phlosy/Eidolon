"""Shared schedule mutation validation for projects, milestones, and tasks."""

from datetime import UTC, datetime


class InvalidScheduleError(ValueError):
    pass


def _comparable(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def apply_schedule_patch(entity, data: dict) -> None:
    """Apply a partial schedule patch after validating the resulting range."""
    start = data.get("planned_start_at", entity.planned_start_at)
    end = data.get("planned_end_at", entity.planned_end_at)
    if start is not None and end is not None and _comparable(end) < _comparable(start):
        raise InvalidScheduleError("planned_end_at must be on or after planned_start_at")
    for field, value in data.items():
        setattr(entity, field, value)
