"""U6: shared readiness facts and durable notification cycles without business decisions."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.base import utcnow
from app.models.dispatch import DispatchNotice
from app.models.event import Event
from app.models.organization import Employee
from app.models.project import Project, Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.models.runtime import RuntimeInstance
from app.repositories import persons
from app.repositories import position as position_repo
from app.runtimes import probe
from app.work import dispatch, dispatch_notices, readiness, tool_reads
from app.workflow import orchestrator as orchestration


@pytest.fixture()
def waiting(db, default_company_id, monkeypatch):
    async def inert(_signal):
        return None

    monkeypatch.setattr(orchestration.orchestrator, "_handle", inert)
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    employee = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    employee.runtime_type = "mock"
    employee.lifecycle_status = "offboarded"
    project = Project(company_id=default_company_id, name="Readiness cycles", status="in_progress")
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Keep original authorization",
        assignee_id=employee.id,
        status="todo",
    )
    db.add(task)
    db.commit()
    runner = orchestration.Orchestrator()
    runner._run_task = AsyncMock()
    return SimpleNamespace(employee=employee, project=project, task=task, runner=runner)


def notices(db, e):
    db.expire_all()
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.task_id == e.task.id,
                Event.type == "task.runtime_unavailable",
            )
            .order_by(Event.id)
        )
    )


def sweep(runner):
    async def run():
        await runner._dispatch_pending()
        await asyncio.sleep(0)

    asyncio.run(run())


def test_recovered_then_failed_again_opens_a_new_notification_cycle(db, waiting):
    e = waiting
    sweep(e.runner)
    sweep(e.runner)
    assert len(notices(db, e)) == 1
    e.employee.lifecycle_status = "active"
    db.commit()
    sweep(e.runner)
    e.runner._run_task.assert_awaited_once_with(e.task.id)
    e.employee.lifecycle_status = "offboarded"
    db.commit()
    sweep(e.runner)
    assert len(notices(db, e)) == 2
    assert e.task.status == "todo" and e.task.assignee_id == e.employee.id


def test_restart_does_not_duplicate_an_unchanged_failure_notice(db, waiting):
    e = waiting
    sweep(e.runner)
    assert len(notices(db, e)) == 1
    restarted = orchestration.Orchestrator()
    restarted._run_task = AsyncMock()
    sweep(restarted)
    assert len(notices(db, e)) == 1
    restarted._run_task.assert_not_awaited()


@pytest.fixture()
def observed_real(db, waiting, tmp_path):
    """Persisted observations only; these fixtures are not real runtime evidence."""
    e = waiting
    e.employee.lifecycle_status = "active"
    e.employee.runtime_type = "hermes"
    e.employee.workspace_path = str(tmp_path)
    provider = Provider(
        company_id=e.employee.company_id,
        name="U6 fixture",
        provider_type="custom",
        base_url="https://provider.invalid/v1",
        enabled=True,
    )
    db.add(provider)
    db.flush()
    for existing in db.scalars(
        select(ModelBinding).where(ModelBinding.employee_id == e.employee.id)
    ):
        existing.is_primary = False
    db.flush()
    binding = ModelBinding(
        employee_id=e.employee.id,
        person_id=persons.resolve_person_id(db, e.employee.id),
        provider_id=provider.id,
        model="fixture-model",
        is_primary=True,
    )
    db.add(binding)
    db.flush()
    instance = db.scalar(
        select(RuntimeInstance).where(RuntimeInstance.employee_id == e.employee.id)
    )
    instance.runtime_type = "hermes"
    instance.deployment_mode = "docker"
    instance.status = "running"
    instance.health_status = "healthy"
    instance.model_binding_id = binding.id
    instance.resource_key = "u6-fixture"
    instance.container_id = "u6-fixture-container"
    fingerprint = probe.model_fingerprint(provider, binding.model)
    instance.metadata_json = {"applied_model_fingerprint": fingerprint}
    instance.metadata_json = {
        **instance.metadata_json,
        "runtime_probe": {
            "instance_fingerprint": probe.instance_fingerprint(instance),
            "facts": {
                name: probe.fact("passed", "u6_observation_fixture")
                for name in probe.RUNTIME_FACT_NAMES
            },
        },
        **{
            name + "_probe": {
                "fingerprint": fingerprint,
                "fact": probe.fact("passed", "u6_observation_fixture"),
            }
            for name in ("provider_connectivity", "selected_model")
        },
    }
    db.add(instance)
    db.commit()
    e.instance, e.provider, e.binding = instance, provider, binding
    return e


def test_http_tool_and_dispatch_expose_same_four_layers_without_probing(
    client, db, observed_real, monkeypatch
):
    e = observed_real
    fixed = utcnow()
    monkeypatch.setattr(readiness, "utcnow", lambda: fixed)
    # provider_configuration is a local observation; stabilize its clock as well.
    original_fact = probe.fact
    monkeypatch.setattr(
        "app.work.runtime_readiness.fact",
        lambda *a, **kw: {
            **original_fact(*a, **kw),
            "checked_at": fixed.isoformat(),
            "expires_at": (fixed + timedelta(seconds=90)).isoformat(),
        },
    )
    external = Mock(side_effect=AssertionError("read surfaces must not probe"))
    monkeypatch.setattr(probe.httpx, "AsyncClient", external)
    monkeypatch.setattr(probe, "probe_control", external)
    before = deepcopy(e.instance.metadata_json)
    report = readiness.readiness_report(db, e.employee).as_dict()
    response = client.get(f"/api/v1/employees/{e.employee.id}/readiness")
    assert response.status_code == 200
    tool = tool_reads._inspect_readiness(
        db, SimpleNamespace(company_id=e.employee.company_id), {"employee_id": e.employee.id}
    )
    assert response.json() == tool == report
    evaluation = dispatch.evaluate_dispatch(db, e.task)
    assert evaluation.dispatchable
    assert evaluation.readiness == {key: report[key] for key in evaluation.readiness}
    assert [s["stage"] for s in report["stages"]] == [
        "employment",
        "provisioning",
        "runtime",
        "execution",
    ]
    assert report["required_for_execution"] == ["workspace", "runtime", "provider"]
    assert all(
        item["evaluated_at"] == fixed.isoformat() and item["reason_codes"]
        for item in report["items"]
    )
    assert report["items"][2]["observed_at"] and report["items"][2]["expires_at"]
    assert e.instance.metadata_json == before
    assert not db.dirty and not db.new
    external.assert_not_called()


@pytest.mark.parametrize(
    "condition, reason",
    [
        ("row_only", "control:not_probed"),
        ("unknown", "control:fixture_unreachable"),
        ("expired", "control:probe_expired"),
    ],
)
def test_unobserved_or_expired_runtime_blocks_all_consumers(db, observed_real, condition, reason):
    e = observed_real
    metadata = deepcopy(e.instance.metadata_json)
    if condition == "row_only":
        metadata.pop("runtime_probe")
    elif condition == "unknown":
        metadata["runtime_probe"]["facts"]["control"] = probe.fact("unknown", "fixture_unreachable")
    else:
        metadata["runtime_probe"]["facts"]["control"] = {
            **probe.fact("passed", "fixture_old"),
            "checked_at": (datetime.now(UTC) - timedelta(minutes=3)).isoformat(),
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
    e.instance.metadata_json = metadata
    db.commit()
    evaluation = dispatch.evaluate_dispatch(db, e.task)
    assert not evaluation.dispatchable and evaluation.waiting_for_environment
    assert evaluation.readiness["blocking_gaps"] == ["runtime"]
    assert reason in evaluation.readiness["items"][2]["reason_codes"]
    sweep(e.runner)
    e.runner._run_task.assert_not_awaited()
    assert e.task.status == "todo"


def test_mock_missing_position_stays_soft_and_secondary_is_not_primary(db, waiting):
    e = waiting
    e.employee.lifecycle_status = "active"
    assignment = position_repo.active_primary_assignment(db, e.employee.id)
    assignment.assignment_type = "secondary"
    assignment.is_primary = False
    db.commit()
    report = readiness.readiness_report(db, e.employee)
    position = report.items[0]
    assert not position.ready and position.facts["assignment_id"] is None
    assert "position_missing" in position.reason_codes
    assert not report.ready_to_work and not report.blocking_gaps
    assert not report.stages[0]["blocking"]
    assert dispatch.evaluate_dispatch(db, e.task).dispatchable


def test_recovery_preserves_management_pause_until_explicitly_released(db, waiting):
    e = waiting
    e.task.status = "blocked"
    db.commit()
    sweep(e.runner)
    e.employee.lifecycle_status = "active"
    db.commit()
    sweep(e.runner)
    evaluation = dispatch.evaluate_dispatch(db, e.task)
    assert not evaluation.waiting_for_environment
    assert dispatch.REASON_TASK_HELD in evaluation.reasons
    e.runner._run_task.assert_not_awaited()
    assert e.task.status == "blocked" and e.task.assignee_id == e.employee.id
    e.task.status = "todo"  # the already-authorized manager releases it
    db.commit()
    sweep(e.runner)
    e.runner._run_task.assert_awaited_once_with(e.task.id)


@pytest.mark.parametrize(
    "change", ["employee_inactive", "project_cancelled", "manager_pause", "expired_probe"]
)
def test_queued_task_rechecks_current_authorization_before_external_effects(
    db, observed_real, monkeypatch, change
):
    e = observed_real
    assert dispatch.evaluate_dispatch(db, e.task).dispatchable
    if change == "employee_inactive":
        e.employee.lifecycle_status = "offboarded"
    elif change == "project_cancelled":
        e.project.status = "cancelled"
    elif change == "manager_pause":
        e.task.status = "blocked"
    else:
        e.instance.metadata_json = {}
    db.commit()
    external = AsyncMock(side_effect=AssertionError("stale dispatch reached runtime"))
    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", external)
    asyncio.run(orchestration.Orchestrator()._run_task(e.task.id))
    external.assert_not_awaited()
    assert db.scalar(select(WorkSession.id).where(WorkSession.task_id == e.task.id)) is None


def test_completed_task_closes_environment_notice_without_reopening(db, waiting):
    sweep(waiting.runner)
    waiting.task.status = "done"
    db.commit()
    sweep(waiting.runner)
    assert len(notices(db, waiting)) == 1
    assert (
        db.scalar(
            select(DispatchNotice.closed_at).where(DispatchNotice.scope_id == waiting.task.id)
        )
        is not None
    )


def test_two_scheduler_processes_share_one_durable_notice(db, waiting):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        runner = orchestration.Orchestrator()
        runner._run_task = AsyncMock()
        sweep(runner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    assert len(notices(db, waiting)) == 1


def test_fault_observation_and_notice_commit_exclude_interleaved_recovery(db, waiting, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as ThreadEvent

    from app.core.database import SessionLocal

    observed, release, writing, committed = (ThreadEvent() for _ in range(4))
    reconcile = dispatch_notices.reconcile
    employee_id = waiting.employee.id

    def paused_reconcile(session, observations):
        observed.set()
        assert release.wait(timeout=5)
        reconcile(session, observations)

    def recover():
        with SessionLocal() as session:
            employee = session.get(Employee, employee_id)
            employee.lifecycle_status = "active"
            writing.set()
            session.commit()
            committed.set()

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(dispatch_notices, "reconcile", paused_reconcile)
        sweep_future = pool.submit(sweep, waiting.runner)
        assert observed.wait(timeout=5)
        writer_future = pool.submit(recover)
        try:
            assert writing.wait(timeout=5)
            assert not committed.wait(timeout=0.2), "recovery raced stale notification facts"
        finally:
            release.set()
        sweep_future.result(timeout=5)
        writer_future.result(timeout=5)
    sweep(waiting.runner)
    waiting.runner._run_task.assert_awaited_once_with(waiting.task.id)
    db.expire_all()
    assert (
        db.scalar(
            select(DispatchNotice.closed_at).where(DispatchNotice.scope_id == waiting.task.id)
        )
        is not None
    )


def test_readiness_foreign_company_tool_is_rejected(db, waiting):
    from app.work.tools import ToolError

    with pytest.raises(ToolError, match="not found"):
        tool_reads._inspect_readiness(
            db, SimpleNamespace(company_id=-1), {"employee_id": waiting.employee.id}
        )


def test_notice_and_event_rollback_together_and_retry_can_recreate(db, waiting, monkeypatch):
    observation = (
        waiting.task.id,
        "task.runtime_unavailable",
        {
            "company_id": waiting.employee.company_id,
            "project_id": waiting.project.id,
            "assignee_id": waiting.employee.id,
        },
    )
    flush = db.flush

    def fail_event(*args, **kwargs):
        if any(isinstance(item, Event) for item in db.new):
            raise RuntimeError("injected event persistence failure")
        return flush(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(db, "flush", fail_event)
        with pytest.raises(RuntimeError, match="persistence failure"):
            dispatch_notices.reconcile(db, [observation])
    db.rollback()
    assert not notices(db, waiting)
    assert db.scalar(select(DispatchNotice.id)) is None
    dispatch_notices.reconcile(db, [observation])
    db.commit()
    notice = db.scalar(select(DispatchNotice))
    assert notice.event_id == notices(db, waiting)[0].id
    assert notice.broadcast_at is None


def test_failed_broadcast_is_retried_with_same_persisted_event_id(db, waiting, monkeypatch):
    offer = Mock(return_value=False)
    monkeypatch.setattr(dispatch_notices, "bus", SimpleNamespace(publish_persisted=offer))
    sweep(waiting.runner)
    event_id = notices(db, waiting)[0].id
    notice = db.scalar(select(DispatchNotice).where(DispatchNotice.event_id == event_id))
    assert notice.broadcast_at is None
    offer.assert_called_once_with(event_id)
    offer.return_value = True
    restarted = orchestration.Orchestrator()
    restarted._run_task = AsyncMock()
    sweep(restarted)
    db.refresh(notice)
    assert notice.broadcast_at is not None
    assert [event.id for event in notices(db, waiting)] == [event_id]
    assert offer.call_count == 2 and offer.call_args.args == (event_id,)
    sweep(restarted)
    assert offer.call_count == 2


def test_reassignment_ends_previous_cycle_and_notifies_new_failure(db, waiting):
    sweep(waiting.runner)
    first = notices(db, waiting)[0]
    employee = db.scalar(select(Employee).where(Employee.slug == "bob"))
    employee.lifecycle_status = "offboarded"
    waiting.task.assignee_id = employee.id
    db.commit()
    sweep(waiting.runner)
    events = notices(db, waiting)
    assert len(events) == 2 and events[0].id == first.id
    assert events[1].payload["assignee_id"] == employee.id
    cycles = list(db.scalars(select(DispatchNotice).order_by(DispatchNotice.id)))
    assert cycles[0].closed_at is not None and cycles[1].closed_at is None
    waiting.runner._run_task.assert_not_awaited()


def test_company_scoped_sweep_does_not_close_or_broadcast_another_company(db, waiting, monkeypatch):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    sweep(waiting.runner)
    event_id = notices(db, waiting)[0].id
    offer = Mock(return_value=True)
    monkeypatch.setattr(dispatch_notices, "bus", SimpleNamespace(publish_persisted=offer))
    token = set_request_identity(
        RequestIdentity(user_id=1, company_id=-1, membership_role="owner", session_id=1)
    )
    try:
        sweep(waiting.runner)
        offer.assert_not_called()
    finally:
        reset_request_identity(token)
    db.expire_all()
    notice = db.scalar(select(DispatchNotice).where(DispatchNotice.event_id == event_id))
    assert notice.closed_at is None


def test_unchanged_sweep_does_not_rewrite_event_or_cycle(db, waiting):
    sweep(waiting.runner)
    event = notices(db, waiting)[0]
    notice = db.scalar(select(DispatchNotice).where(DispatchNotice.event_id == event.id))
    before = (notice.created_at, notice.updated_at, deepcopy(event.payload))
    sweep(waiting.runner)
    db.refresh(notice)
    db.refresh(event)
    assert (notice.created_at, notice.updated_at, event.payload) == before


def test_non_sqlite_observation_never_sends_sqlite_lock_statement():
    connection = Mock()
    connection.exec_driver_sql.side_effect = AssertionError("SQLite SQL sent to PostgreSQL")
    session = Mock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.connection.return_value = connection
    dispatch_notices.begin_observation(session)
    session.connection.assert_called_once_with(
        execution_options={"isolation_level": "SERIALIZABLE"}
    )
