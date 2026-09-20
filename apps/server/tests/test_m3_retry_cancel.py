"""U15 worker lifecycle: actual services/adapters over transport-only doubles."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from test_m3_runtime_turns import hermes_transport, session
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import configure_worker
from test_m3_telemetry import worker_world as _worker_world

from app.core.database import SessionLocal
from app.models.organization import Employee
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.work import telemetry

bound = _bound
worker_world = _worker_world


@pytest.fixture()
async def running_worker(db, bound, monkeypatch, tmp_path, request):
    kind = request.param
    orchestration = configure_worker(db, bound, monkeypatch)
    root = tmp_path / "original-worker"
    root.mkdir()
    bound.row.workspace_path = str(root)
    bound.row.runtime_type = bound.actor.runtime_type = kind
    db.commit()
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    env = SimpleNamespace(
        kind=kind,
        adapter=adapter,
        runner=orchestration.Orchestrator(),
        sessions=[],
        closed=[],
        stops=[],
        stream_entered=asyncio.Event(),
        stop_entered=asyncio.Event(),
        stop_release=asyncio.Event(),
        completed=False,
        stop_error=False,
    )
    env.stop_release.set()
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: adapter)

    async def get_instance(employee):
        return resolve_container_instance(employee.id, kind)

    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", get_instance)
    original_send = adapter.send_task

    async def send(current, *args, **kwargs):
        env.sessions.append(current)
        return await original_send(current, *args, **kwargs)

    monkeypatch.setattr(adapter, "send_task", send)

    async def stop(reference):
        with SessionLocal() as independent:
            actor = independent.get(Employee, bound.actor.id)
            assert actor.current_task_id == bound.task.id
            assert actor.status != "idle"
        env.stops.append(reference)
        env.stop_entered.set()
        await env.stop_release.wait()
        if env.stop_error:
            raise OSError("private-stop-transport-error")

    class PendingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                env.stream_entered.set()
                if env.completed:
                    yield b'data: {"type":"run.completed"}\n\n'
                else:
                    yield b'data: {"type":"assistant.delta","delta":"pending"}\n\n'
                    await asyncio.Event().wait()
            finally:
                env.closed.append(True)

    async def respond(req):
        if req.url.path.endswith("/stop"):
            await stop(req.url.path)
            return httpx.Response(200, json={"ok": True})
        if req.method == "POST":
            return httpx.Response(202, json={"run_id": "live-original-run"})
        return httpx.Response(200, stream=PendingStream())

    class Gateway:
        def __init__(self):
            self.events = asyncio.Queue()
            self.worker = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if self.worker:
                env.closed.append(True)

        async def call(self, method, params, **kwargs):
            if method == "chat.send":
                self.worker = True
                return {"runId": "live-original-run"}
            if method == "agent.wait":
                env.stream_entered.set()
                if env.completed:
                    return {"status": "ok", "runId": "live-original-run"}
                await asyncio.Event().wait()
            if method == "chat.abort":
                await stop(params)
                return {"aborted": True, "runIds": ["live-original-run"]}
            raise AssertionError(method)

    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:
        monkeypatch.setattr(adapter, "_connect", lambda instance: Gateway())
    yield env
    env.stop_release.set()
    for current in env.sessions:
        if current.background is not None:
            current.background.cancel()
            await asyncio.gather(current.background, return_exceptions=True)


def fail_observation(monkeypatch):
    def fail(self, event):
        raise SQLAlchemyError("private-observation-error")

    monkeypatch.setattr(telemetry.AttemptRecorder, "observe", fail)


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
@pytest.mark.parametrize("stop_error", [False, True])
async def test_observation_failure_drains_producer_before_ownership_release(
    db, bound, monkeypatch, running_worker, stop_error, caplog
):
    env = running_worker
    env.stop_error = stop_error
    fail_observation(monkeypatch)
    await env.runner._run_task(bound.task.id)
    assert env.sessions[0].background.done(), "producer outlived worker ownership"
    assert env.closed == [True]
    assert len(env.stops) == 1
    if env.kind == "hermes":
        assert env.stops == ["/v1/runs/live-original-run/stop"]
    else:
        assert env.stops == [{"sessionKey": "agent:main:main", "runId": "live-original-run"}]
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    assert attempt.remote_run_id == "live-original-run"
    assert attempt.status == "unknown" and attempt.ended_at is None
    assert attempt.host_outcome == "failed" and attempt.terminal_source is None
    assert db.get(WorkSession, attempt.work_session_id).status == "failed"
    assert bound.actor.current_task_id is None
    assert "private-stop-transport-error" not in caplog.text
    assert "private-observation-error" not in caplog.text


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_host_cancellation_stops_and_drains_before_finalization(db, bound, running_worker):
    env = running_worker
    runner = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.stream_entered.wait(), 2)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert env.sessions[0].background.done()
        assert env.closed == [True] and len(env.stops) == 1
        db.expire_all()
        attempt = db.scalar(select(RuntimeAttempt))
        assert attempt.host_outcome == "cancelled"
        assert bound.actor.current_task_id is None
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_new_cancellation_during_failure_cleanup_cannot_skip_finalization(
    db, bound, monkeypatch, running_worker
):
    env = running_worker
    fail_observation(monkeypatch)
    env.stop_release.clear()
    runner = asyncio.create_task(env.runner._run_task(bound.task.id))
    try:
        await asyncio.wait_for(env.stop_entered.wait(), 2)
        runner.cancel()
        await asyncio.sleep(0)
        assert not runner.done()
        db.expire_all()
        assert bound.actor.current_task_id == bound.task.id
        env.stop_release.set()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert env.sessions[0].background.done() and env.closed == [True]
        db.expire_all()
        assert bound.actor.current_task_id is None
        attempt = db.scalar(select(RuntimeAttempt))
        assert attempt.host_outcome == "cancelled"
    finally:
        env.stop_release.set()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("running_worker", ["hermes", "openclaw"], indirect=True)
async def test_completed_run_with_missing_artifact_records_host_failure(db, bound, running_worker):
    env = running_worker
    env.completed = True
    await env.runner._run_task(bound.task.id)
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt))
    assert attempt.status == "completed"  # Observed remote fact remains unchanged.
    assert attempt.host_outcome == "failed"
    assert db.get(WorkSession, attempt.work_session_id).status == "failed"


@pytest.mark.parametrize("kind", ["hermes", "openclaw"])
@pytest.mark.parametrize("reference", ["worker", "controlled", "missing"])
async def test_adapter_stop_uses_only_original_explicit_run(monkeypatch, kind, reference):
    current = session()
    current.instance.details[f"run_id:{current.id}"] = "unrelated-stale-run"
    if reference != "missing":
        current.remote_run_id = "original-run"
        current.remote_session_ref = (
            "agent:main:main" if reference == "worker" else "original-controlled-session"
        )
    calls = []

    def respond(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"ok": True})

    class Gateway:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            assert method == "chat.abort"
            calls.append(params)
            return {"aborted": True}

    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:
        monkeypatch.setattr(adapter, "_connect", lambda instance: Gateway())
    await adapter.cancel_task(current)
    assert current.status == "cancelled"
    expected = []
    if reference != "missing":
        expected = (
            ["/v1/runs/original-run/stop"]
            if kind == "hermes"
            else [{"sessionKey": current.remote_session_ref, "runId": "original-run"}]
        )
    assert calls == expected
    assert current.cancel_confirmed is False  # An acknowledgement alone proves no terminal.


@pytest.mark.parametrize("kind", ["hermes", "openclaw"])
async def test_adapter_cancel_awaits_local_producer_cleanup(monkeypatch, kind):
    current, entered, closed = session(), asyncio.Event(), []

    async def producer():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.append(True)

    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    if kind == "openclaw":

        class UnusedGateway:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call(self, *args, **kwargs):
                return {}

        # No real network access. An immediate reply must not accidentally give
        # the old fire-and-forget cleanup enough time to appear correct.
        monkeypatch.setattr(adapter, "_connect", lambda instance: UnusedGateway())
    current.background = asyncio.create_task(producer())
    await entered.wait()
    try:
        await adapter.cancel_task(current)
        assert current.background.done() and closed == [True]
    finally:
        current.background.cancel()
        await asyncio.gather(current.background, return_exceptions=True)
