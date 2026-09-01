"""Event repository (activity feed)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event


def list_events(db: Session, limit: int = 100, actor_employee_id: int | None = None) -> list[Event]:
    stmt = select(Event).order_by(Event.id.desc()).limit(limit)
    if actor_employee_id is not None:
        stmt = stmt.where(Event.actor_employee_id == actor_employee_id)
    return list(db.scalars(stmt))
