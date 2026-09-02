"""GitConnection repository (v0.3 phase 2).

Git connections are company-level infrastructure config and are always scoped
to the current Human User's company when a request identity is active.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.git import GitConnection


def list_connections(db: Session) -> list[GitConnection]:
    stmt = select(GitConnection).order_by(GitConnection.id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(GitConnection.company_id == identity.company_id)
    return list(db.scalars(stmt))


def get_connection(db: Session, connection_id: int) -> GitConnection | None:
    stmt = select(GitConnection).where(GitConnection.id == connection_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(GitConnection.company_id == identity.company_id)
    return db.scalar(stmt)


def create_connection(db: Session, **fields) -> GitConnection:
    identity = get_request_identity()
    if identity is not None:
        fields.setdefault("company_id", identity.company_id)
    connection = GitConnection(**fields)
    db.add(connection)
    db.flush()
    return connection


def delete_connection(db: Session, connection: GitConnection) -> None:
    db.delete(connection)
    db.flush()
