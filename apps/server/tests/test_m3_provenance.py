"""U5: history survives changes to people, work and execution configuration."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.organization import Company, Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.models.project import Project, Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.repositories import persons
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.work import provenance, review_facts
from app.workflow import orchestrator as orchestration


@pytest.fixture()
def execution(db, default_company_id):
    employee = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    person = persons.get_person(db, persons.resolve_person_id(db, employee.id))
    company = db.get(Company, default_company_id)
    project = Project(company_id=company.id, name="Original project", goal="Original goal")
    db.add(project)
    db.flush()
    task = Task(
        project_id=project.id,
        title="Original task",
        description="Build an export",
        acceptance_criteria="Unicode filenames survive",
        produces_json=["source_code", "test_report"],
        assignee_id=employee.id,
        status="todo",
    )
    provider = Provider(
        company_id=company.id,
        name="Original provider",
        provider_type="custom",
        credential_ref="must-not-be-in-history",
        base_url="https://private.invalid/token-secret",
        metadata_json={"api_key": "must-not-be-in-history"},
    )
    db.add_all([task, provider])
    db.flush()
    runtime = runtime_repo.get_instance_for_employee(db, employee.id)
    if runtime is None:
        runtime = runtime_repo.create_instance(db, employee_id=employee.id)
    runtime.runtime_type = employee.runtime_type = "hermes"
    runtime.deployment_mode = "docker"
    runtime.resource_key = "original-resource"
    runtime.runtime_version = "test-version"
    runtime.image_digest = "sha256:original"
    runtime.metadata_json = {"api_key_ref": "must-not-be-in-history"}
    db.commit()
    return SimpleNamespace(
        employee=employee,
        person=person,
        company=company,
        project=project,
        task=task,
        provider=provider,
        runtime=runtime,
    )


def create_session(db, e, **overrides):
    fields = dict(
        task_id=e.task.id,
        employee_id=e.employee.id,
        runtime_type="hermes",
        runtime_instance_id=e.runtime.id,
        provider_id=e.provider.id,
        model="original-model",
        started_at=utcnow(),
    )
    return project_repo.create_work_session(db, **(fields | overrides))


def test_history_survives_rename_transfer_reassignment_and_model_change(db, execution):
    e = execution
    ws = create_session(db, e)
    db.commit()
    original = deepcopy(ws.provenance_snapshot_json)
    assert ws.person_id == e.person.id and ws.company_id == e.company.id
    assert ws.deployment_mode == "docker"
    assert original["person"] == {"id": e.person.id, "name": e.person.name, "slug": e.person.slug}
    assert original["task"]["acceptance_criteria"] == "Unicode filenames survive"
    assert original["task"]["produces"] == ["source_code", "test_report"]
    assert original["runtime"]["execution_mode"] == "real"
    assert original["runtime"]["resource_key"] == "original-resource"
    assert original["provider"]["name"] == "Original provider"
    assert "must-not-be-in-history" not in str(original)
    assert "token-secret" not in str(original)
    assert original["assignments"]
    assignment = position_repo.active_primary_assignment(db, e.employee.id)
    slot = db.get(PositionSlot, assignment.position_slot_id)
    definition = db.get(PositionDefinition, slot.position_definition_id)
    ws.runtime_session_ref = "external-run-1"
    ws.status, ws.ended_at = "completed", utcnow()
    db.commit()

    e.person.name = "Renamed person"
    e.employee.name = "Different membership name"
    e.company.name = "Renamed company"
    e.project.name = "Renamed project"
    e.task.title, e.task.description = "Renamed task", "New requirements"
    e.task.acceptance_criteria, e.task.produces_json = "Different acceptance", ["other"]
    e.task.assignee_id = db.scalar(select(Employee.id).where(Employee.slug == "dana"))
    e.provider.name = "Renamed provider"
    e.runtime.runtime_type, e.runtime.deployment_mode = "openclaw", "process"
    e.runtime.runtime_version, e.runtime.resource_key = "new-version", "replacement"
    definition.name = "Renamed position"
    assignment.effective_to = utcnow()
    next_definition = PositionDefinition(
        company_id=e.company.id,
        name="Transferred position",
        code="transferred",
    )
    next_binding = ModelBinding(
        employee_id=e.employee.id,
        person_id=e.person.id,
        provider_id=e.provider.id,
        model="replacement-model",
        is_primary=True,
    )
    db.add_all([next_definition, next_binding])
    db.flush()
    next_slot = PositionSlot(
        company_id=e.company.id,
        department_id=slot.department_id,
        position_definition_id=next_definition.id,
        slot_code="transferred-1",
    )
    db.add(next_slot)
    db.flush()
    e.runtime.model_binding_id = next_binding.id
    db.add(
        PositionAssignment(
            employee_id=e.employee.id,
            department_id=slot.department_id,
            position_slot_id=next_slot.id,
            position_title_snapshot="New assignment",
        )
    )
    db.commit()
    db.expire_all()
    assert ws.provenance_snapshot_json == original
    assert ws.model == "original-model" and ws.runtime_type == "hermes"
    fact = dict(review_facts.collect_facts(db, e.task))["session"]["provenance"]
    assert fact["state"] == "captured" and fact["snapshot"] == original
    assert fact["runtime_session_ref"] == "external-run-1"
    # Stable references/names are facts even if the live environment disappears.
    db.delete(e.provider)
    db.delete(e.runtime)
    db.commit()
    db.expire_all()
    assert provenance.read(ws)["snapshot"] == original


def test_finished_history_rejects_direct_sql_rewrite(db, execution):
    ws = create_session(db, execution)
    ws.ended_at, ws.status = utcnow(), "completed"
    db.commit()
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.execute(
            text("UPDATE work_sessions SET model = 'rewritten' WHERE id = :id"), {"id": ws.id}
        )
    db.rollback()
    assert db.get(WorkSession, ws.id).model == "original-model"


@pytest.mark.parametrize("finished", [False, True], ids=["running", "finished"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("person_id", 9999),
        ("company_id", 9999),
        ("employee_id", 9999),
        ("task_id", 9999),
        ("runtime_type", "mock"),
        ("runtime_instance_id", None),
        ("provider_id", None),
        ("model", "another-model"),
        ("deployment_mode", "process"),
        ("provenance_snapshot_json", {"version": 1, "person": {"id": 9999}}),
    ],
)
def test_bulk_updates_cannot_change_creation_facts(db, execution, finished, field, value):
    ws = create_session(db, execution)
    if finished:
        ws.ended_at, ws.status = utcnow(), "completed"
    db.commit()
    original = deepcopy(getattr(ws, field))
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.execute(update(WorkSession).where(WorkSession.id == ws.id).values({field: value}))
    db.rollback()
    assert getattr(db.get(WorkSession, ws.id), field) == original


def test_external_reference_is_once_before_end_and_result_updates_still_work(db, execution):
    ws = create_session(db, execution)
    db.commit()
    ws.runtime_session_ref = "allocated-later"
    db.commit()
    ws.runtime_session_ref = "replacement-run"
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.commit()
    db.rollback()
    ws.status, ws.ended_at = "completed", utcnow()
    ws.summary, ws.cost = "Original result", {"tokens": 3}
    db.commit()
    # Late usage measurement remains allowed; it cannot rewrite attribution.
    ws.cost = {"tokens": 5}
    db.commit()
    assert ws.runtime_session_ref == "allocated-later" and ws.cost["tokens"] == 5
    ws.ended_at = None
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.commit()
    db.rollback()


def test_cannot_attach_a_remote_run_after_session_ended(db, execution):
    ws = create_session(db, execution)
    ws.ended_at = utcnow()
    db.commit()
    ws.runtime_session_ref = "late-run"
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.commit()
    db.rollback()


def test_legacy_missing_history_stays_unknown_without_current_name_fallback(db, execution):
    e = execution
    # Represents a pre-v50 row, bypassing the canonical new-session writer.
    legacy = WorkSession(
        task_id=e.task.id,
        employee_id=e.employee.id,
        runtime_type="hermes",
        runtime_session_ref="known-old-run",
        status="completed",
        ended_at=utcnow(),
    )
    db.add(legacy)
    db.commit()
    assert provenance.read(legacy) == {
        "state": "unknown",
        "snapshot": None,
        "runtime_session_ref": "known-old-run",
    }
    assert legacy.person_id is None and legacy.deployment_mode is None
    assert (
        dict(review_facts.collect_facts(db, e.task))["session"]["provenance"]["state"] == "unknown"
    )
    legacy.person_id = e.person.id
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.commit()
    db.rollback()


def test_missing_person_does_not_treat_employee_as_identity(db, execution):
    execution.employee.person_id = None
    execution.runtime.person_id = None
    db.commit()
    ws = create_session(db, execution)
    assert ws.person_id is None and ws.provenance_snapshot_json["person"] is None
    assert ws.provenance_snapshot_json["employee"]["id"] == execution.employee.id


@pytest.mark.parametrize(
    "derived", ["person_id", "company_id", "deployment_mode", "provenance_snapshot_json"]
)
def test_caller_cannot_supply_historical_attribution(db, execution, derived):
    with pytest.raises(ValueError, match="captured by the creation path"):
        create_session(db, execution, **{derived: None})


@pytest.mark.parametrize(
    "field,value", [("task_id", None), ("employee_id", None), ("runtime_type", "unknown")]
)
def test_execution_requires_complete_ownership_and_known_adapter(db, execution, field, value):
    with pytest.raises(ValueError):
        create_session(db, execution, **{field: value})


def test_all_assignments_preserve_distinct_positions_and_missing_legacy_slots(db, execution):
    e = execution
    department = Department(company_id=e.company.id, name="Second department", slug="second")
    definition = PositionDefinition(company_id=e.company.id, name="Second position", code="second")
    db.add_all([department, definition])
    db.flush()
    slot = PositionSlot(
        company_id=e.company.id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code="second-1",
    )
    db.add(slot)
    db.flush()
    secondary = PositionAssignment(
        employee_id=e.employee.id,
        position_slot_id=slot.id,
        assignment_type="secondary",
        is_primary=False,
    )
    legacy = PositionAssignment(
        employee_id=e.employee.id,
        department_id=department.id,
        assignment_type="temporary",
        is_primary=False,
    )
    db.add_all([secondary, legacy])
    db.commit()
    ws = create_session(db, e)
    assignments = {a["id"]: a for a in ws.provenance_snapshot_json["assignments"]}
    assert len(assignments) == 3
    assert assignments[secondary.id]["position"]["name"] == "Second position"
    assert assignments[secondary.id]["slot"]["code"] == "second-1"
    assert assignments[legacy.id]["slot"] is None and assignments[legacy.id]["position"] is None
    assert assignments[legacy.id]["department"]["name"] == "Second department"


@pytest.mark.parametrize("target", ["employee", "provider", "runtime", "private_provider"])
def test_creation_rejects_incompatible_ownership(db, execution, target):
    other = db.scalar(select(Employee).where(Employee.slug == "dana"))
    if target == "employee":
        execution.employee.company_id = 9999
    elif target == "provider":
        execution.provider.company_id = 9999
    elif target == "private_provider":
        execution.provider.scope = "employee"
        execution.provider.owner_person_id = persons.resolve_person_id(db, other.id)
    else:
        execution.runtime.person_id = None
        execution.runtime.employee_id = None
    db.flush()
    with pytest.raises(ValueError):
        create_session(db, execution)
    assert db.scalar(select(WorkSession.id).where(WorkSession.task_id == execution.task.id)) is None


@pytest.mark.parametrize("mode", ["mock", "real"])
def test_dispatch_commits_snapshot_before_any_runtime_effect(db, execution, monkeypatch, mode):
    e = execution
    monkeypatch.setattr(settings, "runtime_mode", mode)
    for binding in db.scalars(
        select(ModelBinding).where(ModelBinding.employee_id == e.employee.id)
    ):
        binding.is_primary = False
    binding = ModelBinding(
        employee_id=e.employee.id,
        person_id=e.person.id,
        provider_id=e.provider.id,
        model="original-model",
        is_primary=True,
    )
    db.add(binding)
    db.commit()
    e.runtime.model_binding_id = binding.id
    db.commit()
    recorded = []

    async def observe_before_external_call(employee):
        # A separate transaction can already read the frozen facts.
        with SessionLocal() as independent:
            ws = independent.scalar(select(WorkSession).where(WorkSession.task_id == e.task.id))
            assert ws is not None
            recorded.append(provenance.read(ws))
            assert ws.runtime_type == ("mock" if mode == "mock" else "hermes")
            assert ws.deployment_mode == ("mock" if mode == "mock" else "docker")
            assert ws.provider_id == (None if mode == "mock" else e.provider.id)
            assert ws.model == (None if mode == "mock" else "original-model")
            assert ws.runtime_instance_id == (None if mode == "mock" else e.runtime.id)
            assert not ws.runtime_session_ref
        raise RuntimeError("injected failure before external allocation")

    start = AsyncMock(side_effect=observe_before_external_call)
    monkeypatch.setattr(orchestration.gateway, "get_or_create_instance", start)
    runner = orchestration.Orchestrator()
    runner._finalize = Mock()
    asyncio.run(runner._run_task(e.task.id))
    start.assert_awaited_once()
    assert recorded[0]["snapshot"]["runtime"]["execution_mode"] == mode
    runner._finalize.assert_called_once()
