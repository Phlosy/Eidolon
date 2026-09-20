"""Cross-session admission uses original remote facts, with transport-only doubles."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from test_m3_management_runner import admitted, ended, finish, source
from test_m3_management_runner import world as _world
from test_m3_runtime_turns import hermes_transport

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.models.base import utcnow
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.project import Task, WorkSession
from app.models.provider import ModelBinding
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import project as project_repo
from app.runtimes import probe
from app.runtimes.base import RuntimeProtocolError, RuntimeSession, accept_remote_run
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.gateway import gateway
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.services import tasks
from app.work import dispatch, management_dispatch, reviews, runtime_admission, telemetry
from app.work.management_runner import ManagementRunner
from app.workflow.orchestrator import Orchestrator

world = _world


@pytest.fixture(params=["hermes", "openclaw"])
def original(db, world, monkeypatch, request):
    kind = request.param
    for actor in (world.manager, world.reviewer):
        actor.runtime_type = kind
        row = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == actor.id))
        row.runtime_type, row.workspace_path = kind, actor.workspace_path
        row.metadata_json = {
            **row.metadata_json,
            "runtime_probe": {
                **row.metadata_json["runtime_probe"],
                "instance_fingerprint": probe.instance_fingerprint(row),
            },
        }
    world.project.status = "in_progress"
    db.commit()
    env = SimpleNamespace(
        world=world,
        kind=kind,
        adapter={"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[kind](),
        state="running",
        calls=[],
        sends=[],
        starts=[],
        release=asyncio.Event(),
        entered=asyncio.Event(),
        on_query=None,
    )
    env.release.set()
    monkeypatch.setitem(gateway._adapters, kind, env.adapter)

    def history(*, accepted=True, attempt_count=1):
        actor = world.manager
        row = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == actor.id))
        task = project_repo.create_task(
            db,
            project_id=world.project.id,
            title="Original task",
            status="in_progress",
            assignee_id=actor.id,
        )
        work = project_repo.create_work_session(
            db,
            task_id=task.id,
            employee_id=actor.id,
            runtime_type=kind,
            runtime_instance_id=row.id,
            provider_id=db.get(ModelBinding, row.model_binding_id).provider_id,
            model="offline-model",
            started_at=utcnow(),
        )
        runtime = RuntimeSession(
            f"old-session-{work.id}", resolve_container_instance(actor.id, kind), task.id
        )
        work.runtime_session_ref = runtime.id
        db.commit()
        recorders = []
        for index in range(attempt_count):
            recorder = telemetry.prepare_attempt(
                work.id,
                runtime,
                "original input",
                request_key=f"worker-{index}",
                kind="worker" if attempt_count == 1 else "controlled",
            )
            if accepted:
                accept_remote_run(
                    runtime,
                    f"original-run-{work.id}-{index}",
                    (
                        "agent:main:main"
                        if attempt_count == 1
                        else f"agent:main:eidolon-{runtime.id}"
                    )
                    if kind == "openclaw"
                    else runtime.id,
                )
            recorder.finish_host("failed", "stream_ended")
            recorders.append(recorder)
        work.status, work.ended_at = "failed", utcnow()
        task.status = "blocked"
        db.commit()
        db.refresh(work)
        return SimpleNamespace(
            task=task, work=work, runtime=runtime, recorder=recorder, recorders=recorders, row=row
        )

    env.history = history

    async def original_status(run_id):
        env.calls.append(run_id)
        env.entered.set()
        await env.release.wait()
        if env.on_query is not None:
            env.on_query()
        if env.state == "unavailable":
            raise OSError("private-query-error")
        return env.state

    async def respond(req):
        if req.method == "POST":
            assert req.url.path == "/v1/runs", "admission must never stop a remote run"
            body = json.loads(req.content)
            env.sends.append(body)
            return httpx.Response(202, json={"run_id": f"new-run-{len(env.sends)}"})
        run_id = req.url.path.split("/")[3]
        if run_id.startswith("original-run-"):
            state = await original_status(run_id)
            return httpx.Response(
                200,
                json={
                    "run_id": "wrong-run" if state == "wrong_run" else run_id,
                    "status": state,
                },
            )
        return httpx.Response(
            200,
            json={
                "run_id": run_id,
                "status": "completed",
                "output": json.dumps(finish()),
            },
        )

    class Gateway:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            if method == "chat.send":
                env.sends.append(params)
                self.sent = params
                return {"runId": params["idempotencyKey"]}
            assert method == "agent.wait", "admission must never stop a remote run"
            run_id = params["runId"]
            if run_id.startswith("original-run-"):
                state = await original_status(run_id)
                return {"runId": "wrong-run" if state == "wrong_run" else run_id, "status": state}
            await self.events.put(
                {
                    "event": "chat",
                    "payload": {
                        "runId": run_id,
                        "sessionKey": self.sent["sessionKey"],
                        "state": "final",
                        "message": {"role": "assistant", "content": json.dumps(finish())},
                    },
                }
            )
            return {"runId": run_id, "status": "ok"}

    if kind == "hermes":
        hermes_transport(monkeypatch, respond)
    else:
        monkeypatch.setattr(env.adapter, "_connect", lambda instance: Gateway())

    async def start(employee):
        with SessionLocal() as independent:
            env.starts.append(
                list(
                    independent.scalars(
                        select(RuntimeAttempt.status).where(
                            RuntimeAttempt.remote_run_id.like("original-%")
                        )
                    )
                )
            )
        # The new Worker claim is the assertion target; stop before any new model or Docker call.
        raise OSError("offline-new-start-boundary")

    monkeypatch.setattr(gateway, "get_or_create_instance", start)
    return env


def resumed(db, original, *, target="same_task", accepted=True):
    old = original.history(accepted=accepted)
    task = old.task
    if target == "other_task":
        task = project_repo.create_task(
            db,
            project_id=old.task.project_id,
            title="Next task",
            status="todo",
            assignee_id=original.world.manager.id,
        )
    else:
        tasks.transition_task(db, task, "todo")
        if target == "reassigned":
            task.assignee_id = original.world.reviewer.id
    db.commit()
    return old, task


@pytest.mark.parametrize("target", ["same_task", "other_task", "reassigned"])
def test_dispatch_reports_original_pending_execution(db, original, target):
    old, task = resumed(db, original, target=target)
    result = dispatch.evaluate_dispatch(db, task)
    assert not result.dispatchable
    assert result.reasons == ("runtime_unavailable",)
    assert result.readiness["unresolved_attempt_ids"] == [old.recorder.attempt_id]
    assert result.needs_management and result.event_type == "task.runtime_unavailable"


@pytest.mark.parametrize("state", ["running", "unknown", "unavailable", "wrong_run"])
async def test_unknown_original_run_never_creates_a_new_worker(db, original, state, caplog):
    old, task = resumed(db, original)
    original.state = state
    await Orchestrator()._run_task(task.id)
    db.expire_all()
    assert original.starts == [], "unknown original run was replaced with a new execution"
    assert original.calls == [old.runtime.remote_run_id] and original.sends == []
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
    assert db.get(RuntimeAttempt, old.recorder.attempt_id).ended_at is None
    assert task.status == "todo" and old.work.status == "failed"
    assert "private-query-error" not in caplog.text


@pytest.mark.parametrize("state", ["completed", "aborted"])
@pytest.mark.parametrize("sweep", [False, True])
async def test_original_terminal_is_saved_before_fresh_worker_admission(
    db, original, state, sweep, monkeypatch
):
    old, task = resumed(db, original, target="reassigned")
    old_end = old.work.ended_at
    original.state = state
    runner = Orchestrator()
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    if sweep:
        await runner._dispatch_pending()
        await asyncio.gather(*list(runner._running.values()))
    else:
        await runner._run_task(task.id)
    db.expire_all()
    terminal = "completed" if state == "completed" else "failed"
    assert original.calls == [old.runtime.remote_run_id]
    assert original.starts == [[terminal]] and original.sends == []
    row = db.get(RuntimeAttempt, old.recorder.attempt_id)
    assert row.status == terminal and row.ended_at is not None
    assert row.host_outcome == "failed" and row.stop_requested_at is None
    assert old.work.status == "failed" and old.work.ended_at == old_end
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 2
    assert task.rework_count == 0


async def test_missing_original_run_reference_stays_unknown(db, original):
    old, task = resumed(db, original, accepted=False)
    await Orchestrator()._run_task(task.id)
    assert original.starts == original.calls == original.sends == []
    db.expire_all()
    assert db.get(RuntimeAttempt, old.recorder.attempt_id).ended_at is None
    assert task.status == "todo"


@pytest.mark.parametrize("change", ["connection", "task_cancelled"])
async def test_query_completion_revalidates_original_identity_and_current_authority(
    db, original, change
):
    old, task = resumed(db, original)
    original.state = "completed"

    def mutate():
        with SessionLocal() as independent:
            if change == "connection":
                independent.get(RuntimeInstance, old.row.id).internal_host = "replacement.invalid"
            else:
                tasks.transition_task(independent, independent.get(Task, task.id), "cancelled")
            independent.commit()

    original.on_query = mutate
    await Orchestrator()._run_task(task.id)
    db.expire_all()
    assert original.calls == [old.runtime.remote_run_id]
    assert original.starts == original.sends == []
    row = db.get(RuntimeAttempt, old.recorder.attempt_id)
    assert (row.ended_at is None) is (change == "connection")
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1


def test_prepare_attempt_rechecks_other_sessions_before_first_send(db, original):
    old, task = resumed(db, original, target="other_task")
    task.status = "in_progress"
    work = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=original.world.manager.id,
        runtime_type=original.kind,
        runtime_instance_id=old.row.id,
        provider_id=old.work.provider_id,
        model="offline-model",
        started_at=utcnow(),
    )
    runtime = RuntimeSession("claimed-before-recheck", old.runtime.instance, task.id)
    work.runtime_session_ref = runtime.id
    db.commit()
    with pytest.raises(RuntimeProtocolError, match="execution_previous_run_unresolved"):
        telemetry.prepare_attempt(
            work.id, runtime, "new input", request_key="worker", kind="worker"
        )
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1
    assert original.sends == []


@pytest.mark.parametrize("same_actor", [True, False])
def test_management_admission_respects_remote_occupancy_without_blocking_other_managers(
    db, original, same_actor
):
    old = original.history()
    if not same_actor:
        original.world.project.management_employee_id = original.world.reviewer.id
        original.world.project.management_person_id = original.world.reviewer.person_id
        db.commit()
    source_id = source(original.world)
    claim = management_dispatch.claim_source(source_id, enabled=True, budget_available=True)
    assert claim.created is not same_actor
    if same_actor:
        assert claim.dispatch.waiting_reason == "recipient_busy"
        assert claim.dispatch.details["unresolved_attempt_ids"] == [old.recorder.attempt_id]


@pytest.mark.parametrize("state", ["running", "completed"])
async def test_management_runner_queries_before_claim_and_send(db, original, state):
    old = original.history()
    original.state = state
    source_id = source(original.world)
    runner = ManagementRunner(enabled=True, budget_available=admitted, budget_check=admitted)
    runner._running = True
    try:
        await runner._admit_and_run(source_id)
    finally:
        await runner.stop()
    db.expire_all()
    observed = db.scalar(
        select(ManagementDispatch).where(ManagementDispatch.source_event_id == source_id)
    )
    assert original.calls == [old.runtime.remote_run_id]
    assert (observed.work_session_id is None) is (state == "running")
    assert len(original.sends) == (0 if state == "running" else 1)
    assert db.get(RuntimeAttempt, old.recorder.attempt_id).status == (
        "unknown" if state == "running" else "completed"
    )


async def test_management_periodic_scan_reaches_original_query(db, original):
    old = original.history()
    original.state = "completed"
    source_id = source(original.world)
    runner = ManagementRunner(
        enabled=True, budget_available=admitted, budget_check=admitted, poll_interval=0.02
    )
    runner.start()
    try:
        await ended(source_id)
    finally:
        await runner.stop()
    assert original.calls == [old.runtime.remote_run_id]
    assert len(original.sends) == 1


async def test_cancellation_during_original_query_never_claims(db, original):
    _, task = resumed(db, original)
    original.release.clear()
    pending = asyncio.create_task(Orchestrator()._run_task(task.id))
    try:
        await asyncio.wait_for(original.entered.wait(), 0.5)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    db.expire_all()
    assert original.starts == original.sends == []
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
    assert task.status == "todo"


async def test_mock_override_cannot_hide_a_known_real_pending_run(db, original, monkeypatch):
    _, task = resumed(db, original)
    monkeypatch.setattr(settings, "runtime_mode", "mock")
    await Orchestrator()._run_task(task.id)
    assert original.starts == original.calls == original.sends == []


async def test_admission_queries_the_original_runtime_despite_new_actor_preference(db, original):
    old, task = resumed(db, original)
    original.world.manager.runtime_type = "mock"
    db.commit()
    original.state = "completed"
    await Orchestrator()._run_task(task.id)
    assert original.calls == [old.runtime.remote_run_id]
    assert original.starts == [["completed"]]


async def test_query_timeout_remains_unknown_and_does_not_admit(db, original, monkeypatch):
    old, task = resumed(db, original)
    original.release.clear()
    monkeypatch.setattr(runtime_admission, "QUERY_TIMEOUT", 0.01)
    await asyncio.wait_for(Orchestrator()._run_task(task.id), 1)
    db.expire_all()
    assert original.calls == [old.runtime.remote_run_id]
    assert original.starts == original.sends == []
    assert db.get(RuntimeAttempt, old.recorder.attempt_id).ended_at is None
    assert task.status == "todo"


async def test_bounded_query_batch_cannot_admit_while_another_original_is_unknown(db, original):
    old = original.history(attempt_count=3)
    tasks.transition_task(db, old.task, "todo")
    db.commit()
    original.state = "completed"
    runner = Orchestrator()
    await runner._run_task(old.task.id)
    assert len(original.calls) == 2 and original.starts == []
    await runner._run_task(old.task.id)
    assert len(original.calls) == 3 and len(set(original.calls)) == 3
    assert original.starts == [["completed", "completed", "completed"]]


async def test_sweep_owns_one_preflight_per_employee_and_shutdown_drains_it(
    db, original, monkeypatch
):
    _, task = resumed(db, original, target="other_task")
    project_repo.create_task(
        db,
        project_id=task.project_id,
        title="Also ready",
        status="todo",
        assignee_id=task.assignee_id,
    )
    db.commit()
    original.release.clear()
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    runner = Orchestrator()
    await runner._dispatch_pending()
    owned = list(runner._running.values())
    try:
        await asyncio.wait_for(original.entered.wait(), 1)
        assert len(owned) == 1
        pending = [t for t in asyncio.all_tasks() if t.get_name().startswith("work-session-task-")]
        assert pending == owned
    finally:
        await runner.stop()
    assert all(job.done() for job in owned)
    assert original.starts == original.sends == []


def test_review_of_same_task_waits_for_original_worker_even_with_another_reviewer(db, original):
    old = original.history()
    old.task.status = "in_review"
    request = reviews.open_review_request(
        db,
        task=old.task,
        requester_employee_id=original.world.manager.id,
        reviewer_employee_id=original.world.reviewer.id,
        commit=False,
    )
    db.commit()
    event = db.scalar(
        select(Event).where(Event.type == "task.review_named", Event.task_id == old.task.id)
    )
    claim = management_dispatch.claim_source(event.id, enabled=True, budget_available=True)
    assert not claim.created
    assert claim.dispatch.review_request_id == request.id
    assert claim.dispatch.details["unresolved_attempt_ids"] == [old.recorder.attempt_id]


async def test_budget_is_rechecked_after_original_query_before_new_management_claim(db, original):
    old = original.history()
    original.state = "completed"
    source_id = source(original.world)
    admissions = []

    async def budget(identifier):
        admissions.append(identifier)
        return len(admissions) == 1

    runner = ManagementRunner(enabled=True, budget_available=budget, budget_check=admitted)
    runner._running = True
    try:
        await runner._admit_and_run(source_id)
    finally:
        await runner.stop()
    observed = management_dispatch.inspect_source(source_id, enabled=True)
    assert original.calls == [old.runtime.remote_run_id] and admissions == [source_id, source_id]
    assert observed.work_session_id is None and observed.waiting_reason == "budget_unavailable"
    assert original.sends == []


async def test_company_scope_prevents_query_and_exposure_of_original_attempts(db, original):
    old = original.history()
    token = set_request_identity(RequestIdentity(1, 9999, "owner", 1))
    try:
        with pytest.raises(RuntimeProtocolError, match="execution_scope_unavailable"):
            runtime_admission.unresolved_attempt_ids(
                db,
                company_id=original.world.project.company_id,
                employee_id=original.world.manager.id,
                task_id=old.task.id,
            )
        await runtime_admission.reconcile_attempts(
            [old.recorder.attempt_id], adapter_for=gateway.adapter_for
        )
    finally:
        reset_request_identity(token)
    assert original.calls == original.sends == original.starts == []


async def test_original_review_run_also_fences_a_requeued_worker_with_another_actor(db, original):
    event_id, task_id, review_id = source(original.world, review=True)
    claim = management_dispatch.claim_source(event_id, enabled=True, budget_available=True)
    assert claim.created
    db.expire_all()
    work = db.get(WorkSession, claim.dispatch.work_session_id)
    runtime = RuntimeSession(
        "old-review-session", resolve_container_instance(work.employee_id, original.kind), None
    )
    work.runtime_session_ref = runtime.id
    db.commit()
    recorder = telemetry.prepare_attempt(
        work.id, runtime, "inspect review", request_key="turn-1", kind="controlled"
    )
    accept_remote_run(
        runtime,
        "original-run-review",
        f"agent:main:eidolon-{runtime.id}" if original.kind == "openclaw" else runtime.id,
    )
    recorder.finish_host("failed", "stream_ended")
    work.status, work.ended_at = "failed", utcnow()
    task = db.get(Task, task_id)
    tasks.transition_task(db, task, "blocked")
    tasks.transition_task(db, task, "todo")
    task.assignee_id = original.world.manager.id
    db.commit()
    evaluation = dispatch.evaluate_dispatch(db, task)
    assert not evaluation.dispatchable
    assert evaluation.readiness["unresolved_attempt_ids"] == [recorder.attempt_id]
    original.state = "completed"
    await Orchestrator()._run_task(task_id)
    db.expire_all()
    assert original.calls == ["original-run-review"] and original.starts == [["completed"]]
    review = db.get(ReviewRequest, review_id)
    assert review.status == "open" and review.verdict is None


async def test_unpersisted_terminal_cannot_release_the_admission_guard(db, original, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    old, task = resumed(db, original)
    original.state = "completed"

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("private-terminal-storage-error")

    monkeypatch.setattr(telemetry.AttemptRecorder, "terminal", unavailable)
    await Orchestrator()._run_task(task.id)
    db.expire_all()
    assert original.calls == [old.runtime.remote_run_id]
    assert original.starts == original.sends == []
    assert db.get(RuntimeAttempt, old.recorder.attempt_id).ended_at is None
    assert task.status == "todo"
