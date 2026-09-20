"""Offline runner evidence; only the adapter's model responses are scripted."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sql_events
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.events.bus import EventBus
from app.events.engine import EventEngine
from app.models.decision import DecisionRecord, ToolAudit
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.organization import Employee
from app.models.project import Project, Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance
from app.providers.secrets.store import get_secret_store
from app.repositories import project as project_repo
from app.runtimes import probe
from app.runtimes.base import RuntimeReply, RuntimeSession
from app.work import authority, contracts, reviews
from app.work.runtime_conversation import ConversationLimits


@pytest.fixture()
def world(db, default_company_id, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "auto")
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    manager = db.scalar(select(Employee).where(Employee.slug == "alice"))
    reviewer = db.scalar(select(Employee).where(Employee.slug == "dana"))
    for actor in (manager, reviewer):
        actor.runtime_type, actor.status = "hermes", "idle"
        workspace = tmp_path / actor.slug
        workspace.mkdir()
        actor.workspace_path = str(workspace)
        provider = Provider(company_id=actor.company_id, name=f"Offline {actor.slug}")
        db.add(provider)
        db.flush()
        for binding in db.scalars(select(ModelBinding).where(ModelBinding.employee_id == actor.id)):
            binding.is_primary = False
        db.flush()
        binding = ModelBinding(
            employee_id=actor.id,
            person_id=actor.person_id,
            provider_id=provider.id,
            model="offline-model",
            is_primary=True,
        )
        db.add(binding)
        db.flush()
        row = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == actor.id))
        row.runtime_type, row.deployment_mode, row.status = "hermes", "docker", "running"
        row.health_status, row.model_binding_id = "healthy", binding.id
        row.container_id = row.container_name = f"offline-container-{actor.id}"
        row.resource_key = f"offline-resource-{actor.id}"
        row.internal_host, row.internal_port = "control.invalid", 8765
        fingerprint = probe.model_fingerprint(provider, binding.model)
        row.metadata_json = {
            "api_key_ref": get_secret_store().store(db, "private-runner-control-token"),
            "applied_model_fingerprint": fingerprint,
        }
        row.metadata_json = {
            **row.metadata_json,
            "runtime_probe": {
                "instance_fingerprint": probe.instance_fingerprint(row),
                "facts": {
                    name: probe.fact("passed", "offline_test") for name in probe.RUNTIME_FACT_NAMES
                },
            },
            **{
                name + "_probe": {
                    "fingerprint": fingerprint,
                    "fact": probe.fact("passed", "offline_test"),
                }
                for name in ("provider_connectivity", "selected_model")
            },
        }
    position = authority.resolve_actor_authority(db, reviewer.id)
    authority.grant_authority(
        db,
        position_definition_id=position.position_definition_id,
        kind=contracts.AuthorityKind.accept_delivery,
        commit=False,
    )
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Runner target",
        goal="Raw project goal is available only through registered reads",
        status="waiting_for_management",
        work_mode="managed",
        planning_fixture="none",
        management_employee_id=manager.id,
        management_person_id=manager.person_id,
    )
    db.commit()
    return SimpleNamespace(manager=manager, reviewer=reviewer, project=project)


def source(world, *, review=False):
    with SessionLocal() as db:
        if review:
            task = project_repo.create_task(
                db, project_id=world.project.id, title="Review facts", status="in_review"
            )
            request = reviews.open_review_request(
                db,
                task=task,
                requester_employee_id=world.manager.id,
                reviewer_employee_id=world.reviewer.id,
                commit=False,
            )
            db.commit()
            event = db.scalar(
                select(Event).where(Event.type == "task.review_named", Event.task_id == task.id)
            )
            return event.id, task.id, request.id
        event = Event(
            type="project.replan_required",
            company_id=world.project.company_id,
            project_id=world.project.id,
            payload={"goal": "Untrusted payload must never enter initial prompts"},
        )
        db.add(event)
        db.commit()
        return event.id


def finish(summary="Saved receipt acknowledged"):
    return {"version": 1, "kind": "finish", "operation_key": "finish", "summary": summary}


def decision(world, *, review_id=None):
    return {
        "version": 1,
        "kind": "decision",
        "operation_key": "apply-decision",
        "decision": {
            "decision_type": "accept_delivery" if review_id else "decompose_project",
            "reason": "Choose based on inspected facts",
            "scope": f"project:{world.project.id}",
            "actions": [
                {
                    "tool": "submit_review_verdict" if review_id else "create_task",
                    "args": (
                        {"review_request_id": review_id, "verdict": "PASS", "notes": "Inspected"}
                        if review_id
                        else {"project_id": world.project.id, "title": "Model selected next work"}
                    ),
                }
            ],
        },
    }


class ScriptedAdapter:
    type = "hermes"
    implemented = True
    supports_controlled_turn = True

    def __init__(self, *replies):
        self.replies = iter(replies)
        self.prompts, self.contexts, self.sessions = [], [], []
        self.create_hook = None
        self.cancelled = []

    async def create_session(self, instance, context):
        self.contexts.append(context)
        session = RuntimeSession(f"runner-{len(self.contexts)}", instance, context.task_id)
        self.sessions.append(session)
        if self.create_hook:
            await self.create_hook(session)
        return session

    async def complete_turn(self, session, prompt, *, request_id, timeout):
        self.prompts.append(json.loads(prompt))
        session.remote_run_id = f"offline-run-{len(self.prompts)}"
        reply = next(self.replies)
        if callable(reply):
            reply = await reply(session, self.prompts[-1])
        return RuntimeReply(
            session.id,
            request_id,
            session.remote_run_id,
            reply if isinstance(reply, str) else json.dumps(reply),
        )

    async def cancel_task(self, session):
        self.cancelled.append(session.remote_run_id)


async def admitted(*args):
    return True


def runner_for(monkeypatch, adapter, **options):
    from app.work import management_runner

    monkeypatch.setattr(management_runner.gateway, "adapter_for", lambda kind: adapter)
    return management_runner.ManagementRunner(
        session_factory=SessionLocal,
        enabled=options.pop("enabled", True),
        budget_available=options.pop("budget_available", admitted),
        budget_check=options.pop("budget_check", admitted),
        poll_interval=options.pop("poll_interval", 0.02),
        **options,
    )


async def eventually(read, predicate=bool):
    async with asyncio.timeout(3):
        while True:
            with SessionLocal() as db:
                value = read(db)
            if predicate(value):
                return value
            await asyncio.sleep(0.01)


async def ended(source_id):
    return await eventually(
        lambda db: db.scalar(
            select(WorkSession)
            .join(ManagementDispatch, ManagementDispatch.work_session_id == WorkSession.id)
            .where(
                ManagementDispatch.source_event_id == source_id, WorkSession.ended_at.is_not(None)
            )
        )
    )


@pytest.mark.parametrize("review", [False, True])
async def test_startup_source_runs_actual_bridge_decision_and_acknowledges_receipt(
    db, world, monkeypatch, review
):
    event_id, task_id, request_id = (
        source(world, review=True) if review else (source(world), None, None)
    )
    read = {
        "version": 1,
        "kind": "read",
        "operation_key": "inspect",
        "tool": "inspect_task_review" if review else "inspect_project",
        "args": {"task_id": task_id} if review else {"project_id": world.project.id},
    }
    adapter = ScriptedAdapter(read, decision(world, review_id=request_id), finish())
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    runner.start()
    try:
        work = await ended(event_id)
        assert work.status == "completed", work.error
        assert work.purpose == ("review" if review else "management") and work.task_id is None
        assert work.employee_id == (world.reviewer.id if review else world.manager.id)
        assert adapter.contexts[0].task_id is None
        assert adapter.prompts[-1]["feedback"]["result"]["decision_status"] == "APPLIED"
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
        assert db.scalar(select(func.count()).select_from(ToolAudit)) == 2
        if review:
            assert db.get(ReviewRequest, request_id).verdict == "PASS"
            assert db.get(Task, task_id).status == "done"
            assert str(task_id) in adapter.prompts[0]["goal"]
            assert "inspect_task_review" in adapter.prompts[0]["goal"]
        else:
            assert db.scalar(select(Task).where(Task.title == "Model selected next work"))
        initial = json.dumps(adapter.prompts[0])
        assert "Untrusted payload" not in initial and "Raw project goal" not in initial
        assert "private-runner-control-token" not in json.dumps(adapter.prompts)
    finally:
        await runner.stop()
    runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()
    assert len(adapter.contexts) == 1


async def test_bus_nudges_durable_scan_without_trusting_broadcast_payload(world, monkeypatch):
    adapter = ScriptedAdapter(decision(world), finish())
    runner = runner_for(monkeypatch, adapter, poll_interval=60)
    event_bus = EventBus()
    event_bus.attach_loop()
    engine = EventEngine(event_bus)
    runner.register(engine)
    runner.register(engine)
    await engine.start()
    runner.start()
    await asyncio.sleep(0.02)
    try:
        event_bus.publish(
            "project.replan_required",
            {"project_id": -1, "employee_id": -1},
            company_id=world.project.company_id,
            project_id=world.project.id,
        )
        with SessionLocal() as db:
            event_id = db.scalar(select(Event.id).where(Event.type == "project.replan_required"))
        event_bus._broadcast(
            "project.replan_required", {"project_id": -1, "employee_id": -1}, -1, event_id=-1
        )
        assert (await ended(event_id)).status == "completed"
        assert engine.stats()["registered_handlers"] == 1
        assert adapter.contexts[0].project_id == world.project.id
        assert adapter.prompts[1]["feedback"]["result"]["decision_status"] == "APPLIED"
        with SessionLocal() as db:
            assert db.scalar(select(Task).where(Task.title == "Model selected next work"))
    finally:
        await runner.stop()
        await engine.stop()


@pytest.mark.parametrize(
    "mode", ["disabled", "missing", "missing_turn", "denied", "error", "timeout"]
)
async def test_budget_gate_is_durable_and_never_touches_adapter(world, monkeypatch, mode):
    event_id = source(world)
    adapter = ScriptedAdapter(finish())

    async def budget(source_id):
        if mode == "error":
            raise RuntimeError("private admission details")
        if mode == "timeout":
            await asyncio.Event().wait()
        return False

    runner = runner_for(
        monkeypatch,
        adapter,
        enabled=mode != "disabled",
        budget_available=None if mode == "missing" else budget,
        budget_check=None if mode == "missing_turn" else admitted,
        admission_timeout=0.02,
    )
    runner.start()
    try:
        row = await eventually(
            lambda db: db.scalar(
                select(ManagementDispatch).where(ManagementDispatch.source_event_id == event_id)
            ),
            lambda row: row and row.waiting_reason is not None,
        )
        await asyncio.sleep(0.05)
        assert row.waiting_reason == ("disabled" if mode == "disabled" else "budget_unavailable")
        assert row.work_session_id is None
        assert not adapter.contexts and not adapter.prompts
    finally:
        await runner.stop()


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("response", ["finish", "natural_text", "exit"])
async def test_no_decision_never_advances_business(world, monkeypatch, review, response):
    event_id, task_id, review_id = (
        source(world, review=True) if review else (source(world), None, None)
    )

    async def exited(session, prompt):
        raise RuntimeError("Runtime exited without a designated response")

    reply = {"finish": finish(), "natural_text": "PASS, looks good", "exit": exited}[response]
    adapter = ScriptedAdapter(reply)
    runner = runner_for(monkeypatch, adapter, limits=ConversationLimits(max_rounds=1))
    runner.start()
    try:
        work = await ended(event_id)
        assert work.status == ("completed" if response == "finish" else "failed")
        with SessionLocal() as db:
            assert db.get(Project, world.project.id).status == "waiting_for_management"
            assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
            assert db.scalar(select(func.count()).select_from(Task)) == int(review)
            if review:
                assert db.get(Task, task_id).status == "in_review"
                assert db.get(ReviewRequest, review_id).verdict is None
    finally:
        await runner.stop()


async def test_duplicate_review_events_and_competing_runners_share_one_execution(
    world, monkeypatch
):
    event_id, task_id, review_id = source(world, review=True)
    with SessionLocal() as db:
        duplicate = Event(
            type="task.review_named",
            company_id=world.project.company_id,
            project_id=world.project.id,
            task_id=task_id,
            payload={"review_request_id": review_id},
        )
        db.add(duplicate)
        db.commit()
        duplicate_id = duplicate.id
    adapter = ScriptedAdapter(finish())
    first, second = runner_for(monkeypatch, adapter), runner_for(monkeypatch, adapter)
    first.start()
    second.start()
    try:
        await ended(event_id)
        duplicate = await eventually(
            lambda db: db.scalar(
                select(ManagementDispatch).where(ManagementDispatch.source_event_id == duplicate_id)
            ),
            lambda row: row and row.resolution == "duplicate",
        )
        assert duplicate.work_session_id is None and duplicate.duplicate_of_id is not None
        assert len(adapter.contexts) == 1
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
    finally:
        await asyncio.gather(first.stop(), second.stop())


async def test_waiting_sources_rotate_and_conversations_are_bounded(world, monkeypatch):
    with SessionLocal() as db:
        for index in range(5):
            waiting = project_repo.create_project(
                db, company_id=world.project.company_id, name=f"No recipient {index}"
            )
            db.add(
                Event(
                    type="project.replan_required",
                    company_id=world.project.company_id,
                    project_id=waiting.id,
                    payload={},
                )
            )
        db.commit()
    event_id = source(world)
    review_id, _task_id, _request_id = source(world, review=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(session, prompt):
        entered.set()
        await release.wait()
        return finish()

    adapter = ScriptedAdapter(held, finish())
    runner = runner_for(monkeypatch, adapter, max_concurrency=1, batch_size=2)
    runner.start()
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.sleep(0.08)
        assert len(adapter.contexts) == 1
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
            waiting = list(
                db.scalars(
                    select(ManagementDispatch).where(
                        ManagementDispatch.waiting_reason == "recipient_missing"
                    )
                )
            )
            assert len(waiting) == 5 and all(row.checked_at for row in waiting)
        release.set()
        assert (await ended(event_id)).status == "completed"
        assert (await ended(review_id)).status == "completed"
        assert len(adapter.contexts) == 2
    finally:
        release.set()
        await runner.stop()


@pytest.mark.parametrize("stage", ["admission", "setup", "turn_budget", "turn"])
@pytest.mark.parametrize("change", ["inactive", "cancelled_project"])
async def test_actor_and_target_changes_across_awaits_block_execution(
    world, monkeypatch, stage, change
):
    event_id = source(world)

    def mutate():
        with SessionLocal() as db:
            if change == "inactive":
                db.get(Employee, world.manager.id).lifecycle_status = "released"
            else:
                db.get(Project, world.project.id).status = "cancelled"
            db.commit()

    async def admission(source_id):
        if stage == "admission":
            mutate()
        return True

    async def setup(session):
        if stage == "setup":
            mutate()

    async def budget(*args):
        if stage == "turn_budget":
            mutate()
        return True

    async def model(session, prompt):
        mutate()
        return decision(world)

    adapter = ScriptedAdapter(model if stage == "turn" else decision(world))
    adapter.create_hook = setup
    runner = runner_for(monkeypatch, adapter, budget_available=admission, budget_check=budget)
    runner.start()
    try:
        if stage == "admission":
            await eventually(
                lambda db: db.scalar(
                    select(ManagementDispatch).where(ManagementDispatch.source_event_id == event_id)
                ),
                lambda row: (
                    row
                    and (row.waiting_reason == "recipient_inactive" or row.resolution == "obsolete")
                ),
            )
            assert not adapter.contexts
        else:
            assert (await ended(event_id)).status == "failed"
        assert len(adapter.prompts) == int(stage == "turn")
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
            assert db.scalar(select(func.count()).select_from(Task)) == 0
    finally:
        await runner.stop()


@pytest.mark.parametrize("stage", ["admission", "setup", "turn_budget", "turn"])
@pytest.mark.parametrize("late", [False, True])
async def test_stop_fences_late_results_and_drains_only_owned_sessions(
    world, monkeypatch, stage, late
):
    event_id = source(world)
    entered = asyncio.Event()

    async def held(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not late:
                raise
        return decision(world) if stage == "turn" else True

    adapter = ScriptedAdapter(held if stage == "turn" else finish())
    if stage == "setup":
        adapter.create_hook = held
    runner = runner_for(
        monkeypatch,
        adapter,
        budget_available=held if stage == "admission" else admitted,
        budget_check=held if stage == "turn_budget" else admitted,
    )
    runner.start()
    await asyncio.wait_for(entered.wait(), 3)
    # start while stop is draining must not admit an overlapping generation.
    stopping = asyncio.create_task(runner.stop())
    await asyncio.sleep(0)
    runner.start()
    await asyncio.gather(stopping, runner.stop())
    await runner.stop()
    with SessionLocal() as db:
        work = db.scalar(select(WorkSession))
        if stage == "admission":
            assert work is None and not adapter.contexts
            row = db.scalar(
                select(ManagementDispatch).where(ManagementDispatch.source_event_id == event_id)
            )
            assert row.waiting_reason == "budget_unavailable"
        else:
            assert work.status == "cancelled" and work.ended_at is not None
            if stage == "turn" and not late:
                assert "remote_stop_unconfirmed" in work.error
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert db.get(Project, world.project.id).status == "waiting_for_management"
    assert not runner._jobs and runner._scanner is None
    assert len(adapter.prompts) == int(stage == "turn")


async def test_existing_running_session_is_not_restarted_or_closed(world, monkeypatch):
    from app.work.management_dispatch import claim_source

    event_id = source(world)
    claim = claim_source(event_id, enabled=True, budget_available=True)
    assert claim.created
    adapter = ScriptedAdapter(finish())
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()
    with SessionLocal() as db:
        work = db.get(WorkSession, claim.dispatch.work_session_id)
        assert work.status == "running" and work.ended_at is None
    assert not adapter.contexts


async def test_finalization_preserves_a_concurrently_terminal_session(world, monkeypatch):
    event_id = source(world)

    async def externally_closed(session, prompt):
        from app.models.base import utcnow

        with SessionLocal() as db:
            work = db.get(WorkSession, prompt["scope"]["work_session_id"])
            work.status, work.ended_at = "failed", utcnow()
            work.error, work.summary = "external_cancellation", "Original terminal facts"
            db.commit()
        return finish("Late summary must not overwrite")

    runner = runner_for(monkeypatch, ScriptedAdapter(externally_closed))
    runner.start()
    try:
        work = await ended(event_id)
    finally:
        await runner.stop()
    assert work.status == "failed" and work.error == "external_cancellation"
    assert work.summary == "Original terminal facts"


async def test_setup_timeout_and_per_source_exception_do_not_starve_other_sources(
    world, monkeypatch
):
    from app.work import management_runner

    first_id = source(world)
    second_id, _task_id, _review_id = source(world, review=True)
    inspect = management_runner.dispatch.inspect_source

    def failing_inspection(source_id, **kwargs):
        if source_id == first_id:
            raise RuntimeError("isolated source failure")
        return inspect(source_id, **kwargs)

    monkeypatch.setattr(management_runner.dispatch, "inspect_source", failing_inspection)
    adapter = ScriptedAdapter(finish())

    async def never_ready(session):
        await asyncio.Event().wait()

    adapter.create_hook = never_ready
    runner = runner_for(monkeypatch, adapter, startup_timeout=0.02)
    runner.start()
    try:
        work = await ended(second_id)
        assert work.status == "failed" and work.error == "runtime_setup_timeout"
        assert not adapter.prompts
    finally:
        await runner.stop()


async def test_repeated_stop_waiter_cancellation_still_drains_owned_cleanup(world, monkeypatch):
    event_id = source(world)
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def model(session, prompt):
        entered.set()
        await asyncio.Event().wait()

    async def cancel(session):
        cancelling.set()
        await release.wait()
        session.cancel_confirmed = True

    adapter = ScriptedAdapter(model)
    adapter.cancel_task = cancel
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    await asyncio.wait_for(entered.wait(), 3)
    stopping = asyncio.create_task(runner.stop())
    try:
        await asyncio.wait_for(cancelling.wait(), 3)
        for _ in range(3):
            stopping.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not stopping.done(), "Cancellation must wait for the owned remote cleanup"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stopping, 3)
        work = await ended(event_id)
        assert work.status == "cancelled" and work.error == "runner_cancelled"
        assert not runner._jobs and runner._scanner is None
    finally:
        release.set()
        await runner.stop()


@pytest.mark.parametrize("termination", ["cancel", "timeout"])
async def test_unknown_remote_submission_never_claims_confirmed_stop(
    world, monkeypatch, termination
):
    event_id = source(world)
    entered = asyncio.Event()

    async def submission_unknown(session, prompt):
        session.remote_run_id = None
        entered.set()
        await asyncio.Event().wait()

    adapter = ScriptedAdapter(submission_unknown)
    runner = runner_for(
        monkeypatch,
        adapter,
        limits=ConversationLimits(turn_timeout_seconds=0.02 if termination == "timeout" else 5),
    )
    runner.start()
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if termination == "cancel":
            await runner.stop()
        work = await ended(event_id)
        assert "remote_stop_unknown" in work.error
        assert not adapter.cancelled, "No unidentified or global abort is permitted"
    finally:
        await runner.stop()


async def test_scanner_database_lock_does_not_block_an_independent_coroutine(world, monkeypatch):
    from app.work import management_runner

    event_id = source(world)
    adapter = ScriptedAdapter(finish())
    runner = runner_for(monkeypatch, adapter)
    loop = asyncio.get_running_loop()
    locked = asyncio.Event()
    scanning = asyncio.Event()
    release, released = threading.Event(), threading.Event()
    discover = management_runner.dispatch.discover_sources

    def scan(**kwargs):
        loop.call_soon_threadsafe(scanning.set)
        return discover(**kwargs)

    monkeypatch.setattr(management_runner.dispatch, "discover_sources", scan)

    def competing_writer():
        with SessionLocal() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            loop.call_soon_threadsafe(locked.set)
            # The fallback bounds the red test even if the scanner blocks the loop.
            release.wait(0.75)
            db.rollback()
        released.set()

    writer = asyncio.create_task(asyncio.to_thread(competing_writer))
    await asyncio.wait_for(locked.wait(), 3)
    runner.start()
    try:
        await asyncio.wait_for(scanning.wait(), 3)
        await asyncio.sleep(0.03)
        assert not released.is_set(), "An unrelated coroutine must run while SQLite waits"
        release.set()
        await writer
        assert (await ended(event_id)).status == "completed"
    finally:
        release.set()
        await writer
        await runner.stop()


@pytest.mark.parametrize("boundary", ["scan", "scan_error", "claim"])
async def test_stop_drains_an_inflight_database_transaction_or_committed_claim(
    world, monkeypatch, boundary
):
    from app.work import management_runner

    source(world)
    adapter = ScriptedAdapter(finish())
    runner = runner_for(monkeypatch, adapter, poll_interval=60)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release, returned = threading.Event(), threading.Event()
    operation = "discover_sources" if boundary.startswith("scan") else "claim_source"
    original = getattr(management_runner.dispatch, operation)

    def held(*args, **kwargs):
        if boundary.startswith("scan"):
            with SessionLocal() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                loop.call_soon_threadsafe(entered.set)
                release.wait(0.75)
            if boundary == "scan_error":
                returned.set()
                raise OperationalError("SELECT", {}, RuntimeError("private DB details"))
            result = original(*args, **kwargs)
        else:
            result = original(*args, **kwargs)
            assert result.created
            loop.call_soon_threadsafe(entered.set)
            release.wait(0.75)
        returned.set()
        return result

    monkeypatch.setattr(management_runner.dispatch, operation, held)
    runner.start()
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not returned.is_set(), "The database boundary must yield to its owner"
        stopping = asyncio.create_task(runner.stop())
        await asyncio.sleep(0.02)
        for _ in range(3):
            stopping.cancel()
            await asyncio.sleep(0)
            assert not stopping.done(), "Shutdown must drain the outstanding transaction"
        release.set()
        _done, pending = await asyncio.wait({stopping}, timeout=0.5)
        assert not pending, "A failed drained transaction must not resume the scan poll wait"
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stopping, 3)
        with SessionLocal() as db:
            work = db.scalar(select(WorkSession))
            if boundary == "claim":
                assert work.status == "cancelled" and work.error == "runner_cancelled"
                assert work.ended_at is not None
            else:
                assert work is None
            assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
        assert not adapter.contexts and not adapter.prompts
        assert not runner._jobs and runner._scanner is None
        assert returned.is_set()
    finally:
        release.set()
        runner._wake.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
        await runner.stop()


@pytest.mark.parametrize("failure", ["before_write", "after_commit", "external_terminal"])
async def test_finalization_retry_keeps_original_outcome_without_replaying_conversation(
    world, monkeypatch, failure
):
    from app.models.base import utcnow

    event_id = source(world)
    adapter = ScriptedAdapter(decision(world), finish("The original completed summary"))
    runner = runner_for(monkeypatch, adapter)
    original = runner._finalize
    attempts = []
    first_failed = asyncio.Event()
    loop = asyncio.get_running_loop()
    write_failed = False

    def fail_terminal_write(connection, cursor, statement, parameters, context, executemany):
        nonlocal write_failed
        if statement.startswith("UPDATE work_sessions SET") and "ended_at=" in statement:
            if not write_failed:
                write_failed = True
                raise OperationalError(statement, parameters, RuntimeError("private DB details"))

    def finalize(view, outcome, runtime_session):
        attempts.append(view.work_session_id)
        try:
            original(view, outcome, runtime_session)
            if len(attempts) == 1 and failure == "after_commit":
                raise OperationalError("COMMIT", {}, RuntimeError("private DB details"))
        finally:
            if len(attempts) == 1:
                loop.call_soon_threadsafe(first_failed.set)

    monkeypatch.setattr(runner, "_finalize", finalize)
    if failure != "after_commit":
        sql_events.listen(engine, "before_cursor_execute", fail_terminal_write)
    runner.start()
    try:
        await asyncio.wait_for(first_failed.wait(), 3)
        if failure == "external_terminal":
            with SessionLocal() as db:
                work = db.get(WorkSession, attempts[0])
                work.status, work.ended_at = "failed", utcnow()
                work.error, work.summary = "external_cancellation", "Original external facts"
                db.commit()
        await runner.stop()
        work = await ended(event_id)
        assert attempts == [work.id, work.id]
        assert work.status == ("failed" if failure == "external_terminal" else "completed")
        assert work.summary == (
            "Original external facts"
            if failure == "external_terminal"
            else "The original completed summary"
        )
        assert work.error == ("external_cancellation" if failure == "external_terminal" else None)
        assert len(adapter.contexts) == 1 and len(adapter.prompts) == 2
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
            assert db.scalar(select(func.count()).select_from(Task)) == 1
    finally:
        if failure != "after_commit":
            sql_events.remove(engine, "before_cursor_execute", fail_terminal_write)
        await runner.stop()


async def test_finalization_exhaustion_retains_terminal_facts_and_fails_stop(world, monkeypatch):
    event_id = source(world)
    review_event_id, _task_id, _review_id = source(world, review=True)
    reviewing = asyncio.Event()

    async def model(session, prompt):
        if prompt["scope"]["purpose"] == "review":
            reviewing.set()
            await asyncio.Event().wait()
        return finish("Preserve this terminal outcome")

    adapter = ScriptedAdapter(model, model)
    runner = runner_for(monkeypatch, adapter)
    original = runner._finalize
    attempts = []

    def unavailable(view, outcome, runtime_session):
        if view.purpose == "review":
            return original(view, outcome, runtime_session)
        attempts.append(view.work_session_id)
        raise OperationalError("UPDATE", {}, RuntimeError("private DB details"))

    monkeypatch.setattr(runner, "_finalize", unavailable)
    runner.start()
    await asyncio.wait_for(reviewing.wait(), 3)
    async with asyncio.timeout(3):
        while event_id in runner._jobs:
            await asyncio.sleep(0.01)
    with pytest.raises(RuntimeError, match="finalization exhausted"):
        await runner.stop()
    assert (await ended(review_event_id)).status == "cancelled"
    with pytest.raises(RuntimeError, match="finalization exhausted"):
        await runner.stop()
    with pytest.raises(RuntimeError, match="finalization exhausted"):
        runner.start()
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert runner.finalization_failures[attempts[0]]["status"] == "completed"
    assert runner.finalization_failures[attempts[0]]["summary"] == "Preserve this terminal outcome"
    assert "private DB details" not in repr(runner.finalization_failures)
    assert not runner._jobs and runner._scanner is None
    assert len(adapter.contexts) == 2 and len(adapter.prompts) == 2
    with SessionLocal() as db:
        work = db.get(WorkSession, attempts[0])
        assert work.status == "running" and work.ended_at is None


@pytest.mark.parametrize("remote_state", ["unknown", "unconfirmed"])
async def test_cancelled_cleanup_retries_and_drains_repeated_stop_cancellation(
    world, monkeypatch, remote_state
):
    event_id = source(world)
    entered, retrying = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    async def model(session, prompt):
        if remote_state == "unknown":
            session.remote_run_id = None
        entered.set()
        await asyncio.Event().wait()

    adapter = ScriptedAdapter(model)
    runner = runner_for(monkeypatch, adapter)
    original = runner._finalize
    attempts = []

    def finalize(view, outcome, runtime_session):
        attempts.append(view.work_session_id)
        if len(attempts) == 1:
            raise OperationalError("UPDATE", {}, RuntimeError("private DB details"))
        loop.call_soon_threadsafe(retrying.set)
        release.wait(0.75)
        original(view, outcome, runtime_session)

    monkeypatch.setattr(runner, "_finalize", finalize)
    runner.start()
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        stopping = asyncio.create_task(runner.stop())
        await asyncio.wait_for(retrying.wait(), 3)
        for _ in range(3):
            stopping.cancel()
            await asyncio.sleep(0)
            assert not stopping.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stopping, 3)
        work = await ended(event_id)
        assert attempts == [work.id, work.id]
        assert work.status == "cancelled"
        assert work.error == f"runner_cancelled; remote_stop_{remote_state}"
        assert len(adapter.prompts) == 1
        assert len(adapter.cancelled) == int(remote_state == "unconfirmed")
        assert not runner._jobs and runner._scanner is None
    finally:
        release.set()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
        await runner.stop()


@pytest.mark.parametrize("mismatch", ["adapter_type", "controlled_turn"])
async def test_unsupported_adapter_fails_claim_without_remote_setup(world, monkeypatch, mismatch):
    event_id = source(world)
    adapter = ScriptedAdapter(finish())
    if mismatch == "adapter_type":
        adapter.type = "openclaw"
    else:
        adapter.supports_controlled_turn = False
    runner = runner_for(monkeypatch, adapter)
    runner.start()
    try:
        work = await ended(event_id)
        assert work.status == "failed" and work.error == "controlled_turn_unsupported"
        assert not adapter.contexts and not adapter.sessions and not adapter.prompts
        assert not adapter.cancelled
    finally:
        await runner.stop()


async def test_offloaded_transactions_preserve_the_runner_company_scope(world, monkeypatch):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )
    from app.models.organization import Company

    event_id = source(world)
    with SessionLocal() as db:
        foreign = Company(name="Foreign runner scope", slug="foreign-runner-scope")
        db.add(foreign)
        db.flush()
        project = project_repo.create_project(db, company_id=foreign.id, name="Private project")
        db.add(
            Event(
                type="project.replan_required",
                company_id=foreign.id,
                project_id=project.id,
                payload={},
            )
        )
        db.commit()
    adapter = ScriptedAdapter(finish())
    runner = runner_for(monkeypatch, adapter)
    token = set_request_identity(
        RequestIdentity(
            user_id=1, company_id=world.project.company_id, membership_role="owner", session_id=1
        )
    )
    try:
        runner.start()
    finally:
        reset_request_identity(token)
    try:
        assert (await ended(event_id)).status == "completed"
        with SessionLocal() as db:
            assert list(db.scalars(select(ManagementDispatch.source_event_id))) == [event_id]
        assert len(adapter.contexts) == 1
    finally:
        await runner.stop()


async def test_stop_cancels_owned_turn_before_a_slow_scanner_has_drained(world, monkeypatch):
    from app.work import management_runner

    event_id = source(world)
    entered, reply_ready, scanning = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    async def model(session, prompt):
        entered.set()
        await reply_ready.wait()
        return decision(world)

    adapter = ScriptedAdapter(model)
    runner = runner_for(monkeypatch, adapter)
    discover = management_runner.dispatch.discover_sources

    def held_scan(**kwargs):
        result = discover(**kwargs)
        loop.call_soon_threadsafe(scanning.set)
        release.wait(0.75)
        return result

    runner.start()
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        monkeypatch.setattr(management_runner.dispatch, "discover_sources", held_scan)
        runner._wake.set()
        await asyncio.wait_for(scanning.wait(), 3)
        stopping = asyncio.create_task(runner.stop())
        await asyncio.sleep(0.02)
        assert not stopping.done()
        reply_ready.set()
        assert (await ended(event_id)).status == "cancelled"
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 0
            assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert len(adapter.prompts) == 1
    finally:
        release.set()
        if stopping is not None:
            await stopping
        await runner.stop()
