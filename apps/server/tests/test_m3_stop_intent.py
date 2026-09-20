"""U15 stop requests survive the host; only original-run observations confirm end."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_m3_runtime_turns import hermes_transport
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import prepare
from test_m3_telemetry import worker_world as _worker_world

from app.core.database import SessionLocal
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError, accept_remote_run
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter

bound = _bound
worker_world = _worker_world


@pytest.fixture(params=["hermes", "openclaw"])
def original_run(db, bound, monkeypatch, request):
    w = bound
    kind = request.param
    # The fixture has already captured immutable provenance. For OpenClaw use a
    # new work session instead of rewriting that historical snapshot.
    if kind == "openclaw":
        from app.models.base import utcnow
        from app.repositories import project as project_repo
        from app.runtimes.container_common import resolve_container_instance

        w.work.status, w.work.ended_at = "failed", utcnow()
        w.row.runtime_type = w.actor.runtime_type = kind
        db.flush()
        w.work = project_repo.create_work_session(
            db,
            task_id=w.task.id,
            employee_id=w.actor.id,
            runtime_type=kind,
            runtime_instance_id=w.row.id,
            provider_id=w.provider.id,
            model="original-model",
            started_at=utcnow(),
        )
        w.work.runtime_session_ref = w.runtime.id
        db.commit()
        w.runtime.instance = resolve_container_instance(w.actor.id, kind)
    w.recorder = prepare(w)
    remote_session = f"agent:main:eidolon-{w.runtime.id}" if kind == "openclaw" else w.runtime.id
    accept_remote_run(w.runtime, "original-run", remote_session)
    w.adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    w.calls, w.remote_state, w.stop_error = [], "running", False
    w.expected_run, w.expected_reason, w.turn_failure = "original-run", "cancelled", False
    w.on_stop = None
    w.connection_targets = []
    w.stop_entered, w.stop_release, w.query_release = (asyncio.Event() for _ in range(3))
    w.stop_release.set()
    w.query_release.set()

    async def observed(method, reference):
        w.calls.append((method, reference))
        with SessionLocal() as independent:
            row = independent.get(RuntimeAttempt, w.recorder.attempt_id)
            assert row.stop_requested_at is not None, "remote I/O preceded durable stop intent"
            assert row.stop_reason == w.expected_reason
        if method == "stop" and w.stop_error:
            raise OSError("private-stop-response-lost")
        if method == "stop" and w.on_stop is not None:
            w.on_stop()
        if method == "stop":
            w.stop_entered.set()
            await w.stop_release.wait()
        else:
            await w.query_release.wait()
        if method == "query" and w.remote_state == "unavailable":
            raise OSError("private-query-failure")

    async def respond(req):
        w.connection_targets.append(str(req.url))
        if req.url.path == "/v1/runs":
            return httpx.Response(202, json={"run_id": w.expected_run})
        if req.method == "GET" and w.turn_failure:
            w.turn_failure = False
            raise OSError("private-controlled-transport-failure")
        action = "stop" if req.method == "POST" else "query"
        await observed(action, req.url.path)
        return httpx.Response(
            200,
            json={
                "run_id": "another-run" if w.remote_state == "wrong_run" else w.expected_run,
                "status": w.remote_state,
                "ok": True,
            },
        )

    class Gateway:
        def __init__(self):
            self.events = asyncio.Queue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            if method == "chat.send":
                return {"runId": w.expected_run}
            if method == "agent.wait" and w.turn_failure:
                w.turn_failure = False
                raise OSError("private-controlled-transport-failure")
            assert method in {"chat.abort", "agent.wait"}
            await observed("stop" if method == "chat.abort" else "query", params)
            return {
                "aborted": True,
                "runId": "another-run" if w.remote_state == "wrong_run" else w.expected_run,
                "status": w.remote_state,
            }

    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:

        def connect(instance):
            w.connection_targets.append(instance.details["base_url"])
            return Gateway()

        monkeypatch.setattr(w.adapter, "_connect", connect)
    return w


async def test_controlled_turn_failure_uses_durable_stop_and_original_query(db, original_run):
    w = original_run
    w.recorder = prepare(w, "controlled-failure")
    w.expected_run = (
        w.recorder.idempotency_key if w.adapter.type.value == "openclaw" else "controlled-run"
    )
    w.expected_reason, w.turn_failure, w.remote_state = "runtime_error", True, "aborted"
    with pytest.raises(OSError, match="private-controlled-transport-failure"):
        await w.adapter.complete_turn(
            w.runtime, "private prompt", request_id="controlled-failure", timeout=2
        )
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.stop_reason == "runtime_error" and row.stop_state == "terminal_observed"
    assert row.status == "failed" and w.runtime.cancel_confirmed is True
    assert [call[0] for call in w.calls] == ["stop", "query"]
    assert w.task.status == "in_progress"


@pytest.mark.parametrize("outcome", ["aborted", "completed", "running", "unavailable", "wrong_run"])
async def test_stop_is_durable_before_io_and_ack_is_not_terminal(db, original_run, outcome, caplog):
    w = original_run
    w.remote_state = outcome
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.stop_requested_at is not None and row.stop_reason == "cancelled"
    assert [call[0] for call in w.calls] == ["stop", "query"]
    assert all("original-run" in str(call[1]) for call in w.calls)
    confirmed = outcome in {"aborted", "completed"}
    assert w.runtime.cancel_confirmed is confirmed
    assert row.stop_state == ("terminal_observed" if confirmed else "unconfirmed")
    assert row.status == ({"aborted": "failed", "completed": "completed"}.get(outcome, "accepted"))
    assert (row.ended_at is not None) is confirmed
    assert row.host_outcome is None
    assert w.task.status == "in_progress" and w.work.status == "running"
    assert "private-query-failure" not in caplog.text


async def test_lost_stop_response_can_be_confirmed_by_original_run_query(db, original_run, caplog):
    w = original_run
    w.stop_error, w.remote_state = True, "aborted"
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.status == "failed" and row.stop_state == "terminal_observed"
    assert w.runtime.cancel_confirmed is True
    assert [call[0] for call in w.calls] == ["stop", "query"]
    assert "private-stop-response-lost" not in caplog.text


@pytest.mark.parametrize("change", ["connection", "credential", "version", "owner"])
async def test_stop_query_rechecks_identity_after_remote_wait(db, original_run, change):
    from app.models.runtime import RuntimeInstance
    from app.providers.secrets.store import get_secret_store

    w = original_run

    def change_binding():
        with SessionLocal() as independent:
            row = independent.get(RuntimeInstance, w.row.id)
            if change == "connection":
                row.internal_host = "replacement.invalid"
            elif change == "credential":
                row.metadata_json = {
                    **row.metadata_json,
                    "api_key_ref": get_secret_store().store(independent, "replacement-token"),
                }
            elif change == "version":
                row.runtime_version = "replacement-version"
            else:
                row.person_id = 99999
            independent.commit()

    w.remote_state, w.on_stop = "aborted", change_binding
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.stop_requested_at is not None and row.stop_reason == "cancelled"
    assert row.status == "accepted" and row.ended_at is None
    assert row.stop_state == "unconfirmed" and w.runtime.cancel_confirmed is False
    assert [call[0] for call in w.calls] == ["stop", "query"]
    assert "replacement.invalid" not in str(w.connection_targets)
    assert w.task.status == "in_progress" and w.work.status == "running"


async def test_handle_replaced_while_waiting_cannot_redirect_terminal_observation(db, original_run):
    w = original_run
    original = w.recorder
    next_attempt = prepare(w, "next-turn")
    next_attempt.accepted("next-run", w.runtime.remote_session_ref)
    w.runtime.attempt = original

    def rebind():
        w.runtime.attempt = next_attempt
        w.runtime.remote_run_id = "next-run"
        w.runtime.instance.details["base_url"] = "http://replacement.invalid:1234"

    w.remote_state, w.on_stop = "aborted", rebind
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    assert "replacement.invalid" not in str(w.connection_targets)
    assert db.get(RuntimeAttempt, original.attempt_id).status == "failed"
    assert db.get(RuntimeAttempt, next_attempt.attempt_id).status == "accepted"
    assert db.get(RuntimeAttempt, next_attempt.attempt_id).stop_requested_at is None
    assert w.runtime.cancel_confirmed is False


async def test_repeated_cancellation_waits_for_durable_stop_cleanup(db, original_run):
    w = original_run
    w.stop_release.clear()
    w.remote_state = "aborted"
    pending = asyncio.create_task(w.adapter.cancel_task(w.runtime))
    try:
        await asyncio.wait_for(w.stop_entered.wait(), 1)
        for _ in range(2):
            pending.cancel()
            await asyncio.sleep(0)
        assert not pending.done()
        w.stop_release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        db.expire_all()
        assert db.get(RuntimeAttempt, w.recorder.attempt_id).stop_state == "terminal_observed"
    finally:
        w.stop_release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("waiting", ["stop", "query"])
async def test_stop_and_query_each_have_a_finite_wait(db, original_run, monkeypatch, waiting):
    from app.runtimes import cancellation

    w = original_run
    monkeypatch.setattr(cancellation, "STOP_TIMEOUT_SECONDS", 0.01)
    (w.stop_release if waiting == "stop" else w.query_release).clear()
    w.remote_state = "aborted"
    await asyncio.wait_for(w.adapter.cancel_task(w.runtime), 1)
    db.expire_all()
    assert db.get(RuntimeAttempt, w.recorder.attempt_id).stop_state == (
        "terminal_observed" if waiting == "stop" else "unconfirmed"
    )


async def test_already_terminal_stop_keeps_original_outcome_and_does_no_io(db, original_run):
    w = original_run
    source = "hermes.status" if w.adapter.type.value == "hermes" else "openclaw.wait"
    w.recorder.terminal("completed", source)
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.stop_state == "terminal_observed" and row.status == "completed"
    assert w.runtime.cancel_confirmed is True and w.calls == []


@pytest.mark.parametrize("damage", ["runtime", "handle"])
async def test_stop_records_intent_but_never_contacts_replacement(db, original_run, damage):
    w = original_run
    if damage == "runtime":
        w.row.internal_host = "replacement.invalid"
        db.commit()
    else:
        w.runtime.remote_run_id = "another-run"
    await w.adapter.cancel_task(w.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, w.recorder.attempt_id)
    assert row.stop_state == "unconfirmed"
    assert w.calls == [] and w.runtime.cancel_confirmed is False


async def test_stop_storage_failure_still_drains_local_producer(original_run, caplog):
    w = original_run
    entered, closed = asyncio.Event(), []

    async def producer():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.append(True)

    def unavailable():
        raise SQLAlchemyError("private-stop-storage-error")

    w.runtime.attempt = replace(w.recorder, session_factory=unavailable)
    w.runtime.background = asyncio.create_task(producer())
    await entered.wait()
    try:
        await w.adapter.cancel_task(w.runtime)
        assert w.runtime.background.done() and closed == [True]
        assert w.calls == [] and w.runtime.cancel_confirmed is False
        assert "private-stop-storage-error" not in caplog.text
    finally:
        w.runtime.background.cancel()
        await asyncio.gather(w.runtime.background, return_exceptions=True)


def test_first_stop_intent_is_immutable_and_closes_request_admission(db, bound):
    recorder = prepare(bound)
    assert db.get(RuntimeAttempt, recorder.attempt_id).stop_state == "not_recorded"
    recorder.request_stop("cancelled", session=bound.runtime)
    db.expire_all()
    row = db.get(RuntimeAttempt, recorder.attempt_id)
    first = row.stop_requested_at
    recorder.request_stop("budget_denied", session=bound.runtime)
    db.expire_all()
    assert (row.stop_reason, row.stop_requested_at) == ("cancelled", first)
    for key in ("worker", "new-request"):
        with pytest.raises(RuntimeProtocolError, match="stop_requested"):
            prepare(bound, key)
    for assignment in (
        "stop_requested_at=NULL",
        "stop_reason='host_error'",
        "stop_reason=NULL, stop_requested_at=NULL",
    ):
        with pytest.raises(IntegrityError):
            db.execute(
                text(f"UPDATE runtime_attempts SET {assignment} WHERE id=:id"), {"id": row.id}
            )
        db.rollback()


def test_concurrent_stop_requests_keep_first_committed_intent(db, bound):
    recorder = prepare(bound)
    gate = Barrier(2)

    def stop(reason):
        gate.wait(timeout=5)
        recorder.request_stop(reason)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(stop, ["cancelled", "budget_denied"]))
    db.expire_all()
    row = db.get(RuntimeAttempt, recorder.attempt_id)
    assert row.stop_reason in {"cancelled", "budget_denied"}
    assert row.stop_requested_at is not None
    assert len(list(db.scalars(select(RuntimeAttempt)))) == 1


def test_stop_intent_rejects_cross_company_before_write(db, bound):
    recorder = prepare(bound)
    token = set_request_identity(RequestIdentity(1, 9999, "owner", 1))
    try:
        with pytest.raises(RuntimeProtocolError, match="scope_unavailable"):
            recorder.request_stop("cancelled")
    finally:
        reset_request_identity(token)
    db.expire_all()
    assert db.get(RuntimeAttempt, recorder.attempt_id).stop_requested_at is None


@pytest.mark.parametrize("collision", ["id", "idempotency_key"])
def test_sql_replace_cannot_erase_original_execution_identity(db, bound, collision):
    recorder = prepare(bound)
    row = db.get(RuntimeAttempt, recorder.attempt_id)
    values = {column.name: getattr(row, column.name) for column in RuntimeAttempt.__table__.columns}
    values.update(request_key="replacement", request_digest="b" * 64)
    if collision == "id":
        values["idempotency_key"] = "new-key"
    else:
        values["id"] += 1000
    with pytest.raises(IntegrityError):
        db.execute(RuntimeAttempt.__table__.insert().prefix_with("OR REPLACE").values(**values))
    db.rollback()
    assert db.get(RuntimeAttempt, recorder.attempt_id).request_key == "worker"


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_original_work_with_attempt_cannot_be_replaced_or_deleted(db, bound, operation):
    prepare(bound)
    row = db.get(WorkSession, bound.work.id)
    values = {column.name: getattr(row, column.name) for column in WorkSession.__table__.columns}
    db.rollback()
    db.execute(text("PRAGMA foreign_keys=OFF"))
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 0
    try:
        with pytest.raises(IntegrityError):
            if operation == "replace":
                values["summary"] = "rewritten original"
                db.execute(
                    WorkSession.__table__.insert().prefix_with("OR REPLACE").values(**values)
                )
            else:
                db.execute(text("DELETE FROM work_sessions WHERE id=:id"), {"id": row.id})
    finally:
        db.rollback()
        db.execute(text("PRAGMA foreign_keys=ON"))


def test_stop_recovers_original_run_in_fresh_process_without_resubmission(db, bound):
    recorder = prepare(bound)
    accept_remote_run(bound.runtime, "original-run", bound.runtime.id)
    recorder.request_stop("cancelled")
    # Current employee preference does not replace the captured runtime identity.
    bound.actor.runtime_type = "openclaw"
    db.commit()
    script = r"""
import asyncio, json, sys
import httpx
from app.runtimes.hermes.adapter import HermesAdapter
from app.work.runtime_stop import stop_attempt
calls = []
def respond(request):
    calls.append([request.method, request.url.path])
    return httpx.Response(200, json={"run_id":"original-run", "status":"aborted"})
original = httpx.AsyncClient
httpx.AsyncClient = lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
result = asyncio.run(stop_attempt(int(sys.argv[1]), adapter=HermesAdapter(), reason="cancelled"))
print(json.dumps({"state":result, "calls":calls}))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(recorder.attempt_id)],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(child.stdout) == {
        "state": "terminal_observed",
        "calls": [["POST", "/v1/runs/original-run/stop"], ["GET", "/v1/runs/original-run"]],
    }
    db.expire_all()
    assert db.get(RuntimeAttempt, recorder.attempt_id).stop_state == "terminal_observed"
    assert bound.task.status == "in_progress"
