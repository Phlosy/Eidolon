"""Live controlled-session withdrawal through real runners and both adapters."""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from test_m3_management_runner import decision, ended, finish, runner_for, source
from test_m3_management_runner import world as _world
from test_m3_runtime_turns import hermes_transport

from app.core.database import SessionLocal
from app.models.decision import DecisionRecord
from app.models.project import Project, Task, WorkSession
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes import probe
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.services import tasks
from app.work.telemetry import AttemptRecorder

world = _world


@pytest.fixture(params=["hermes", "openclaw"])
def controlled(db, world, monkeypatch, request):
    kind = request.param
    for actor in (world.manager, world.reviewer):
        actor.runtime_type = kind
        row = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == actor.id))
        row.runtime_type = kind
        row.metadata_json = {
            **row.metadata_json,
            "runtime_probe": {
                **row.metadata_json["runtime_probe"],
                "instance_fingerprint": probe.instance_fingerprint(row),
            },
        }
    db.commit()
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind]()
    env = SimpleNamespace(
        adapter=adapter,
        kind=kind,
        prompts=[],
        runs=[],
        sessions=[],
        stops=[],
        queries=[],
        entered=asyncio.Event(),
        release=asyncio.Event(),
        replies=[finish()],
        hold_at=0,
        remote_state="aborted",
        expected_reason="cancelled",
        gateway_closed=[],
    )

    def submitted(prompt, run_id, session_id):
        env.prompts.append(json.loads(prompt))
        env.runs.append(run_id)
        env.sessions.append(session_id)
        return len(env.prompts) - 1

    async def reply(index):
        if index == env.hold_at:
            env.entered.set()
            await env.release.wait()
        return json.dumps(env.replies[index])

    def stopped(run_id, session_id):
        env.stops.append((run_id, session_id))
        with SessionLocal() as independent:
            row = independent.scalar(
                select(RuntimeAttempt).where(RuntimeAttempt.remote_run_id == run_id)
            )
            assert row.stop_requested_at is not None, "stop preceded its durable intent"
            assert row.stop_reason == env.expected_reason

    async def respond(req):
        if req.url.path == "/v1/runs":
            body = json.loads(req.content)
            run_id = f"controlled-run-{len(env.prompts) + 1}"
            submitted(body["input"], run_id, body["session_id"])
            return httpx.Response(202, json={"run_id": run_id})
        run_id = req.url.path.split("/")[3]
        index = env.runs.index(run_id)
        if req.url.path.endswith("/stop"):
            stopped(run_id, env.sessions[index])
            return httpx.Response(200, json={"ok": True})
        if any(item[0] == run_id for item in env.stops):
            env.queries.append(run_id)
            return httpx.Response(200, json={"run_id": run_id, "status": env.remote_state})
        output = await reply(index)
        return httpx.Response(200, json={"run_id": run_id, "status": "completed", "output": output})

    class Gateway:
        def __init__(self):
            self.events = asyncio.Queue()
            self.index = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if self.index is not None:
                env.gateway_closed.append(self.index)

        async def call(self, method, params, **kwargs):
            if method == "chat.send":
                run_id = params["idempotencyKey"]
                self.index = submitted(params["message"], run_id, params["sessionKey"])
                return {"runId": run_id}
            if method == "chat.abort":
                stopped(params["runId"], params["sessionKey"])
                return {"aborted": True}
            assert method == "agent.wait"
            run_id = params["runId"]
            if self.index is None:
                env.queries.append(run_id)
                return {"runId": run_id, "status": env.remote_state}
            output = await reply(self.index)
            await self.events.put(
                {
                    "event": "chat",
                    "payload": {
                        "runId": run_id,
                        "sessionKey": env.sessions[self.index],
                        "state": "final",
                        "message": {"role": "assistant", "content": output},
                    },
                }
            )
            return {"runId": run_id, "status": "ok"}

    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:
        monkeypatch.setattr(adapter, "_connect", lambda instance: Gateway())
    return env


def withdraw(world, change, task_id=None, *, commit=True):
    with SessionLocal() as db:
        project = db.get(Project, world.project.id)
        if change == "project_cancelled":
            project.status = "cancelled"
        elif change == "manager_reassigned":
            project.management_employee_id = world.reviewer.id
            project.management_person_id = world.reviewer.person_id
        else:
            tasks.transition_task(db, db.get(Task, task_id), change)
        db.flush()
        if commit:
            db.commit()


@pytest.mark.parametrize(
    "change", ["project_cancelled", "manager_reassigned", "blocked", "cancelled"]
)
@pytest.mark.parametrize("remote_state", ["aborted", "running"])
async def test_committed_scope_withdrawal_interrupts_original_controlled_run(
    db, world, controlled, monkeypatch, change, remote_state
):
    env = controlled
    review = change in {"blocked", "cancelled"}
    event_id, task_id, request_id = (
        source(world, review=True) if review else (source(world), None, None)
    )
    env.remote_state = remote_state
    env.expected_reason = "reassigned" if change == "manager_reassigned" else "cancelled"
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        withdraw(world, change, task_id)
        # No in-memory nudge: the ordinary periodic scan must find committed facts.
        work = await ended(event_id)
        assert work.status == "cancelled", work.error
        assert work.error.startswith(env.expected_reason)
        db.expire_all()
        attempt = db.scalar(select(RuntimeAttempt))
        assert attempt.stop_reason == env.expected_reason
        assert attempt.host_outcome == "cancelled"
        confirmed = remote_state == "aborted"
        assert attempt.stop_state == ("terminal_observed" if confirmed else "unconfirmed")
        assert ("remote_stop_unconfirmed" in work.error) is not confirmed
        assert env.stops == [(env.runs[0], env.sessions[0])]
        assert env.queries == env.runs and len(env.prompts) == 1
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert work.employee_id == (world.reviewer.id if review else world.manager.id)
        if review:
            assert db.get(Task, task_id).status == change
            assert db.get(ReviewRequest, request_id).verdict is None
        elif change == "project_cancelled":
            assert db.get(Project, world.project.id).status == "cancelled"
    finally:
        await runner.stop()
    assert not runner._jobs


async def test_saved_stop_intent_interrupts_an_otherwise_valid_controlled_scope(
    db, world, controlled, monkeypatch
):
    env = controlled
    event_id = source(world)
    env.expected_reason = "budget_denied"
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        row = db.scalar(select(RuntimeAttempt))
        AttemptRecorder(row.id, row.request_key, row.idempotency_key).request_stop("budget_denied")
        work = await ended(event_id)
        assert work.status == "cancelled" and work.error.startswith("budget_denied")
        assert len(env.stops) == 1
        db.expire_all()
        assert db.get(Project, world.project.id).status == "waiting_for_management"
    finally:
        await runner.stop()


async def test_rolled_back_scope_change_leaves_controlled_run_running(
    db, world, controlled, monkeypatch
):
    env = controlled
    source(world)
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        withdraw(world, "project_cancelled", commit=False)
        runner._wake.set()
        await asyncio.sleep(0.1)
        db.expire_all()
        assert db.scalar(select(WorkSession)).status == "running"
        assert db.scalar(select(RuntimeAttempt)).stop_requested_at is None
        assert env.stops == []
    finally:
        await runner.stop()


async def test_own_formal_verdict_receipt_remains_live_during_periodic_checks(
    db, world, controlled, monkeypatch
):
    env = controlled
    event_id, task_id, request_id = source(world, review=True)
    env.replies = [
        {
            "version": 1,
            "kind": "read",
            "operation_key": "inspect",
            "tool": "inspect_task_review",
            "args": {"task_id": task_id},
        },
        decision(world, review_id=request_id),
        finish(),
    ]
    env.hold_at = 2
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        assert env.prompts[-1]["feedback"]["result"]["decision_status"] == "APPLIED"
        runner._wake.set()
        await asyncio.sleep(0.1)
        db.expire_all()
        assert db.get(Task, task_id).status == "done"
        assert db.get(ReviewRequest, request_id).verdict == "PASS"
        assert db.scalar(select(WorkSession)).status == "running"
        assert env.stops == []
        env.release.set()
        assert (await ended(event_id)).status == "completed"
        db.expire_all()
        assert all(row.stop_requested_at is None for row in db.scalars(select(RuntimeAttempt)))
    finally:
        await runner.stop()


async def test_scope_withdrawal_during_setup_stops_without_inventing_an_attempt(
    db, world, controlled, monkeypatch
):
    env = controlled
    event_id = source(world)
    entered = asyncio.Event()

    async def held_setup(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(env.adapter, "create_session", held_setup)
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    try:
        await asyncio.wait_for(entered.wait(), 3)
        withdraw(world, "project_cancelled")
        assert (await ended(event_id)).status == "cancelled"
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)) is None
        assert env.prompts == env.stops == []
    finally:
        await runner.stop()


async def test_shutdown_drains_a_directly_owned_controlled_conversation(
    world, controlled, monkeypatch
):
    from app.work.management_dispatch import claim_source

    env = controlled
    event_id = source(world)
    claim = claim_source(event_id, enabled=True, budget_available=True)
    assert claim.created
    runner = runner_for(monkeypatch, env.adapter)
    runner.start()
    pending = asyncio.create_task(runner._run_owned(claim.dispatch))
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        await runner.stop()
        assert pending.done(), "shutdown abandoned a directly owned controlled conversation"
        assert (await ended(event_id)).status == "cancelled"
        assert len(env.stops) == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await runner.stop()


async def test_shutdown_still_owns_a_direct_conversation_during_finalization(
    db, world, controlled, monkeypatch
):
    from app.work.management_dispatch import claim_source

    env = controlled
    event_id = source(world)
    claim = claim_source(event_id, enabled=True, budget_available=True)
    assert claim.created
    runner = runner_for(monkeypatch, env.adapter)
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()
    finalize = runner._finalize

    def held_finalization(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), "test did not release finalization"
        finalize(*args)

    monkeypatch.setattr(runner, "_finalize", held_finalization)
    env.release.set()
    runner.start()
    pending = asyncio.create_task(runner._run_owned(claim.dispatch))
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        stopping = asyncio.create_task(runner.stop())
        await asyncio.sleep(0.05)
        assert not stopping.done(), "shutdown abandoned original finalization"
        release.set()
        await stopping
        assert pending.done()
        assert (await ended(event_id)).status == "completed"
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)).stop_requested_at is None
        assert env.stops == []
    finally:
        release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        if stopping is not None:
            await stopping
        await runner.stop()


async def test_stop_observation_storage_failure_preserves_intent_before_io(
    db, world, controlled, monkeypatch, caplog
):
    from app.work import management_runner

    env = controlled
    event_id = source(world)
    runner = runner_for(monkeypatch, env.adapter)
    loop = asyncio.get_running_loop()
    failed = asyncio.Event()
    unavailable = True
    record = management_runner.record_stop_intent

    def save(db, attempt_id, reason):
        if unavailable:
            loop.call_soon_threadsafe(failed.set)
            raise SQLAlchemyError("private-controlled-storage-failure")
        return record(db, attempt_id, reason)

    runner.start()
    try:
        await asyncio.wait_for(env.entered.wait(), 3)
        monkeypatch.setattr(management_runner, "record_stop_intent", save)
        withdraw(world, "project_cancelled")
        await asyncio.wait_for(failed.wait(), 3)
        await asyncio.sleep(0.05)
        db.expire_all()
        assert db.scalar(select(RuntimeAttempt)).stop_requested_at is None
        assert db.scalar(select(WorkSession)).status == "running"
        assert env.stops == []
        unavailable = False
        assert (await ended(event_id)).status == "cancelled"
        assert len(env.stops) == 1
        assert "private-controlled-storage-failure" not in caplog.text
    finally:
        unavailable = False
        await runner.stop()
