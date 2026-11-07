"""Shared test helpers: a small real projection and its scratch tables.

``KindCounts`` counts facts per kind in a throw-away table. It is idempotent via a
seen-set, the pattern every projection with non-idempotent arithmetic must follow.
The tables are created per test module and dropped again so `alembic check` in
test_kernel_guards stays clean.
"""

from contextlib import contextmanager

from sqlalchemy import text

from eidolon.kernel.consumers import Registry
from eidolon.kernel.facts import Actor, NewFact
from eidolon.kernel.store import FactStore

store = FactStore()

SCRATCH_DDL = (
    "CREATE TABLE kind_counts (kind text PRIMARY KEY, n int NOT NULL)",
    "CREATE TABLE kind_counts_seen (seq bigint PRIMARY KEY)",
)
SCRATCH_DROP = "DROP TABLE IF EXISTS kind_counts, kind_counts_seen"


@contextmanager
def scratch_tables(engine):
    with engine.begin() as conn:
        for ddl in SCRATCH_DDL:
            conn.execute(text(ddl))
    try:
        yield
    finally:
        with engine.begin() as conn:
            conn.execute(text(SCRATCH_DROP))


class KindCounts:
    name = "kind_counts"
    handles = None

    def __init__(self, fail_on_seq: int | None = None, on_handle=None):
        self.fail_on_seq = fail_on_seq
        self.on_handle = on_handle
        self.calls = 0

    def handle(self, db, fact):
        self.calls += 1
        if self.on_handle is not None:
            self.on_handle(fact)
        if fact.seq == self.fail_on_seq:
            raise RuntimeError(f"boom at {fact.seq}")
        inserted = db.execute(
            text("INSERT INTO kind_counts_seen (seq) VALUES (:seq) ON CONFLICT DO NOTHING"),
            {"seq": fact.seq},
        ).rowcount
        if inserted:
            db.execute(
                text(
                    "INSERT INTO kind_counts (kind, n) VALUES (:kind, 1) "
                    "ON CONFLICT (kind) DO UPDATE SET n = kind_counts.n + 1"
                ),
                {"kind": fact.kind},
            )

    def reset(self, db):
        db.execute(text("TRUNCATE kind_counts, kind_counts_seen"))


def counts(db):
    return dict(db.execute(text("SELECT kind, n FROM kind_counts ORDER BY kind")).all())


def seed(db, company, n, kind="work.task.created", prefix=""):
    facts = [
        store.append(
            db,
            NewFact(company, Actor.system(), kind, {"i": i}, idempotency_key=f"{prefix}{kind}-{i}"),
        )
        for i in range(n)
    ]
    db.commit()
    return facts


def registry_with(*consumers) -> Registry:
    registry = Registry()
    for consumer in consumers:
        registry.register(consumer)
    return registry
