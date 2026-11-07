"""K6: cursors, idempotent handlers, replay as the only recovery, rebuild from zero."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from support import KindCounts, counts, scratch_tables, seed, store

from eidolon.db import SessionLocal
from eidolon.kernel import consumers
from eidolon.kernel.consumers import (
    RebuildBlocked,
    Registry,
    function_consumer,
    process,
    rebuild,
    replay_all,
)


@pytest.fixture(autouse=True)
def _scratch(database):
    with scratch_tables(database):
        yield


def test_process_handles_everything_and_advances_cursor(db):
    company = uuid4()
    seed(db, company, 5)
    seed(db, company, 2, kind="work.task.completed")
    projection = KindCounts()

    results = process(SessionLocal, projection, limit=3)
    assert [r.scanned for r in results] == [3, 3, 1]
    assert counts(db) == {"work.task.completed": 2, "work.task.created": 5}
    assert consumers.cursor(db, "kind_counts") == store.last_seq(db)

    # Nothing new: one empty batch, cursor untouched.
    assert process(SessionLocal, projection)[0].scanned == 0


def test_failed_batch_rolls_back_effects_and_cursor(db):
    company = uuid4()
    facts = seed(db, company, 4)
    projection = KindCounts(fail_on_seq=facts[2].seq)

    with pytest.raises(RuntimeError, match="boom"):
        process(SessionLocal, projection, limit=10)
    assert counts(db) == {}  # the two handled facts were rolled back with the batch
    assert consumers.cursor(db, "kind_counts") == 0

    projection.fail_on_seq = None
    process(SessionLocal, projection, limit=10)
    assert counts(db) == {"work.task.created": 4}


def test_crash_between_handle_and_cursor_is_replayed(db, monkeypatch):
    """The effects landed but the cursor did not (process died in between)."""
    company = uuid4()
    seed(db, company, 3)
    projection = KindCounts()

    real_set_cursor = consumers.set_cursor

    def dying_set_cursor(session, name, seq):
        raise RuntimeError("process died")

    monkeypatch.setattr(consumers, "set_cursor", dying_set_cursor)
    with pytest.raises(RuntimeError, match="died"):
        process(SessionLocal, projection)
    assert counts(db) == {} and consumers.cursor(db, "kind_counts") == 0

    # Simulate a partial commit from a *different* code path: effects committed,
    # cursor not. Replay must then re-handle those facts without double counting.
    session = SessionLocal()
    for fact in store.stream(session):
        projection.handle(session, fact)
    session.commit()
    session.close()
    assert counts(db) == {"work.task.created": 3}

    monkeypatch.setattr(consumers, "set_cursor", real_set_cursor)
    handled = replay_all(SessionLocal, [projection])
    assert handled == {"kind_counts": 3}
    assert counts(db) == {"work.task.created": 3}  # idempotent: still 3, not 6
    assert consumers.cursor(db, "kind_counts") == store.last_seq(db)


def test_rebuild_reproduces_projection_from_zero(db):
    company = uuid4()
    seed(db, company, 6)
    seed(db, company, 1, kind="agent.run.completed")
    projection = KindCounts()
    process(SessionLocal, projection)
    before = counts(db)

    # Corrupt the read model; rebuild must not trust it.
    db.execute(text("UPDATE kind_counts SET n = 999"))
    db.commit()
    assert rebuild(SessionLocal, projection) == 7
    assert counts(db) == before == {"agent.run.completed": 1, "work.task.created": 6}


def test_handles_filter_skips_but_still_advances(db):
    company = uuid4()
    seed(db, company, 3)
    seed(db, company, 2, kind="money.cost.recorded")
    seen = []
    only_money = function_consumer(
        "money_only", ["money.cost.recorded"], lambda _db, fact: seen.append(fact.kind)
    )
    results = process(SessionLocal, only_money, limit=10)
    assert seen == ["money.cost.recorded"] * 2
    assert results[0].scanned == 5 and results[0].handled == 2
    assert consumers.cursor(db, "money_only") == store.last_seq(db)


def test_registry_rejects_duplicates_and_bad_kinds():
    registry = Registry()
    registry.register(function_consumer("a", None, lambda db, f: None))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(function_consumer("a", None, lambda db, f: None))
    with pytest.raises(ValueError):
        registry.register(function_consumer("b", ["not.namespaced"], lambda db, f: None))
    assert registry.projections() == []
    registry.register(KindCounts())
    assert [p.name for p in registry.projections()] == ["kind_counts"]


def test_rebuild_coexists_with_open_readers_and_never_hangs_on_locks(db):
    """A projection is read while it is rebuilt (the API serves it; a test's session
    has it open). ``reset`` must not take a lock readers hold off, and if something
    really does hold the tables, rebuild gives up loudly instead of hanging."""
    company = uuid4()
    seed(db, company, 3)
    projection = KindCounts()
    process(SessionLocal, projection)
    reader = SessionLocal()
    try:
        assert dict(reader.execute(text("SELECT kind, n FROM kind_counts")).all())  # tx open
        assert rebuild(SessionLocal, projection) == 3  # completes with the reader still open
        assert counts(db) == {"work.task.created": 3}
    finally:
        reader.close()
    db.commit()  # our own read tx would otherwise keep the exclusive LOCK below waiting
    locker = SessionLocal()
    try:
        locker.execute(text("LOCK TABLE kind_counts IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(RebuildBlocked, match="locked by another session"):
            rebuild(SessionLocal, projection, lock_timeout="500ms")
    finally:
        locker.rollback()
        locker.close()
    assert rebuild(SessionLocal, projection) == 3  # the cursor was not reset by the failed try
