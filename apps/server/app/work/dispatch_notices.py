"""Record notification occurrences atomically with their source Event.

The scheduler computes readiness first. These rows only deduplicate observations;
they never enable a task. Persisted events remain available to consumer recovery.
"""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.redaction import redact_data
from app.core.request_context import get_request_identity
from app.events.bus import bus
from app.models.base import utcnow
from app.models.dispatch import DispatchNotice
from app.models.event import Event
from app.repositories import persons


def begin_observation(db: Session) -> None:
    """Read a consistent sweep and record its cycles before releasing the snapshot."""
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.connection(execution_options={"isolation_level": "SERIALIZABLE"})


def reconcile(db: Session, observations: list[tuple[int | None, str, dict]]) -> None:
    """One scheduler sweep in its existing transaction; only changed cycles write."""
    current = {
        (
            "task" if task_id is not None else "project",
            int(task_id or payload["project_id"]),
            event_type,
        ): payload
        for task_id, event_type, payload in observations
    }
    identity = get_request_identity()
    if identity is not None and any(
        int(payload["company_id"]) != identity.company_id for payload in current.values()
    ):
        raise ValueError("dispatch notice belongs to another company")
    query = select(DispatchNotice).where(DispatchNotice.closed_at.is_(None))
    if identity is not None:
        query = query.where(DispatchNotice.company_id == identity.company_id)
    active = list(db.scalars(query))
    unchanged = set()
    for notice in active:
        key = (notice.scope_type, notice.scope_id, notice.event_type)
        payload = current.get(key)
        if (
            payload is None
            or payload.get("assignee_id") != notice.assignee_id
            or payload["company_id"] != notice.company_id
        ):
            notice.closed_at = utcnow()
        else:
            unchanged.add(key)
    db.flush()
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    for (scope_type, scope_id, event_type), payload in current.items():
        company_id = int(payload["company_id"])
        if (scope_type, scope_id, event_type) in unchanged:
            continue
        now = utcnow()
        result = db.execute(
            insert(DispatchNotice)
            .values(
                company_id=company_id,
                scope_type=scope_type,
                scope_id=scope_id,
                event_type=event_type,
                assignee_id=payload.get("assignee_id"),
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["scope_type", "scope_id", "event_type"],
                index_where=DispatchNotice.closed_at.is_(None),
            )
            .returning(DispatchNotice.id)
        )
        notice_id = result.scalar_one_or_none()
        if notice_id is None:
            continue
        actor = payload.get("assignee_id")
        event = Event(
            company_id=company_id,
            project_id=int(payload["project_id"]),
            task_id=scope_id if scope_type == "task" else None,
            actor_employee_id=actor,
            actor_person_id=persons.write_person_id(db, actor) if actor is not None else None,
            type=event_type,
            payload=redact_data({**payload, "dispatch_notice_id": notice_id}),
        )
        db.add(event)
        db.flush()
        db.execute(
            update(DispatchNotice).where(DispatchNotice.id == notice_id).values(event_id=event.id)
        )


def broadcast_pending(limit: int = 100) -> None:
    """Retry offers that were not recorded, reusing Event ID instead of reinserting.

    A crash after enqueue and before broadcast_at can offer the same event again.
    Consumers must use source IDs for idempotency; this is not an execution receipt.
    """
    with SessionLocal() as db:
        query = (
            select(DispatchNotice.id, DispatchNotice.event_id)
            .where(
                DispatchNotice.closed_at.is_(None),
                DispatchNotice.broadcast_at.is_(None),
                DispatchNotice.event_id.is_not(None),
            )
            .order_by(DispatchNotice.id)
            .limit(limit)
        )
        identity = get_request_identity()
        if identity is not None:
            query = query.where(DispatchNotice.company_id == identity.company_id)
        pending = list(db.execute(query))
    for notice_id, event_id in pending:
        if bus.publish_persisted(event_id):
            with SessionLocal() as db:
                db.execute(
                    update(DispatchNotice)
                    .where(
                        DispatchNotice.id == notice_id,
                        DispatchNotice.event_id == event_id,
                        DispatchNotice.closed_at.is_(None),
                        DispatchNotice.broadcast_at.is_(None),
                    )
                    .values(broadcast_at=utcnow())
                )
                db.commit()
