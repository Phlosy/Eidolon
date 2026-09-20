"""Durable U8 admission using persisted observations, never real-runtime evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.dispatch import DispatchNotice
from app.models.event import Event
from app.models.organization import Company, Employee
from app.models.project import Task, WorkSession
from app.models.provider import ModelBinding, Provider
from app.models.review import ReviewRequest
from app.models.runtime import RuntimeInstance
from app.repositories import persons
from app.repositories import project as project_repo
from app.runtimes import probe
from app.work import contracts, management_dispatch, reviews


@pytest.fixture()
def targets(db, default_company_id, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "auto")
    manager = db.scalar(select(Employee).where(Employee.slug == "charlie"))
    reviewer = db.scalar(select(Employee).where(Employee.slug == "dana"))
    environments = {}
    for actor in (manager, reviewer):
        actor.runtime_type = "hermes"
        actor.status = "idle"
        workspace = tmp_path / actor.slug
        workspace.mkdir()
        actor.workspace_path = str(workspace)
        provider = Provider(company_id=actor.company_id, name="Local observations", enabled=True)
        db.add(provider)
        db.flush()
        for binding in db.scalars(select(ModelBinding).where(ModelBinding.employee_id == actor.id)):
            binding.is_primary = False
        db.flush()
        binding = ModelBinding(
            employee_id=actor.id,
            person_id=persons.resolve_person_id(db, actor.id),
            provider_id=provider.id,
            model="local-observation-model",
            is_primary=True,
        )
        db.add(binding)
        db.flush()
        instance = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == actor.id))
        instance.runtime_type = "hermes"
        instance.deployment_mode = "docker"
        instance.status = "running"
        instance.health_status = "healthy"
        instance.model_binding_id = binding.id
        instance.resource_key = f"local-observation-{actor.id}"
        instance.container_id = f"local-observation-container-{actor.id}"
        fingerprint = probe.model_fingerprint(provider, binding.model)
        instance.metadata_json = {"applied_model_fingerprint": fingerprint}
        instance.metadata_json = {
            **instance.metadata_json,
            "runtime_probe": {
                "instance_fingerprint": probe.instance_fingerprint(instance),
                "facts": {
                    name: probe.fact("passed", "local_test") for name in probe.RUNTIME_FACT_NAMES
                },
            },
            **{
                name + "_probe": {
                    "fingerprint": fingerprint,
                    "fact": probe.fact("passed", "local_test"),
                }
                for name in ("provider_connectivity", "selected_model")
            },
        }
        environments[actor.id] = SimpleNamespace(
            provider=provider, binding=binding, instance=instance
        )
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Durable sources",
        status="waiting_for_management",
        management_employee_id=manager.id,
        management_person_id=persons.resolve_person_id(db, manager.id),
    )
    task = project_repo.create_task(
        db, project_id=project.id, title="Review target", status="in_review"
    )
    review = reviews.open_review_request(
        db,
        task=task,
        requester_employee_id=manager.id,
        reviewer_employee_id=reviewer.id,
        reason="Inspect facts",
        commit=False,
    )
    db.commit()
    event = db.scalar(
        select(Event).where(Event.type == "task.review_named", Event.task_id == task.id)
    )
    return SimpleNamespace(
        manager=manager,
        reviewer=reviewer,
        project=project,
        task=task,
        review=review,
        review_event=event,
        environments=environments,
    )


def source(db, targets, kind="task.blocked", **fields):
    values = dict(
        type=kind,
        company_id=targets.project.company_id,
        project_id=targets.project.id,
        task_id=targets.task.id,
        payload={"secret": "never-project-this"},
    )
    if kind == "task.review_named":
        values["payload"] = {"review_request_id": targets.review.id}
    event = Event(**(values | fields))
    db.add(event)
    db.commit()
    return event.id


def claim(event_id, **options):
    return management_dispatch.claim_source(
        event_id, enabled=True, budget_available=True, **options
    )


def test_missed_broadcast_is_discovered_and_claimed_without_business_changes(db, targets):
    event_id = source(db, targets)
    task_count = db.scalar(select(func.count()).select_from(Task))
    assert event_id in management_dispatch.discover_sources()
    result = claim(event_id)
    assert result.created
    view = result.dispatch.as_dict()
    db.expire_all()
    work = db.get(WorkSession, view["work_session_id"])
    assert work.purpose == "management" and work.task_id is None
    assert work.project_id == targets.project.id
    assert work.employee_id == targets.manager.id
    environment = targets.environments[targets.manager.id]
    assert (
        work.runtime_type,
        work.deployment_mode,
        work.runtime_instance_id,
        work.provider_id,
        work.model,
    ) == (
        "hermes",
        "docker",
        environment.instance.id,
        environment.provider.id,
        environment.binding.model,
    )
    assert work.person_id == targets.manager.person_id
    assert not claim(event_id).created
    assert targets.project.status == "waiting_for_management" and targets.task.status == "in_review"
    assert db.scalar(select(func.count()).select_from(Task)) == task_count
    assert targets.manager.status == "idle"
    assert "never-project-this" not in str(view)


def test_review_duplicate_sources_reuse_one_execution_even_after_end(db, targets):
    first = claim(targets.review_event.id)
    db.expire_all()
    work = db.get(WorkSession, first.dispatch.work_session_id)
    assert work.purpose == "review" and work.task_id is None
    assert work.review_request_id == targets.review.id and work.employee_id == targets.reviewer.id
    work.status = "failed"
    work.ended_at = utcnow()
    work.error = "Local failure"
    db.commit()
    duplicate = claim(source(db, targets, "task.review_named"))
    assert not duplicate.created and duplicate.dispatch.resolution == "duplicate"
    assert duplicate.dispatch.duplicate_of_id == first.dispatch.id
    assert duplicate.dispatch.as_dict()["work_session"]["status"] == "failed"
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1


@pytest.mark.parametrize("duplicate_source", [False, True])
def test_concurrent_claims_create_one_session(db, targets, duplicate_source):
    one = targets.review_event.id
    two = source(db, targets, "task.review_named") if duplicate_source else one
    barrier = Barrier(2)

    def run(identifier):
        barrier.wait(timeout=5)
        return claim(identifier)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (one, two)))
    assert sum(result.created for result in results) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ("manager", "recipient_missing"),
        ("inactive", "recipient_inactive"),
        ("busy", "recipient_busy"),
        ("person", "recipient_person_missing"),
        ("mock", "real_runtime_required"),
        ("override", "runtime_override"),
        ("fixture", "fixture_project"),
        ("runtime", "environment_not_ready"),
        ("instance_owner", "runtime_identity_mismatch"),
        ("binding_owner", "runtime_identity_mismatch"),
        ("provider_company", "runtime_identity_mismatch"),
        ("manager_person", "recipient_person_mismatch"),
    ],
)
def test_waiting_is_observable_and_never_substitutes(db, targets, monkeypatch, change, reason):
    actor = targets.manager
    environment = targets.environments[actor.id]
    if change == "manager":
        targets.project.management_employee_id = None
    elif change == "inactive":
        actor.lifecycle_status = "suspended"
    elif change == "busy":
        actor.status = "busy"
    elif change == "person":
        actor.person_id = None
    elif change == "mock":
        actor.runtime_type = "mock"
    elif change == "override":
        monkeypatch.setattr(settings, "runtime_mode", "mock")
    elif change == "fixture":
        targets.project.planning_fixture = "deterministic_template"
    elif change == "runtime":
        environment.instance.metadata_json = {}
    elif change == "instance_owner":
        environment.instance.employee_id = None
    elif change == "binding_owner":
        environment.binding.employee_id = targets.reviewer.id
    elif change == "provider_company":
        environment.provider.company_id = None
    elif change == "manager_person":
        targets.project.management_person_id = targets.reviewer.person_id
    event_id = source(db, targets)
    result = claim(event_id)
    assert not result.created and result.dispatch.waiting_reason == reason
    assert result.dispatch.checked_at is not None
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0


def test_opt_in_and_budget_admission_default_closed(db, targets):
    event_id = source(db, targets)
    assert management_dispatch.claim_source(event_id).dispatch.waiting_reason == "disabled"
    assert (
        management_dispatch.claim_source(event_id, enabled=True).dispatch.waiting_reason
        == "budget_unavailable"
    )
    assert claim(event_id).created


def test_waiting_rechecks_current_designation_but_linked_source_never_reroutes(db, targets):
    targets.project.management_employee_id = None
    event_id = source(db, targets)
    assert claim(event_id).dispatch.waiting_reason == "recipient_missing"
    targets.project.management_employee_id = targets.reviewer.id
    targets.project.management_person_id = targets.reviewer.person_id
    db.commit()
    first = claim(event_id)
    assert first.created and first.dispatch.recipient_employee_id == targets.reviewer.id
    targets.project.management_employee_id = targets.manager.id
    targets.project.management_person_id = targets.manager.person_id
    db.commit()
    assert claim(event_id).dispatch.work_session_id == first.dispatch.work_session_id
    assert not claim(event_id).created


@pytest.mark.parametrize("state", ["completed", "cancelled", "rejected"])
def test_terminal_project_cannot_launch(db, targets, state):
    targets.project.status = state
    result = claim(source(db, targets))
    assert not result.created and result.dispatch.resolution == "obsolete"


@pytest.mark.parametrize("condition", ["decided", "superseded", "task_status"])
def test_old_reviews_are_obsolete(db, targets, condition):
    if condition == "decided":
        targets.review.status = "decided"
    elif condition == "task_status":
        targets.task.status = "done"
    else:
        db.add(
            ReviewRequest(
                company_id=targets.project.company_id,
                project_id=targets.project.id,
                task_id=targets.task.id,
                reviewer_employee_id=targets.reviewer.id,
                requested_by_employee_id=targets.manager.id,
                requested_at=utcnow(),
            )
        )
    db.commit()
    assert claim(targets.review_event.id).dispatch.resolution == "obsolete"


def test_closed_notice_is_obsolete_and_missing_target_is_observed(db, targets):
    event_id = source(db, targets)
    db.add(
        DispatchNotice(
            company_id=targets.project.company_id,
            scope_type="task",
            scope_id=targets.task.id,
            event_type="task.blocked",
            event_id=event_id,
            closed_at=utcnow(),
        )
    )
    db.commit()
    assert claim(event_id).dispatch.resolution == "obsolete"
    invalid = source(db, targets, project_id=None)
    assert invalid in management_dispatch.discover_sources()
    assert claim(invalid).dispatch.resolution == "invalid"


def test_scan_is_bounded_fair_and_contract_exact(db, targets):
    assert management_dispatch.SOURCE_EVENTS == contracts.DECISION_NEEDED_EVENTS | {
        "task.review_named"
    }
    ids = [source(db, targets) for _ in range(3)]
    unsupported = source(db, targets, "project.management_assigned")
    assert len(management_dispatch.discover_sources(limit=2)) == 2
    assert len(management_dispatch.discover_sources(limit=2)) == 2
    assert management_dispatch.discover_sources(limit=2) == []
    pending = management_dispatch.pending_source_ids(limit=1)
    management_dispatch.inspect_source(pending[0])
    assert management_dispatch.pending_source_ids(limit=1) != pending
    assert unsupported not in management_dispatch.pending_source_ids()
    assert set(ids) <= set(management_dispatch.pending_source_ids())
    for limit in (0, 101, -1):
        with pytest.raises(ValueError):
            management_dispatch.discover_sources(limit=limit)


def test_cross_company_source_does_not_expose_foreign_payload_or_target(db, targets):
    other = Company(name="Foreign", slug="dispatch-foreign")
    db.add(other)
    db.flush()
    foreign = project_repo.create_project(db, company_id=other.id, name="Private")
    event_id = source(db, targets, project_id=foreign.id)
    result = claim(event_id)
    assert result.dispatch.resolution == "invalid" and result.dispatch.project_id is None
    assert (
        management_dispatch.list_dispatches(
            db, company_id=targets.project.company_id, project_id=foreign.id
        )
        == []
    )
    assert (
        management_dispatch.list_dispatches(db, company_id=other.id, project_id=targets.project.id)
        == []
    )


def test_running_worker_occupancy_is_rechecked_before_claim(db, targets):
    event_id = source(db, targets)
    assert (
        management_dispatch.inspect_source(
            event_id, enabled=True, budget_available=True
        ).waiting_reason
        is None
    )
    project_repo.create_work_session(
        db, task_id=targets.task.id, employee_id=targets.manager.id, runtime_type="mock"
    )
    db.commit()
    assert claim(event_id).dispatch.waiting_reason == "recipient_busy"


def test_source_session_and_recipient_are_immutable_after_claim(db, targets):
    event_id = source(db, targets)
    result = claim(event_id)
    for assignment in (
        "source_event_id=999999",
        "project_id=NULL",
        "work_session_id=NULL",
        "recipient_employee_id=NULL",
    ):
        with pytest.raises(IntegrityError, match="immutable"):
            db.execute(
                text(f"UPDATE management_dispatches SET {assignment} WHERE id=:id"),
                {"id": result.dispatch.id},
            )
        db.rollback()
    with pytest.raises(IntegrityError, match="immutable"):
        db.execute(
            text("DELETE FROM management_dispatches WHERE id=:id"), {"id": result.dispatch.id}
        )
    db.rollback()


def test_claim_starts_with_physical_lock_and_sees_committed_worker(db, targets):
    from threading import Event as ThreadEvent

    from sqlalchemy import event as sqlalchemy_event

    from app.work.dispatch_notices import begin_observation

    event_id = source(db, targets)
    attempted = ThreadEvent()
    statements = []
    engine = db.get_bind()

    def before_cursor(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)
        if statement == "BEGIN IMMEDIATE":
            attempted.set()

    with SessionLocal() as worker:
        begin_observation(worker)
        project_repo.create_work_session(
            worker, task_id=targets.task.id, employee_id=targets.manager.id, runtime_type="mock"
        )
        sqlalchemy_event.listen(engine, "before_cursor_execute", before_cursor)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(claim, event_id)
                assert attempted.wait(timeout=5)
                assert not future.done()
                worker.commit()
                result = future.result(timeout=5)
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", before_cursor)
    assert statements[0] == "BEGIN IMMEDIATE"
    assert not result.created and result.dispatch.waiting_reason == "recipient_busy"


def test_provenance_failure_rolls_back_created_session_but_keeps_waiting_fact(
    db, targets, monkeypatch
):
    event_id = source(db, targets)
    original = project_repo.create_work_session

    def failed(db, **fields):
        original(db, **fields)
        raise ValueError("local failure after insert")

    monkeypatch.setattr(project_repo, "create_work_session", failed)
    result = claim(event_id)
    assert not result.created and result.dispatch.waiting_reason == "provenance_invalid"
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0
    monkeypatch.setattr(project_repo, "create_work_session", original)
    assert claim(event_id).created
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1


def test_exception_before_commit_leaves_no_orphan_or_claim(db, targets, monkeypatch):
    from app.models.management_dispatch import ManagementDispatch

    event_id = source(db, targets)
    original = project_repo.create_work_session

    def failed(db, **fields):
        original(db, **fields)
        raise RuntimeError("local failure")

    monkeypatch.setattr(project_repo, "create_work_session", failed)
    with pytest.raises(RuntimeError, match="local failure"):
        claim(event_id)
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0
    assert db.scalar(select(func.count()).select_from(ManagementDispatch)) == 0
    monkeypatch.setattr(project_repo, "create_work_session", original)
    assert claim(event_id).created


def test_environment_recovery_rechecks_without_any_external_probe(db, targets, monkeypatch):
    from unittest.mock import Mock

    environment = targets.environments[targets.manager.id]
    observations = deepcopy(environment.instance.metadata_json)
    environment.instance.metadata_json = {}
    event_id = source(db, targets)
    external = Mock(side_effect=AssertionError("admission cannot probe"))
    monkeypatch.setattr(probe.httpx, "AsyncClient", external)
    monkeypatch.setattr(probe, "probe_control", external)
    assert claim(event_id).dispatch.waiting_reason == "environment_not_ready"
    environment.instance.metadata_json = observations
    db.commit()
    assert claim(event_id).created
    external.assert_not_called()


def test_position_is_soft_and_authority_is_left_for_tool_execution(db, targets):
    from app.models.position import PositionAssignment

    for assignment in db.scalars(
        select(PositionAssignment).where(PositionAssignment.employee_id == targets.manager.id)
    ):
        assignment.effective_to = utcnow()
    event_id = source(db, targets)
    assert claim(event_id).created


@pytest.mark.parametrize(
    "condition,reason",
    [
        ("inactive", "recipient_inactive"),
        ("busy", "recipient_busy"),
        ("runtime", "environment_not_ready"),
        ("missing", "recipient_missing"),
        ("foreign", "recipient_company_mismatch"),
    ],
)
def test_review_uses_only_current_designated_actor(db, targets, condition, reason):
    if condition == "inactive":
        targets.reviewer.lifecycle_status = "suspended"
    elif condition == "busy":
        targets.reviewer.status = "busy"
    elif condition == "runtime":
        targets.environments[targets.reviewer.id].instance.metadata_json = {}
    elif condition == "missing":
        targets.review.reviewer_employee_id = 999999
    else:
        other = Company(name="Other", slug="other-review-company")
        db.add(other)
        db.flush()
        targets.reviewer.company_id = other.id
    db.commit()
    result = claim(targets.review_event.id)
    assert not result.created and result.dispatch.waiting_reason == reason
    if condition in {"missing", "foreign"}:
        assert result.dispatch.recipient_employee_id is None


def test_projection_scope_pagination_and_live_session_facts(db, targets):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    ids = [source(db, targets) for _ in range(3)]
    management_dispatch.discover_sources()
    first = claim(ids[0])
    db.expire_all()
    work = db.get(WorkSession, first.dispatch.work_session_id)
    work.status = "completed"
    work.ended_at = utcnow()
    work.error = "Bearer secret-value-123456"
    db.commit()
    views = management_dispatch.list_dispatches(
        db, company_id=targets.project.company_id, project_id=targets.project.id, limit=2
    )
    assert len(views) == 2
    older = management_dispatch.list_dispatches(
        db,
        company_id=targets.project.company_id,
        project_id=targets.project.id,
        limit=2,
        before_id=views[-1]["id"],
    )
    assert len(older) == 2
    assert not {v["id"] for v in views} & {v["id"] for v in older}
    complete = next(v for v in views + older if v["source_event_id"] == ids[0])
    assert complete["work_session"]["status"] == "completed"
    assert (
        complete["work_session"]["ended_at"]
        and "secret-value" not in complete["work_session"]["error"]
    )
    token = set_request_identity(
        RequestIdentity(user_id=1, company_id=99999, membership_role="owner", session_id=1)
    )
    try:
        assert (
            management_dispatch.list_dispatches(
                db, company_id=targets.project.company_id, project_id=targets.project.id
            )
            == []
        )
        assert claim(ids[1]).dispatch is None
        assert management_dispatch.discover_sources() == []
        assert management_dispatch.pending_source_ids() == []
    finally:
        reset_request_identity(token)


@pytest.mark.parametrize("malformed", [None, True, "1", -1, 999999])
def test_invalid_review_reference_is_observed_without_foreign_data(db, targets, malformed):
    identifier = source(
        db,
        targets,
        "task.review_named",
        payload={"review_request_id": malformed, "private": "hidden"},
    )
    view = claim(identifier).dispatch
    assert view.resolution == "invalid" and view.review_request_id is None
    assert "hidden" not in str(view.as_dict())


def test_orm_source_and_link_guards_match_sql(db, targets):
    from app.models.management_dispatch import ManagementDispatch

    event_id = source(db, targets)
    result = claim(event_id)
    for field, value in (
        ("source_event_id", 999999),
        ("project_id", None),
        ("work_session_id", None),
        ("recipient_employee_id", targets.reviewer.id),
    ):
        row = db.get(ManagementDispatch, result.dispatch.id)
        setattr(row, field, value)
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()
    row = db.get(ManagementDispatch, result.dispatch.id)
    db.delete(row)
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_missing_or_mutated_source_resolves_pending_observation(db, targets):
    event_id = source(db, targets)
    management_dispatch.discover_sources()
    db.execute(text("DELETE FROM events WHERE id=:id"), {"id": event_id})
    db.commit()
    result = claim(event_id)
    assert result.dispatch.resolution == "invalid"
    assert event_id not in management_dispatch.pending_source_ids()


@pytest.mark.parametrize(
    "status", ["working", "researching", "learning", "reflecting", "meeting", "offline", "error"]
)
def test_nonidle_employee_status_blocks_admission_without_a_running_session(db, targets, status):
    targets.manager.status = status
    result = claim(source(db, targets))
    assert not result.created and result.dispatch.waiting_reason == "recipient_busy"


def test_raw_observation_cannot_omit_existing_source_task(db, targets):
    from app.models.management_dispatch import ManagementDispatch

    event_id = source(db, targets, "project.replan_required")
    with pytest.raises(IntegrityError, match="invalid management dispatch source"):
        db.execute(
            ManagementDispatch.__table__.insert().values(
                source_event_id=event_id,
                source_type="project.replan_required",
                purpose="management",
                company_id=targets.project.company_id,
                project_id=targets.project.id,
            )
        )
    db.rollback()


def test_raw_session_link_rejects_mock_and_wrong_project(db, targets):
    event_id = source(db, targets)
    view = management_dispatch.inspect_source(event_id)
    wrong = project_repo.create_project(
        db,
        company_id=targets.project.company_id,
        name="Other project",
        management_employee_id=targets.manager.id,
    )
    environment = targets.environments[targets.manager.id]
    for project, runtime in ((targets.project, "mock"), (wrong, "hermes")):
        fields = (
            {}
            if runtime == "mock"
            else dict(
                runtime_instance_id=environment.instance.id,
                provider_id=environment.provider.id,
                model=environment.binding.model,
            )
        )
        work = project_repo.create_work_session(
            db,
            purpose="management",
            project_id=project.id,
            employee_id=targets.manager.id,
            runtime_type=runtime,
            **fields,
        )
        db.commit()
        with pytest.raises(IntegrityError, match="invalid management dispatch link"):
            db.execute(
                text(
                    "UPDATE management_dispatches SET work_session_id=:work, "
                    "waiting_reason=NULL WHERE id=:id"
                ),
                {"work": work.id, "id": view.id},
            )
        db.rollback()


def test_continuous_new_batches_do_not_starve_existing_waiting_rows(db, targets):
    old = source(db, targets)
    for identifier in management_dispatch.discover_sources():
        management_dispatch.inspect_source(identifier)
    seen = set()
    for _ in range(3):
        fresh = {source(db, targets) for _ in range(2)}
        assert set(management_dispatch.discover_sources(limit=2)) == fresh
        pending = management_dispatch.pending_source_ids(limit=2)
        seen.update(pending)
        for identifier in pending:
            management_dispatch.inspect_source(identifier)
    assert old in seen
