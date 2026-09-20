"""U13 durable execution facts; transport doubles never stand in for domain writes."""

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_m3_management_runtime import world as _management_world
from test_m3_runtime_conversation import world as _worker_world
from test_m3_runtime_turns import hermes_transport, session

from app.core.database import SessionLocal
from app.models.base import as_utc, utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeEvent, RuntimeEventKind, RuntimeProtocolError
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.work import telemetry

management_world = _management_world
worker_world = _worker_world


@pytest.mark.parametrize("terminal", [{}, {"status": "unrecognized"}, {"status": "timeout"}])
async def test_openclaw_worker_requires_explicit_terminal_success(monkeypatch, terminal):
    class Gateway:
        events = asyncio.Queue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            return {"runId": "saved-run"} if method == "chat.send" else terminal

    adapter, current = OpenClawAdapter(), session()
    monkeypatch.setattr(adapter, "_connect", lambda instance: Gateway())
    await adapter._pump(current, "never persisted prompt", 2)
    events = list(current.queue._queue)
    assert not any(event.kind == RuntimeEventKind.completed for event in events)
    assert events[-1].kind == RuntimeEventKind.error


async def test_hermes_worker_eof_is_explicit_failure(monkeypatch):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "saved-run"})
        return httpx.Response(200, text='data: {"type":"assistant.delta","delta":"partial"}\n\n')

    hermes_transport(monkeypatch, respond)
    current = session()
    await HermesAdapter()._pump(current, "never persisted prompt", 1)
    events = list(current.queue._queue)
    assert events[-1].kind == RuntimeEventKind.error
    assert not any(event.kind == RuntimeEventKind.completed for event in events)


@pytest.mark.parametrize("purpose", ["management", "review"])
async def test_management_turn_intent_and_acceptance_are_committed_before_wait(
    db, monkeypatch, management_world, purpose
):
    from test_m3_management_runtime import session_for

    from app.core.database import SessionLocal
    from app.models.project import WorkSession
    from app.models.runtime_execution import RuntimeAttempt
    from app.runtimes.base import RuntimeSession
    from app.runtimes.container_common import resolve_container_instance
    from app.work.runtime_conversation import run_conversation

    work = session_for(db, management_world, purpose, bound=True)
    current = RuntimeSession(
        id="durable-management",
        instance=resolve_container_instance(work.employee_id, "hermes"),
        task_id=None,
    )
    submitted = []

    def respond(request):
        with SessionLocal() as independent:
            attempts = list(independent.scalars(select(RuntimeAttempt).order_by(RuntimeAttempt.id)))
            row = attempts[-1]
            if request.method == "POST":
                assert row.status == "prepared" and row.remote_run_id is None
                assert 0 < (as_utc(row.deadline_at) - utcnow()).total_seconds() <= 60
                assert row.idempotency_key == request.headers["Idempotency-Key"]
                submitted.append(row.id)
                return httpx.Response(202, json={"run_id": f"saved-run-{len(submitted)}"})
            assert row.status == "accepted" and row.accepted_at is not None
            assert row.remote_run_id == f"saved-run-{len(submitted)}"
            reply = (
                {
                    "version": 1,
                    "kind": "read",
                    "operation_key": "read",
                    "tool": "inspect_project",
                    "args": {"project_id": management_world.project.id},
                }
                if len(submitted) == 1
                else {"version": 1, "kind": "finish", "operation_key": "finish", "summary": "done"}
            )
            return httpx.Response(
                200,
                json={
                    "run_id": row.remote_run_id,
                    "status": "completed",
                    "output": json.dumps(reply),
                },
            )

    hermes_transport(monkeypatch, respond)

    async def admitted(*args):
        return True

    result = await run_conversation(
        work.id,
        adapter=HermesAdapter(),
        runtime_session=current,
        goal="inspect",
        budget_check=admitted,
    )
    assert result["status"] == "finished"
    db.expire_all()
    attempts = list(
        db.scalars(select(RuntimeAttempt).where(RuntimeAttempt.work_session_id == work.id))
    )
    assert len(attempts) == 2
    assert {row.attempt_number for row in attempts} == {1}
    assert {row.status for row in attempts} == {"completed"}
    assert len({row.request_key for row in attempts}) == 2
    assert db.get(WorkSession, work.id).technical_retry_count == 0
    assert db.get(WorkSession, work.id).runtime_session_ref == current.id
    assert management_world.task.status == "in_review"


@pytest.fixture()
def bound(db, worker_world):
    worker_world.work.runtime_session_ref = worker_world.runtime.id
    db.commit()
    return worker_world


def prepare(world, key="worker", **kwargs):
    return telemetry.prepare_attempt(
        world.work.id, world.runtime, "private prompt", request_key=key, kind="controlled", **kwargs
    )


def test_request_reuse_and_explicit_retry_keep_distinct_counters(db, bound):
    first = prepare(bound, "round-1")
    assert prepare(bound, "round-1").attempt_id == first.attempt_id
    with pytest.raises(RuntimeProtocolError, match="request_conflict"):
        telemetry.prepare_attempt(
            bound.work.id, bound.runtime, "changed prompt", request_key="round-1", kind="controlled"
        )
    with pytest.raises(RuntimeProtocolError, match="request_conflict"):
        prepare(bound, "round-1", request_context={"changed": True})
    first.accepted("first-run", bound.runtime.id)
    first.finish_host("failed", "runtime_error")
    with pytest.raises(RuntimeProtocolError, match="retry_not_safe"):
        prepare(bound, "round-1", retry_of=first.attempt_id)
    first.terminal("failed", "hermes.status")
    retried = prepare(bound, "round-1", retry_of=first.attempt_id)
    second = prepare(bound, "round-2")
    db.expire_all()
    rows = list(db.scalars(select(RuntimeAttempt).order_by(RuntimeAttempt.id)))
    assert [row.attempt_number for row in rows] == [1, 2, 1]
    assert len({row.idempotency_key for row in rows}) == 3
    assert db.get(WorkSession, bound.work.id).technical_retry_count == 1
    assert retried.attempt_id != second.attempt_id
    assert bound.task.rework_count == 0


async def test_unknown_attempt_reconciles_after_fresh_process_query_without_resending(db, bound):
    recorder = prepare(bound)
    recorder.accepted("durable-original-run", bound.runtime.id)
    recorder.finish_host("failed", "stream_ended")
    db.expire_all()
    row = db.get(RuntimeAttempt, recorder.attempt_id)
    assert row.status == "unknown" and row.ended_at is None and row.terminal_source is None
    script = """
import asyncio, json, sys
import httpx
from app.work.telemetry import query_attempt, reconstruct_attempt
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.hermes import adapter as module
attempt_id = int(sys.argv[1])
runtime_type, instance, reference = reconstruct_attempt(attempt_id)
client = httpx.AsyncClient
requests = []
def respond(request):
    assert request.method == "GET"
    assert request.url.path == "/v1/runs/durable-original-run"
    assert request.headers["Authorization"] == "Bearer private-control-token"
    requests.append(request.method)
    return httpx.Response(200, json={"run_id": "durable-original-run", "status": "completed"})
module.httpx.AsyncClient = lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
result = asyncio.run(query_attempt(attempt_id, adapter=HermesAdapter()))
print(json.dumps({"local": reference.local_session_ref, "remote": reference.remote_run_id,
                  "status": result.status, "requests": requests}))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(recorder.attempt_id)],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(child.stdout) == {
        "local": bound.runtime.id,
        "remote": "durable-original-run",
        "status": "completed",
        "requests": ["GET"],
    }
    db.expire_all()
    assert row.status == "unknown"  # Query is read-only; reconciliation is explicit.
    recorder.terminal("completed", "hermes.status")
    db.expire_all()
    assert row.status == "completed" and row.ended_at is not None
    assert row.host_outcome == "failed" and row.host_reason == "stream_ended"
    assert bound.task.status == "in_progress"
    with pytest.raises(RuntimeProtocolError, match="terminal_conflict"):
        recorder.terminal("failed", "hermes.status")


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_key", "replacement"),
        ("internal_host", "replacement.invalid"),
        ("runtime_version", "replacement-version"),
        ("person_id", 9999),
        ("employee_id", 9999),
    ],
)
async def test_reference_query_refuses_replaced_runtime_before_io(db, bound, field, value):
    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    setattr(bound.row, field, value)
    db.commit()

    class MustNotQuery:
        type = "hermes"

        async def query_run(self, *args):
            pytest.fail("replacement runtime must never receive the old run query")

    with pytest.raises(RuntimeProtocolError, match="runtime_changed"):
        await telemetry.query_attempt(recorder.attempt_id, adapter=MustNotQuery())


def test_duplicate_out_of_order_late_and_hostile_telemetry_never_changes_business(db, bound):
    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    states = (
        bound.task.status,
        bound.work.status,
        db.scalar(select(func.count(DecisionRecord.id))),
        db.scalar(select(func.count(ToolAudit.id))),
    )
    event = RuntimeEvent(
        RuntimeEventKind.completed,
        {
            "event_id": "same-provider-event",
            "summary": "private-control-token",
            "usage": {"unrecognized": 123},
        },
    )
    recorder.observe(event)
    recorder.observe(event)
    recorder.observe(
        RuntimeEvent("task.completed", {"status": "done", "api_key": "private-control-token"})
    )
    recorder.observe(
        RuntimeEvent(
            RuntimeEventKind.tool_call,
            {
                "tool": "private-control-token",
                "args": {"secret": "private-control-token"},
                "event": "tool.failed",
            },
            ts=utcnow() - timedelta(days=1),
        )
    )
    recorder.observe(RuntimeEvent(RuntimeEventKind.thinking, {"content": "hidden reasoning"}))
    recorder.observe(RuntimeEvent(RuntimeEventKind.message, {"content": "token fragment"}))
    recorder.observe(
        RuntimeEvent(
            RuntimeEventKind.message,
            {
                "complete": True,
                "content": "private text",
                "usage": {"input_tokens": True, "output_tokens": -1},
            },
        )
    )
    recorder.observe({"kind": "session.completed", "token": "private-control-token"})
    recorder.observe(RuntimeEvent([], {}, ts="invalid"))
    recorder.observe(RuntimeEvent(RuntimeEventKind.tool_call, {"event": []}, ts="invalid"))
    db.expire_all()
    row = db.get(RuntimeAttempt, recorder.attempt_id)
    assert row.status == "accepted" and row.ended_at is None  # telemetry is not a terminal receipt
    recorder.terminal("completed", "hermes.status")
    db.refresh(row)
    progress = row.last_progress_at
    recorder.observe(RuntimeEvent(RuntimeEventKind.error, {"message": "late failure"}))
    db.expire_all()
    assert row.status == "completed" and row.last_progress_at == progress
    assert states == (
        bound.task.status,
        bound.work.status,
        db.scalar(select(func.count(DecisionRecord.id))),
        db.scalar(select(func.count(ToolAudit.id))),
    )
    records = telemetry.list_telemetry(db, bound.work.id)
    serialized = json.dumps([record.details_json for record in records])
    assert not any(
        secret in serialized
        for secret in (
            "private-control-token",
            "hidden reasoning",
            "token fragment",
            "private text",
        )
    )
    assert not any("usage" in record.details_json for record in records)
    assert (
        sum(
            record.event_key == telemetry._digest("external:same-provider-event")
            for record in records
        )
        == 1
    )
    assert any(record.kind == "tool.failed" for record in records)


def test_telemetry_is_paginated_expires_and_prunes_without_erasing_attempt(db, bound):
    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    for index in range(4):
        recorder.observe(RuntimeEvent(RuntimeEventKind.artifact, {"event_id": str(index)}))
    first = telemetry.list_telemetry(db, bound.work.id, limit=2)
    second = telemetry.list_telemetry(db, bound.work.id, after_id=first[-1].id, limit=2)
    assert len(first) == len(second) == 2 and first[-1].id < second[0].id
    with pytest.raises(ValueError):
        telemetry.list_telemetry(db, bound.work.id, limit=101)
    future = utcnow() + timedelta(days=8)
    assert telemetry.list_telemetry(db, bound.work.id, now=future) == []
    assert telemetry.prune_telemetry(db, limit=2, now=future) == 2
    assert telemetry.prune_telemetry(db, limit=2, now=future) == 2
    assert telemetry.prune_telemetry(db, limit=2, now=future) == 1
    db.commit()
    assert db.get(RuntimeAttempt, recorder.attempt_id).remote_run_id == "original-run"


@pytest.mark.parametrize("adapter_type", ["hermes", "openclaw"])
@pytest.mark.parametrize("channel", ["worker", "controlled"])
async def test_acceptance_storage_failure_prevents_wait_or_reply(
    adapter_type, monkeypatch, channel
):
    current = session()
    calls = []

    class BrokenObserver:
        request_key = "one"
        idempotency_key = "durable-intent-key"

        def accepted(self, *args):
            raise RuntimeError("storage unavailable")

    current.attempt = BrokenObserver()
    if adapter_type == "hermes":

        def respond(request):
            calls.append(request.method + " " + request.url.path)
            if request.url.path == "/v1/runs":
                return httpx.Response(202, json={"run_id": "accepted-run"})
            assert request.url.path == "/v1/runs/accepted-run/stop"
            return httpx.Response(200, json={})

        hermes_transport(monkeypatch, respond)
        adapter = HermesAdapter()
    else:
        from test_m3_runtime_turns import Gateway

        gateway = Gateway()
        original = gateway.call

        async def call(method, params, **kwargs):
            calls.append(method)
            return await original(method, params, **kwargs)

        gateway.call = call
        adapter = OpenClawAdapter()
        monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    if channel == "controlled":
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await adapter.complete_turn(current, "private prompt", request_id="one", timeout=1)
    else:
        await adapter._pump(current, "private prompt", 1)
        assert list(current.queue._queue)[-1].kind == RuntimeEventKind.error
    assert not any("agent.wait" in call or call.startswith("GET") for call in calls)


def test_attempt_identity_and_remote_outcome_guards_cover_raw_sql(db, bound):
    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    recorder.terminal("completed", "hermes.status")
    for assignment in (
        "request_key='rewrite'",
        "remote_run_id='replacement'",
        "status='failed'",
        "ended_at=NULL",
        "instance_fingerprint='replacement'",
        "work_session_id=9999",
    ):
        with pytest.raises(IntegrityError, match="runtime execution fact"):
            db.execute(
                text(f"UPDATE runtime_attempts SET {assignment} WHERE id=:id"),
                {"id": recorder.attempt_id},
            )
        db.rollback()
    with pytest.raises(IntegrityError, match="runtime execution fact"):
        db.execute(text("DELETE FROM runtime_attempts WHERE id=:id"), {"id": recorder.attempt_id})
    db.rollback()


def test_legacy_retry_history_stays_unknown(db, bound):
    legacy = WorkSession(
        task_id=bound.task.id,
        employee_id=bound.actor.id,
        runtime_type="hermes",
        status="completed",
        ended_at=utcnow(),
    )
    db.add(legacy)
    db.commit()
    assert legacy.technical_retry_count is None
    with pytest.raises(IntegrityError, match="runtime execution fact"):
        db.execute(
            text("UPDATE work_sessions SET technical_retry_count=0 WHERE id=:id"), {"id": legacy.id}
        )
    db.rollback()


@pytest.mark.parametrize("changed", ["person", "company"])
def test_query_refuses_membership_reattribution(db, bound, changed):
    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    if changed == "company":
        bound.actor.company_id = 9999
    else:
        bound.actor.person_id = bound.row.person_id = 9999
    db.commit()
    with pytest.raises(RuntimeProtocolError, match="owner_changed"):
        telemetry.reconstruct_attempt(recorder.attempt_id)


def test_historical_company_scopes_observation_and_attempt_readers(db, bound):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    recorder = prepare(bound)
    recorder.accepted("original-run", bound.runtime.id)
    token = set_request_identity(RequestIdentity(1, 9999, "owner", 1))
    try:
        for reader in (telemetry.list_attempts, telemetry.list_telemetry):
            with pytest.raises(RuntimeProtocolError, match="scope_unavailable"):
                reader(db, bound.work.id)
        with pytest.raises(RuntimeProtocolError, match="owner_changed"):
            telemetry.reconstruct_attempt(recorder.attempt_id)
        with pytest.raises(RuntimeProtocolError, match="scope_unavailable"):
            recorder.remaining_timeout(60)
    finally:
        reset_request_identity(token)


def configure_worker(db, bound, monkeypatch):
    from app.core.config import settings
    from app.models.provider import ModelBinding
    from app.runtimes.container_common import resolve_container_instance
    from app.workflow import orchestrator as orchestration

    bound.work.status, bound.work.ended_at = "failed", utcnow()
    bound.task.status = "todo"
    bound.actor.runtime_type = "hermes"
    for binding in db.scalars(
        select(ModelBinding).where(ModelBinding.employee_id == bound.actor.id)
    ):
        binding.is_primary = False
    binding = ModelBinding(
        employee_id=bound.actor.id,
        person_id=bound.actor.person_id,
        provider_id=bound.provider.id,
        model="original-model",
        is_primary=True,
    )
    db.add(binding)
    db.flush()
    bound.row.model_binding_id = binding.id
    db.commit()
    monkeypatch.setattr(settings, "runtime_mode", "real")
    monkeypatch.setattr(
        orchestration.dispatch_runtime,
        "evaluate_dispatch",
        lambda db, task: orchestration.dispatch_runtime.DispatchEvaluation(task.id, True),
    )
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: HermesAdapter())

    async def get_instance(employee):
        return resolve_container_instance(employee.id, "hermes")

    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", get_instance)
    return orchestration


@pytest.mark.parametrize("completed", [True, False])
async def test_worker_orchestrator_persists_intent_acceptance_and_honest_eof(
    db, bound, monkeypatch, completed, tmp_path
):
    bound.row.workspace_path = str(tmp_path / "worker-workspace")
    Path(bound.row.workspace_path).mkdir()
    db.commit()
    orchestration = configure_worker(db, bound, monkeypatch)
    observed = []
    submitted = []

    def respond(request):
        with SessionLocal() as independent:
            attempt = independent.scalar(select(RuntimeAttempt).order_by(RuntimeAttempt.id.desc()))
            assert attempt is not None
            work = independent.get(WorkSession, attempt.work_session_id)
            assert attempt.local_session_ref == work.runtime_session_ref
            if request.method == "POST":
                submitted.append(json.loads(request.content))
                assert attempt.status == "prepared" and attempt.remote_run_id is None
                assert attempt.idempotency_key == request.headers["Idempotency-Key"]
                observed.append(attempt.id)
                return httpx.Response(202, json={"run_id": "worker-original-run"})
            assert attempt.status == "accepted" and attempt.remote_run_id == "worker-original-run"
            if completed:
                # A real Worker terminal needs real files; the transport double
                # writes its output just as the requested runtime tool would.
                directory = (
                    Path(bound.row.workspace_path)
                    / ".eidolon"
                    / "outputs"
                    / hashlib.sha256(attempt.local_session_ref.encode()).hexdigest()
                    / hashlib.sha256(attempt.idempotency_key.encode()).hexdigest()
                )
                directory.mkdir(parents=True)
                body = b"Durable worker output.\n"
                (directory / "result.md").write_bytes(body)
                (directory / "manifest.json").write_text(
                    json.dumps(
                        {
                            "protocol": "eidolon.artifacts.v1",
                            "local_session_ref": attempt.local_session_ref,
                            "request_ref": attempt.idempotency_key,
                            "complete": True,
                            "artifacts": [
                                {
                                    "path": "result.md",
                                    "type": "other",
                                    "title": "Worker output",
                                    "size_bytes": len(body),
                                    "sha256": hashlib.sha256(body).hexdigest(),
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            payload = (
                {"type": "run.completed"}
                if completed
                else {"type": "task.completed", "status": "completed"}
            )
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n")

    hermes_transport(monkeypatch, respond)
    await orchestration.Orchestrator()._run_task(bound.task.id)
    db.expire_all()
    assert len(observed) == 1
    attempt = db.get(RuntimeAttempt, observed[0])
    assert submitted[0].get("session_id") == attempt.remote_session_ref
    work = db.get(WorkSession, attempt.work_session_id)
    assert attempt.status == ("completed" if completed else "unknown")
    assert bool(attempt.ended_at) == completed
    assert work.status == ("completed" if completed else "failed")
    assert bound.task.status == ("in_review" if completed else "blocked")
    assert "tokens" not in work.cost
    if completed:
        from app.models.drive import DriveNode
        from app.services.drive import read_content

        node = db.scalar(select(DriveNode).where(DriveNode.work_session_id == work.id))
        assert node is not None and read_content(node) == "Durable worker output.\n"


@pytest.mark.parametrize("cancelled", [True, False])
@pytest.mark.parametrize("failure_count", [1, 99])
async def test_host_outcome_storage_failure_preserves_conversation_cleanup(
    db, bound, monkeypatch, caplog, cancelled, failure_count
):
    from test_m3_runtime_conversation import admitted

    from app.work.runtime_conversation import run_conversation

    original = telemetry.AttemptRecorder.finish_host
    writes = []

    def fail_write(self, *args):
        writes.append(args)
        if len(writes) <= failure_count:
            raise SQLAlchemyError("private-control-token")
        return original(self, *args)

    monkeypatch.setattr(telemetry.AttemptRecorder, "finish_host", fail_write)

    def respond(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "accepted-run"})
        if cancelled:
            raise asyncio.CancelledError
        raise OSError("private-control-token")

    hermes_transport(monkeypatch, respond)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await run_conversation(
                bound.work.id,
                adapter=HermesAdapter(),
                runtime_session=bound.runtime,
                goal="inspect",
                budget_check=admitted,
            )
    else:
        result = await run_conversation(
            bound.work.id,
            adapter=HermesAdapter(),
            runtime_session=bound.runtime,
            goal="inspect",
            budget_check=admitted,
        )
        assert result["reason"] == "runtime_failed"
        assert result["telemetry_error"] == "host_outcome_not_saved"
    assert bound.runtime.attempt is None
    assert bound.runtime.status == ("cancelled" if cancelled else "failed")
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    assert attempt.host_outcome is None
    assert "runtime host outcome could not be saved" in caplog.text
    assert "private-control-token" not in caplog.text


@pytest.mark.parametrize("cancelled", [True, False])
@pytest.mark.parametrize("failure_count", [1, 99])
async def test_host_outcome_storage_failure_does_not_skip_worker_finalization(
    db, bound, monkeypatch, caplog, cancelled, failure_count
):
    orchestration = configure_worker(db, bound, monkeypatch)
    original = telemetry.AttemptRecorder.finish_host
    writes = []

    def fail_write(self, *args):
        writes.append(args)
        if len(writes) <= failure_count:
            raise SQLAlchemyError("private-control-token")
        return original(self, *args)

    monkeypatch.setattr(telemetry.AttemptRecorder, "finish_host", fail_write)
    adapter = HermesAdapter()

    async def fail_send(*args):
        if cancelled:
            raise asyncio.CancelledError
        raise OSError("private-control-token")

    monkeypatch.setattr(adapter, "send_task", fail_send)
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: adapter)
    runner = orchestration.Orchestrator()
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await runner._run_task(bound.task.id)
    else:
        await runner._run_task(bound.task.id)
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    work = db.get(WorkSession, attempt.work_session_id)
    assert work.status == "failed" and work.ended_at is not None
    assert "private-control-token" not in (work.error or "")
    assert "runtime host outcome could not be saved" in caplog.text
    assert "private-control-token" not in caplog.text


def test_openclaw_operation_error_has_only_structured_reason():
    event = OpenClawAdapter._map_event(
        {
            "event": "session.operation",
            "payload": {"state": "error", "error": "Bearer private-control-token"},
        }
    )
    assert "private-control-token" not in json.dumps(event.data)


@pytest.mark.parametrize(
    "assignment",
    [
        "accepted_at='2026-09-20'",
        "host_reason='host_error', host_observed_at='2026-09-20'",
        "status='completed', ended_at='2026-09-20', terminal_source='hermes.status'",
        "status='failed', ended_at='2026-09-20', terminal_source='openclaw.wait'",
        "status='completed', ended_at='2026-09-20', terminal_source='mock.stream'",
    ],
)
def test_sql_cannot_fabricate_acceptance_or_unreferenced_remote_outcome(db, bound, assignment):
    recorder = prepare(bound)
    with pytest.raises(IntegrityError):
        db.execute(
            text(f"UPDATE runtime_attempts SET {assignment} WHERE id=:id"),
            {"id": recorder.attempt_id},
        )
    db.rollback()
