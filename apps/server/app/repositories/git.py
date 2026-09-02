"""GitConnection repository (v0.3 phase 2).

Git connections are company-level infrastructure config: the list is
scope-free (no employee scoping, unlike Provider accounts).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.git import GitConnection


def list_connections(db: Session) -> list[GitConnection]:
    return list(db.scalars(select(GitConnection).order_by(GitConnection.id)))


def get_connection(db: Session, connection_id: int) -> GitConnection | None:
    return db.get(GitConnection, connection_id)


def create_connection(db: Session, **fields) -> GitConnection:
    connection = GitConnection(**fields)
    db.add(connection)
    db.flush()
    return connection


def delete_connection(db: Session, connection: GitConnection) -> None:
    db.delete(connection)
    db.flush()
