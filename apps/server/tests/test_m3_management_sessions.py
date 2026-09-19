"""U8 foundation: explicit purposes keep management/review out of worker execution."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.models.base import as_utc, utcnow
from app.models.organization import Company, Employee
from app.models.project import Task, WorkSession
from app.models.provider import Provider
from app.models.review import ReviewRequest
from app.repositories import persons
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.schemas.project import WorkSessionOut
from app.work import provenance, review_facts


@pytest.fixture()
def session_targets(db, default_company_id):
    manager = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    reviewer = db.scalar(select(Employee).where(Employee.slug == "dana"))
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Explicit management target",
        status="in_progress",
        management_employee_id=manager.id,
        management_person_id=persons.resolve_person_id(db, manager.id),
    )
    task = project_repo.create_task(
        db,
        project_id=project.id,
        title="Work awaiting a named review",
        status="in_review",
        assignee_id=manager.id,
        acceptance_criteria="Preserve purpose and target history",
    )
    review = ReviewRequest(
        company_id=default_company_id,
        project_id=project.id,
        task_id=task.id,
        requested_by_employee_id=manager.id,
        reviewer_employee_id=reviewer.id,
        reason="Inspect the delivered work",
        requested_at=utcnow(),
    )
    other_company = Company(name="Other company", slug="u8-other")
    db.add_all([review, other_company])
    db.flush()
    other_project = project_repo.create_project(
        db, company_id=other_company.id, name="Other target", management_employee_id=manager.id
    )
    db.commit()
    return SimpleNamespace(
        manager=manager,
        reviewer=reviewer,
        project=project,
        task=task,
        review=review,
        other_company=other_company,
        other_project=other_project,
    )


def _create(db, targets, purpose, /, **overrides):
    fields = {
        "employee_id": targets.reviewer.id if purpose == "review" else targets.manager.id,
        "runtime_type": "mock",
        "started_at": utcnow(),
    }
    if purpose == "worker":
        # Exercise backwards-compatible callers that omit purpose entirely.
        fields["task_id"] = targets.task.id
    elif purpose == "management":
        fields.update(purpose=purpose, project_id=targets.project.id)
    else:
        fields.update(purpose=purpose, review_request_id=targets.review.id)
    return project_repo.create_work_session(db, **(fields | overrides))


@pytest.mark.parametrize("purpose", ["worker", "management", "review"])
def test_creation_and_read_shape_keep_explicit_targets(db, session_targets, purpose):
    targets = session_targets
    original_states = (targets.project.status, targets.task.status, targets.review.status)
    session = _create(db, targets, purpose)
    db.commit()
    db.expire_all()
    view = WorkSessionOut.model_validate(session).model_dump()
    assert view["purpose"] == session.purpose == purpose
    assert view["task_id"] == (targets.task.id if purpose == "worker" else None)
    assert view["project_id"] == (None if purpose == "worker" else targets.project.id)
    assert view["review_request_id"] == (targets.review.id if purpose == "review" else None)
    snapshot = provenance.read(session)["snapshot"]
    assert snapshot["purpose"] == purpose
    assert snapshot["project"]["id"] == targets.project.id
    assert (
        snapshot["task"] is None
        if purpose == "management"
        else (snapshot["task"]["id"] == targets.task.id)
    )
    if purpose == "review":
        assert snapshot["review_request"] == {
            "id": targets.review.id,
            "project_id": targets.project.id,
            "task_id": targets.task.id,
            "requested_by_employee_id": targets.manager.id,
            "reviewer_employee_id": targets.reviewer.id,
            "reason": "Inspect the delivered work",
            "requested_at": as_utc(targets.review.requested_at).isoformat(),
        }
    else:
        assert snapshot["review_request"] is None
    assert (targets.project.status, targets.task.status, targets.review.status) == original_states


def test_review_and_management_do_not_replace_worker_session_facts(db, session_targets):
    targets = session_targets
    worker = _create(db, targets, "worker")
    management = _create(db, targets, "management")
    review = _create(db, targets, "review")
    db.commit()
    assert project_repo.get_running_session_for_task(db, targets.task.id).id == worker.id
    assert {row.id for row in targets.task.sessions} == {worker.id}
    assert management.task is None and review.task is None
    assert dict(review_facts.collect_facts(db, targets.task))["session"]["work_session_id"] == (
        worker.id
    )
    assert (
        db.scalar(
            select(func.count()).select_from(Task).where(Task.project_id == targets.project.id)
        )
        == 1
    )


def test_management_can_start_before_the_project_has_any_task(db, session_targets):
    project = project_repo.create_project(
        db,
        company_id=session_targets.project.company_id,
        name="Unplanned project",
        management_employee_id=session_targets.manager.id,
    )
    session = _create(db, session_targets, "management", project_id=project.id)
    db.commit()
    assert session.task_id is None and session.provenance_snapshot_json["task"] is None
    assert project_repo.list_tasks(db, project.id) == []
    assert project.status == "requested"


def test_review_accepts_an_explicit_matching_project(db, session_targets):
    session = _create(db, session_targets, "review", project_id=session_targets.project.id)
    assert session.project_id == session_targets.review.project_id
    assert session.task_id is None


@pytest.mark.parametrize(
    "purpose,overrides",
    [
        ("worker", {"task_id": None}),
        ("worker", {"task_id": 99999}),
        ("worker", {"purpose": None}),
        ("worker", {"purpose": "unknown"}),
        ("worker", {"project_id": "project"}),
        ("worker", {"review_request_id": "review"}),
        ("management", {"project_id": None}),
        ("management", {"project_id": 99999}),
        ("management", {"task_id": "task"}),
        ("management", {"review_request_id": "review"}),
        ("review", {"review_request_id": None}),
        ("review", {"review_request_id": 99999}),
        ("review", {"task_id": "task"}),
        ("review", {"project_id": "other_project"}),
    ],
)
def test_creation_rejects_missing_mixed_or_conflicting_targets(
    db, session_targets, purpose, overrides
):
    fields = {
        key: getattr(session_targets, value).id
        if isinstance(value, str) and value in {"project", "task", "review", "other_project"}
        else value
        for key, value in overrides.items()
    }
    with pytest.raises(ValueError):
        _create(db, session_targets, purpose, **fields)
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0


@pytest.mark.parametrize("purpose", ["management", "review"])
@pytest.mark.parametrize(
    "invalid", ["missing_actor", "wrong_actor", "inactive_actor", "cross_company", "request_scope"]
)
def test_nonworker_creator_rejects_invalid_actor_and_company(db, session_targets, purpose, invalid):
    targets = session_targets
    actor = targets.reviewer if purpose == "review" else targets.manager
    fields = {}
    if invalid == "missing_actor":
        fields["employee_id"] = 99999
    elif invalid == "wrong_actor":
        fields["employee_id"] = targets.manager.id if purpose == "review" else targets.reviewer.id
    elif invalid == "inactive_actor":
        actor.lifecycle_status = "suspended"
    elif invalid == "cross_company":
        actor.company_id = targets.other_company.id
    token = None
    if invalid == "request_scope":
        token = set_request_identity(
            RequestIdentity(
                user_id=1,
                company_id=targets.other_company.id,
                membership_role="owner",
                session_id=1,
            )
        )
    db.flush()
    try:
        with pytest.raises(ValueError):
            _create(db, targets, purpose, **fields)
        assert db.scalar(select(func.count()).select_from(WorkSession)) == 0
    finally:
        if token is not None:
            reset_request_identity(token)


def test_management_requires_an_explicit_designated_actor(db, session_targets):
    session_targets.project.management_employee_id = None
    db.flush()
    with pytest.raises(ValueError, match="active designated"):
        _create(db, session_targets, "management")


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_concurrent_reassignment_rejects_cached_creation(db, session_targets, purpose):
    targets = session_targets
    row = targets.review if purpose == "review" else targets.project
    field = "reviewer_employee_id" if purpose == "review" else "management_employee_id"
    original_actor = getattr(row, field)
    next_actor = targets.manager.id if purpose == "review" else targets.reviewer.id
    # This session retains a cached target. Another transaction reassigns it
    # before creation; the INSERT guard must read the committed designation.
    with Session(db.get_bind()) as concurrent:
        concurrent.execute(
            update(type(row)).where(type(row).id == row.id).values({field: next_actor})
        )
        concurrent.commit()
    assert getattr(row, field) == original_actor
    with pytest.raises(IntegrityError, match="invalid work session target"):
        _create(db, targets, purpose)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0


@pytest.mark.parametrize("broken", ["company", "project", "task", "missing_task"])
def test_review_request_must_point_to_coherent_existing_work(db, session_targets, broken):
    targets = session_targets
    if broken == "company":
        targets.review.company_id = targets.other_company.id
    elif broken == "project":
        targets.review.project_id = targets.other_project.id
    elif broken == "task":
        targets.task.project_id = targets.other_project.id
    else:
        targets.review.task_id = 99999
    db.flush()
    with pytest.raises(ValueError):
        _create(db, targets, "review")


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_nonworker_history_preserves_original_target_facts(db, session_targets, purpose):
    targets = session_targets
    session = _create(db, targets, purpose)
    db.commit()
    snapshot = deepcopy(session.provenance_snapshot_json)
    targets.project.name = "Renamed project"
    targets.project.management_employee_id = targets.reviewer.id
    targets.task.title = "Changed task"
    targets.review.reason = "Changed review reason"
    targets.review.reviewer_employee_id = targets.manager.id
    session.status, session.ended_at = "completed", utcnow()
    session.summary = "A model exit is not a business decision"
    db.commit()
    db.expire_all()
    assert provenance.read(session)["snapshot"] == snapshot
    assert (targets.project.status, targets.task.status, targets.review.status) == (
        "in_progress",
        "in_review",
        "open",
    )
    assert targets.review.verdict is None


@pytest.mark.parametrize("purpose", ["management", "review"])
@pytest.mark.parametrize("field", ["purpose", "project_id", "review_request_id", "task_id"])
@pytest.mark.parametrize("write", ["sql", "orm"])
def test_purpose_and_target_are_immutable(db, session_targets, purpose, field, write):
    session = _create(db, session_targets, purpose)
    db.commit()
    session_id = session.id
    original = getattr(session, field)
    value = "worker" if field == "purpose" else 99999
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        if write == "sql":
            db.execute(
                text(f"UPDATE work_sessions SET {field} = :value WHERE id = :id"),
                {"id": session_id, "value": value},
            )
        else:
            setattr(session, field, value)
            db.flush()
    db.rollback()
    assert getattr(db.get(WorkSession, session_id), field) == original


@pytest.mark.parametrize("write", ["sql", "orm"])
def test_worker_cannot_be_reclassified_to_a_valid_management_shape(db, session_targets, write):
    session = _create(db, session_targets, "worker")
    db.commit()
    session_id = session.id
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        if write == "sql":
            db.execute(
                text(
                    "UPDATE work_sessions SET purpose='management', task_id=NULL, "
                    "project_id=:project WHERE id=:id"
                ),
                {"id": session_id, "project": session_targets.project.id},
            )
        else:
            session.purpose, session.task_id = "management", None
            session.project_id = session_targets.project.id
            db.flush()
    db.rollback()
    assert db.get(WorkSession, session_id).purpose == "worker"
    assert session.task_id == session_targets.task.id


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_nonworker_keeps_original_runtime_ref_and_ended_once_guards(db, session_targets, purpose):
    session = _create(db, session_targets, purpose)
    session.runtime_session_ref = "first-run"
    db.commit()
    session_id = session.id
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        db.execute(
            update(WorkSession)
            .where(WorkSession.id == session_id)
            .values(runtime_session_ref="replacement")
        )
    db.rollback()
    session.status, session.ended_at = "completed", utcnow()
    db.commit()
    with pytest.raises(IntegrityError, match="provenance is immutable"):
        session.ended_at = None
        db.flush()
    db.rollback()
    session.cost = {"tokens": 7}
    db.commit()
    assert session.cost == {"tokens": 7}


@pytest.mark.parametrize("purpose", ["management", "review"])
def test_nonworker_uses_canonical_runtime_and_provider_provenance(db, session_targets, purpose):
    actor = session_targets.reviewer if purpose == "review" else session_targets.manager
    runtime = runtime_repo.get_instance_for_employee(db, actor.id)
    if runtime is None:
        runtime = runtime_repo.create_instance(db, employee_id=actor.id)
    runtime.runtime_type, runtime.deployment_mode = "hermes", "docker"
    provider = Provider(
        company_id=actor.company_id, name="Purpose provider", provider_type="custom"
    )
    db.add(provider)
    db.flush()
    session = _create(
        db,
        session_targets,
        purpose,
        runtime_type="hermes",
        runtime_instance_id=runtime.id,
        provider_id=provider.id,
        model="purpose-model",
    )
    assert session.deployment_mode == "docker"
    assert session.provenance_snapshot_json["provider"]["id"] == provider.id
    assert session.provenance_snapshot_json["runtime"]["execution_mode"] == "real"
    assert session.provenance_snapshot_json["model"] == "purpose-model"
    runtime.person_id, runtime.employee_id = None, None
    db.flush()
    with pytest.raises(ValueError, match="runtime does not match"):
        _create(db, session_targets, purpose, runtime_type="hermes", runtime_instance_id=runtime.id)
    provider.company_id = session_targets.other_company.id
    db.flush()
    with pytest.raises(ValueError, match="provider is not visible"):
        _create(db, session_targets, purpose, runtime_type="hermes", provider_id=provider.id)
    with pytest.raises(ValueError, match="mock execution cannot claim"):
        _create(db, session_targets, purpose, model="claimed-real-model")


def test_legacy_worker_defaults_without_fabricating_snapshot(db, session_targets):
    legacy = WorkSession(
        task_id=session_targets.task.id,
        employee_id=session_targets.manager.id,
        runtime_type="mock",
        runtime_session_ref="legacy-run",
    )
    db.add(legacy)
    db.commit()
    view = WorkSessionOut.model_validate(legacy).model_dump()
    assert view["purpose"] == "worker"
    assert view["project_id"] is None and view["review_request_id"] is None
    assert provenance.read(legacy) == {
        "state": "unknown",
        "snapshot": None,
        "runtime_session_ref": "legacy-run",
    }
