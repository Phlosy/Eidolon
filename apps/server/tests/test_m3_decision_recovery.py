"""Recover only aggregation of committed effects; never execute missing actions."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import IntegrityError
from test_m3_action_confirmations import owner as confirmation_owner
from test_m3_action_confirmations import proposal
from test_m3_action_confirmations import world as _confirmation_world
from test_m3_runtime_bridge import _counts, _decision, _send
from test_m3_runtime_bridge import world as _world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.decision import DecisionRecord
from app.models.position import PositionAuthorityGrant
from app.work import action_confirmations, decisions, recovery
from app.work import tool_executor as executor
from app.work.runtime_operations import RuntimeExecution
from app.workflow.orchestrator import Orchestrator

world = _world
confirmation_world = _confirmation_world


def unfinished(world, monkeypatch, *, key="original"):
    with monkeypatch.context() as patch:
        patch.setattr(decisions, "refresh_from_receipts", lambda db, identifier: None)
        result = _send(world, _decision(world, key=key))
    assert result["decision_status"] == "EXECUTING"
    return result["decision_id"]


def scan(**kwargs):
    return recovery.reconcile_decision_receipts(through_id=recovery.decision_boundary(), **kwargs)


CRASH = """
import os, sys
from app.work import decisions
from app.work.runtime_bridge import handle_reply
decisions.refresh_from_receipts = lambda *args: os._exit(23)
handle_reply(int(sys.argv[1]), sys.argv[2])
"""

RESTART = """
import json
from dataclasses import asdict
from app.work import recovery
through = recovery.decision_boundary()
print(json.dumps([asdict(recovery.reconcile_decision_receipts(through_id=through)),
    asdict(recovery.reconcile_decision_receipts(through_id=through))]))
"""


@pytest.mark.parametrize("outcome", ["APPLIED", "PARTIALLY_APPLIED", "FAILED"])
def test_process_exit_after_last_effect_recovers_only_original_aggregate(db, world, outcome):
    request = _decision(world)
    denied = {"tool": "update_task", "args": {"task_id": world.target.id, "status": "done"}}
    if outcome == "PARTIALLY_APPLIED":
        request["decision"]["actions"][1] = denied
    elif outcome == "FAILED":
        request["decision"]["actions"] = [denied, denied]
    crashed = subprocess.run(
        [sys.executable, "-c", CRASH, str(world.session.id), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert crashed.returncode == 23, crashed.stderr
    record = db.scalar(select(DecisionRecord))
    assert record.status == "EXECUTING" and record.resolved_at is None
    audits = decisions.tool_audits_for(db, record.id)
    assert len(audits) == 2
    before = _counts()
    actor = record.actor_person_id
    db.rollback()
    restarted = subprocess.run(
        [sys.executable, "-c", RESTART],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert restarted.returncode == 0, restarted.stderr
    batches = json.loads(restarted.stdout)
    assert [batch["items"][0]["outcome"] for batch in batches] == ["repaired", "already_resolved"]
    db.expire_all()
    assert record.status == outcome and record.resolved_at is not None
    assert record.actor_person_id == actor
    assert [row.id for row in decisions.tool_audits_for(db, record.id)] == [
        row.id for row in audits
    ]
    assert _counts() == before
    when = record.resolved_at
    scan()
    db.refresh(record)
    assert record.resolved_at == when


def test_partial_actions_stay_unresolved_without_guessing_missing_request(db, world, monkeypatch):
    original = RuntimeExecution.execute_action

    def only_first(self, index, *args, **kwargs):
        if index == 1:
            raise SystemExit
        return original(self, index, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeExecution, "execute_action", only_first)
        with pytest.raises(SystemExit):
            _send(world, _decision(world))
    before = _counts()
    result = scan()
    assert result.items[0].outcome == "actions_incomplete"
    assert db.scalar(select(DecisionRecord)).status == "EXECUTING"
    assert _counts() == before


def test_concurrent_scans_preserve_one_resolution(db, world, monkeypatch):
    identifier = unfinished(world, monkeypatch)
    barrier = Barrier(2)

    def run():
        barrier.wait()
        return scan()

    before = _counts()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(batch.items[0].outcome for batch in results) == ["already_resolved", "repaired"]
    db.expire_all()
    assert db.get(DecisionRecord, identifier).status == "APPLIED" and _counts() == before


def test_real_sql_failure_rolls_back_aggregate_and_retries_same_receipts(db, world, monkeypatch):
    identifier = unfinished(world, monkeypatch)
    before = _counts()
    db.execute(
        text(
            "CREATE TRIGGER reject_decision_recovery BEFORE UPDATE ON decision_records "
            "WHEN NEW.status = 'APPLIED' "
            "BEGIN SELECT RAISE(ABORT, 'injected decision recovery failure'); END"
        )
    )
    db.commit()
    try:
        with pytest.raises(IntegrityError, match="injected decision recovery failure"):
            scan()
        db.expire_all()
        record = db.get(DecisionRecord, identifier)
        assert record.status == "EXECUTING" and record.resolved_at is None
        assert _counts() == before
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER reject_decision_recovery"))
        db.commit()
    assert scan().items[0].outcome == "repaired"
    assert scan().items[0].outcome == "already_resolved" and _counts() == before


def test_startup_boundary_and_company_scope_are_preserved(db, world, monkeypatch):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    first = unfinished(world, monkeypatch)
    through = recovery.decision_boundary()
    second = unfinished(world, monkeypatch, key="later")
    page = recovery.reconcile_decision_receipts(through_id=through, limit=1)
    assert page.complete and [item.decision_id for item in page.items] == [first]
    db.expire_all()
    assert db.get(DecisionRecord, second).status == "EXECUTING"
    token = set_request_identity(RequestIdentity(1, 99999, "owner", 1))
    try:
        assert recovery.decision_boundary() == 0
        assert recovery.reconcile_decision_receipts(through_id=second).items == ()
    finally:
        reset_request_identity(token)


async def test_actual_orchestrator_start_recovers_decision_even_with_dispatch_disabled(
    db, world, monkeypatch
):
    identifier = unfinished(world, monkeypatch)
    world.session.status, world.session.ended_at = "completed", utcnow()
    db.commit()
    owner = Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(3):
            while True:
                db.expire_all()
                if db.get(DecisionRecord, identifier).status == "APPLIED":
                    break
                await asyncio.sleep(0.02)
        assert owner._decision_recovery_through is None
    finally:
        await owner.stop()


def test_pending_confirmation_is_not_resolved_but_final_effect_is_aggregated(
    client, db, confirmation_world, monkeypatch
):
    w = confirmation_world
    work, _, result, confirmation_id = proposal(db, w, monkeypatch)
    record_id = result["decision_id"]
    before = _counts()
    pending = scan()
    assert pending.items[0].outcome == "awaiting_confirmation" and _counts() == before
    identity = confirmation_owner(client, db, w)
    work.status, work.ended_at = "completed", utcnow()
    db.commit()
    # Model the same after-effect/before-aggregation window for the existing
    # approval executor. Both original proposal and final receipt are real.
    with monkeypatch.context() as patch:
        patch.setattr(decisions, "refresh_from_receipts", lambda *args: None)
        action_confirmations.review(db, confirmation_id, approve=True, **identity)
        action_confirmations.execute(db, confirmation_id, **identity)
    db.expire_all()
    assert db.get(DecisionRecord, record_id).status == "EXECUTING"
    assert w.task.description.count("|confirmed effect|") == 1
    receipts = [row.id for row in decisions.tool_audits_for(db, record_id)]
    assert len(receipts) == 2
    assert scan().items[0].outcome == "repaired"
    db.expire_all()
    assert db.get(DecisionRecord, record_id).status == "APPLIED"
    assert decisions.action_summary(db, record_id)["total"] == 1
    assert [row.id for row in decisions.tool_audits_for(db, record_id)] == receipts


def test_original_receipts_do_not_require_current_action_authority(db, world, monkeypatch):
    identifier = unfinished(world, monkeypatch)
    record = db.get(DecisionRecord, identifier)
    original = (record.actor_person_id, record.context_json, record.context_hash, record.reason)
    db.execute(delete(PositionAuthorityGrant))
    db.commit()
    monkeypatch.setattr(executor, "execute_tool", lambda *a, **kw: pytest.fail("replayed action"))
    before = _counts()
    assert scan().items[0].outcome == "repaired"
    db.refresh(record)
    assert (
        record.actor_person_id,
        record.context_json,
        record.context_hash,
        record.reason,
    ) == original
    assert record.status == "APPLIED" and _counts() == before


def test_superseded_source_is_rechecked_under_writer_lock(db, world, monkeypatch):
    identifier = unfinished(world, monkeypatch)
    replacement = _send(world, _decision(world, key="replacement"))["decision_id"]
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 2:
            with SessionLocal() as other:
                decisions.supersede(
                    other, other.get(DecisionRecord, identifier), by_decision_id=replacement
                )
        return SessionLocal()

    result = recovery.reconcile_decision_receipts(through_id=identifier, session_factory=factory)
    assert result.items[0].outcome == "already_resolved"
    db.expire_all()
    record = db.get(DecisionRecord, identifier)
    assert record.status == "SUPERSEDED" and record.superseded_by_id == replacement


def test_no_operation_and_resolved_sources_require_no_writer(db, world, monkeypatch):
    finished = _send(world, _decision(world))["decision_id"]
    ctx = executor.context_for_work_session(db, world.session.id)
    legacy = decisions.open_decision(
        db,
        ctx,
        decision_type="decompose_project",
        reason="Original direct intent",
        scope=f"project:{world.project.id}",
    )
    legacy.status = "EXECUTING"
    db.commit()

    def assert_read_only(connection, cursor, statement, parameters, context, executemany):
        assert not statement.upper().startswith(("BEGIN IMMEDIATE", "UPDATE", "INSERT", "DELETE"))

    def readonly():
        source = SessionLocal()
        event.listen(source.connection(), "before_cursor_execute", assert_read_only)
        return source

    result = recovery.reconcile_decision_receipts(through_id=legacy.id, session_factory=readonly)
    assert [(item.decision_id, item.outcome) for item in result.items] == [
        (finished, "already_resolved"),
        (legacy.id, "no_runtime_operation"),
    ]


@pytest.mark.parametrize("bounds", [dict(limit=True), dict(limit=101), dict(after_id=2)])
def test_invalid_decision_pages_fail_before_read(bounds):
    with pytest.raises(ValueError, match="invalid recovery page"):
        recovery.reconcile_decision_receipts(
            through_id=1,
            session_factory=lambda: pytest.fail("unexpected read"),
            **bounds,
        )


async def test_orchestrator_retains_failed_decision_page(db, world, monkeypatch):
    identifier = unfinished(world, monkeypatch)
    world.session.status, world.session.ended_at = "completed", utcnow()
    db.commit()
    calls = []
    actual = recovery.reconcile_decision_receipts

    def fail_once(**kwargs):
        calls.append(kwargs["after_id"])
        if len(calls) == 1:
            raise IntegrityError("private", {}, Exception("storage unavailable"))
        return actual(**kwargs)

    monkeypatch.setattr(recovery, "reconcile_decision_receipts", fail_once)
    owner = Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(3):
            while True:
                db.expire_all()
                if db.get(DecisionRecord, identifier).status == "APPLIED":
                    break
                await asyncio.sleep(0.02)
        assert calls == [0, 0]
        await owner._handle({"type": "sweep"})
        assert calls == [0, 0]
    finally:
        await owner.stop()
