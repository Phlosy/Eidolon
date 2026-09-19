"""Deliver in-memory follow-up effects only after their database transaction commits.

These callbacks are not durable jobs. The committed source rows remain the recovery
facts when delivery fails or the process exits before a callback can run.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from app.core.logging import get_logger

logger = get_logger(__name__)
_STATE_KEY = "eidolon_transaction_effects"


@dataclass
class _PendingEffects:
    callbacks: dict[SessionTransaction, list[Callable[[], object]]] = field(default_factory=dict)
    committed: set[SessionTransaction] = field(default_factory=set)


def after_commit(db: Session, callback: Callable[[], object]) -> None:
    """Attach delivery to the current savepoint or outer transaction."""
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        raise RuntimeError("after_commit requires an active transaction")
    state = db.info.setdefault(_STATE_KEY, _PendingEffects())
    state.callbacks.setdefault(transaction, []).append(callback)


def ensure_sqlite_transaction(db: Session) -> None:
    """Keep SQLite SAVEPOINT releases inside a real, rollback-capable transaction.

    The legacy sqlite3 driver does not BEGIN for reads. SQLAlchemy may therefore
    report an active Session transaction while SQLite has none; a first SAVEPOINT
    would otherwise become the physical transaction and RELEASE would commit it.
    """
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


@event.listens_for(Session, "after_commit")
def _mark_committed(db: Session) -> None:
    state = db.info.get(_STATE_KEY)
    if state is not None:
        transaction = db.get_nested_transaction() or db.get_transaction()
        state.committed.add(transaction)


@event.listens_for(Session, "after_transaction_end")
def _finish_transaction(db: Session, transaction: SessionTransaction) -> None:
    state = db.info.get(_STATE_KEY)
    if state is None:
        return
    callbacks = state.callbacks.pop(transaction, [])
    committed = transaction in state.committed
    state.committed.discard(transaction)
    if transaction.parent is not None:
        if committed and callbacks:
            state.callbacks.setdefault(transaction.parent, []).extend(callbacks)
        return

    # Closing/rolling back a Session also reaches this hook, without after_commit.
    # Clear session-owned state before delivery; callbacks may start other work.
    db.info.pop(_STATE_KEY, None)
    if committed:
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.exception("post-commit effect delivery failed")
