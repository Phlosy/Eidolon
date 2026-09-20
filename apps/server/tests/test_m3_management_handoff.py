"""A real tool decision hands a taskless project to a newly awakened manager."""

import json

import pytest
from sqlalchemy import func, select
from test_m3_management_runner import ScriptedAdapter, ended, eventually, finish, runner_for, source
from test_m3_management_runner import world as world

from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.project import Task, WorkSession
from app.work import management_dispatch, tool_writes
from app.work.runtime_bridge import BridgeError, handle_reply


def delegate(world, employee_id):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": "delegate-once",
        "decision": {
            "decision_type": "delegate_management",
            "reason": "Hand the project to the designated successor",
            "scope": f"project:{world.project.id}",
            "actions": [
                {
                    "tool": "delegate_project",
                    "args": {"project_id": world.project.id, "to_employee_id": employee_id},
                }
            ],
        },
    }


async def test_delegated_manager_gets_one_new_source_without_reassigning_original_history(
    db, world, monkeypatch
):
    initial = source(world)
    envelope = delegate(world, world.reviewer.id)
    adapter = ScriptedAdapter(envelope, finish())
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    try:
        next_source = await eventually(
            lambda observer: observer.scalar(
                select(Event.id).where(
                    Event.project_id == world.project.id,
                    Event.type == "project.replan_required",
                    Event.id != initial,
                )
            )
        )
        successor = await ended(next_source)
        original = await ended(initial)
        assert successor.status == "completed"
        assert successor.employee_id == world.reviewer.id
        assert original.employee_id == world.manager.id
        assert original.id != successor.id
        # The old manager loses live authority after its applied delegation.
        with pytest.raises(BridgeError):
            handle_reply(original.id, json.dumps(envelope))
    finally:
        await runner.stop()
    db.expire_all()
    assert world.project.management_employee_id == world.reviewer.id
    assert world.project.status == "waiting_for_management"
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 2
    assert db.scalar(select(func.count()).select_from(ManagementDispatch)) == 2
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1
    records = list(db.scalars(select(ManagementDispatch).order_by(ManagementDispatch.id)))
    assert [(r.source_event_id, r.recipient_employee_id, r.work_session_id) for r in records] == [
        (initial, world.manager.id, original.id),
        (next_source, world.reviewer.id, successor.id),
    ]
    assert (
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.project_id == world.project.id, Event.type == "project.replan_required")
        )
        == 2
    )
    assert len(adapter.contexts) == 2 and len(adapter.prompts) == 2


async def test_same_manager_and_replayed_envelope_do_not_create_another_wakeup(
    db, world, monkeypatch
):
    initial = source(world)
    envelope = delegate(world, world.manager.id)
    adapter = ScriptedAdapter(envelope, envelope, finish())
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    try:
        work = await ended(initial)
        assert work.status == "completed", work.error
    finally:
        await runner.stop()
    db.expire_all()
    assert world.project.status == "waiting_for_management"
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
    assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.project_id == world.project.id, Event.type == "project.replan_required")
        )
        == 1
    )
    assert len(adapter.contexts) == 1


def test_failed_handoff_source_rolls_back_pointer_and_delegation_event(db, world, monkeypatch):
    initial = source(world)
    claim = management_dispatch.claim_source(initial, enabled=True, budget_available=True)
    assert claim.created
    original = tool_writes.bus.publish

    def fail_wakeup(kind, *args, **kwargs):
        if kind == "project.replan_required":
            raise RuntimeError("injected source persistence failure")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(tool_writes.bus, "publish", fail_wakeup)
    result = handle_reply(
        claim.dispatch.work_session_id, json.dumps(delegate(world, world.reviewer.id))
    )
    assert result["decision_status"] == "FAILED"
    assert not result["results"][0]["ok"]
    assert result["results"][0]["reason"] == "handler_failed"
    db.expire_all()
    assert world.project.management_employee_id == world.manager.id
    assert list(db.scalars(select(Event.id).where(Event.project_id == world.project.id))) == [
        initial
    ]
    assert db.scalar(select(func.count()).select_from(ManagementDispatch)) == 1
    assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1
