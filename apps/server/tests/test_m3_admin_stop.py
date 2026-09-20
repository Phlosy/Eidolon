"""Committed task controls stop their original live worker through real adapters."""

import asyncio

import pytest
from sqlalchemy import select
from test_m3_retry_cancel import bound as _bound
from test_m3_retry_cancel import running_worker as _running_worker
from test_m3_retry_cancel import worker_world as _worker_world

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.organization import Employee
from app.models.project import Task, WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.services import tasks
from app.work import tool_executor, tool_writes
from app.workflow import orchestrator as orchestration

bound = _bound
worker_world = _worker_world
running_worker = _running_worker
pytestmark = pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)


@pytest.fixture()
async def live_worker(running_worker, bound, monkeypatch):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    monkeypatch.setattr(orchestration, "orchestrator", env.runner)
    env.runner.start()
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        yield env, pending
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await env.runner.stop()


def change_task(bound, status, *, via="service", commit=True):
    with SessionLocal() as fresh:
        target = fresh.get(Task, bound.task.id)
        if via == "tool":
            actor = fresh.get(Employee, bound.actor.id)
            handler = (
                tool_writes._cancel_task
                if status == "cancelled"
                else tool_writes._mark_task_blocked
            )
            handler(
                fresh,
                tool_executor.context_for_employee(fresh, actor, origin="test"),
                {"task_id": target.id, "reason": "Manager stopped original execution"},
            )
        else:
            tasks.transition_task(fresh, target, status)
        if commit:
            fresh.commit()


@pytest.mark.parametrize("via", ["service", "tool", "patch"])
@pytest.mark.parametrize("status", ["cancelled", "blocked"])
async def test_committed_task_stop_reaches_original_live_worker(
    client, db, bound, live_worker, via, status
):
    env, pending = live_worker
    if via == "patch":
        response = client.patch(f"/api/v1/tasks/{bound.task.id}", json={"status": status})
        assert response.status_code == 200, response.text
    else:
        change_task(bound, status, via=via)
    # This test task has not yielded back to the worker since the control commit.
    # The intent must already be visible before any cancellation transport runs.
    with SessionLocal() as independent:
        assert independent.scalar(select(RuntimeAttempt)).stop_reason == status
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(pending), 1)
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    assert attempt.stop_reason == status
    assert attempt.stop_state == "unconfirmed"
    assert attempt.host_outcome == "cancelled"
    assert bound.task.status == status and bound.task.rework_count == 0
    assert db.get(WorkSession, attempt.work_session_id).status == "failed"
    assert len(env.stops) == 1 and env.sessions[0].background.done()
    assert bound.actor.current_task_id is None


async def test_rolled_back_control_has_no_stop_effect(db, bound, live_worker):
    env, pending = live_worker
    change_task(bound, "blocked", commit=False)
    await asyncio.sleep(0)
    await env.runner._handle({"type": "sweep"})
    db.expire_all()
    assert bound.task.status == "in_progress" and not pending.done()
    assert db.scalar(select(RuntimeAttempt)).stop_requested_at is None
    assert env.stops == []


async def test_lost_notice_is_recovered_while_dispatch_is_disabled(
    db, bound, live_worker, monkeypatch
):
    env, pending = live_worker
    monkeypatch.setattr(env.runner, "notify", lambda signal: None)
    change_task(bound, "blocked")
    await env.runner._handle({"type": "sweep"})
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(pending), 1)
    db.expire_all()
    assert db.scalar(select(RuntimeAttempt)).stop_reason == "blocked"
    assert len(env.stops) == 1 and bound.task.status == "blocked"


async def test_assignment_alone_and_stale_work_notice_do_not_cancel_current_worker(
    db, bound, live_worker
):
    env, pending = live_worker
    successor = db.scalar(select(Employee).where(Employee.slug == "dana"))
    bound.task.assignee_id = successor.id
    db.commit()
    await env.runner._handle({"type": "stop_work", "work_session_id": bound.work.id})
    await env.runner._handle({"type": "sweep"})
    assert not pending.done() and env.stops == []
    db.expire_all()
    assert db.scalar(select(RuntimeAttempt)).stop_requested_at is None


async def test_shutdown_drains_directly_owned_worker(db, live_worker):
    env, pending = live_worker
    await env.runner.stop()
    assert pending.done(), "shutdown abandoned a live owned WorkSession"
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    assert attempt.stop_reason == "shutdown" and attempt.host_outcome == "cancelled"
    assert env.sessions[0].background.done() and len(env.stops) == 1


async def test_stop_during_instance_start_prevents_first_remote_submission(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    monkeypatch.setattr(orchestration, "orchestrator", env.runner)
    entered = asyncio.Event()

    async def starting(employee):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", starting)
    env.runner.start()
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        change_task(bound, "blocked")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(pending), 1)
        assert env.sessions == [] and env.stops == []
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)) is None
        assert bound.actor.current_task_id is None and bound.task.status == "blocked"
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await env.runner.stop()


async def test_task_stop_before_attempt_is_checked_even_without_notification(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    monkeypatch.setattr(orchestration, "orchestrator", env.runner)
    entered, release = asyncio.Event(), asyncio.Event()
    original = orchestration.gateway.get_or_create_instance

    async def starting(employee):
        entered.set()
        await release.wait()
        return await original(employee)

    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", starting)
    # No dispatcher loop or notification consumer; admission must read Task state.
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        change_task(bound, "blocked")
        release.set()
        await asyncio.wait_for(asyncio.shield(pending), 1)
        assert env.sessions == [] and env.stops == []
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)) is None
        assert bound.actor.current_task_id is None and bound.task.status == "blocked"
    finally:
        release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_old_executor_done_callback_preserves_new_owner(bound, running_worker, monkeypatch):
    env = running_worker
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    monkeypatch.setattr(
        orchestration.dispatch_runtime,
        "evaluate_dispatch",
        lambda db, task: orchestration.dispatch_runtime.DispatchEvaluation(
            task.id, True, assignee_id=task.assignee_id
        ),
    )
    await env.runner._dispatch_pending()
    original = env.runner._running[bound.actor.id]
    replacement = None
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        replacement = asyncio.create_task(asyncio.Event().wait())
        env.runner._running[bound.actor.id] = replacement
        original.cancel()
        await asyncio.gather(original, return_exceptions=True)
        await asyncio.sleep(0)
        assert env.runner._running.get(bound.actor.id) is replacement
    finally:
        original.cancel()
        if replacement is not None:
            replacement.cancel()
        await asyncio.gather(
            original, *([replacement] if replacement else []), return_exceptions=True
        )
        await env.runner.stop()
