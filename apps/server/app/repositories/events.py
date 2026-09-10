"""Event repository (activity feed)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event
from app.repositories import persons as person_repo


def list_events(db: Session, limit: int = 100, actor_employee_id: int | None = None) -> list[Event]:
    stmt = select(Event).order_by(Event.id.desc()).limit(limit)
    if actor_employee_id is not None:
        # R1.4：行为主体口径切 actor_person_id（单一入口换算，带旧口径回落）
        stmt = stmt.where(
            person_repo.read_criterion(
                db, actor_employee_id, Event.actor_person_id, Event.actor_employee_id
            )
        )
    return list(db.scalars(stmt))
