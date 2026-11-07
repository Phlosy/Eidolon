"""K1–K5: the fact log records exactly once, in order, immutably, verifiably."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from eidolon.db import SessionLocal
from eidolon.kernel import hashchain
from eidolon.kernel.facts import (
    Actor,
    ActorKind,
    InvalidKind,
    InvalidPayload,
    NewFact,
    validate_kind,
)
from eidolon.kernel.store import FactStore, IdempotencyConflict, PayloadTooLarge

store = FactStore()
SYSTEM = Actor.system()


def new(company, key, *, kind="work.task.created", payload=None, actor=SYSTEM, **extra):
    return NewFact(
        company_id=company,
        actor=actor,
        kind=kind,
        payload=payload if payload is not None else {"key": key},
        idempotency_key=key,
        **extra,
    )


def test_append_assigns_global_and_per_company_order(db):
    a, b = uuid4(), uuid4()
    facts = [
        store.append(db, new(a, "a1")),
        store.append(db, new(b, "b1")),
        store.append(db, new(a, "a2")),
        store.append(db, new(a, "a3")),
        store.append(db, new(b, "b2")),
    ]
    db.commit()

    assert [f.seq for f in facts] == sorted(f.seq for f in facts)
    assert [f.chain_seq for f in facts if f.company_id == a] == [1, 2, 3]
    assert [f.chain_seq for f in facts if f.company_id == b] == [1, 2]

    chain_a = store.chain(db, a)
    assert chain_a[0].prev_hash == hashchain.GENESIS_HASH
    assert chain_a[1].prev_hash == chain_a[0].hash
    assert chain_a[2].prev_hash == chain_a[1].hash
    assert store.verify_chain(db, a).ok and store.verify_chain(db, b).ok
    assert store.verify_chain(db, a).checked == 3

    first = facts[0]
    assert first.correlation_id == first.id  # a root fact correlates with itself
    assert first.created_at.tzinfo is not None
    assert store.get(db, first.id) == first


def test_causation_and_correlation_are_kept(db):
    company = uuid4()
    root = store.append(db, new(company, "root"))
    child = store.append(
        db,
        new(company, "child", causation_id=root.id, correlation_id=root.correlation_id),
    )
    db.commit()
    assert child.causation_id == root.id
    assert [f.idempotency_key for f in store.by_correlation(db, root.id)] == ["root", "child"]


def test_idempotent_append_returns_original_and_rejects_conflict(db):
    company = uuid4()
    first = store.append(db, new(company, "k", payload={"n": 1}))
    db.commit()

    again = store.append(db, new(company, "k", payload={"n": 1}))
    assert again == first
    assert store.last_seq(db) == first.seq

    with pytest.raises(IdempotencyConflict):
        store.append(db, new(company, "k", payload={"n": 2}))
    db.rollback()

    # Same key in another company is a different fact.
    other = store.append(db, new(uuid4(), "k", payload={"n": 2}))
    assert other.id != first.id


def test_facts_cannot_be_updated_or_deleted(db):
    company = uuid4()
    fact = store.append(db, new(company, "k"))
    db.commit()

    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(
            text("UPDATE facts SET kind = 'work.task.deleted' WHERE id = :id"), {"id": fact.id}
        )
    db.rollback()
    with pytest.raises(IntegrityError, match="append-only"):
        db.execute(text("DELETE FROM facts WHERE id = :id"), {"id": fact.id})
    db.rollback()
    assert store.get(db, fact.id) == fact


def _disable_trigger(db):
    db.execute(text("ALTER TABLE facts DISABLE TRIGGER facts_no_update_delete"))


def _enable_trigger(db):
    db.execute(text("ALTER TABLE facts ENABLE TRIGGER facts_no_update_delete"))


def test_tampering_breaks_the_chain(db):
    company = uuid4()
    facts = [store.append(db, new(company, f"k{i}")) for i in range(4)]
    db.commit()
    assert store.verify_chain(db, company).ok

    # A privileged attacker edits history behind the trigger's back.
    _disable_trigger(db)
    db.execute(
        text('UPDATE facts SET payload = \'{"key": "forged"}\'::jsonb WHERE id = :id'),
        {"id": facts[1].id},
    )
    db.commit()
    report = store.verify_chain(db, company)
    assert not report.ok and report.broken_at == 2 and report.reason == "hash mismatch"
    assert report.checked == 1

    # Put the original payload back; the hash is unchanged so the chain is whole again.
    db.execute(
        text('UPDATE facts SET payload = \'{"key": "k1"}\'::jsonb WHERE id = :id'),
        {"id": facts[1].id},
    )
    db.commit()
    assert store.verify_chain(db, company).ok

    # Removing a fact from the middle leaves a gap.
    db.execute(text("DELETE FROM facts WHERE id = :id"), {"id": facts[2].id})
    db.commit()
    report = store.verify_chain(db, company)
    assert not report.ok and report.broken_at == 4 and report.reason.startswith("gap")
    _enable_trigger(db)
    db.commit()


def test_concurrent_appends_do_not_fork_the_chain(database):
    company = uuid4()

    def writer(n):
        with SessionLocal() as session:
            for i in range(10):
                store.append(session, new(company, f"w{n}-{i}"))
                session.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(8)))

    with SessionLocal() as session:
        chain = store.chain(session, company, limit=1000)
        assert [f.chain_seq for f in chain] == list(range(1, 81))
        assert len({f.hash for f in chain}) == 80
        assert store.verify_chain(session, company).ok


def test_payload_must_be_plain_json_and_bounded(db):
    company = uuid4()
    with pytest.raises(InvalidPayload, match="datetime"):
        new(company, "k", payload={"at": datetime.now(UTC)})
    with pytest.raises(InvalidPayload, match="NaN"):
        new(company, "k", payload={"x": float("nan")})
    with pytest.raises(InvalidPayload, match="keys must be strings"):
        new(company, "k", payload={1: "x"})
    with pytest.raises(InvalidPayload, match="set"):
        new(company, "k", payload={"s": {1, 2}})

    small = FactStore(payload_max_bytes=64)
    with pytest.raises(PayloadTooLarge):
        small.append(db, new(company, "big", payload={"text": "x" * 100}))
    # Canonical bytes are what is measured, not Python repr.
    small.append(db, new(company, "fits", payload={"a": 1, "b": [1, 2, 3]}))


def test_kinds_are_namespaced():
    assert validate_kind("work.task.created") == "work.task.created"
    assert validate_kind("agent.tool.call_completed") == "agent.tool.call_completed"
    for bad in ("task.created", "Work.task.created", "work.task", "work..created", "x"):
        with pytest.raises(InvalidKind):
            validate_kind(bad)
    with pytest.raises(InvalidKind, match="unknown layer"):
        validate_kind("marketing.lead.created")


def test_actor_rules():
    assert Actor.system().as_dict() == {"kind": "system", "id": None}
    with pytest.raises(ValueError):
        Actor(ActorKind.agent)
    with pytest.raises(ValueError):
        Actor(ActorKind.system, "root")
    assert Actor(ActorKind.human, "user-1").id == "user-1"


def test_stream_filters_by_kind_and_company(db):
    a, b = uuid4(), uuid4()
    store.append(db, new(a, "1", kind="work.task.created"))
    store.append(db, new(a, "2", kind="work.task.completed"))
    store.append(db, new(b, "3", kind="work.task.created"))
    db.commit()
    created = store.stream(db, kinds=["work.task.created"])
    assert [f.company_id for f in created] == [a, b]
    assert [f.idempotency_key for f in store.stream(db, company_id=a)] == ["1", "2"]
    assert store.stream(db, after_seq=store.last_seq(db)) == []
