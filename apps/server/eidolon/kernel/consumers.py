"""Cursor-based consumers of the fact log (K6).

A consumer is anything that reacts to facts: a projection that maintains a read
model, a scheduler that creates runs, a notifier. Each one has a durable cursor
(``fact_cursors.last_seq``). Processing a batch and advancing the cursor happen in
one transaction, so after a crash the batch is simply seen again. Handlers must
therefore be idempotent — the same discipline the legacy code enforced per
subsystem, now enforced once.

The fact log *is* the outbox: nothing is ever "published" separately from being
recorded, so there is no window in which a fact exists without its consequences
eventually following.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from eidolon.kernel.facts import Fact, validate_kind
from eidolon.kernel.store import FactCursorRow, FactStore


@runtime_checkable
class Consumer(Protocol):
    name: str
    # ``None`` means every fact. Otherwise only these kinds reach ``handle``; the
    # cursor still moves past everything else.
    handles: frozenset[str] | None

    def handle(self, db: Session, fact: Fact) -> None: ...


def starts_at_head(consumer: Consumer) -> bool:
    """A consumer whose *first* cursor is the current head rather than zero.

    Projections must replay from zero (that is what makes them rebuildable). A
    reactor that spends money or talks to the outside world (the reflector calls a
    model per completed run) must not: registering it on a database with history
    would otherwise act on every historical fact at once — a bill, not a replay.
    Declare ``start_at_head = True`` on such consumers."""
    return bool(getattr(consumer, "start_at_head", False))


@runtime_checkable
class Projection(Consumer, Protocol):
    """A consumer whose state is entirely derived and can be rebuilt from zero."""

    def reset(self, db: Session) -> None: ...


@dataclass
class Registry:
    consumers: dict[str, Consumer] = field(default_factory=dict)

    def register(self, consumer: Consumer) -> Consumer:
        if consumer.name in self.consumers:
            raise ValueError(f"consumer {consumer.name!r} already registered")
        if consumer.handles is not None:
            for kind in consumer.handles:
                validate_kind(kind)
        self.consumers[consumer.name] = consumer
        return consumer

    def projections(self) -> list[Projection]:
        return [c for c in self.consumers.values() if isinstance(c, Projection)]


def cursor(db: Session, consumer_name: str) -> int:
    return stored_cursor(db, consumer_name) or 0


def stored_cursor(db: Session, consumer_name: str) -> int | None:
    """The cursor row's value, or None when the consumer has never run."""
    query = select(FactCursorRow.last_seq).where(FactCursorRow.consumer == consumer_name)
    return db.scalar(query)


def set_cursor(db: Session, consumer_name: str, seq: int) -> None:
    now = datetime.now(UTC)
    stmt = insert(FactCursorRow).values(consumer=consumer_name, last_seq=seq, updated_at=now)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["consumer"], set_={"last_seq": seq, "updated_at": now}
        )
    )


@dataclass(frozen=True)
class BatchResult:
    consumer: str
    scanned: int
    handled: int
    cursor: int


def process_batch(
    db: Session, consumer: Consumer, *, store: FactStore | None = None, limit: int = 200
) -> BatchResult:
    """Handle up to ``limit`` facts after the cursor and advance it, in ``db``'s transaction."""
    store = store or FactStore()
    stored = stored_cursor(db, consumer.name)
    if stored is None and starts_at_head(consumer):
        head = store.last_seq(db)
        set_cursor(db, consumer.name, head)
        return BatchResult(consumer.name, 0, 0, head)
    start = stored or 0
    facts = store.stream(db, after_seq=start, limit=limit)
    handled = 0
    for fact in facts:
        if consumer.handles is None or fact.kind in consumer.handles:
            consumer.handle(db, fact)
            handled += 1
    end = facts[-1].seq if facts else start
    if end != start:
        set_cursor(db, consumer.name, end)
    return BatchResult(consumer.name, len(facts), handled, end)


def process(
    factory: sessionmaker,
    consumer: Consumer,
    *,
    store: FactStore | None = None,
    limit: int = 200,
    until_drained: bool = True,
) -> list[BatchResult]:
    """Run batches, each in its own transaction, until the consumer has caught up."""
    results: list[BatchResult] = []
    while True:
        db = factory()
        try:
            result = process_batch(db, consumer, store=store, limit=limit)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        results.append(result)
        if not until_drained or result.scanned < limit:
            return results


class RebuildBlocked(RuntimeError):
    """Another session holds a lock on the projection's tables and ``lock_timeout``
    expired. Rebuild never waits indefinitely: a hang here would look like a dead
    worker, not like the lock it is."""


def clear_tables(db: Session, *tables: str) -> None:
    """How a projection's ``reset`` empties its tables. ``DELETE`` rather than
    ``TRUNCATE``: TRUNCATE needs ACCESS EXCLUSIVE, so any open read transaction (an
    API request, a test's session, a psql shell) blocks the rebuild — and the
    rebuild then blocks every new reader. DELETE takes ROW EXCLUSIVE and coexists
    with readers under MVCC; they keep seeing the old rows until the reset commits."""
    for table in tables:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise ValueError(f"not a table name: {table!r}")
        db.execute(text(f"DELETE FROM {table}"))


def rebuild(
    factory: sessionmaker,
    projection: Projection,
    *,
    store: FactStore | None = None,
    lock_timeout: str = "10s",
) -> int:
    """Throw a projection's state away and derive it again from the full log.

    The reset runs under ``lock_timeout`` so a projection whose tables are locked
    elsewhere fails fast with ``RebuildBlocked`` instead of hanging."""
    db = factory()
    try:
        if not re.fullmatch(r"\d+(ms|s|min)?", lock_timeout):
            raise ValueError(f"lock_timeout must look like '10s' or '500ms', got {lock_timeout!r}")
        # SET cannot take a bind parameter; set_config can (is_local=true == SET LOCAL)
        db.execute(text("SELECT set_config('lock_timeout', :t, true)"), {"t": lock_timeout})
        try:
            projection.reset(db)
        except OperationalError as exc:
            if "lock timeout" in str(exc).lower() or "lock_timeout" in str(exc).lower():
                raise RebuildBlocked(
                    f"rebuild {projection.name}: its tables are locked by another session "
                    f"(lock_timeout {lock_timeout})"
                ) from exc
            raise
        set_cursor(db, projection.name, 0)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return sum(r.handled for r in process(factory, projection, store=store))


def replay_all(
    factory: sessionmaker, consumers: Iterable[Consumer], *, store: FactStore | None = None
) -> dict[str, int]:
    """Bring every consumer up to date. This is the whole crash-recovery story:
    whatever a dead process did not finish is still in the log, and each
    consumer's cursor says where it stopped."""
    return {c.name: sum(r.handled for r in process(factory, c, store=store)) for c in consumers}


def function_consumer(
    name: str, handles: Iterable[str] | None, fn: Callable[[Session, Fact], None]
) -> Consumer:
    """Small helper for consumers that are just a function."""

    class _Fn:
        def __init__(self) -> None:
            self.name = name
            self.handles = frozenset(handles) if handles is not None else None

        def handle(self, db: Session, fact: Fact) -> None:
            fn(db, fact)

    return _Fn()
