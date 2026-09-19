"""U7: every runtime call revalidates the immutable session actor and live scope."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.organization import Company, Employee
from app.models.person import Person
from app.models.project import Project, Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.work import tool_executor as executor


@pytest.fixture()
def running_actor(db, default_company_id):
    employee = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    company = db.get(Company, default_company_id)
    person = db.get(Person, employee.person_id)
    project = Project(company_id=company.id, name="Runtime scope", status="in_progress")
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Current execution",
        assignee_id=employee.id,
        status="in_progress",
    )
    db.add(task)
    db.flush()
    session = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=employee.id,
        runtime_type="hermes",
        model="original-model",
        started_at=utcnow(),
    )
    db.commit()
    return SimpleNamespace(
        employee=employee,
        company=company,
        person=person,
        project=project,
        task=task,
        session=session,
    )


def test_work_session_context_uses_backend_attribution(db, running_actor):
    e = running_actor
    assert executor.context_for_work_session(db, e.session.id).as_dict() == {
        "employee_id": e.employee.id,
        "person_id": e.person.id,
        "company_id": e.company.id,
        "origin": "work_session",
        "work_session_id": e.session.id,
        "task_id": e.task.id,
        "project_id": e.project.id,
        "runtime_type": "hermes",
    }


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_non_running_session_cannot_supply_an_actor(db, running_actor, status):
    running_actor.session.status = status
    db.commit()
    with pytest.raises(executor.ToolExecutionError, match="running") as excinfo:
        executor.context_for_work_session(db, running_actor.session.id)
    assert excinfo.value.reason == "work_session_not_running"


def test_ended_session_cannot_supply_an_actor_even_if_status_is_running(db, running_actor):
    running_actor.session.ended_at = utcnow()
    db.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, running_actor.session.id)
    assert excinfo.value.reason == "work_session_not_running"


@pytest.mark.parametrize(
    "missing", ["snapshot", "person", "company", "employee", "task", "project"]
)
def test_unknown_attribution_is_not_reconstructed_from_current_rows(db, running_actor, missing):
    e = running_actor
    snapshot = deepcopy(e.session.provenance_snapshot_json)
    if missing == "snapshot":
        snapshot = None
    else:
        snapshot[missing] = None
    # Pre-U5 or incomplete imported history, bypassing the canonical creator.
    legacy = WorkSession(
        task_id=e.task.id,
        employee_id=e.employee.id,
        person_id=None if missing == "person" else e.person.id,
        company_id=None if missing == "company" else e.company.id,
        runtime_type="hermes",
        provenance_snapshot_json=snapshot,
    )
    db.add(legacy)
    db.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, legacy.id)
    assert excinfo.value.reason == "work_session_identity_unknown"
    assert legacy.provenance_snapshot_json == snapshot


@pytest.mark.parametrize("field", ["person", "company", "employee", "task", "project"])
def test_attribution_must_match_the_immutable_session(db, running_actor, field):
    e = running_actor
    snapshot = deepcopy(e.session.provenance_snapshot_json)
    snapshot[field]["id"] = 999999
    inconsistent = WorkSession(
        task_id=e.task.id,
        employee_id=e.employee.id,
        person_id=e.person.id,
        company_id=e.company.id,
        runtime_type="hermes",
        provenance_snapshot_json=snapshot,
    )
    db.add(inconsistent)
    db.commit()
    with pytest.raises(executor.ToolExecutionError):
        executor.context_for_work_session(db, inconsistent.id)


@pytest.mark.parametrize(
    "row, changes, reason",
    [
        ("session", {"status": "completed"}, "work_session_not_running"),
        ("session", {"ended_at": utcnow()}, "work_session_not_running"),
        ("employee", {"company_id": 999999}, "work_session_identity_mismatch"),
        ("employee", {"person_id": 999999}, "work_session_identity_mismatch"),
        ("employee", {"lifecycle_status": "offboarded"}, "employee_not_active"),
        ("employee", {"lifecycle_status": "suspended"}, "employee_not_active"),
        ("task", {"assignee_id": None}, "work_session_task_invalid"),
        ("task", {"assignee_id": 999999}, "work_session_task_invalid"),
        ("task", {"project_id": 999999}, "work_session_project_invalid"),
        ("project", {"company_id": 999999}, "work_session_project_invalid"),
    ],
)
def test_context_rechecks_changes_committed_by_another_connection(
    db, running_actor, row, changes, reason
):
    e = running_actor
    cached = getattr(e, row)
    assert executor.context_for_work_session(db, e.session.id).employee_id == e.employee.id
    with SessionLocal() as writer:
        writer.execute(update(type(cached)).where(type(cached).id == cached.id).values(**changes))
        writer.commit()
    # The original identity map still contains the old row (expire_on_commit=False).
    assert any(getattr(cached, field) != value for field, value in changes.items())
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, e.session.id)
    assert excinfo.value.reason == reason


@pytest.mark.parametrize(
    "status", ["backlog", "todo", "blocked", "in_review", "done", "failed", "cancelled", "rejected"]
)
def test_task_must_still_be_in_execution(db, running_actor, status):
    with SessionLocal() as writer:
        writer.execute(update(Task).where(Task.id == running_actor.task.id).values(status=status))
        writer.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, running_actor.session.id)
    assert excinfo.value.reason == "work_session_task_invalid"


@pytest.mark.parametrize("status", ["completed", "cancelled", "rejected", "waiting_for_management"])
def test_project_must_still_allow_execution(db, running_actor, status):
    with SessionLocal() as writer:
        writer.execute(
            update(Project).where(Project.id == running_actor.project.id).values(status=status)
        )
        writer.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, running_actor.session.id)
    assert excinfo.value.reason == "work_session_project_invalid"


def test_task_cannot_move_to_another_live_project_in_the_same_company(db, running_actor):
    e = running_actor
    other = Project(company_id=e.company.id, name="Another project", status="in_progress")
    db.add(other)
    db.commit()
    with SessionLocal() as writer:
        writer.execute(update(Task).where(Task.id == e.task.id).values(project_id=other.id))
        writer.commit()
    with pytest.raises(executor.ToolExecutionError) as excinfo:
        executor.context_for_work_session(db, e.session.id)
    assert excinfo.value.reason == "work_session_project_invalid"


@pytest.mark.parametrize("row", ["session", "employee", "person", "company", "task", "project"])
def test_deleted_attribution_or_scope_cannot_be_resurrected_from_cache(db, running_actor, row):
    e = running_actor
    cached = getattr(e, row)
    session_id = e.session.id
    assert executor.context_for_work_session(db, session_id).employee_id == e.employee.id
    with SessionLocal() as writer:
        writer.execute(delete(type(cached)).where(type(cached).id == cached.id))
        writer.commit()
    with pytest.raises(executor.ToolExecutionError):
        executor.context_for_work_session(db, session_id)


def test_desired_runtime_and_model_changes_preserve_existing_session_identity(db, running_actor):
    e = running_actor
    original = deepcopy(e.session.provenance_snapshot_json)
    before = executor.context_for_work_session(db, e.session.id)
    provider = Provider(company_id=e.company.id, name="Next provider")
    db.add(provider)
    db.flush()
    binding = ModelBinding(
        employee_id=e.employee.id,
        person_id=e.person.id,
        provider_id=provider.id,
        model="next-model",
    )
    db.add(binding)
    e.employee.runtime_type = "openclaw"
    e.employee.runtime_config = {"desired_provider_id": provider.id, "model": "next-model"}
    e.employee.name = "Renamed employee"
    e.person.name = "Renamed person"
    e.company.name = "Renamed company"
    e.project.name = "Renamed project"
    db.commit()
    after = executor.context_for_work_session(db, e.session.id)
    assert after == before
    assert e.session.provenance_snapshot_json == original
    assert e.session.model == "original-model"


def test_missing_primary_position_allows_facts_but_does_not_bypass_write_authority(
    db, running_actor
):
    e = running_actor
    assignment = position_repo.active_primary_assignment(db, e.employee.id)
    assignment.effective_to = utcnow()
    db.commit()
    context = executor.context_for_work_session(db, e.session.id)
    read = executor.execute_tool(
        db, name="inspect_project", args={"project_id": e.project.id}, context=context
    )
    assert read.ok, read.error
    write = executor.execute_tool(
        db,
        name="create_task",
        args={"project_id": e.project.id, "title": "No authority"},
        context=context,
    )
    assert not write.ok and write.reason == "not_authorized"


def test_context_factory_does_not_complete_the_callers_transaction(db, running_actor, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("the runtime bridge owns the transaction")

    monkeypatch.setattr(db, "commit", forbidden)
    monkeypatch.setattr(db, "rollback", forbidden)
    assert executor.context_for_work_session(db, running_actor.session.id).employee_id
