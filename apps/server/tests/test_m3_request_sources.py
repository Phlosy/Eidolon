"""Accepted structured requests remain attributable and usable across real exits."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_m3_runtime_conversation import ScriptedAdapter, decision, finish, read, run
from test_m3_runtime_conversation import world as _world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.project import Task, WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.models.runtime_operation import RuntimeOperation
from app.repositories import project as project_repo
from app.work import runtime_bridge as bridge
from app.work import telemetry
from app.work.runtime_conversation import ConversationLimits
from app.work.runtime_operations import RuntimeExecution

world = _world


async def test_controlled_read_decision_and_finish_keep_original_body_and_attempt(db, world):
    requests = [read(world), decision(world), finish()]
    result = await run(world, ScriptedAdapter(*requests))
    assert result["status"] == "finished"
    with SessionLocal() as observer:
        attempts = list(observer.scalars(select(RuntimeAttempt).order_by(RuntimeAttempt.id)))
        assert len(attempts) == 3
        for attempt, request, turn in zip(attempts, requests, result["turns"], strict=True):
            assert attempt.reply_operation_id == turn["result"]["operation_id"]
            operation = observer.get(RuntimeOperation, attempt.reply_operation_id)
            assert bridge.parse_reply(bridge.stored_request_body(operation)) == bridge.parse_reply(
                json.dumps(request)
            )
            assert operation.request_body == json.dumps(request)
            assert attempt.host_reason == "reply_received"
            assert "private-control-token" not in operation.request_body


async def test_identical_request_in_new_turn_keeps_one_operation_with_two_source_attempts(world):
    request = decision(world)
    result = await run(world, ScriptedAdapter(request, request, finish()))
    assert result["status"] == "finished"
    with SessionLocal() as db:
        attempts = list(db.scalars(select(RuntimeAttempt).order_by(RuntimeAttempt.id)))
        assert attempts[0].reply_operation_id == attempts[1].reply_operation_id
        assert attempts[0].reply_operation_id != attempts[2].reply_operation_id
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 1


def _child(program, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", program, *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )


_CRASH_REQUEST = """
import asyncio, json, os, sys
from app.core.database import SessionLocal
from app.models.project import WorkSession
from app.runtimes.base import RuntimeSession
from app.runtimes.container_common import resolve_container_instance
from app.work.runtime_conversation import ConversationLimits, run_conversation
from app.work.runtime_operations import RuntimeExecution
from test_m3_runtime_conversation import ScriptedAdapter, admitted
work_id, phase = int(sys.argv[1]), sys.argv[3]
with SessionLocal() as db:
    work = db.get(WorkSession, work_id)
    employee_id, task_id = work.employee_id, work.task_id
session = RuntimeSession(id="host-controlled-test", task_id=task_id,
    instance=resolve_container_instance(employee_id, "hermes", session_factory=SessionLocal))
original = RuntimeExecution.execute_action
def crash(self, index, *args, **kwargs):
    if phase == "intent":
        os._exit(23)
    result = original(self, index, *args, **kwargs)
    if index == 0:
        os._exit(23)
    return result
RuntimeExecution.execute_action = crash
asyncio.run(run_conversation(work_id, adapter=ScriptedAdapter(json.loads(sys.argv[2])),
    runtime_session=session, goal="Execute the supplied two-action decision",
    budget_check=admitted, limits=ConversationLimits(max_rounds=1,
    turn_timeout_seconds=120, total_timeout_seconds=120), session_factory=SessionLocal))
"""

_DATABASE_REPLAY = """
import json, os, sys
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.runtime_execution import RuntimeAttempt
from app.models.runtime_operation import RuntimeOperation
from app.work.runtime_bridge import handle_reply, stored_request_body
from app.work.runtime_operations import RuntimeExecution
work_id = int(sys.argv[1])
with SessionLocal() as db:
    attempt = db.scalar(select(RuntimeAttempt).where(RuntimeAttempt.work_session_id == work_id)
        .order_by(RuntimeAttempt.id.desc()).limit(1))
    attempt_id = attempt.id
    operation = db.get(RuntimeOperation, attempt.reply_operation_id)
    body = stored_request_body(operation)
if sys.argv[2] == "crash":
    original = RuntimeExecution.execute_action
    def crash(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 1:
            os._exit(24)
        return result
    RuntimeExecution.execute_action = crash
print(json.dumps(handle_reply(work_id, body, _runtime_attempt_id=attempt_id)))
"""


@pytest.mark.parametrize("phase", ["intent", "first_action"])
def test_fresh_process_replays_saved_source_without_caller_request(world, phase):
    request = decision(world)
    request["decision"]["actions"].append(
        {"tool": "create_task", "args": {"project_id": world.project.id, "title": "Second task"}}
    )
    crashed = _child(_CRASH_REQUEST, world.work.id, json.dumps(request), phase)
    assert crashed.returncode == 23, crashed.stderr
    with SessionLocal() as db:
        attempt = db.scalar(select(RuntimeAttempt))
        operation = db.scalar(select(RuntimeOperation))
        assert attempt.reply_operation_id == operation.id
        assert bridge.parse_reply(bridge.stored_request_body(operation)) == bridge.parse_reply(
            json.dumps(request)
        )
        assert db.get(DecisionRecord, operation.decision_id).status == "EXECUTING"
        first_audits = list(db.scalars(select(ToolAudit.id)))
        assert len(first_audits) == (1 if phase == "first_action" else 0)
        original_events = list(db.scalars(select(Event.id)))
    # This is the existing bridge, given only the stored source. Automatic owner
    # recovery and remote I/O are a separate component, not claimed by this test.
    recovered_crash = _child(_DATABASE_REPLAY, world.work.id, "crash")
    assert recovered_crash.returncode == 24, recovered_crash.stderr
    first = _child(_DATABASE_REPLAY, world.work.id, "finish")
    with SessionLocal() as db:
        saved_events = list(db.scalars(select(Event.id).order_by(Event.id)))
        saved_audits = list(db.scalars(select(ToolAudit.id).order_by(ToolAudit.id)))
    second = _child(_DATABASE_REPLAY, world.work.id, "finish")
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    result = json.loads(first.stdout)
    assert result == json.loads(second.stdout)
    assert result["decision_status"] == "APPLIED" and result["action_indices"] == [0, 1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 1
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
        audits = list(db.scalars(select(ToolAudit.id).order_by(ToolAudit.id)))
        assert audits == saved_audits
        assert list(db.scalars(select(Event.id).order_by(Event.id))) == saved_events
        assert len(audits) == 2 and audits[: len(first_audits)] == first_audits
        assert set(original_events) <= set(db.scalars(select(Event.id)))
        for title in ("Model chosen task", "Second task"):
            assert db.scalar(select(func.count()).select_from(Task).where(Task.title == title)) == 1


def _prepare(world, *, state="host_completed", key="original", kind="controlled"):
    with SessionLocal() as db:
        db.get(WorkSession, world.work.id).runtime_session_ref = world.runtime.id
        db.commit()
    recorder = telemetry.prepare_attempt(
        world.work.id,
        world.runtime,
        "Isolated original host input",
        request_key=key,
        kind=kind,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    if state == "prepared":
        return recorder
    recorder.accepted(f"run-{key}", world.runtime.id)
    if state == "accepted":
        return recorder
    recorder.terminal("completed", "host.reply")
    if state == "native_completed":
        return recorder
    recorder.finish_host("completed", "reply_received")
    return recorder


@pytest.mark.parametrize("state", ["prepared", "accepted", "native_completed", "stopped", "stale"])
def test_source_must_be_the_latest_completed_unstopped_host_reply(world, state):
    recorder = _prepare(world, state=state)
    if state == "stopped":
        recorder.request_stop("cancelled")
    if state == "stale":
        _prepare(world, state="prepared", key="later")
    with pytest.raises(bridge.BridgeError) as error:
        bridge.handle_reply(
            world.work.id, json.dumps(decision(world)), _runtime_attempt_id=recorder.attempt_id
        )
    assert error.value.reason == "reply_attempt_mismatch"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0


@pytest.mark.parametrize("identifier", [True, 0, -1, "1", 2**63, 999999])
def test_invalid_source_id_never_creates_intent(world, identifier):
    with pytest.raises(bridge.BridgeError) as error:
        bridge.handle_reply(world.work.id, json.dumps(finish()), _runtime_attempt_id=identifier)
    assert error.value.reason == "reply_attempt_mismatch"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0


@pytest.mark.parametrize("rejected", ["invalid", "action_limit"])
async def test_unaccepted_reply_never_becomes_a_recovery_source(world, rejected):
    result = await run(
        world,
        ScriptedAdapter("not JSON" if rejected == "invalid" else decision(world)),
        limits=ConversationLimits(max_rounds=1, max_actions=0),
    )
    assert result["reason"] == ("round_limit" if rejected == "invalid" else "action_limit")
    with SessionLocal() as db:
        attempt = db.scalar(select(RuntimeAttempt))
        assert attempt.reply_operation_id is None
        assert attempt.host_reason == "reply_received"
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0


def test_one_attempt_cannot_accept_a_second_operation(world):
    recorder = _prepare(world)
    result = bridge.handle_reply(
        world.work.id, json.dumps(read(world)), _runtime_attempt_id=recorder.attempt_id
    )
    with pytest.raises(bridge.BridgeError, match="reply_attempt_mismatch"):
        bridge.handle_reply(
            world.work.id, json.dumps(finish()), _runtime_attempt_id=recorder.attempt_id
        )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 1
        assert (
            db.get(RuntimeAttempt, recorder.attempt_id).reply_operation_id == result["operation_id"]
        )


def test_stop_between_actions_retains_only_the_original_committed_receipt(world, monkeypatch):
    recorder = _prepare(world)
    request = decision(world)
    request["decision"]["actions"].append(
        {"tool": "create_task", "args": {"project_id": world.project.id, "title": "Declined task"}}
    )
    original = RuntimeExecution.execute_action

    def stop_after_first(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 0:
            recorder.request_stop("cancelled")
        return result

    monkeypatch.setattr(RuntimeExecution, "execute_action", stop_after_first)
    result = bridge.handle_reply(
        world.work.id, json.dumps(request), _runtime_attempt_id=recorder.attempt_id
    )
    assert result["status"] == "interrupted" and result["reason"] == "reply_attempt_mismatch"
    assert result["action_indices"] == [0]
    with SessionLocal() as db:
        assert db.scalar(select(Task.id).where(Task.title == "Declined task")) is None
        assert bridge.stored_request_body(db.get(RuntimeOperation, result["operation_id"]))


def test_reply_binding_and_request_intent_rollback_together_on_real_sql_failure(world):
    recorder = _prepare(world)
    with SessionLocal() as db:
        engine = db.get_bind()

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_test_reply BEFORE UPDATE OF reply_operation_id "
            "ON runtime_attempts WHEN NEW.reply_operation_id IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT, 'injected reply binding failure'); END"
        )
    try:
        with pytest.raises(bridge.BridgeError, match="request interrupted by storage failure"):
            bridge.handle_reply(
                world.work.id, json.dumps(decision(world)), _runtime_attempt_id=recorder.attempt_id
            )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER reject_test_reply")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 0
        assert db.get(RuntimeAttempt, recorder.attempt_id).reply_operation_id is None
    result = bridge.handle_reply(
        world.work.id, json.dumps(decision(world)), _runtime_attempt_id=recorder.attempt_id
    )
    assert result["decision_status"] == "APPLIED"


def test_original_source_and_binding_cannot_be_edited_or_replaced(world):
    recorder = _prepare(world)
    result = bridge.handle_reply(
        world.work.id, json.dumps(read(world)), _runtime_attempt_id=recorder.attempt_id
    )
    with SessionLocal() as db:
        for sql in (
            "UPDATE runtime_operations SET request_body=NULL WHERE id=:op",
            "UPDATE runtime_operations SET request_body='{}' WHERE id=:op",
            "INSERT OR REPLACE INTO runtime_operations "
            "SELECT * FROM runtime_operations WHERE id=:op",
            "UPDATE runtime_attempts SET reply_operation_id=NULL WHERE id=:attempt",
            "UPDATE runtime_attempts SET reply_operation_id=999999 WHERE id=:attempt",
        ):
            with pytest.raises(IntegrityError, match="invalid runtime"):
                db.execute(
                    text(sql), {"op": result["operation_id"], "attempt": recorder.attempt_id}
                )
            db.rollback()
        operation = db.get(RuntimeOperation, result["operation_id"])
        assert bridge.stored_request_body(operation) == json.dumps(read(world))
        assert db.get(RuntimeAttempt, recorder.attempt_id).reply_operation_id == operation.id


@pytest.mark.parametrize("change", ["missing", "malformed", "digest", "kind", "key", "count"])
def test_saved_body_is_never_reconstructed_or_trusted_without_validation(world, change):
    result = bridge.handle_reply(world.work.id, json.dumps(read(world)))
    with SessionLocal() as db:
        operation = db.get(RuntimeOperation, result["operation_id"])
        source = SimpleNamespace(
            **{
                column.name: getattr(operation, column.name)
                for column in operation.__table__.columns
            }
        )
    if change == "missing":
        source.request_body = None
    elif change == "malformed":
        source.request_body = "{"
    else:
        field = {
            "digest": "request_digest",
            "kind": "kind",
            "key": "operation_key",
            "count": "requested_action_count",
        }[change]
        setattr(source, field, 2 if change == "count" else "different")
    with pytest.raises(bridge.BridgeError) as error:
        bridge.stored_request_body(source)
    assert error.value.reason == (
        "request_body_unknown" if change == "missing" else "request_body_mismatch"
    )


def test_maximum_utf8_input_and_equivalent_retransmission_preserve_first_body(world):
    request = finish("汉")
    size = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
    request["summary"] += "x" * (bridge.MAX_REPLY_BYTES - size)
    raw = json.dumps(request, ensure_ascii=False)
    assert len(raw.encode("utf-8")) == bridge.MAX_REPLY_BYTES
    result = bridge.handle_reply(world.work.id, raw)
    # Same normalized body, with smaller separators; first accepted text is immutable.
    assert (
        bridge.handle_reply(
            world.work.id, json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        )
        == result
    )
    with SessionLocal() as db:
        assert bridge.stored_request_body(db.get(RuntimeOperation, result["operation_id"])) == raw


def test_reply_from_another_work_session_cannot_supply_this_request(world):
    recorder = _prepare(world)
    with SessionLocal() as db:
        other = project_repo.create_work_session(
            db,
            task_id=world.task.id,
            employee_id=world.actor.id,
            runtime_type="hermes",
            runtime_instance_id=world.row.id,
            provider_id=world.provider.id,
            model="original-model",
            started_at=utcnow(),
        )
        db.commit()
        identifier = other.id
    with pytest.raises(bridge.BridgeError, match="reply_attempt_mismatch"):
        bridge.handle_reply(
            identifier, json.dumps(finish()), _runtime_attempt_id=recorder.attempt_id
        )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0


@pytest.mark.parametrize("body", ["{", "null", "[]", "{}", '"text"', "x" * 65537])
def test_direct_sql_cannot_insert_unbounded_or_unidentified_body(world, body):
    with SessionLocal() as db:
        with pytest.raises(IntegrityError, match="invalid runtime request source"):
            db.execute(
                text(
                    "INSERT INTO runtime_operations "
                    "(created_at,updated_at,work_session_id,operation_key,request_digest,kind,"
                    "requested_action_count,request_body) "
                    "VALUES ('2026-01-01','2026-01-01',:work,'read',:digest,'read',1,:body)"
                ),
                {"work": world.work.id, "digest": "a" * 64, "body": body},
            )
