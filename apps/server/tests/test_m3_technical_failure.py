"""Technical exit preserves manageable tasks and authentic manager decisions."""

import asyncio
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from test_m3_retry_cancel import bound as _bound
from test_m3_retry_cancel import running_worker as _running_worker
from test_m3_retry_cancel import worker_world as _worker_world

from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.economy import ComputeUsage
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.project import Task, WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import project as project_repo
from app.work import management_dispatch
from app.work.runtime_bridge import handle_reply
from app.workflow.orchestrator import Orchestrator

bound = _bound
worker_world = _worker_world
running_worker = _running_worker


def manager_work(db, world):
    work = project_repo.create_work_session(
        db,
        task_id=None,
        project_id=world.project.id,
        employee_id=world.actor.id,
        purpose="management",
        runtime_type=world.row.runtime_type,
        runtime_instance_id=world.row.id,
        provider_id=world.provider.id,
        model="original-model",
        started_at=utcnow(),
    )
    db.commit()
    return work


def unblock(work, task_id):
    request = {
        "version": 1,
        "kind": "decision",
        "operation_key": "manager-resume",
        "decision": {
            "decision_type": "reassign_task",
            "reason": "The original run ended; resume this task after inspecting its facts",
            "scope": f"task:{task_id}",
            "actions": [{"tool": "update_task", "args": {"task_id": task_id, "status": "todo"}}],
        },
    }
    return handle_reply(work.id, json.dumps(request)), request


def blocked_events(db, task_id):
    return list(
        db.scalars(select(Event).where(Event.task_id == task_id, Event.type == "task.blocked"))
    )


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_only_a_real_manager_action_resumes_a_technically_failed_task(
    db, bound, running_worker, monkeypatch
):
    env = running_worker
    env.completed = True  # Remote completion is known; the required artifact is missing.
    await env.runner._run_task(bound.task.id)
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    work = db.get(WorkSession, attempt.work_session_id)
    assert attempt.status == "completed" and attempt.host_outcome == "failed"
    assert work.status == "failed" and bound.task.status == "blocked"
    original_end = work.ended_at
    assert work.technical_retry_count == bound.task.rework_count == 0
    assert bound.task.actual_end_at is None
    events = blocked_events(db, bound.task.id)
    assert len(events) == 1 and events[0].payload["work_session_id"] == work.id
    assert events[0].payload["technical_retry_count"] == 0
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
    assert not list(
        db.scalars(
            select(Event).where(
                Event.project_id == bound.project.id,
                Event.type.in_(["task.failed", "project.replan_required"]),
            )
        )
    )
    await env.runner._run_task(bound.task.id)
    assert len(env.sessions) == 1, "blocked task automatically restarted"

    manager = manager_work(db, bound)
    receipt, request = unblock(manager, bound.task.id)
    assert receipt["decision_status"] == "APPLIED", receipt
    assert handle_reply(manager.id, json.dumps(request)) == receipt
    db.expire_all()
    assert bound.task.status == "todo" and bound.task.rework_count == 0
    decision = db.get(DecisionRecord, receipt["decision_id"])
    assert decision.actor_employee_id == bound.actor.id
    audit = db.get(ToolAudit, receipt["results"][0]["audit_id"])
    assert audit.work_session_id == manager.id and audit.outcome == "applied"
    manager.status, manager.ended_at = "completed", utcnow()
    db.commit()

    entered = asyncio.Event()

    async def starting(employee):
        entered.set()
        await asyncio.Event().wait()

    from app.workflow import orchestrator

    monkeypatch.setattr(orchestrator.gateway, "get_or_create_instance", starting)
    pending = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        db.expire_all()
        following = project_repo.get_running_session_for_task(db, bound.task.id)
        assert following is not None and following.id != work.id
        assert work.status == "failed" and work.ended_at == original_end
        assert bound.task.status == "in_progress" and bound.task.rework_count == 0
        assert len(list(db.scalars(select(RuntimeAttempt)))) == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


def test_blocked_fact_survives_lost_broadcast_and_repeated_finalization(db, bound, monkeypatch):
    monkeypatch.setattr(bus, "_broadcast", lambda *args, **kwargs: False)
    runner = Orchestrator()
    runner._finalize(bound.task.id, False, [], "runtime_error", 1, work_session_id=bound.work.id)
    with SessionLocal() as independent:
        task = independent.get(Task, bound.task.id)
        work = independent.get(WorkSession, bound.work.id)
        assert task.status == "blocked" and work.status == "failed"
        saved = blocked_events(independent, task.id)
        assert len(saved) == 1
        event_id = saved[0].id
        assert saved[0].payload["work_session_id"] == work.id
        cost_ids = list(
            independent.scalars(
                select(ComputeUsage.id).where(ComputeUsage.work_session_id == work.id)
            )
        )
        assert len(cost_ids) == 1
    runner._finalize(bound.task.id, False, [], "late_error", 50, work_session_id=bound.work.id)
    management_dispatch.discover_sources()
    db.expire_all()
    assert [event.id for event in blocked_events(db, bound.task.id)] == [event_id]
    assert (
        list(
            db.scalars(select(ComputeUsage.id).where(ComputeUsage.work_session_id == bound.work.id))
        )
        == cost_ids
    )
    source = db.scalar(
        select(ManagementDispatch).where(ManagementDispatch.source_event_id == event_id)
    )
    assert source is not None and source.task_id == bound.task.id
    assert source.purpose == "management" and source.project_id == bound.project.id
    assert bound.work.error == "runtime_error" and bound.work.cost["duration_sec"] == 1


def test_blocked_notification_and_session_failure_commit_atomically(db, bound, monkeypatch):
    original = bus.publish

    def publish(kind, data, **kwargs):
        if kind == "task.blocked":
            raise SQLAlchemyError("injected blocked-event storage failure")
        return original(kind, data, **kwargs)

    monkeypatch.setattr(bus, "publish", publish)
    with pytest.raises(SQLAlchemyError, match="injected blocked-event"):
        Orchestrator()._finalize(
            bound.task.id, False, [], "runtime_error", 1, work_session_id=bound.work.id
        )
    db.expire_all()
    assert bound.work.status == "running" and bound.task.status == "in_progress"
    assert bound.work.ended_at is None and blocked_events(db, bound.task.id) == []
    assert (
        db.scalar(select(ComputeUsage).where(ComputeUsage.work_session_id == bound.work.id)) is None
    )


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "done"])
def test_existing_business_terminal_cannot_be_reopened_by_failure_or_manager(db, bound, terminal):
    # An earlier authoritative business outcome is part of the input history.
    bound.task.status, bound.task.actual_end_at = terminal, utcnow()
    db.commit()
    db.refresh(bound.task)
    original_end = bound.task.actual_end_at
    Orchestrator()._finalize(
        bound.task.id, False, [], "late_error", 1, work_session_id=bound.work.id
    )
    manager = manager_work(db, bound)
    receipt, _ = unblock(manager, bound.task.id)
    assert receipt["decision_status"] != "APPLIED"
    assert receipt["results"][0]["ok"] is False
    db.expire_all()
    assert bound.task.status == terminal and bound.task.actual_end_at == original_end
    assert bound.work.status == "failed" and blocked_events(db, bound.task.id) == []
