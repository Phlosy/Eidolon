"""Fresh-process takeover of original controlled work, without another model turn."""

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event as sql_events
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_m3_management_runner import decision, finish, source
from test_m3_management_runner import world as _world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.management_dispatch import ManagementDispatch
from app.models.organization import Employee
from app.models.project import Project, Task, WorkSession
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance as RuntimeRow
from app.models.runtime_execution import RuntimeAttempt
from app.models.runtime_operation import RuntimeOperation
from app.repositories import project as project_repo
from app.runtimes import probe
from app.runtimes.base import RuntimeSession, accept_remote_run
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.work import controlled_resume, recovery, telemetry
from app.work import runtime_bridge as bridge
from app.work.management_dispatch import claim_source
from app.work.management_runner import ManagementFinalizationError, ManagementRunner

world = _world


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


_CRASH_ORIGINAL = """
import asyncio, json, os, sys
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import WorkSession
from app.runtimes.base import RuntimeSession
from app.runtimes.container_common import resolve_container_instance
from app.work.management_dispatch import claim_source
from app.work.runtime_conversation import ConversationLimits, run_conversation
from app.work.runtime_operations import RuntimeExecution
from app.work import runtime_bridge
from test_m3_runtime_conversation import ScriptedAdapter, admitted
settings.runtime_mode = "auto"
settings.allow_planning_fixtures = False
claim = claim_source(int(sys.argv[1]), enabled=True, budget_available=True)
assert claim.created, claim
phase = sys.argv[3]
if phase == "before_send":
    os._exit(23)
work_id = claim.dispatch.work_session_id
with SessionLocal() as db:
    work = db.get(WorkSession, work_id)
    actor = work.employee_id
runtime = RuntimeSession(id=f"original-{work_id}", task_id=None,
    instance=resolve_container_instance(actor, "hermes", session_factory=SessionLocal))
if phase in {"intent", "first_action"}:
    original = RuntimeExecution.execute_action
    def crash(self, index, *args, **kwargs):
        if phase == "intent":
            os._exit(23)
        result = original(self, index, *args, **kwargs)
        if index == 0:
            os._exit(23)
        return result
    RuntimeExecution.execute_action = crash
elif phase == "finish":
    original = runtime_bridge.handle_reply
    def crash(*args, **kwargs):
        result = original(*args, **kwargs)
        os._exit(23)
    runtime_bridge.handle_reply = crash
asyncio.run(run_conversation(work_id, adapter=ScriptedAdapter(json.loads(sys.argv[2])),
    runtime_session=runtime, goal="Act on the original explicitly addressed source",
    budget_check=admitted, limits=ConversationLimits(max_rounds=1,
        turn_timeout_seconds=float(sys.argv[4]), total_timeout_seconds=float(sys.argv[4])),
    session_factory=SessionLocal))
"""

_RECOVER_ORIGINAL = """
import asyncio, json, os, sys
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.project import Task, WorkSession
from app.models.runtime_operation import RuntimeOperation
from app.work.management_runner import ManagementRunner
from app.work.runtime_operations import RuntimeExecution
from app.runtimes.gateway import gateway
settings.runtime_mode = "auto"
work_id = int(sys.argv[1])
class NoNewTurn:
    type = "hermes"
    supports_controlled_turn = True
    async def create_session(self, *args, **kwargs):
        raise AssertionError("recovery must not create another remote session")
    async def complete_turn(self, *args, **kwargs):
        raise AssertionError("recovery must not send another model request")
    async def query_run(self, *args, **kwargs):
        raise AssertionError("saved terminal reply needs no native query")
    async def cancel_task(self, *args, **kwargs):
        raise AssertionError("saved terminal reply needs no native cancellation")
gateway.adapter_for = lambda kind: NoNewTurn()
if sys.argv[2] == "crash":
    original = RuntimeExecution.execute_action
    def crash(self, index, *args, **kwargs):
        result = original(self, index, *args, **kwargs)
        if index == 1:
            os._exit(24)
        return result
    RuntimeExecution.execute_action = crash
async def recover():
    runner = ManagementRunner(enabled=False, poll_interval=0.01, max_concurrency=1)
    runner.start(recover_only=True)
    try:
        async with asyncio.timeout(8):
            while True:
                with SessionLocal() as db:
                    work = db.get(WorkSession, work_id)
                    if work.status != "running":
                        break
                await asyncio.sleep(0.01)
    finally:
        await runner.stop()
asyncio.run(recover())
with SessionLocal() as db:
    work = db.get(WorkSession, work_id)
    print(json.dumps({"id":work.id,"status":work.status,"summary":work.summary,
        "error":work.error,"ended_at":work.ended_at.isoformat(),
        "operations":list(db.scalars(select(RuntimeOperation.id)
            .where(RuntimeOperation.work_session_id == work_id).order_by(RuntimeOperation.id))),
        "audits":list(db.scalars(select(ToolAudit.id)
            .where(ToolAudit.work_session_id == work_id).order_by(ToolAudit.id))),
        "events":list(db.scalars(select(Event.id)
            .where(Event.project_id == work.project_id).order_by(Event.id))),
        "tasks":list(db.scalars(select(Task.id)
            .where(Task.project_id == work.project_id).order_by(Task.id))),
        "decisions":list(db.scalars(select(DecisionRecord.status)
            .where(DecisionRecord.project_id == work.project_id).order_by(DecisionRecord.id)))}))
"""


def _crash_source(event_id, request, phase, *, seconds=120):
    crashed = _child(_CRASH_ORIGINAL, event_id, json.dumps(request), phase, seconds)
    assert crashed.returncode == 23, crashed.stderr
    with SessionLocal() as db:
        work_id = db.scalar(
            select(ManagementDispatch.work_session_id).where(
                ManagementDispatch.source_event_id == event_id
            )
        )
        work = db.get(WorkSession, work_id)
        assert work.status == "running" and work.ended_at is None
        return work_id


@pytest.mark.parametrize("phase", ["intent", "first_action"])
def test_startup_takes_over_partial_original_request_across_two_process_exits(world, phase):
    event_id = source(world)
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Second original action"},
        }
    )
    work_id = _crash_source(event_id, request, phase)
    second_crash = _child(_RECOVER_ORIGINAL, work_id, "crash")
    assert second_crash.returncode == 24, (second_crash.stdout, second_crash.stderr)
    recovered = _child(_RECOVER_ORIGINAL, work_id, "finish")
    repeated = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert recovered.returncode == repeated.returncode == 0, (recovered.stderr, repeated.stderr)
    result = json.loads(recovered.stdout)
    assert result == json.loads(repeated.stdout)
    assert result["id"] == work_id and result["status"] == "failed"
    assert result["error"] == "conversation_interrupted"
    assert len(result["operations"]) == 1 and len(result["audits"]) == 2
    assert len(result["tasks"]) == 2 and result["decisions"] == ["APPLIED"]


@pytest.mark.parametrize("review", [False, True])
def test_saved_finish_closes_only_original_host_without_inventing_business_verdict(world, review):
    if review:
        event_id, task_id, review_id = source(world, review=True)
    else:
        event_id = source(world)
    work_id = _crash_source(event_id, finish("Original acknowledgement"), "finish")
    first = _child(_RECOVER_ORIGINAL, work_id, "finish")
    second = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    result = json.loads(first.stdout)
    assert result == json.loads(second.stdout)
    assert result["status"] == "completed" and result["summary"] == "Original acknowledgement"
    assert result["error"] is None and not result["decisions"] and not result["audits"]
    if review:
        with SessionLocal() as db:
            assert db.get(Task, task_id).status == "in_review"
            assert db.get(ReviewRequest, review_id).verdict is None


def test_before_send_crash_gets_explicit_missing_source_result_without_new_work(world):
    event_id = source(world)
    work_id = _crash_source(event_id, finish(), "before_send")
    recovered = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["status"] == "failed" and result["error"] == "execution_attempt_unknown"
    assert not result["operations"] and not result["audits"] and not result["tasks"]


def original(world, *, seconds=30, accepted=True, kind="hermes"):
    if kind != "hermes":
        with SessionLocal() as db:
            actor = db.get(Employee, world.manager.id)
            row = db.scalar(select(RuntimeRow).where(RuntimeRow.employee_id == actor.id))
            actor.runtime_type = row.runtime_type = kind
            row.metadata_json = {
                **row.metadata_json,
                "runtime_probe": {
                    **row.metadata_json["runtime_probe"],
                    "instance_fingerprint": probe.instance_fingerprint(row),
                },
            }
            db.commit()
    event_id = source(world)
    claim = claim_source(event_id, enabled=True, budget_available=True)
    assert claim.created
    work_id = claim.dispatch.work_session_id
    runtime = RuntimeSession(
        f"saved-{work_id}",
        resolve_container_instance(world.manager.id, kind, session_factory=SessionLocal),
        None,
    )
    with SessionLocal() as db:
        db.get(WorkSession, work_id).runtime_session_ref = runtime.id
        db.commit()
    recorder = telemetry.prepare_attempt(
        work_id,
        runtime,
        "Original finite request",
        request_key="original",
        kind="controlled",
        deadline_at=None if seconds is None else utcnow() + timedelta(seconds=seconds),
    )
    if accepted:
        accept_remote_run(
            runtime,
            f"original-run-{work_id}",
            f"agent:main:eidolon-{runtime.id}" if kind == "openclaw" else runtime.id,
        )
    return SimpleNamespace(id=work_id, recorder=recorder, runtime=runtime, event_id=event_id)


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return HermesAdapter()


def facts(work_id):
    with SessionLocal() as db:
        work = db.get(WorkSession, work_id)
        attempt = db.scalar(
            select(RuntimeAttempt)
            .where(RuntimeAttempt.work_session_id == work_id)
            .order_by(RuntimeAttempt.id.desc())
            .limit(1)
        )
        return {
            "status": work.status,
            "error": work.error,
            "ended": work.ended_at,
            "remote": None if attempt is None else attempt.status,
            "host": None if attempt is None else attempt.host_outcome,
            "stop": None if attempt is None else attempt.stop_reason,
            "native_end": None if attempt is None else attempt.ended_at,
            "audits": list(
                db.scalars(select(ToolAudit.id).where(ToolAudit.work_session_id == work_id))
            ),
        }


def owner(monkeypatch, adapter, **kwargs):
    from app.runtimes.gateway import gateway

    monkeypatch.setattr(gateway, "adapter_for", lambda kind: adapter)
    return ManagementRunner(enabled=False, poll_interval=0.01, **kwargs)


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_original_native_terminal_does_not_authorize_unaccepted_model_text(
    world, monkeypatch, terminal
):
    work = original(world)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "run_id": work.runtime.remote_run_id,
                "status": terminal,
                "output": json.dumps(decision(world)),
            },
        )

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    await runner._resume_owned(work.id)
    result = facts(work.id)
    assert result["status"] == "failed"
    assert result["error"] == (
        "request_source_unknown" if terminal == "completed" else "remote_failed"
    )
    assert result["remote"] == terminal and result["native_end"] is not None
    assert result["host"] == "failed" and result["audits"] == []
    assert calls == [("GET", f"/v1/runs/{work.runtime.remote_run_id}")]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0


async def test_running_original_waits_until_observed_terminal_without_new_turn(world, monkeypatch):
    work = original(world)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(request):
        calls.append(request.method)
        if len(calls) == 1:
            entered.set()
            return httpx.Response(200, json={"status": "running"})
        await release.wait()
        return httpx.Response(200, json={"status": "completed"})

    monkeypatch.setattr(controlled_resume, "POLL_INTERVAL", 0.01)
    runner = owner(monkeypatch, transport(monkeypatch, respond))
    pending = asyncio.create_task(runner._resume_owned(work.id))
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.sleep(0.03)
    assert facts(work.id)["status"] == "running" and not pending.done()
    release.set()
    await asyncio.wait_for(pending, 2)
    assert facts(work.id)["error"] == "request_source_unknown"
    assert calls == ["GET", "GET"] and not runner._owned


@pytest.mark.parametrize(
    "mode", ["unknown", "404", "expired", "deadline_unknown", "reference_unknown"]
)
async def test_unknown_remote_and_missing_limits_keep_admission_fence(world, monkeypatch, mode):
    work = original(
        world,
        seconds=None if mode == "deadline_unknown" else -1 if mode == "expired" else 0.12,
        accepted=mode != "reference_unknown",
    )
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        return (
            httpx.Response(404)
            if mode == "404"
            else httpx.Response(200, json={"status": "unknown"})
        )

    monkeypatch.setattr(controlled_resume, "POLL_INTERVAL", 0.01)
    runner = owner(monkeypatch, transport(monkeypatch, respond))
    await runner._resume_owned(work.id)
    result = facts(work.id)
    expected = {
        "404": "recovery_wait_unavailable",
        "deadline_unknown": "execution_deadline_unknown",
        "reference_unknown": "execution_reference_unknown",
    }.get(mode, "execution_deadline_expired")
    assert result["status"] == "failed" and result["error"].startswith(expected)
    assert result["remote"] == "unknown" and result["native_end"] is None
    assert result["host"] == "failed" and result["stop"] in {"timeout", "host_error"}
    assert all(path.startswith(f"/v1/runs/{work.runtime.remote_run_id}") for _, path in calls)
    assert sum(method == "POST" for method, _ in calls) == (0 if mode == "reference_unknown" else 1)
    if mode in {"expired", "deadline_unknown"}:
        assert calls[0][0] == "POST"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1


@pytest.mark.parametrize("reason", ["cancelled", "budget_denied", "reassigned", "timeout"])
async def test_saved_stop_still_cancels_original_remote_before_host_finalization(
    world, monkeypatch, reason
):
    work = original(world)
    work.recorder.request_stop(reason)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        assert facts(work.id)["status"] == "running"
        return httpx.Response(200, json={"status": "failed"})

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    await runner._resume_owned(work.id)
    result = facts(work.id)
    assert result["status"] == ("failed" if reason == "timeout" else "cancelled")
    assert result["error"] == reason and result["stop"] == reason
    assert result["remote"] == "failed" and result["audits"] == []
    assert calls == [
        ("POST", f"/v1/runs/{work.runtime.remote_run_id}/stop"),
        ("GET", f"/v1/runs/{work.runtime.remote_run_id}"),
    ]


async def test_repeated_shutdown_cancellation_drains_original_stop_and_final_write(
    world, monkeypatch
):
    work = original(world)
    waiting, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(request):
        calls.append(request.method)
        if request.method == "POST":
            stopping.set()
            await release.wait()
        elif len(calls) == 1:
            waiting.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"status": "failed"})

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    runner.start(recover_only=True)
    await asyncio.wait_for(waiting.wait(), 2)
    shutdown = asyncio.create_task(runner.stop())
    await asyncio.wait_for(stopping.wait(), 2)
    shutdown.cancel()
    for job in list(runner._recovery_jobs.values()):
        job.cancel()
    await asyncio.sleep(0.01)
    assert not shutdown.done() and facts(work.id)["status"] == "running"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(shutdown, 2)
    result = facts(work.id)
    assert result["status"] == "cancelled" and result["remote"] == "failed"
    assert calls == ["GET", "POST", "GET"]
    assert not runner._owned and not runner._recovery_jobs and runner._scanner is None


@pytest.mark.parametrize("change", ["work_terminal", "runtime", "stop", "project_pause"])
async def test_late_native_response_rechecks_original_authority_and_identity(
    world, monkeypatch, change
):
    work = original(world)
    calls = []

    def respond(request):
        calls.append(request.method)
        if len(calls) == 1:
            with SessionLocal() as db:
                if change == "work_terminal":
                    row = db.get(WorkSession, work.id)
                    row.status, row.error, row.ended_at = "cancelled", "Administrator", utcnow()
                elif change == "runtime":
                    db.get(RuntimeRow, work.runtime.instance.details["row_id"]).internal_port += 1
                elif change == "stop":
                    telemetry.record_stop_intent(db, work.recorder.attempt_id, "budget_denied")
                else:
                    db.get(Project, world.project.id).status = "paused"
                db.commit()
        return httpx.Response(200, json={"status": "completed"})

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    await runner._resume_owned(work.id)
    result = facts(work.id)
    assert result["audits"] == []
    if change == "work_terminal":
        assert result["status"] == "cancelled" and result["error"] == "Administrator"
    elif change == "runtime":
        assert result["status"] == "failed" and result["native_end"] is None and calls == ["GET"]
    elif change == "stop":
        assert result["status"] == "cancelled" and result["error"] == "budget_denied"
    else:
        assert result["status"] == "failed"
        with SessionLocal() as db:
            assert db.get(Project, world.project.id).status == "paused"
    assert calls == (["GET"] if change == "runtime" else ["GET", "POST", "GET"])


async def test_partial_original_request_rolls_back_failed_receipt_then_reuses_committed_action(
    world, monkeypatch
):
    request = decision(world)
    request["decision"]["actions"].append(
        {"tool": "create_task", "args": {"project_id": world.project.id, "title": "Second action"}}
    )
    work_id = _crash_source(source(world), request, "intent")
    with SessionLocal() as db:
        db.execute(
            text(
                "CREATE TRIGGER reject_recovered_receipt "
                "BEFORE UPDATE OF action_index ON tool_audits "
                "WHEN NEW.action_index = 1 "
                "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
            )
        )
        db.commit()
    calls = []
    runner = owner(monkeypatch, transport(monkeypatch, lambda req: calls.append(req.method)))
    with pytest.raises(IntegrityError):
        await runner._resume_owned(work_id)
    first = facts(work_id)
    assert first["status"] == "running" and len(first["audits"]) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(DecisionRecord.status)) == "EXECUTING"
        db.execute(text("DROP TRIGGER reject_recovered_receipt"))
        db.commit()
    await runner._resume_owned(work_id)
    result = facts(work_id)
    assert result["status"] == "failed" and result["error"] == "conversation_interrupted"
    assert len(result["audits"]) == 2 and result["audits"][:1] == first["audits"]
    assert not calls


async def test_exhausted_finalization_is_visible_and_same_source_can_retry(world, monkeypatch):
    work = original(world)
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, json={"status": "completed"})

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    with SessionLocal() as db:
        db.execute(
            text(
                "CREATE TRIGGER reject_recovered_host BEFORE UPDATE OF status ON work_sessions "
                "BEGIN SELECT RAISE(ABORT, 'injected finalization failure'); END"
            )
        )
        db.commit()
    with pytest.raises(ManagementFinalizationError):
        await runner._resume_owned(work.id)
    assert facts(work.id)["status"] == "running" and facts(work.id)["host"] is None
    assert work.id in runner.finalization_failures
    with SessionLocal() as db:
        db.execute(text("DROP TRIGGER reject_recovered_host"))
        db.commit()
    await runner._resume_owned(work.id)
    assert facts(work.id)["error"] == "request_source_unknown" and not runner.finalization_failures
    assert calls == ["GET"]


@pytest.mark.parametrize("limit", [True, 0, 101])
def test_controlled_page_rejects_invalid_limits_without_reading(limit):
    with pytest.raises(ValueError, match="invalid recovery page"):
        recovery.running_controlled(
            through_id=0, limit=limit, session_factory=lambda: pytest.fail("unexpected read")
        )


async def test_company_scope_blocks_source_lookup_scanning_finalization_and_io(world, monkeypatch):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    work = original(world)
    saved = controlled_resume.source_for_work(work.id)
    calls = []
    runner = owner(monkeypatch, transport(monkeypatch, lambda req: calls.append(req.method)))
    token = set_request_identity(RequestIdentity(1, 99999, "owner", 1))
    try:
        assert controlled_resume.source_for_work(work.id) is None
        assert recovery.work_session_boundary() == 0
        assert recovery.running_controlled(through_id=work.id).items == ()
        await runner._resume_owned(work.id)
        runner._finalize(saved, {"status": "finished", "reason": "finish"}, None)
    finally:
        reset_request_identity(token)
    assert facts(work.id)["status"] == "running" and calls == []


@pytest.mark.parametrize("stop", [False, True])
async def test_openclaw_recovery_queries_and_stops_original_run_only(world, monkeypatch, stop):
    from app.runtimes.openclaw.adapter import OpenClawAdapter

    work = original(world, kind="openclaw")
    if stop:
        work.recorder.request_stop("blocked")
    calls = []

    class Client:
        async def call(self, method, params, **kwargs):
            calls.append((method, params))
            return {"runId": work.runtime.remote_run_id, "state": "aborted" if stop else "ok"}

    @asynccontextmanager
    async def connect(instance):
        assert instance.details["row_id"] == work.runtime.instance.details["row_id"]
        yield Client()

    adapter = OpenClawAdapter()
    monkeypatch.setattr(adapter, "_connect", connect)
    await owner(monkeypatch, adapter)._resume_owned(work.id)
    result = facts(work.id)
    assert result["error"] == ("blocked" if stop else "request_source_unknown")
    assert result["status"] == ("cancelled" if stop else "failed")
    assert [method for method, _ in calls] == (
        ["chat.abort", "agent.wait"] if stop else ["agent.wait"]
    )
    for method, params in calls:
        assert params["runId"] == work.runtime.remote_run_id
        if method == "chat.abort":
            assert params["sessionKey"] == work.runtime.remote_session_ref


@pytest.mark.parametrize("phase", ["intent", "finish"])
def test_expired_original_can_aggregate_committed_actions_but_cannot_start_missing_ones(
    world, phase
):
    work_id = _crash_source(source(world), decision(world), phase, seconds=0.5)
    time.sleep(0.5)
    resumed = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["status"] == "failed"
    if phase == "finish":
        assert result["error"] == "conversation_interrupted"
        assert len(result["tasks"]) == len(result["audits"]) == 1
        assert result["decisions"] == ["APPLIED"]
    else:
        assert result["error"] == "execution_deadline_expired"
        assert result["tasks"] == result["audits"] == []
        assert result["decisions"] == ["EXECUTING"]


def test_original_review_verdict_is_resumed_once_without_inventing_later_acknowledgement(world):
    event_id, task_id, review_id = source(world, review=True)
    work_id = _crash_source(event_id, decision(world, review_id=review_id), "intent")
    first = _child(_RECOVER_ORIGINAL, work_id, "finish")
    second = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    result = json.loads(first.stdout)
    assert result == json.loads(second.stdout)
    assert result["status"] == "failed" and result["error"] == "conversation_interrupted"
    assert len(result["audits"]) == 1 and result["decisions"] == ["APPLIED"]
    with SessionLocal() as db:
        assert db.get(Task, task_id).status == "done"
        assert db.get(ReviewRequest, review_id).verdict == "PASS"


@pytest.mark.parametrize("damage", ["missing_body", "bad_digest", "stopped"])
async def test_unusable_saved_request_never_reconstructs_actions_from_other_inputs(
    world, monkeypatch, damage
):
    work = original(world)
    work.recorder.terminal("completed", "host.reply")
    work.recorder.finish_host("completed", "reply_received")

    # Arrange older NULL history or a mismatched source at INSERT. The guards
    # correctly forbid corrupting immutable source identities via UPDATE.
    def legacy(_mapper, _connection, target):
        if damage == "missing_body":
            target.request_body = None
        else:
            target.request_digest = "0" * 64

    if damage in {"missing_body", "bad_digest"}:
        sql_events.listen(RuntimeOperation, "before_insert", legacy)
    try:
        bridge.handle_reply(
            work.id, json.dumps(finish()), _runtime_attempt_id=work.recorder.attempt_id
        )
    finally:
        if damage in {"missing_body", "bad_digest"}:
            sql_events.remove(RuntimeOperation, "before_insert", legacy)
    if damage == "stopped":
        work.recorder.request_stop("cancelled")
    calls = []
    await owner(
        monkeypatch, transport(monkeypatch, lambda req: calls.append(req.method))
    )._resume_owned(work.id)
    result = facts(work.id)
    assert (
        result["error"]
        == {
            "missing_body": "request_body_unknown",
            "bad_digest": "request_body_mismatch",
            "stopped": "cancelled",
        }[damage]
    )
    assert result["status"] == ("cancelled" if damage == "stopped" else "failed")
    assert not calls and result["audits"] == [] and result["host"] == "completed"


@pytest.mark.parametrize("had_attempt", [False, True])
def test_finalization_cannot_overwrite_a_newer_original_attempt(world, had_attempt):
    work = original(world)
    if had_attempt:
        saved = controlled_resume.source_for_work(work.id)
    else:
        from dataclasses import replace

        saved = replace(controlled_resume.source_for_work(work.id), attempt_id=None)
    if had_attempt:
        work.recorder.terminal("completed", "host.reply")
        telemetry.prepare_attempt(
            work.id,
            work.runtime,
            "Newer original turn",
            request_key="next",
            kind="controlled",
            deadline_at=utcnow() + timedelta(seconds=30),
        )
    ManagementRunner()._finalize(saved, {"status": "finished", "reason": "finish"}, None)
    assert facts(work.id)["status"] == "running" and facts(work.id)["host"] is None


async def test_two_recovery_owners_share_original_action_receipts(world, monkeypatch):
    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Concurrent second action"},
        }
    )
    work_id = _crash_source(source(world), request, "intent")
    calls = []
    adapter = transport(monkeypatch, lambda req: calls.append(req.method))
    first, second = owner(monkeypatch, adapter), owner(monkeypatch, adapter)
    await asyncio.gather(first._resume_owned(work_id), second._resume_owned(work_id))
    result = facts(work_id)
    assert result["status"] == "failed" and result["error"] == "conversation_interrupted"
    assert len(result["audits"]) == 2 and calls == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 1
        assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1


async def test_recovery_scanner_retries_same_source_after_storage_failure_without_new_admission(
    world, monkeypatch
):
    from sqlalchemy.exc import OperationalError
    from test_m3_management_runner import eventually

    from app.work import management_dispatch

    work = original(world)
    saved_lookup, seen = controlled_resume.source_for_work, []

    def lookup(work_id, **kwargs):
        seen.append(work_id)
        if len(seen) == 1:
            raise OperationalError("read failure", None, Exception("offline storage"))
        return saved_lookup(work_id, **kwargs)

    monkeypatch.setattr(controlled_resume, "source_for_work", lookup)
    monkeypatch.setattr(
        management_dispatch, "discover_sources", lambda **kwargs: pytest.fail("new admission")
    )
    calls = []

    def respond(req):
        calls.append(req.method)
        return httpx.Response(200, json={"status": "completed"})

    runner = owner(monkeypatch, transport(monkeypatch, respond))
    runner.start(recover_only=True)
    try:
        await eventually(lambda db: db.get(WorkSession, work.id).ended_at)
        assert seen == [work.id, work.id] and calls == ["GET"]
        assert runner._recovery_cursor == runner._recovery_through == work.id
    finally:
        await runner.stop()


async def test_scanner_respects_captured_cohort_page_limit_and_shared_capacity(world, monkeypatch):
    from test_m3_management_runner import eventually

    work = original(world)

    def add_work():
        with SessionLocal() as db:
            saved = db.get(WorkSession, work.id)
            row = project_repo.create_work_session(
                db,
                task_id=None,
                purpose="management",
                project_id=saved.project_id,
                employee_id=saved.employee_id,
                runtime_type=saved.runtime_type,
                runtime_instance_id=saved.runtime_instance_id,
                provider_id=saved.provider_id,
                model=saved.model,
                started_at=utcnow(),
            )
            db.commit()
            return row.id

    ids = [work.id, *[add_work() for _ in range(4)]]
    first = recovery.running_controlled(through_id=ids[-1], limit=2)
    second = recovery.running_controlled(through_id=ids[-1], after_id=first.cursor, limit=2)
    assert [item.work_session_id for item in first.items + second.items] == ids[:4]
    entered, release = [], asyncio.Event()

    async def hold(work_id):
        entered.append(work_id)
        await release.wait()

    runner = ManagementRunner(enabled=False, poll_interval=0.01, max_concurrency=2)
    monkeypatch.setattr(runner, "_resume_owned", hold)
    runner.start(recover_only=True)
    later = add_work()
    try:
        await eventually(lambda db: len(entered) == 2)
        await asyncio.sleep(0.03)
        assert entered == ids[:2] and len(runner._recovery_jobs) == 2
        release.set()
        await eventually(lambda db: len(entered) == len(ids))
        assert entered == ids and later not in entered
    finally:
        release.set()
        await runner.stop()


def test_original_read_is_resumed_with_one_durable_receipt(world):
    request = {
        "version": 1,
        "kind": "read",
        "operation_key": "original-read",
        "tool": "inspect_project",
        "args": {"project_id": world.project.id},
    }
    work_id = _crash_source(source(world), request, "intent")
    first = _child(_RECOVER_ORIGINAL, work_id, "finish")
    second = _child(_RECOVER_ORIGINAL, work_id, "finish")
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    result = json.loads(first.stdout)
    assert result == json.loads(second.stdout)
    assert result["error"] == "conversation_interrupted" and len(result["audits"]) == 1
    assert not result["tasks"] and not result["decisions"]


@pytest.mark.parametrize("withdrawal", ["paused", "stop_between_actions"])
async def test_original_actions_recheck_withdrawal_before_each_remaining_effect(
    world, monkeypatch, withdrawal
):
    from app.work.runtime_operations import RuntimeExecution

    request = decision(world)
    request["decision"]["actions"].append(
        {
            "tool": "create_task",
            "args": {"project_id": world.project.id, "title": "Withdrawn second action"},
        }
    )
    work_id = _crash_source(
        source(world), request, "first_action" if withdrawal == "paused" else "intent"
    )
    if withdrawal == "paused":
        with SessionLocal() as db:
            db.get(Project, world.project.id).status = "paused"
            db.commit()
    else:
        execute = RuntimeExecution.execute_action

        def withdraw(self, index, *args, **kwargs):
            result = execute(self, index, *args, **kwargs)
            if index == 0:
                with SessionLocal() as db:
                    attempt = db.scalar(select(RuntimeAttempt))
                    telemetry.record_stop_intent(db, attempt.id, "budget_denied")
                    db.commit()
            return result

        monkeypatch.setattr(RuntimeExecution, "execute_action", withdraw)
    calls = []
    await owner(
        monkeypatch, transport(monkeypatch, lambda req: calls.append(req.method))
    )._resume_owned(work_id)
    result = facts(work_id)
    assert result["status"] == ("cancelled" if withdrawal == "stop_between_actions" else "failed")
    assert len(result["audits"]) == 1 and not calls
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(DecisionRecord.status)) == "EXECUTING"


async def test_inflight_scan_does_not_erase_an_owner_failure_rewind(world, monkeypatch):
    work = original(world)
    with SessionLocal() as db:
        prior = db.get(WorkSession, work.id)
        later = project_repo.create_work_session(
            db,
            task_id=None,
            purpose="management",
            project_id=prior.project_id,
            employee_id=prior.employee_id,
            runtime_type=prior.runtime_type,
            runtime_instance_id=prior.runtime_instance_id,
            provider_id=prior.provider_id,
            model=prior.model,
            started_at=utcnow(),
        )
        db.commit()
        later_id = later.id
    runner = ManagementRunner(max_concurrency=2)
    runner._running = True
    runner._recovery_cursor, runner._recovery_through = work.id, later_id
    fail = asyncio.Event()

    async def failing_owner():
        await fail.wait()
        raise RuntimeError("injected owner storage failure")

    task = asyncio.create_task(failing_owner())
    runner._recovery_jobs[work.id] = task
    task.add_done_callback(lambda done: runner._recovery_done(work.id, done))
    read_started, read_release, owner_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered = []

    async def read(operation, **kwargs):
        batch = operation(**kwargs)
        read_started.set()
        await read_release.wait()
        return batch

    async def hold(work_id):
        entered.append(work_id)
        await owner_release.wait()

    monkeypatch.setattr(runner, "_database", read)
    monkeypatch.setattr(runner, "_resume_owned", hold)
    pending = asyncio.create_task(runner._start_recovery())
    await read_started.wait()
    fail.set()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert runner._recovery_cursor == work.id - 1
    read_release.set()
    await pending
    assert runner._recovery_cursor == work.id - 1
    await runner._start_recovery()
    await asyncio.sleep(0)
    assert entered == [later_id, work.id]
    owner_release.set()
    await runner.stop()


async def test_direct_owner_uses_shared_capacity_and_cannot_be_replaced_by_recovery(
    world, monkeypatch
):
    from app.work.management_runner import _OwnedConversation

    work = original(world)
    source_view = controlled_resume.source_for_work(work.id)
    held = asyncio.create_task(asyncio.Event().wait())
    runner = ManagementRunner(max_concurrency=1)
    owned = _OwnedConversation(source_view, held)
    runner._owned[work.id] = owned
    try:
        assert runner._available_capacity() == 0
        await runner._resume_owned(work.id)
        assert runner._owned[work.id] is owned and facts(work.id)["status"] == "running"
    finally:
        held.cancel()
        await asyncio.gather(held, return_exceptions=True)


async def test_stale_owner_cannot_record_stop_or_budget_withdrawal_for_newer_attempt(world):
    from app.runtimes.base import RuntimeProtocolError
    from app.work.management_runner import _OwnedConversation

    work = original(world)
    saved = controlled_resume.source_for_work(work.id)
    work.recorder.terminal("completed", "host.reply")
    latest = telemetry.prepare_attempt(
        work.id,
        work.runtime,
        "Next controlled turn",
        request_key="next",
        kind="controlled",
        deadline_at=utcnow() + timedelta(seconds=30),
    )
    with SessionLocal() as db:
        db.get(Project, world.project.id).status = "paused"
        db.commit()
    calls = []

    async def deny(work_id):
        calls.append(work_id)
        return False

    runner = ManagementRunner(budget_monitor=deny)
    held = asyncio.create_task(asyncio.Event().wait())
    runner._owned[work.id] = _OwnedConversation(saved, held)
    try:
        with pytest.raises(RuntimeProtocolError, match="execution_source_changed"):
            runner._stop_reason(saved)
        await runner._interrupt_invalid_owned()
        assert calls == [] and not held.cancelling()
        with SessionLocal() as db:
            assert db.get(RuntimeAttempt, latest.attempt_id).stop_requested_at is None
    finally:
        held.cancel()
        await asyncio.gather(held, return_exceptions=True)


async def test_late_budget_response_cannot_withdraw_a_successor_attempt(world):
    from app.work.management_runner import _OwnedConversation

    work = original(world)
    saved = controlled_resume.source_for_work(work.id)
    successors = []

    async def deny(_work_id):
        work.recorder.terminal("completed", "host.reply")
        successors.append(
            telemetry.prepare_attempt(
                work.id,
                work.runtime,
                "Successor during budget observation",
                request_key="successor",
                kind="controlled",
                deadline_at=utcnow() + timedelta(seconds=30),
            ).attempt_id
        )
        return False

    runner = ManagementRunner(budget_monitor=deny)
    held = asyncio.create_task(asyncio.Event().wait())
    runner._owned[work.id] = _OwnedConversation(saved, held)
    try:
        await runner._interrupt_invalid_owned()
        assert len(successors) == 1 and not held.cancelling()
        with SessionLocal() as db:
            assert db.get(RuntimeAttempt, successors[0]).stop_requested_at is None
            assert db.get(RuntimeAttempt, work.recorder.attempt_id).stop_requested_at is None
    finally:
        held.cancel()
        await asyncio.gather(held, return_exceptions=True)
