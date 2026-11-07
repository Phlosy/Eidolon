"""K9: the wake-up follows the commit, never a rollback."""

from uuid import uuid4

from eidolon.db import SessionLocal
from eidolon.kernel.facts import Actor, NewFact
from eidolon.kernel.notify import listening
from eidolon.kernel.store import FactStore

store = FactStore()


def _fact(key):
    return NewFact(uuid4(), Actor.system(), "work.task.created", {"k": key}, idempotency_key=key)


def test_notify_only_on_commit(database):
    with listening() as listener:
        assert listener.wait(0.2) == 0

        with SessionLocal() as db:
            store.append(db, _fact("rolled-back"))
            db.rollback()
        assert listener.wait(0.5) == 0

        with SessionLocal() as db:
            store.append(db, _fact("committed"))
            assert listener.wait(0.2) == 0  # not yet: still inside the transaction
            db.commit()
        assert listener.wait(2.0) >= 1
