"""Discovery must make bounded progress through durable history, across restarts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Barrier

import pytest
from sqlalchemy import DateTime, bindparam, func, insert, select, text
from test_m3_management_dispatch import claim, source
from test_m3_management_dispatch import targets as targets

from app.core.database import SessionLocal
from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.models.base import utcnow
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.organization import Company
from app.work import management_dispatch


@contextmanager
def company_scope(company_id):
    token = set_request_identity(
        RequestIdentity(user_id=1, company_id=company_id, membership_role="owner", session_id=1)
    )
    try:
        yield
    finally:
        reset_request_identity(token)


def observed_history(db, targets, count):
    before = db.scalar(select(func.max(Event.id))) or 0
    db.execute(
        insert(Event),
        [
            {
                "type": "task.blocked",
                "company_id": targets.project.company_id,
                "project_id": targets.project.id,
                "task_id": targets.task.id,
                "payload": {},
            }
            for _ in range(count)
        ],
    )
    now = utcnow()
    db.execute(
        insert(ManagementDispatch.__table__).from_select(
            [
                "source_event_id",
                "source_type",
                "purpose",
                "company_id",
                "project_id",
                "task_id",
                "details_json",
                "created_at",
                "updated_at",
            ],
            select(
                Event.id,
                Event.type,
                text("'management'"),
                Event.company_id,
                Event.project_id,
                Event.task_id,
                text("'{}'"),
                bindparam("now", type_=DateTime),
                bindparam("now", type_=DateTime),
            ).where(Event.id > before),
        ),
        {"now": now},
    )
    db.commit()


def measured_discovery():
    steps = 0

    @contextmanager
    def factory():
        nonlocal steps
        with SessionLocal() as db:
            connection = db.connection().connection.driver_connection

            def count():
                nonlocal steps
                steps += 100
                return 0

            connection.set_progress_handler(count, 100)
            try:
                yield db
            finally:
                connection.set_progress_handler(None, 0)

    assert management_dispatch.discover_sources(session_factory=factory) == []
    return steps


@pytest.mark.parametrize("scoped", [False, True])
def test_idle_discovery_cost_does_not_grow_with_observed_history(db, targets, scoped):
    with company_scope(targets.project.company_id) if scoped else nullcontext():
        observed_history(db, targets, 100)
        for _ in range(3):
            management_dispatch.discover_sources()
        small = measured_discovery()
        observed_history(db, targets, 9900)
        # A new Session for every batch also proves progress survives a scanner restart.
        for _ in range(101):
            management_dispatch.discover_sources()
        large = measured_discovery()
    print(f"DISCOVERY_VM_STEPS scoped={scoped} rows_100={small} rows_10000={large}")
    assert large <= small + 2000, f"Idle SQLite VM work grew from {small} to {large} steps"


@pytest.mark.parametrize("mutation", ["insert", "type_change"])
def test_eligible_low_id_event_rewinds_progress_without_replaying_observed_sources(
    db, targets, mutation
):
    low = source(db, targets, "project.management_assigned", task_id=None)
    high = source(db, targets)
    for scope in (nullcontext(), company_scope(targets.project.company_id)):
        with scope:
            management_dispatch.discover_sources()
    if mutation == "insert":
        db.execute(text("DELETE FROM events WHERE id=:id"), {"id": low})
        db.execute(
            insert(Event).values(
                id=low,
                type="project.replan_required",
                company_id=targets.project.company_id,
                project_id=targets.project.id,
                payload={},
            )
        )
    else:
        db.execute(
            text("UPDATE events SET type='project.replan_required' WHERE id=:id"), {"id": low}
        )
    db.commit()
    with company_scope(targets.project.company_id):
        assert management_dispatch.discover_sources() == [low]
        assert management_dispatch.discover_sources() == []
    assert management_dispatch.discover_sources() == []
    assert (
        db.scalar(
            select(func.count())
            .select_from(ManagementDispatch)
            .where(ManagementDispatch.source_event_id.in_([low, high]))
        )
        == 2
    )


def test_backfill_does_not_skip_older_sources_observed_out_of_order(db, targets):
    ids = [source(db, targets, kind) for kind in sorted(management_dispatch.SOURCE_EVENTS)]
    management_dispatch.inspect_source(ids[-1])
    found = []
    for _ in range(6):
        found.extend(management_dispatch.discover_sources(limit=2))
    assert set(found) == {targets.review_event.id, *ids[:-1]}
    assert len(found) == len(set(found))
    assert management_dispatch.discover_sources(limit=2) == []
    newest = source(db, targets)
    assert management_dispatch.discover_sources(limit=2) == [newest]


def test_discovery_rollback_does_not_advance_past_failed_observation(db, targets, monkeypatch):
    ids = [source(db, targets) for _ in range(2)]
    original = management_dispatch._observe

    def fail(db, event):
        result = original(db, event)
        if event.id == ids[-1]:
            raise RuntimeError("injected failure before checkpoint commit")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(management_dispatch, "_observe", fail)
        with pytest.raises(RuntimeError, match="checkpoint commit"):
            management_dispatch.discover_sources()
    assert db.scalar(select(func.count()).select_from(ManagementDispatch)) == 0
    assert set(management_dispatch.discover_sources()) == {targets.review_event.id, *ids}


def test_company_and_global_discovery_progress_are_independent(db, targets):
    foreign = Company(name="Other discovery", slug="other-discovery")
    db.add(foreign)
    db.commit()
    own_id = source(db, targets)
    foreign_id = source(db, targets, company_id=foreign.id)
    with company_scope(targets.project.company_id):
        assert set(management_dispatch.discover_sources()) == {targets.review_event.id, own_id}
        assert management_dispatch.discover_sources() == []
    with company_scope(foreign.id):
        assert management_dispatch.discover_sources() == [foreign_id]
    late_id = source(db, targets)
    assert management_dispatch.discover_sources() == [late_id]
    with company_scope(targets.project.company_id):
        assert management_dispatch.discover_sources() == []


def test_concurrent_scanners_commit_disjoint_batches_without_skipping_new_arrivals(db, targets):
    ids = {targets.review_event.id, *(source(db, targets) for _ in range(5))}
    barrier = Barrier(2)

    def scan():
        barrier.wait(timeout=5)
        return management_dispatch.discover_sources(limit=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scan) for _ in range(2)]
        batches = [future.result(timeout=10) for future in futures]
    assert len(set(batches[0] + batches[1])) == 4
    new = source(db, targets)
    rest = management_dispatch.discover_sources(limit=3)
    assert set(batches[0] + batches[1] + rest) == ids | {new}
    assert management_dispatch.discover_sources() == []


@pytest.mark.parametrize("exception", [ValueError, TypeError, KeyError])
def test_readiness_errors_wait_durably_then_recover_same_source(
    db, targets, monkeypatch, exception
):
    identifier = source(db, targets)

    def unreadable(*_args):
        raise exception("private readiness diagnostic")

    with monkeypatch.context() as patch:
        patch.setattr(management_dispatch.readiness, "readiness_report", unreadable)
        result = claim(identifier)
        assert not result.created and result.dispatch.work_session_id is None
        assert result.dispatch.waiting_reason == "environment_not_ready"
        assert "private" not in str(result.dispatch.as_dict())
    resumed = claim(identifier)
    assert resumed.created and resumed.dispatch.id == result.dispatch.id
    assert not claim(identifier).created
