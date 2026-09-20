"""Real Task Evidence must retain its accepted worker and formal domain result."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident

import pytest
from sqlalchemy import delete, event, select
from test_evidence_pipeline import _def
from test_m3_pinned_handoff import completed_world as _completed_world
from test_m3_pinned_handoff import conversation_world as _conversation_world
from test_m3_pinned_handoff import decide
from test_m3_pinned_handoff import delivered_world as _delivered_world
from test_m3_pinned_handoff import workspace_world as _workspace_world

from app.core.database import SessionLocal, engine
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.evidence import normalize, pipeline
from app.evidence.collectors import REGISTRY
from app.models.assessment import CompetencyExpectation
from app.models.base import utcnow
from app.models.competency import CompetencyEvidence
from app.models.project import WorkSession
from app.repositories import position as position_repo
from app.repositories import project as project_repo

completed_world = _completed_world
conversation_world = _conversation_world
delivered_world = _delivered_world
workspace_world = _workspace_world


def expect_execution(db, world):
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=world.task.id,
            competency_definition_id=_def(db),
            role="primary",
        )
    )
    db.commit()


@pytest.fixture()
def source_world(db, delivered_world):
    expect_execution(db, delivered_world)
    return delivered_world


def candidates(db, world):
    return REGISTRY.get("task").collect(db, {"task_id": world.task.id})


def saved(db, world):
    return list(
        db.scalars(
            select(CompetencyEvidence).where(
                CompetencyEvidence.source_kind == "task",
                CompetencyEvidence.source_id == world.task.id,
            )
        )
    )


def test_formal_pass_consumes_the_actual_source_once(db, source_world):
    w = source_world
    assert w.task.status == "in_review"
    assert (
        pipeline.handle_event(db, {"type": "task.completed", "data": {"id": w.task.id}})["created"]
        == 0
    )
    request = decide(db, w)
    message = {
        "type": "task.review_passed",
        "data": {"task_id": w.task.id, "review_request_id": request.id},
    }
    assert pipeline.handle_event(db, message)["created"] == 1
    db.commit()
    original = saved(db, w)[0]
    assert pipeline.handle_event(db, message)["created"] == 0
    db.commit()
    assert [row.id for row in saved(db, w)] == [original.id]
    assert original.person_id == w.work.person_id
    assert original.metadata_json["work_session_id"] == w.work.id


def test_real_evidence_keeps_original_performer_and_names(db, source_world):
    w = source_world
    decide(db, w)
    snapshot = w.work.provenance_snapshot_json
    w.task.assignee_id = w.consumer.id
    w.task.title = "Renamed after delivery"
    w.project.name = "Today's project name"
    w.actor.name = "Today's employee name"
    db.commit()
    source = candidates(db, w)
    assert len(source) == 1
    candidate = source[0]
    assert candidate.employee_id == w.work.employee_id
    assert candidate.person_id == w.work.person_id
    assert candidate.source_ref == f"task:{w.task.id} ({snapshot['task']['title']})"
    assert candidate.metadata["project_name"] == snapshot["project"]["name"]
    assert candidate.metadata["person_name"] == snapshot["person"]["name"]
    assert candidate.metadata["interpretation_basis"] == "current_policy"
    row, created = normalize.upsert_evidence(db, candidate)
    db.commit()
    assert created and row.person_id == w.work.person_id
    assert row.employee_id == w.work.employee_id


def test_forced_done_without_formal_pass_has_no_real_success_evidence(db, source_world):
    w = source_world
    w.task.status = "done"
    db.commit()
    assert candidates(db, w) == []


def test_unknown_historical_snapshot_is_not_filled_from_current_employee(db, source_world):
    w = source_world
    decide(db, w)
    # Legacy rows never captured these facts; do not defeat the normal dispatch guard
    # or mutate an immutable snapshot just to arrange historical input.
    legacy = WorkSession(
        task_id=w.task.id,
        employee_id=w.actor.id,
        company_id=w.work.company_id,
        person_id=w.work.person_id,
        runtime_type="hermes",
        status="completed",
        ended_at=utcnow(),
    )
    db.add(legacy)
    db.commit()
    assert legacy.provenance_snapshot_json is None
    assert candidates(db, w) == []


@pytest.mark.parametrize("damage", ["employee", "source"])
def test_normalizer_rejects_swapped_task_source_or_performer(db, source_world, damage):
    w = source_world
    decide(db, w)
    candidate = candidates(db, w)[0]
    forged = (
        replace(candidate, employee_id=w.consumer.id)
        if damage == "employee"
        else replace(candidate, source_id=w.downstream.id)
    )
    with pytest.raises(ValueError):
        normalize.upsert_evidence(db, forged)
    db.rollback()
    assert saved(db, w) == []


def test_original_person_survives_loss_of_current_employee_mapping(db, source_world):
    w = source_world
    decide(db, w)
    original_person = w.work.person_id
    w.actor.person_id = None
    db.commit()
    candidate = candidates(db, w)[0]
    row, created = normalize.upsert_evidence(db, candidate)
    db.commit()
    assert created and row.person_id == original_person


def test_concurrent_consumers_reuse_the_same_evidence(db, source_world):
    w = source_world
    decide(db, w)
    candidate = candidates(db, w)[0]
    db.rollback()
    first_written, release_first, second_boundary = Event(), Event(), Event()
    second_thread = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if second_thread and get_ident() == second_thread[0]:
            sql = statement.lstrip().upper()
            if sql.startswith("BEGIN IMMEDIATE") or sql.startswith(
                "INSERT INTO COMPETENCY_EVIDENCE"
            ):
                second_boundary.set()

    def first():
        with SessionLocal() as session:
            row, created = normalize.upsert_evidence(session, candidate)
            first_written.set()
            assert release_first.wait(5)
            session.commit()
            return row.id, created

    def second():
        second_thread.append(get_ident())
        with SessionLocal() as session:
            row, created = normalize.upsert_evidence(session, candidate)
            session.commit()
            return row.id, created

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            left = threads.submit(first)
            assert first_written.wait(5)
            right = threads.submit(second)
            try:
                assert second_boundary.wait(5)
            finally:
                release_first.set()
            outcomes = [left.result(timeout=10), right.result(timeout=10)]
    finally:
        release_first.set()
        event.remove(engine, "before_cursor_execute", observe)
    assert len({identifier for identifier, _ in outcomes}) == 1
    assert sorted(created for _, created in outcomes) == [False, True]
    assert len(saved(db, w)) == 1


def test_evidence_writer_keeps_the_callers_rollback_boundary(db, source_world):
    w = source_world
    decide(db, w)
    normalize.upsert_evidence(db, candidates(db, w)[0])
    assert len(saved(db, w)) == 1
    db.rollback()
    assert saved(db, w) == []


@pytest.mark.parametrize(
    "damage",
    ["person", "company", "work", "project", "source_kind", "legacy_target", "mock_successor"],
)
def test_saved_real_candidate_cannot_change_its_original_source(db, source_world, damage):
    w = source_world
    decide(db, w)
    candidate = candidates(db, w)[0]
    key = {
        "person": "person_id",
        "company": "company_id",
        "work": "work_session_id",
        "project": "project_id",
    }.get(damage)
    if key:
        candidate = replace(candidate, metadata={**candidate.metadata, key: 999999})
    elif damage == "source_kind":
        candidate = replace(candidate, source_type="test")
    elif damage == "legacy_target":
        w.downstream.status = "done"
        w.downstream.assignee_id = w.actor.id
        db.commit()
        candidate = replace(candidate, source_id=w.downstream.id)
    else:
        project_repo.create_work_session(
            db, task_id=w.task.id, employee_id=w.actor.id, runtime_type="mock", started_at=utcnow()
        )
        db.commit()
    with pytest.raises(ValueError):
        normalize.upsert_evidence(db, candidate)
    db.rollback()


def test_ended_assignment_uses_original_position_with_current_policy_label(db, source_world):
    w = source_world
    decide(db, w)
    primary = [a for a in w.work.provenance_snapshot_json["assignments"] if a["is_primary"]]
    assert len(primary) == 1 and primary[0]["position"]
    db.execute(
        delete(CompetencyExpectation).where(
            CompetencyExpectation.scope_kind == "task", CompetencyExpectation.scope_id == w.task.id
        )
    )
    for assignment in position_repo.active_assignments(db, w.actor.id):
        assignment.effective_to = utcnow()
    db.commit()
    assert position_repo.employee_current_position(db, w.actor.id) is None
    source = candidates(db, w)
    assert source
    assert all(row.metadata["reason"] == "original_position_expectation" for row in source)
    assert all(row.metadata["interpretation_basis"] == "current_policy" for row in source)
    assert all(row.employee_id == w.work.employee_id for row in source)


def test_original_task_kind_keeps_one_dedup_key_after_current_kind_changes(db, source_world):
    w = source_world
    decide(db, w)
    first, _ = normalize.upsert_evidence(db, candidates(db, w)[0])
    db.commit()
    w.task.kind = "testing"
    db.commit()
    candidate = candidates(db, w)[0]
    assert candidate.source_type == "task"
    assert candidate.metadata["task_kind"] == w.work.provenance_snapshot_json["task"]["kind"]
    second, created = normalize.upsert_evidence(db, candidate)
    db.commit()
    assert not created and first.id == second.id


def test_current_company_scope_cannot_collect_or_normalize_foreign_work(db, source_world):
    w = source_world
    decide(db, w)
    candidate = candidates(db, w)[0]
    token = set_request_identity(RequestIdentity(1, 999999, "owner", 1))
    try:
        assert candidates(db, w) == []
        with pytest.raises(ValueError, match="company"):
            normalize.upsert_evidence(db, candidate)
        db.rollback()
    finally:
        reset_request_identity(token)
    assert saved(db, w) == []


def test_any_mock_session_still_discounts_real_accepted_task(db, conversation_world, request):
    w = conversation_world
    project_repo.create_work_session(
        db,
        task_id=w.task.id,
        employee_id=w.actor.id,
        runtime_type="mock",
        status="failed",
        started_at=utcnow(),
        ended_at=utcnow(),
    )
    db.commit()
    # The existing fixture now creates and accepts a later real worker.
    w = request.getfixturevalue("delivered_world")
    expect_execution(db, w)
    decide(db, w)
    candidate = candidates(db, w)[0]
    assert candidate.metadata["work_session_id"] == w.work.id
    assert candidate.environment == "mock"
    assert candidate.reliability == pytest.approx(0.35)


def test_business_failure_uses_actual_original_worker_without_inventing_pass(db, source_world):
    w = source_world
    w.task.status = "failed"
    w.task.assignee_id = w.consumer.id
    db.commit()
    candidate = candidates(db, w)[0]
    assert candidate.employee_id == w.work.employee_id
    assert candidate.signal == 30
    assert candidate.person_id == w.work.person_id
