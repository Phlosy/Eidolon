"""Regression coverage for administrative changes during task execution."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.base import utcnow
from app.models.enums import TaskStatus
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.runtimes.base import ProducedArtifact
from app.services import artifacts as artifact_service
from app.services import tasks as task_service
from app.services.economy.costs import ComputeCostService
from app.work import tool_executor, tool_writes
from app.workflow import orchestrator as orchestrator_module


@pytest.fixture()
def effects(monkeypatch):
    """Keep external follow-up effects observable; artifacts use the temp Drive."""
    observed = SimpleNamespace(events=[], reflect=Mock(), charge=Mock(), notify=Mock())
    monkeypatch.setattr(
        orchestrator_module.bus,
        "publish",
        lambda kind, data, **scope: observed.events.append((kind, data, scope)),
    )
    monkeypatch.setattr(orchestrator_module.reflection, "reflect", observed.reflect)
    monkeypatch.setattr(ComputeCostService, "record_for_session", observed.charge)
    monkeypatch.setattr(orchestrator_module.orchestrator, "notify", observed.notify)
    return observed


@pytest.fixture()
def execution(db, default_company_id, effects):
    people = {row.slug: row for row in org_repo.list_employees(db, default_company_id)}
    executor, next_assignee = people["charlie"], people["dana"]
    occupancy = [(row, row.status, row.current_task_id) for row in (executor, next_assignee)]
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Running task administrative changes",
        description="",
        status="in_progress",
        work_mode="managed",
    )
    task = task_service.create_task(
        db,
        project_id=project.id,
        title="Original execution",
        kind="general",
        assignee_id=executor.id,
        status="todo",
    )
    task_service.transition_task(db, task, "in_progress")
    session = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=executor.id,
        runtime_type="mock",
        status="running",
        started_at=utcnow(),
    )
    executor.status, executor.current_task_id = "working", task.id
    next_assignee.status, next_assignee.current_task_id = "idle", None
    db.commit()
    runner = orchestrator_module.Orchestrator()
    runner._advance = Mock()
    yield SimpleNamespace(
        project=project,
        task=task,
        session=session,
        executor=executor,
        next_assignee=next_assignee,
        runner=runner,
    )
    db.rollback()
    db.expire_all()
    project.status = "cancelled"
    for row in project_repo.list_tasks(db, project.id):
        for work_session in row.sessions:
            if work_session.status == "running":
                work_session.status = "cancelled"
    for row, status, current_task_id in occupancy:
        row.status, row.current_task_id = status, current_task_id
    db.commit()


def _produced(success=True):
    return (
        [ProducedArtifact("other", "Runtime output", "Produced by the running worker.")]
        if success
        else []
    )


@pytest.mark.parametrize("status", ["cancelled", "blocked"])
@pytest.mark.parametrize("success", [True, False], ids=["runtime-success", "runtime-error"])
@pytest.mark.parametrize("transport", ["patch", "tool"])
def test_finalization_preserves_manager_state_and_releases_executor(
    client, db, execution, effects, status, success, transport
):
    task, session, executor = execution.task, execution.session, execution.executor
    if transport == "patch":
        response = client.patch(f"/api/v1/tasks/{task.id}", json={"status": status})
        assert response.status_code == 200, response.text
    else:
        handler = (
            tool_writes._cancel_task if status == "cancelled" else tool_writes._mark_task_blocked
        )
        handler(
            db,
            tool_executor.context_for_employee(db, executor, origin="test"),
            {"task_id": task.id, "reason": "Manager changed the plan"},
        )
    db.expire_all()
    previous_end = task.actual_end_at
    effects.events.clear()
    error = None if success else "runtime failed"

    execution.runner._finalize(task.id, success, _produced(success), error, 1.25)

    db.expire_all()
    assert task.status == status
    assert task.actual_end_at == previous_end
    assert session.status == ("completed" if success else "failed")
    assert session.ended_at is not None
    assert session.error == error
    assert session.cost["duration_sec"] == 1.25
    assert executor.status == "idle"
    assert executor.current_task_id is None
    effects.reflect.assert_called_once_with(task.id, executor.id, success, 1.25, error)
    effects.charge.assert_called_once()
    execution.runner._advance.assert_not_called()
    assert not {
        "task.in_review",
        "task.review_required",
        "task.completed",
        "task.failed",
    } & {kind for kind, _, _ in effects.events}
    artifacts = artifact_service.list_artifact_nodes(db, project_id=execution.project.id)
    assert len(artifacts) == int(success)
    if artifacts:
        assert artifacts[0].owner_employee_id == executor.id
        assert artifacts[0].work_session_id == session.id


@pytest.mark.parametrize("transport", ["patch", "tool"])
@pytest.mark.parametrize("next_busy", [False, True], ids=["next-idle", "next-busy"])
def test_reassignment_keeps_session_author_and_preserves_next_assignee(
    client, db, execution, effects, transport, next_busy
):
    task, executor, next_assignee = execution.task, execution.executor, execution.next_assignee
    other_task = task_service.create_task(
        db,
        project_id=execution.project.id,
        title="Next assignee's existing work",
        kind="general",
        assignee_id=next_assignee.id,
        status="in_progress" if next_busy else "backlog",
    )
    if next_busy:
        next_assignee.status, next_assignee.current_task_id = "working", other_task.id
    db.commit()
    if transport == "patch":
        response = client.patch(f"/api/v1/tasks/{task.id}", json={"assignee_id": next_assignee.id})
        assert response.status_code == 200, response.text
    else:
        tool_writes._assign_task(
            db,
            tool_executor.context_for_employee(db, executor, origin="test"),
            {"task_id": task.id, "employee_id": next_assignee.id},
        )
    effects.events.clear()

    execution.runner._finalize(task.id, True, _produced(), None, 2.0)

    db.expire_all()
    artifacts = artifact_service.list_artifact_nodes(db, project_id=execution.project.id)
    assert len(artifacts) == 1
    assert artifacts[0].owner_employee_id == executor.id
    assert artifacts[0].work_session_id == execution.session.id
    assert task.assignee_id == next_assignee.id
    assert task.status == "in_review"
    assert executor.status == "idle"
    assert executor.current_task_id is None
    assert next_assignee.status == ("working" if next_busy else "idle")
    assert next_assignee.current_task_id == (other_task.id if next_busy else None)
    effects.reflect.assert_called_once_with(task.id, executor.id, True, 2.0, None)
    status_events = [data for kind, data, _ in effects.events if kind == "employee.status_changed"]
    assert status_events == [{"id": executor.id, "to": "idle"}]


def test_finalization_does_not_release_executor_now_occupied_by_another_task(
    db, execution, effects
):
    other_task = task_service.create_task(
        db,
        project_id=execution.project.id,
        title="Worker's later task",
        kind="general",
        assignee_id=execution.executor.id,
        status="in_progress",
    )
    execution.executor.current_task_id = other_task.id
    db.commit()

    execution.runner._finalize(execution.task.id, True, _produced(), None, 1.0)

    db.expire_all()
    assert execution.executor.status == "working"
    assert execution.executor.current_task_id == other_task.id
    assert execution.session.status == "completed"
    assert "employee.status_changed" not in {kind for kind, _, _ in effects.events}
    effects.reflect.assert_called_once_with(
        execution.task.id, execution.executor.id, True, 1.0, None
    )


@pytest.mark.parametrize("retry_success", [True, False], ids=["repeated-success", "error-fallback"])
def test_finished_session_cannot_duplicate_artifacts_or_rewrite_task_on_retry(
    db, execution, effects, retry_success
):
    execution.runner._finalize(execution.task.id, True, _produced(), None, 2.0)
    event_count = len(effects.events)

    execution.runner._finalize(
        execution.task.id,
        retry_success,
        _produced(retry_success),
        None if retry_success else "post-commit failure",
        0.0,
    )

    db.expire_all()
    assert execution.task.status == "in_review"
    assert execution.session.status == "completed"
    assert execution.session.cost["duration_sec"] == 2.0
    assert len(artifact_service.list_artifact_nodes(db, project_id=execution.project.id)) == 1
    assert len(effects.events) == event_count
    effects.reflect.assert_called_once()
    effects.charge.assert_called_once()
    execution.runner._advance.assert_called_once_with(execution.task.id, True)


def test_requeued_task_waits_for_original_executor_before_starting_next_assignee(
    db, execution, effects, monkeypatch
):
    task_service.transition_task(db, execution.task, "blocked")
    task_service.transition_task(db, execution.task, "todo")
    execution.task.assignee_id = execution.next_assignee.id
    db.commit()
    start_runtime = AsyncMock(side_effect=AssertionError("task already has an executor"))
    monkeypatch.setattr(orchestrator_module.gateway, "get_or_create_instance", start_runtime)

    asyncio.run(execution.runner._run_task(execution.task.id))

    start_runtime.assert_not_awaited()
    db.expire_all()
    assert len(execution.task.sessions) == 1
    assert execution.task.status == "todo"
    assert execution.session.status == "running"
    assert execution.executor.current_task_id == execution.task.id
    assert execution.next_assignee.status == "idle"
    execution.runner._finalize(execution.task.id, True, _produced(), None, 2.0)
    db.expire_all()
    assert execution.task.status == "todo"
    assert execution.task.assignee_id == execution.next_assignee.id
    assert execution.session.status == "completed"
    assert execution.executor.status == "idle"
    effects.reflect.assert_called_once_with(
        execution.task.id, execution.executor.id, True, 2.0, None
    )


def test_old_session_callback_cannot_finalize_reassigned_followup_session(db, execution, effects):
    execution.runner._finalize(
        execution.task.id, True, _produced(), None, 2.0, work_session_id=execution.session.id
    )
    db.expire_all()
    task_service.transition_task(db, execution.task, "rejected")
    task_service.transition_task(db, execution.task, "todo")
    execution.task.assignee_id = execution.next_assignee.id
    task_service.transition_task(db, execution.task, "in_progress")
    following = project_repo.create_work_session(
        db,
        task_id=execution.task.id,
        employee_id=execution.next_assignee.id,
        runtime_type="mock",
        status="running",
        started_at=utcnow(),
    )
    execution.next_assignee.status = "working"
    execution.next_assignee.current_task_id = execution.task.id
    db.commit()
    event_count = len(effects.events)

    execution.runner._finalize(
        execution.task.id,
        False,
        [],
        "late error callback",
        0.0,
        work_session_id=execution.session.id,
    )

    db.expire_all()
    assert following.status == "running"
    assert following.ended_at is None
    assert execution.task.status == "in_progress"
    assert execution.next_assignee.status == "working"
    assert execution.next_assignee.current_task_id == execution.task.id
    assert len(effects.events) == event_count
    effects.reflect.assert_called_once()
    effects.charge.assert_called_once()


@pytest.fixture()
def guided_phase(db, default_company_id, effects):
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Guided batch completion",
        description="",
        status="in_progress",
        work_mode="guided",
    )
    phase = delivery_repo.create_phase(
        db,
        project_id=project.id,
        phase_type="initiation",
        name="Active guided phase",
        order=0,
        status="in_progress",
        gate_required=False,
        started_at=utcnow(),
    )
    following = delivery_repo.create_phase(
        db,
        project_id=project.id,
        phase_type="requirements_analysis",
        name="Following phase",
        order=1,
        status="pending",
    )
    db.commit()
    yield SimpleNamespace(project=project, phase=phase, following=following)
    db.rollback()
    project.status = "cancelled"
    db.commit()


def _phase_task(db, guided_phase, status):
    task = project_repo.create_task(
        db,
        project_id=guided_phase.project.id,
        phase_id=guided_phase.phase.id,
        title=f"Guided {status} task",
        kind="general",
        status="backlog",
    )
    if status != "backlog":
        task_service.transition_task(db, task, "todo")
    if status not in {"backlog", "todo"}:
        task_service.transition_task(db, task, "in_progress")
    if status in {"in_review", "done", "rejected"}:
        task_service.transition_task(db, task, "in_review")
    if status in {"done", "rejected", "failed", "cancelled", "blocked"}:
        task_service.transition_task(db, task, status)
    db.commit()
    db.refresh(task)
    return task


@pytest.mark.parametrize("status", ["failed", "cancelled", "blocked"])
def test_guided_completion_rejects_abnormal_tasks_without_partial_progress(
    client, db, guided_phase, effects, status
):
    normal = _phase_task(db, guided_phase, "backlog")
    abnormal = _phase_task(db, guided_phase, status)
    previous_end = abnormal.actual_end_at

    response = client.post(
        f"/api/v1/projects/{guided_phase.project.id}/phases/{guided_phase.phase.id}/complete"
    )

    assert response.status_code == 409, response.text
    assert status in response.json()["detail"]
    db.expire_all()
    assert abnormal.status == status
    assert abnormal.actual_end_at == previous_end
    assert normal.status == "backlog"
    assert normal.actual_start_at is None and normal.actual_end_at is None
    assert guided_phase.phase.status == "in_progress"
    assert guided_phase.phase.completed_at is None
    assert guided_phase.following.status == "pending"
    assert effects.events == []
    effects.notify.assert_not_called()


def test_guided_completion_preserves_normal_batch_completion_and_notifies(
    client, db, guided_phase, effects
):
    tasks = [
        _phase_task(db, guided_phase, status)
        for status in ("backlog", "todo", "in_progress", "in_review", "rejected", "done")
    ]
    already_done_end = tasks[-1].actual_end_at

    response = client.post(
        f"/api/v1/projects/{guided_phase.project.id}/phases/{guided_phase.phase.id}/complete"
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert all(task.status == TaskStatus.done.value for task in tasks)
    assert all(
        task.actual_start_at is not None and task.actual_end_at is not None for task in tasks
    )
    assert tasks[-1].actual_end_at == already_done_end
    assert guided_phase.phase.status == "completed"
    assert guided_phase.following.status == "in_progress"
    updates = [data for kind, data, _ in effects.events if kind == "task.updated"]
    assert {item["id"] for item in updates} == {task.id for task in tasks[:-1]}
    assert all(item["status"] == "done" for item in updates)
    effects.notify.assert_called_once_with({"type": "dispatch"})
