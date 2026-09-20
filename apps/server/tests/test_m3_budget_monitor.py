"""A read-only spending permit can withdraw already-running original work."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from test_m3_controlled_stop import controlled as _controlled
from test_m3_management_runner import runner_for, source
from test_m3_management_runner import world as _world
from test_m3_retry_cancel import running_worker as _running_worker
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import worker_world as _worker_world

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import Task, WorkSession
from app.models.review import ReviewRequest
from app.models.runtime_execution import RuntimeAttempt
from app.work import budget_monitor, telemetry
from app.work.management_runner import ManagementRunner
from app.workflow.orchestrator import Orchestrator

world = _world
controlled = _controlled
bound = _bound
worker_world = _worker_world
running_worker = _running_worker


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
@pytest.mark.parametrize(
    "decision", [False, None, "error", True, "timeout", "storage_failure", "all_storage_failure"]
)
async def test_worker_budget_permit_controls_original_run(
    db, bound, running_worker, monkeypatch, decision, caplog
):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    if decision == "timeout":
        monkeypatch.setattr(budget_monitor, "READ_TIMEOUT_SECONDS", 0.02)
    storage_failure = decision in {"storage_failure", "all_storage_failure"}
    if storage_failure:

        def fail_stop(*args):
            raise SQLAlchemyError("private-storage-body")

        monkeypatch.setattr(budget_monitor, "record_stop_intent", fail_stop)
        if decision == "all_storage_failure":
            monkeypatch.setattr(telemetry, "record_stop_intent", fail_stop)
        else:

            class StopsAfterDurableIntent(list):
                def append(self, value):
                    with SessionLocal() as independent:
                        assert (
                            independent.scalar(select(RuntimeAttempt)).stop_reason
                            == "budget_denied"
                        )
                    super().append(value)

            env.stops = StopsAfterDurableIntent()
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    calls = []
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        row = db.scalar(select(RuntimeAttempt))

        async def monitor(work_id):
            calls.append(work_id)
            # Budget I/O must not hold a database transaction or writer lock.
            with SessionLocal() as independent:
                independent.connection().exec_driver_sql("BEGIN IMMEDIATE")
                independent.rollback()
            if decision == "error":
                raise OSError("private-budget-provider-body")
            if decision == "timeout":
                await asyncio.Event().wait()
            return False if storage_failure else decision

        env.runner._budget_monitor = monitor
        await env.runner._handle({"type": "sweep"})
        assert calls == [row.work_session_id]
        db.expire_all()
        if decision is True:
            assert row.stop_requested_at is None and not pending.done()
            assert env.stops == []
        else:
            reason = "budget_denied" if decision is False or storage_failure else "host_error"
            if not storage_failure:
                assert row.stop_reason == reason
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(pending), 2)
            db.expire_all()
            work = db.get(WorkSession, row.work_session_id)
            assert work.status == "failed" and work.error == reason
            assert bound.task.status == "blocked" and bound.task.rework_count == 0
            assert env.sessions[0].background.done()
            if decision == "all_storage_failure":
                assert row.stop_requested_at is None and row.stop_state == "not_recorded"
                assert env.stops == [] and env.sessions[0].cancel_confirmed is False
            else:
                assert row.stop_reason == reason and row.stop_state == "unconfirmed"
                assert len(env.stops) == 1
        assert "private-budget-provider-body" not in caplog.text
        assert "private-storage-body" not in caplog.text
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await env.runner.stop()


@pytest.mark.parametrize("factory", [Orchestrator, ManagementRunner])
def test_budget_monitor_constructor_rejects_noncallable(factory):
    with pytest.raises(ValueError, match="budget_monitor"):
        factory(budget_monitor=True)


async def test_completed_controlled_owner_ignores_late_budget_denial(
    db, world, controlled, monkeypatch
):
    env = controlled
    runner = runner_for(monkeypatch, env.adapter)
    runner._running = True
    pending = asyncio.create_task(runner._admit_and_run(source(world)))
    entered, release = asyncio.Event(), asyncio.Event()
    observation = None
    try:
        await asyncio.wait_for(env.entered.wait(), 2)
        row = db.scalar(select(RuntimeAttempt))

        async def monitor(_):
            entered.set()
            await release.wait()
            return False

        runner._budget_monitor = monitor
        observation = asyncio.create_task(runner._interrupt_invalid_owned())
        await asyncio.wait_for(entered.wait(), 1)
        env.release.set()
        await asyncio.wait_for(pending, 2)
        release.set()
        await observation
        db.expire_all()
        assert db.get(WorkSession, row.work_session_id).status == "completed"
        assert row.stop_requested_at is None and env.stops == []
    finally:
        release.set()
        if observation is not None:
            await asyncio.gather(observation, return_exceptions=True)
        await runner.stop()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_shutdown_joins_budget_check_before_releasing_worker(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    entered, drained = asyncio.Event(), asyncio.Event()

    async def monitor(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    env.runner._budget_monitor = monitor
    env.runner.start()
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        await asyncio.wait_for(entered.wait(), 2)
        await env.runner.stop()
        assert drained.is_set() and pending.done()
        assert env.sessions[0].background.done() and len(env.stops) == 1
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)).stop_reason == "shutdown"
    finally:
        await env.runner.stop()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("remote_state", ["aborted", "running"])
async def test_controlled_budget_monitor_stops_original_run_without_domain_effects(
    db, world, controlled, monkeypatch, review, remote_state
):
    env = controlled
    event_id, task_id, review_id = (
        source(world, review=True) if review else (source(world), None, None)
    )
    env.expected_reason, env.remote_state = "budget_denied", remote_state
    runner = runner_for(monkeypatch, env.adapter)
    runner._running = True
    pending = asyncio.create_task(runner._admit_and_run(event_id))
    calls = []
    try:
        await asyncio.wait_for(env.entered.wait(), 2)
        row = db.scalar(select(RuntimeAttempt))

        async def monitor(work_id):
            calls.append(work_id)
            return False

        runner._budget_monitor = monitor
        await runner._interrupt_invalid_owned()
        assert calls == [row.work_session_id]
        db.expire_all()
        assert row.stop_reason == "budget_denied"
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(pending), 2)
        db.expire_all()
        work = db.get(WorkSession, row.work_session_id)
        assert work.status == "cancelled" and work.error.startswith("budget_denied")
        assert len(env.stops) == 1 and len(env.prompts) == 1
        assert row.stop_state == (
            "terminal_observed" if remote_state == "aborted" else "unconfirmed"
        )
        if review:
            assert db.get(Task, task_id).status == "in_review"
            assert db.get(ReviewRequest, review_id).verdict is None
    finally:
        env.expected_reason = "shutdown"
        await runner.stop()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_late_budget_denial_cannot_cancel_replacement_owner(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    entered, release = asyncio.Event(), asyncio.Event()
    replacement = observation = None
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        row = db.scalar(select(RuntimeAttempt))

        async def monitor(_):
            entered.set()
            await release.wait()
            return False

        env.runner._budget_monitor = monitor
        observation = asyncio.create_task(env.runner._handle({"type": "sweep"}))
        await asyncio.wait_for(entered.wait(), 0.2)
        replacement = asyncio.create_task(asyncio.Event().wait())
        env.runner._workers[row.work_session_id] = replacement
        release.set()
        await observation
        db.expire_all()
        assert row.stop_requested_at is None
        assert not pending.done() and not replacement.done() and env.stops == []
    finally:
        release.set()
        for task in (observation, replacement, pending):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(t for t in (observation, replacement, pending) if t is not None),
            return_exceptions=True,
        )
        await env.runner.stop()


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_preexisting_stop_reason_wins_over_late_budget_denial(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        row = db.scalar(select(RuntimeAttempt))

        async def monitor(_):
            # The budget response can arrive after another stop was committed.
            telemetry.AttemptRecorder(row.id, row.request_key, row.idempotency_key).request_stop(
                "cancelled"
            )
            return False

        env.runner._budget_monitor = monitor
        await env.runner._handle({"type": "sweep"})
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(pending), 2)
        db.expire_all()
        assert row.stop_reason == "cancelled"
        assert db.get(WorkSession, row.work_session_id).error == "cancelled"
        assert len(env.stops) == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await env.runner.stop()
