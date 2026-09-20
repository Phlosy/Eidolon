"""Accepted Worker read failures recover the original run without another submit."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from test_m3_runtime_turns import hermes_transport
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import configure_worker
from test_m3_telemetry import worker_world as _worker_world

from app.core.database import SessionLocal
from app.models.base import as_utc, utcnow
from app.models.drive import DriveNode
from app.models.project import WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt, RuntimeTelemetry
from app.runtimes import worker_recovery
from app.runtimes.base import RuntimeProtocolError
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.runtimes.openclaw.rpc import GatewayError
from app.work import telemetry

bound = _bound
worker_world = _worker_world


@pytest.fixture(params=["hermes", "openclaw"])
def recovering(db, bound, monkeypatch, tmp_path, request):
    kind = request.param
    orchestration = configure_worker(db, bound, monkeypatch)
    root = tmp_path / "recovery-worker"
    root.mkdir()
    bound.row.workspace_path = str(root)
    bound.row.runtime_type = bound.actor.runtime_type = kind
    db.commit()
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    env = SimpleNamespace(
        adapter=adapter,
        runner=orchestration.Orchestrator(),
        kind=kind,
        sessions=[],
        sends=[],
        queries=[],
        stops=[],
        query_failures=0,
        state="completed",
        initial_error="network",
        on_query=None,
        entered=asyncio.Event(),
        release=asyncio.Event(),
        retry_counts=[],
        query_times=[],
    )
    env.release.set()
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: adapter)

    async def get_instance(employee):
        return resolve_container_instance(employee.id, kind)

    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", get_instance)
    send = adapter.send_task

    async def capture(session, *args, **kwargs):
        with SessionLocal() as independent:
            row = independent.get(RuntimeAttempt, session.attempt.attempt_id)
            assert row.deadline_at is not None, "send preceded durable deadline"
        env.sessions.append(session)
        return await send(session, *args, **kwargs)

    monkeypatch.setattr(adapter, "send_task", capture)

    def output(attempt):
        directory = (
            root
            / ".eidolon"
            / "outputs"
            / hashlib.sha256(attempt.local_session_ref.encode()).hexdigest()
            / hashlib.sha256(attempt.idempotency_key.encode()).hexdigest()
        )
        directory.mkdir(parents=True, exist_ok=True)
        body = b"Original run survived a read failure.\n"
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
                            "title": "Recovered output",
                            "size_bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        }
                    ],
                }
            )
        )

    async def query(run_id):
        env.queries.append(run_id)
        if env.stops:
            return "running"  # A stop acknowledgement is still not a terminal.
        env.query_times.append(asyncio.get_running_loop().time())
        with SessionLocal() as independent:
            row = independent.scalar(
                select(RuntimeAttempt).where(RuntimeAttempt.remote_run_id == run_id)
            )
            work = independent.get(WorkSession, row.work_session_id)
            env.retry_counts.append(work.technical_retry_count)
            assert 1 <= work.technical_retry_count <= 2, "query preceded durable retry admission"
            if env.state == "completed":
                output(row)
        env.entered.set()
        await env.release.wait()
        if env.on_query:
            env.on_query()
        if env.query_failures:
            env.query_failures -= 1
            raise (
                httpx.ReadError("private-query-connection-loss")
                if kind == "hermes"
                else GatewayError("connection_closed")
            )
        return env.state

    async def respond(req):
        if req.url.path == "/v1/runs":
            env.sends.append(json.loads(req.content))
            if env.initial_error == "unaccepted":
                raise httpx.ReadTimeout("private-lost-create-ack")
            return httpx.Response(202, json={"run_id": "recovered-original"})
        if req.url.path.endswith("/events"):
            if env.initial_error == "auth":
                return httpx.Response(403)
            if env.initial_error == "identity":
                return httpx.Response(
                    200, text='data: {"run_id":"foreign","type":"run.completed"}\n\n'
                )
            raise httpx.ReadError("private-stream-disconnect")
        if req.url.path.endswith("/stop"):
            env.stops.append(req.url.path)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200, json={"run_id": "recovered-original", "status": await query("recovered-original")}
        )

    class Gateway:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            if method == "chat.send":
                env.sends.append(params)
                self.sent = True
                if env.initial_error == "unaccepted":
                    raise GatewayError("connection_closed")
                return {"runId": params["idempotencyKey"]}
            if method == "chat.abort":
                env.stops.append(params)
                return {"aborted": True}
            if method == "artifacts.list":
                return {"artifacts": []}
            assert method == "agent.wait"
            if self.sent:
                if env.initial_error == "auth":
                    raise GatewayError("authentication_rejected")
                if env.initial_error == "identity":
                    return {"runId": "foreign", "status": "ok"}
                raise GatewayError("connection_closed")
            return {"runId": params["runId"], "status": await query(params["runId"])}

    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:
        monkeypatch.setattr(adapter, "_connect", lambda instance: Gateway())
    return env


def facts(db):
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    return attempt, db.get(WorkSession, attempt.work_session_id)


@pytest.mark.parametrize("query_failures", [0, 1])
async def test_transient_read_failure_recovers_original_output_without_resubmission(
    db, bound, recovering, query_failures
):
    env = recovering
    env.query_failures = query_failures
    await env.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert work.status == "completed" and bound.task.status == "in_review"
    assert attempt.status == "completed" and attempt.host_outcome == "completed"
    assert work.technical_retry_count == query_failures + 1 and bound.task.rework_count == 0
    assert len(env.sends) == len(env.sessions) == 1 and env.stops == []
    assert env.retry_counts == list(range(1, query_failures + 2))
    if query_failures:
        assert env.query_times[1] - env.query_times[0] >= 0.45
    assert set(env.queries) == {attempt.remote_run_id}
    assert db.get(RuntimeArtifactCollection, attempt.id) is not None
    node = db.scalar(select(DriveNode).where(DriveNode.work_session_id == work.id))
    assert node is not None
    warnings = list(
        db.scalars(select(RuntimeTelemetry).where(RuntimeTelemetry.kind == "runtime.warning"))
    )
    retry_warnings = [
        row for row in warnings if row.details_json.get("reason") == "transport_retry"
    ]
    assert len(retry_warnings) == query_failures + 1
    assert env.sessions[0].background.done()


async def test_exhausted_read_recovery_stops_original_and_blocks_task(
    db, bound, recovering, caplog
):
    env = recovering
    env.query_failures = 10
    await env.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert work.technical_retry_count == 2, "retry allowance was not enforced and recorded"
    assert work.status == "failed" and bound.task.status == "blocked"
    assert attempt.status == "unknown" and attempt.stop_state == "unconfirmed"
    assert db.get(RuntimeArtifactCollection, attempt.id) is None
    assert len(env.sends) == len(env.stops) == 1 and env.retry_counts == [1, 2]
    assert bound.task.rework_count == 0
    assert "private-query-connection-loss" not in caplog.text


@pytest.mark.parametrize("failure", ["auth", "identity", "unaccepted"])
async def test_nonretryable_or_unaccepted_failure_never_resubmits(db, bound, recovering, failure):
    env = recovering
    env.initial_error = failure
    await env.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert work.technical_retry_count == 0 and bound.task.rework_count == 0
    assert work.status == "failed" and bound.task.status == "blocked"
    assert len(env.sends) == 1 and env.retry_counts == []
    assert attempt.ended_at is None


async def test_cancellation_during_wait_recovery_drains_original_before_release(
    db, bound, recovering
):
    env = recovering
    env.release.clear()
    job = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.entered.wait(), 2.5)
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    attempt, work = facts(db)
    assert len(env.stops) == 1 and len(env.sends) == 1
    assert work.status == "failed" and bound.task.status == "blocked"
    assert attempt.stop_reason == "cancelled" and work.technical_retry_count == 1
    assert env.sessions[0].background.done()


async def test_retry_storage_failure_prevents_query_and_preserves_cleanup(
    db, bound, recovering, monkeypatch
):
    from sqlalchemy.exc import SQLAlchemyError

    calls = []

    def unavailable(*args, **kwargs):
        calls.append(True)
        raise SQLAlchemyError("private-retry-storage-error")

    monkeypatch.setattr(telemetry.AttemptRecorder, "check_worker_wait", unavailable, raising=False)
    await recovering.runner._run_task(bound.task.id)
    assert calls == [True]
    attempt, work = facts(db)
    assert work.technical_retry_count == 0 and work.status == "failed"
    assert recovering.retry_counts == [] and len(recovering.sends) == 1
    assert attempt.stop_requested_at is not None and recovering.sessions[0].background.done()


@pytest.mark.parametrize("timeout", [0, 901, float("inf"), float("nan")])
async def test_invalid_timeout_is_rejected_before_a_durable_request_is_prepared(
    db, bound, recovering, timeout
):
    bound.actor.runtime_config = {"run_timeout_seconds": timeout}
    db.commit()
    await recovering.runner._run_task(bound.task.id)
    db.expire_all()
    assert recovering.sends == []
    assert db.scalar(select(RuntimeAttempt)) is None, (
        "invalid configuration left a phantom pending run"
    )
    work = db.scalar(select(WorkSession).order_by(WorkSession.id.desc()))
    assert work.status == "failed" and bound.task.status == "blocked"


@pytest.mark.parametrize("state", ["running", "unknown"])
async def test_recovery_never_resets_the_original_worker_deadline(db, bound, recovering, state):
    bound.actor.runtime_config = {"run_timeout_seconds": 1.3}
    db.commit()
    recovering.state = state
    await asyncio.wait_for(recovering.runner._run_task(bound.task.id), 2.2)
    attempt, work = facts(db)
    assert work.status == "failed" and bound.task.status == "blocked"
    assert work.technical_retry_count == 1
    assert attempt.ended_at is None and attempt.stop_state == "unconfirmed"
    assert len(recovering.sends) == len(recovering.stops) == 1
    assert 0 < (as_utc(attempt.deadline_at) - as_utc(attempt.created_at)).total_seconds() <= 1.3


@pytest.mark.parametrize("unknown", [False, True])
async def test_expired_original_deadline_prevents_worker_send(
    db, bound, recovering, monkeypatch, unknown
):
    original = telemetry.prepare_attempt

    def expired(*args, **kwargs):
        kwargs["deadline_at"] = None if unknown else utcnow() - timedelta(seconds=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(telemetry, "prepare_attempt", expired)
    await recovering.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert recovering.sends == recovering.queries == []
    assert attempt.remote_run_id is None and attempt.ended_at is None
    assert work.status == "failed" and bound.task.status == "blocked"


async def test_each_recovery_query_is_bounded(db, bound, recovering, monkeypatch):
    recovering.release.clear()
    monkeypatch.setattr(worker_recovery, "QUERY_TIMEOUT", 0.01)
    await asyncio.wait_for(recovering.runner._run_task(bound.task.id), 2.5)
    attempt, work = facts(db)
    assert recovering.retry_counts == [1, 2] and work.technical_retry_count == 2
    assert work.status == "failed" and bound.task.status == "blocked"
    assert len(recovering.stops) == 1 and attempt.stop_state == "unconfirmed"


async def test_saved_budget_stop_during_backoff_prevents_recovery_query(
    db, bound, recovering, monkeypatch
):
    original_check = telemetry.AttemptRecorder.check_worker_wait

    def reserve_then_stop(recorder, **kwargs):
        count = original_check(recorder, **kwargs)
        if kwargs.get("reserve_retry"):
            recorder.request_stop("budget_denied", session=kwargs["session"])
        return count

    monkeypatch.setattr(telemetry.AttemptRecorder, "check_worker_wait", reserve_then_stop)
    await recovering.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert recovering.retry_counts == [] and len(recovering.stops) == 1
    assert attempt.stop_reason == "budget_denied" and work.technical_retry_count == 1
    assert work.status == "failed" and bound.task.status == "blocked"


async def test_connection_changed_during_query_cannot_publish_recovered_output(
    db, bound, recovering
):
    from app.models.runtime import RuntimeInstance

    def replace_connection():
        with SessionLocal() as independent:
            independent.get(RuntimeInstance, bound.row.id).internal_host = "replacement.invalid"
            independent.commit()

    recovering.on_query = replace_connection
    await recovering.runner._run_task(bound.task.id)
    attempt, work = facts(db)
    assert attempt.ended_at is None and work.status == "failed"
    assert bound.task.status == "blocked" and db.get(RuntimeArtifactCollection, attempt.id) is None
    assert recovering.retry_counts == [1] and recovering.stops == []


async def test_concurrent_retry_reservations_share_the_session_limit(db, bound, recovering):
    recovering.release.clear()
    job = asyncio.create_task(recovering.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(recovering.entered.wait(), 2.5)
        current = recovering.sessions[0]

        def reserve():
            try:
                return current.attempt.check_worker_wait(session=current, reserve_retry=True)
            except RuntimeProtocolError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: reserve(), range(3)))
        assert results.count(2) == 1 and results.count("execution_retry_exhausted") == 2
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    _, work = facts(db)
    assert work.technical_retry_count == 2 and bound.task.rework_count == 0
