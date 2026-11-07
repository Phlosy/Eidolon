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

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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
    query = select(FactCursorRow.last_seq).where(FactCursorRow.consumer == consumer_name)
    return db.scalar(query) or 0


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
    start = cursor(db, consumer.name)
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


def rebuild(
    factory: sessionmaker, projection: Projection, *, store: FactStore | None = None
) -> int:
    """Throw a projection's state away and derive it again from the full log."""
    db = factory()
    try:
        projection.reset(db)
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
