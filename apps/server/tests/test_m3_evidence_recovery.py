"""Task Evidence recovery uses committed original facts across actual process death."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from test_evidence_pipeline import _def
from test_m3_evidence_sources import completed_world as _completed_world
from test_m3_evidence_sources import conversation_world as _conversation_world
from test_m3_evidence_sources import delivered_world as _delivered_world
from test_m3_evidence_sources import saved
from test_m3_evidence_sources import source_world as _source_world
from test_m3_evidence_sources import workspace_world as _workspace_world
from test_m3_pinned_handoff import decide, open_review

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.evidence import pipeline
from app.evidence.collectors import REGISTRY
from app.evidence.policy import TASK_KIND_HINTS
from app.models.assessment import CompetencyExpectation
from app.models.base import utcnow
from app.models.project import Task, WorkSession
from app.work import recovery
from app.workflow.orchestrator import Orchestrator

completed_world = _completed_world
conversation_world = _conversation_world
delivered_world = _delivered_world
source_world = _source_world
workspace_world = _workspace_world


_PASS = """
import os, sys
from sqlalchemy import event
from app.core.database import SessionLocal
from app.models.review import ReviewRequest
from app.work import reviews

def die(db):
    if not db.in_nested_transaction():
        os._exit(23)
with SessionLocal() as db:
    request = db.get(ReviewRequest, int(sys.argv[1]))
    event.listen(db, 'after_commit', die)
    reviews.submit_verdict(db, request=request,
        reviewer_employee_id=request.reviewer_employee_id, verdict='PASS',
        notes='Explicit test verdict, not a model judgement')
raise AssertionError('formal PASS commit boundary was not reached')
"""

_START = """
import json, os, sys
from threading import Event
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.core.database import SessionLocal
from app.models.competency import CompetencyEvidence
from app.workflow.orchestrator import orchestrator

finished, scans = Event(), []
run = orchestrator._handle
async def observe(signal):
    await run(signal)
    if signal['type'] in ('sweep', 'dispatch'):
        with SessionLocal() as db:
            ids = list(db.scalars(select(CompetencyEvidence.id).where(
                CompetencyEvidence.source_kind == 'task',
                CompetencyEvidence.source_id == int(sys.argv[1])
            ).order_by(CompetencyEvidence.id)))
        if ids and sys.argv[2] == 'crash':
            os._exit(24)
        scans.append(ids)
        finished.set()
orchestrator._handle = observe
with TestClient(app):
    assert finished.wait(15), 'startup sweep did not finish'
print(json.dumps({'evidence_ids': scans[0]}))
"""


def process(script, *args, expected=0, enabled=True):
    result = subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        env={**os.environ, "EIDOLON_EVIDENCE_PIPELINE_ENABLED": str(enabled).lower()},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        return json.loads(result.stdout.strip().splitlines()[-1])


def facts(db, task_id):
    """Compare actual rows, retaining IDs, provenance, amounts and timestamps."""
    db.expire_all()
    sessions = "SELECT id FROM work_sessions WHERE task_id=:task_id"
    attempts = f"SELECT id FROM runtime_attempts WHERE work_session_id IN ({sessions})"
    predicates = {
        "tasks": "id=:task_id",
        "work_sessions": "task_id=:task_id",
        "review_requests": "task_id=:task_id",
        "review_facts": (
            "review_request_id IN (SELECT id FROM review_requests WHERE task_id=:task_id)"
        ),
        "runtime_attempts": f"work_session_id IN ({sessions})",
        "runtime_artifact_collections": f"attempt_id IN ({attempts})",
        "drive_nodes": "task_id=:task_id",
        "drive_revisions": "node_id IN (SELECT id FROM drive_nodes WHERE task_id=:task_id)",
        "compute_usage": f"work_session_id IN ({sessions})",
        "events": "task_id=:task_id",
    }
    return {
        table: [
            dict(row)
            for row in db.execute(
                text(
                    f"SELECT * FROM {table} WHERE {where} ORDER BY "
                    + ("attempt_id" if table == "runtime_artifact_collections" else "id")
                ),
                {"task_id": task_id},
            ).mappings()
        ]
        for table, where in predicates.items()
    }


def recover(**kwargs):
    kwargs.setdefault("through_id", recovery.task_boundary())
    return recovery.reconcile_task_evidence(**kwargs)


def test_real_restart_recovers_committed_pass_without_duplicate_facts(
    db, source_world, record_property
):
    w = source_world
    # This fixture stops before the ordinary host receipt. Settle that independent
    # committed-worker recovery before comparing Evidence-only restart effects.
    recovery.reconcile_committed_workers(through_id=w.work.id)
    request = open_review(db, w)
    task_id, work_id, person_id = w.task.id, w.work.id, w.work.person_id
    db.rollback()
    process(_PASS, request.id, expected=23)
    before = facts(db, task_id)
    assert before["tasks"][0]["status"] == "done"
    assert before["review_requests"][0]["verdict"] == "PASS"
    assert saved(db, w) == []
    db.rollback()

    process(_START, task_id, "crash", expected=24)
    assert facts(db, task_id) == before
    evidence = saved(db, w)
    assert len(evidence) == 1
    ids = [row.id for row in evidence]
    assert evidence[0].person_id == person_id
    assert evidence[0].metadata_json["work_session_id"] == work_id
    created_at = evidence[0].created_at
    db.rollback()
    for _ in range(2):
        assert process(_START, task_id, "finish") == {"evidence_ids": ids}
    for _ in range(2):
        pipeline.handle_event(db, {"type": "task.review_passed", "data": {"task_id": task_id}})
        db.commit()
    assert facts(db, task_id) == before
    assert [row.id for row in saved(db, w)] == ids
    assert saved(db, w)[0].created_at == created_at
    record_property(
        "process_facts",
        json.dumps(
            {
                "exits": [23, 24, 0, 0],
                "source_before": before,
                "source_after": facts(db, task_id),
                "evidence_ids": ids,
                "person_id": person_id,
                "work_session_id": work_id,
            },
            default=str,
        ),
    )


def test_startup_keeps_evidence_gate_separate_from_dispatch(db, source_world):
    w = source_world
    decide(db, w)
    task_id = w.task.id
    db.rollback()
    assert process(_START, task_id, "finish", enabled=False) == {"evidence_ids": []}
    result = process(_START, task_id, "finish")
    assert len(result["evidence_ids"]) == 1


def test_task_recovery_pages_keep_original_scope_and_startup_cohort(db, source_world):
    w = source_world
    decide(db, w)
    through = recovery.task_boundary()
    later = Task(
        project_id=w.project.id, title="After startup", status="done", assignee_id=w.actor.id
    )
    db.add(later)
    db.commit()
    first = recover(through_id=through, limit=1)
    assert first.cursor == w.task.id and not first.complete
    assert first.items[0].outcome == "recovered"
    assert first.items[0].work_session_id == w.work.id
    second = recover(through_id=through, after_id=first.cursor, limit=1)
    assert second.cursor == w.downstream.id and second.complete
    assert second.items[0].outcome == "not_terminal"
    assert all(row.task_id < later.id for row in first.items + second.items)
    again = recover(through_id=through)
    assert again.items[0].outcome == "already_recorded"
    assert again.items[0].evidence_ids == first.items[0].evidence_ids
    token = set_request_identity(RequestIdentity(1, w.project.company_id + 1000, "owner", 1))
    try:
        assert recovery.task_boundary() == 0
        assert recover(through_id=through).items == ()
    finally:
        reset_request_identity(token)


@pytest.mark.parametrize(
    "bounds",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"through_id": -1},
        {"after_id": 2, "through_id": 1},
    ],
)
def test_task_recovery_rejects_invalid_bounds(bounds):
    with pytest.raises(ValueError, match="recovery page"):
        recover(**bounds)


@pytest.mark.parametrize("source", ["nonterminal", "unapproved", "unknown", "mock"])
def test_ineligible_task_sources_are_explained_without_taking_writer(
    db, source_world, monkeypatch, source
):
    w = source_world
    if source == "unapproved":
        w.task.status = "done"
    elif source in {"unknown", "mock"}:
        decide(db, w)
        db.add(
            WorkSession(
                task_id=w.task.id,
                employee_id=w.actor.id,
                company_id=w.project.company_id,
                person_id=w.work.person_id,
                runtime_type="mock" if source == "mock" else "hermes",
                status="completed",
                started_at=utcnow(),
                ended_at=utcnow(),
            )
        )
    db.commit()

    def forbidden(_db):
        pytest.fail("ineligible sources must not acquire the writer")

    monkeypatch.setattr(recovery, "begin_observation", forbidden)
    row = recover(through_id=w.task.id).items[0]
    assert (
        row.outcome
        == {
            "nonterminal": "not_terminal",
            "unapproved": "source_unknown",
            "unknown": "source_unknown",
            "mock": "not_real_worker",
        }[source]
    )
    assert saved(db, w) == []


@pytest.mark.parametrize("change", ["mock_successor", "outcome"])
def test_recovery_rechecks_source_and_outcome_after_read_only_page(
    db, source_world, monkeypatch, change
):
    w = source_world
    decide(db, w)
    task_id, actor_id = w.task.id, w.actor.id
    original = recovery.begin_observation

    def change_before_lock(session):
        with SessionLocal() as other:
            if change == "mock_successor":
                other.add(
                    WorkSession(
                        task_id=task_id,
                        employee_id=actor_id,
                        runtime_type="mock",
                        status="completed",
                        started_at=utcnow(),
                        ended_at=utcnow(),
                    )
                )
            else:
                other.get(Task, task_id).status = "failed"
            other.commit()
        original(session)

    monkeypatch.setattr(recovery, "begin_observation", change_before_lock)
    assert recover(through_id=task_id).items[0].outcome == "source_changed"
    assert saved(db, w) == []


def test_storage_failure_rolls_back_all_task_candidates_and_retries(db, source_world):
    w = source_world
    decide(db, w)
    second = _def(db, "collaboration")
    db.add(
        CompetencyExpectation(
            scope_kind="task", scope_id=w.task.id, competency_definition_id=second, role="secondary"
        )
    )
    db.commit()
    db.execute(
        text(
            "CREATE TRIGGER reject_second_evidence BEFORE INSERT ON competency_evidence "
            f"WHEN NEW.competency_definition_id = {second} "
            "BEGIN SELECT RAISE(ABORT, 'evidence unavailable'); END"
        )
    )
    db.commit()
    try:
        with pytest.raises(IntegrityError, match="evidence unavailable"):
            recover(through_id=w.task.id)
        assert saved(db, w) == []
    finally:
        db.execute(text("DROP TRIGGER reject_second_evidence"))
        db.commit()
    result = recover(through_id=w.task.id)
    assert result.items[0].outcome == "recovered"
    assert len(result.items[0].evidence_ids) == 2


def test_live_event_and_recovery_share_the_same_evidence(db, source_world):
    w = source_world
    decide(db, w)
    task_id = w.task.id
    db.rollback()
    barrier = Barrier(2)

    def live():
        barrier.wait(timeout=5)
        with SessionLocal() as session:
            pipeline.handle_event(session, {"type": "task.review_passed", "data": {"id": task_id}})
            session.commit()

    def restart():
        barrier.wait(timeout=5)
        return recover(through_id=task_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(live), pool.submit(restart)]
        for call in calls:
            call.result(timeout=10)
    db.expire_all()
    assert len(saved(db, w)) == 1


def test_failed_recovery_page_keeps_its_orchestrator_cursor(db, source_world, monkeypatch):
    w = source_world
    decide(db, w)
    monkeypatch.setattr(settings, "evidence_pipeline_enabled", True)
    original = recovery.reconcile_task_evidence
    attempts = []

    def once(**kwargs):
        attempts.append(kwargs)
        result = original(**kwargs)
        if len(attempts) == 1:
            raise RuntimeError("temporary evidence storage failure")
        return result

    monkeypatch.setattr(recovery, "reconcile_task_evidence", once)

    async def scenario():
        runner = Orchestrator()
        runner.start()
        try:
            with pytest.raises(RuntimeError, match="temporary evidence"):
                await runner._handle({"type": "sweep"})
            assert runner._evidence_recovery_cursor == 0
            await runner._handle({"type": "sweep"})
            assert runner._evidence_recovery_through is None
        finally:
            await runner.stop()

    asyncio.run(scenario())
    assert attempts[0] == attempts[1]
    assert len(saved(db, w)) == 1


def test_missing_current_expectations_is_reported_without_inventing_evidence(
    db, source_world, monkeypatch
):
    w = source_world
    decide(db, w)
    db.execute(delete(CompetencyExpectation))
    db.commit()
    monkeypatch.setitem(TASK_KIND_HINTS, w.work.provenance_snapshot_json["task"]["kind"], [])
    assert recover(through_id=w.task.id).items[0].outcome == "no_expectations"
    assert saved(db, w) == []


def test_rejected_later_candidate_rolls_back_the_whole_task(db, source_world, monkeypatch):
    w = source_world
    decide(db, w)
    db.add(
        CompetencyExpectation(
            scope_kind="task",
            scope_id=w.task.id,
            competency_definition_id=_def(db, "collaboration"),
            role="secondary",
        )
    )
    db.commit()
    collector = REGISTRY.get("task")
    collect = collector.collect

    def corrupt_second(session, payload):
        candidates = collect(session, payload)
        candidates[1] = replace(
            candidates[1], metadata={**candidates[1].metadata, "company_id": 999999}
        )
        return candidates

    monkeypatch.setattr(collector, "collect", corrupt_second)
    result = recover(through_id=w.task.id).items[0]
    assert result.outcome == "source_rejected" and result.reason
    assert result.evidence_ids == () and saved(db, w) == []


def test_business_failure_is_recovered_without_fabricating_a_verdict(db, source_world):
    w = source_world
    w.task.status = "failed"
    w.task.assignee_id = w.consumer.id
    db.commit()
    result = recover(through_id=w.task.id).items[0]
    assert result.outcome == "recovered"
    evidence = saved(db, w)[0]
    assert evidence.employee_id == w.work.employee_id
    assert evidence.person_id == w.work.person_id and evidence.signal == 30
