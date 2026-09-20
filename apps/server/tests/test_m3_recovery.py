"""Process death must not separate Worker results from durable notification sources.

The runtime result is scripted; provenance, accepted bytes, costs, transactions and
management discovery use their real services against the isolated test database.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_acceptance import completed_world as _completed_world
from test_m3_artifact_acceptance import conversation_world as _conversation_world
from test_m3_artifact_acceptance import workspace_world as _workspace_world

from app.core.config import settings
from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.drive import DriveNode, DriveRevision
from app.models.economy import ComputeUsage
from app.models.enums import Currency
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.project import Task, WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.services import drive
from app.workflow.orchestrator import Orchestrator

conversation_world = _conversation_world
workspace_world = _workspace_world
completed_world = _completed_world

COMPLETION_EVENTS = (
    "artifact.created",
    "task.in_review",
    "task.review_required",
    "task.completed",
    "employee.status_changed",
)


@pytest.fixture()
def finalizing_world(db, completed_world):
    w = completed_world
    w.actor.status, w.actor.current_task_id = "working", w.task.id
    db.commit()
    return w


def finalize(w):
    Orchestrator()._finalize(
        w.task.id, True, w.items, None, 1.0, work_session_id=w.work.id, runtime_session=w.runtime
    )


def completion_events(db, w):
    return list(
        db.scalars(
            select(Event)
            .where(Event.task_id == w.task.id, Event.type.in_(COMPLETION_EVENTS))
            .order_by(Event.id)
        )
    )


FINALIZE_PROCESS = """
import os, sys
from sqlalchemy import event
from app.core.database import SessionLocal
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.artifacts import text_artifact
from app.runtimes.base import RuntimeSession
from app.work import runtime_artifacts, telemetry
from app.workflow import orchestrator as module

attempt_id, task_id, work_id = map(int, sys.argv[1:4])
_, instance, reference = telemetry.reconstruct_attempt(attempt_id)
with SessionLocal() as db:
    row = db.get(RuntimeAttempt, attempt_id)
    observer = telemetry.AttemptRecorder(row.id, row.request_key, row.idempotency_key)
    runtime = RuntimeSession(
        id=reference.local_session_ref, instance=instance, task_id=task_id,
        attempt=observer, remote_run_id=reference.remote_run_id,
        remote_session_ref=reference.remote_session_ref, status="completed",
    )
    runtime_artifacts.bind_context(row.artifact_context_json, runtime)
items = [text_artifact(
    runtime, source_ref="result.py", title="result.py",
    body=b"print('exact original')\\r\\n", artifact_type="source_code",
)]

if sys.argv[4] == "crash":
    def die_after_domain_commit(db):
        # Ignore cost-service SAVEPOINT commits; only the original outer commit
        # is durable. after_commit precedes transaction-effect delivery.
        if not db.in_nested_transaction():
            os._exit(23)

    def crashing_session():
        db = SessionLocal()
        event.listen(db, "after_commit", die_after_domain_commit)
        return db

    module.SessionLocal = crashing_session

module.Orchestrator()._finalize(
    task_id, True, items, None, 1.0, work_session_id=work_id, runtime_session=runtime,
)
"""


def run_finalizer(w, action):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            FINALIZE_PROCESS,
            str(w.recorder.attempt_id),
            str(w.task.id),
            str(w.work.id),
            action,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_process_exit_after_domain_commit_preserves_recoverable_notification(db, finalizing_world):
    w = finalizing_world
    crashed = run_finalizer(w, "crash")
    assert crashed.returncode == 23, crashed.stderr
    db.expire_all()
    assert w.work.status == "completed" and w.task.status == "in_review"
    assert w.actor.status == "idle" and w.actor.current_task_id is None
    receipt = db.get(RuntimeArtifactCollection, w.recorder.attempt_id)
    assert receipt is not None
    entry = receipt.entries_json[0]
    revision = db.get(DriveRevision, entry["revision_id"])
    node = db.get(DriveNode, revision.node_id)
    assert drive.read_revision_content(node, revision) == w.items[0].content
    usage = db.scalar(select(ComputeUsage).where(ComputeUsage.work_session_id == w.work.id))
    assert usage is not None and usage.currency == Currency.credit.value
    usage_id = usage.id
    sources = completion_events(db, w)
    assert [source.type for source in sources] == list(COMPLETION_EVENTS)
    source_ids = [source.id for source in sources]
    review_source_id = next(
        source.id for source in sources if source.type == "task.review_required"
    )
    assert all(source.company_id == w.work.company_id for source in sources)
    assert all(source.actor_person_id == w.work.person_id for source in sources)
    db.rollback()

    recovery = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from app.work.management_dispatch import discover_sources; "
            "print(json.dumps([discover_sources(limit=100), discover_sources(limit=100)]))",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    first, second = json.loads(recovery.stdout)
    assert review_source_id in first and second == []
    repeated = run_finalizer(w, "replay")
    assert repeated.returncode == 0, repeated.stderr
    db.expire_all()
    assert [source.id for source in completion_events(db, w)] == source_ids
    dispatches = list(
        db.scalars(
            select(ManagementDispatch).where(ManagementDispatch.source_event_id == review_source_id)
        )
    )
    assert len(dispatches) == 1 and dispatches[0].work_session_id is None
    assert dispatches[0].company_id == w.work.company_id
    assert list(db.scalars(select(DriveNode.id).where(DriveNode.work_session_id == w.work.id))) == [
        node.id
    ]
    assert list(
        db.scalars(select(ComputeUsage.id).where(ComputeUsage.work_session_id == w.work.id))
    ) == [usage_id]


@pytest.mark.parametrize("event_type", COMPLETION_EVENTS)
def test_event_insert_failure_rolls_back_the_entire_finalization(
    db, finalizing_world, monkeypatch, event_type
):
    w = finalizing_world
    broadcasts = []
    monkeypatch.setattr(bus, "_broadcast", lambda *args, **kwargs: broadcasts.append(args))
    # A real storage rejection, after earlier events/artifact/cost rows were
    # flushed, must abort the same outer domain transaction.
    db.execute(
        text(
            "CREATE TRIGGER reject_completion_event BEFORE INSERT ON events "
            f"WHEN NEW.task_id = {w.task.id} AND NEW.type = '{event_type}' "
            "BEGIN SELECT RAISE(ABORT, 'injected completion event failure'); END"
        )
    )
    db.commit()
    try:
        with pytest.raises(IntegrityError, match="injected completion event failure"):
            finalize(w)
        db.expire_all()
        assert w.work.status == "running" and w.task.status == "in_progress"
        assert w.actor.status == "working" and w.actor.current_task_id == w.task.id
        assert db.get(RuntimeArtifactCollection, w.recorder.attempt_id) is None
        assert not list(db.scalars(select(DriveNode).where(DriveNode.work_session_id == w.work.id)))
        assert not list(
            db.scalars(select(ComputeUsage).where(ComputeUsage.work_session_id == w.work.id))
        )
        assert completion_events(db, w) == []
        assert broadcasts == []
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER reject_completion_event"))
        db.commit()
    # Retrying the unchanged finalization after storage recovers creates one
    # complete result. Rolled-back revision bytes cannot become extra DB facts.
    finalize(w)
    db.expire_all()
    assert w.work.status == "completed" and w.task.status == "in_review"
    assert [source.type for source in completion_events(db, w)] == list(COMPLETION_EVENTS)
    assert (
        len(list(db.scalars(select(DriveNode).where(DriveNode.work_session_id == w.work.id)))) == 1
    )
    assert (
        len(list(db.scalars(select(ComputeUsage).where(ComputeUsage.work_session_id == w.work.id))))
        == 1
    )


def test_concurrent_finalizers_publish_one_committed_result(db, finalizing_world, monkeypatch):
    w = finalizing_world
    observed = []

    def inspect_broadcast(kind, data, company_id, *, event_id):
        if kind not in COMPLETION_EVENTS:
            return False
        # A fresh connection at the actual delivery boundary sees both source
        # and domain state; no callback can expose an uncommitted event.
        with SessionLocal() as reader:
            assert reader.get(Event, event_id) is not None
            assert reader.get(WorkSession, w.work.id).status == "completed"
            assert reader.get(Task, w.task.id).status == "in_review"
            assert reader.get(RuntimeArtifactCollection, w.recorder.attempt_id) is not None
        observed.append((kind, event_id))
        return True

    monkeypatch.setattr(bus, "_broadcast", inspect_broadcast)
    barrier = Barrier(2)

    def finish():
        barrier.wait(timeout=5)
        finalize(w)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(finish), pool.submit(finish)
        first.result(timeout=10)
        second.result(timeout=10)
    db.expire_all()
    sources = completion_events(db, w)
    assert [source.type for source in sources] == list(COMPLETION_EVENTS)
    assert observed == [(source.type, source.id) for source in sources]
    assert (
        len(list(db.scalars(select(DriveNode).where(DriveNode.work_session_id == w.work.id)))) == 1
    )
    assert (
        len(list(db.scalars(select(ComputeUsage).where(ComputeUsage.work_session_id == w.work.id))))
        == 1
    )


@pytest.mark.parametrize("reviewer_available", [True, False])
def test_gated_fixture_retains_verdict_dependent_notification_payload(
    db, finalizing_world, monkeypatch, reviewer_available
):
    w = finalizing_world
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    w.project.planning_fixture = "deterministic_template"
    if not reviewer_available:
        w.project.management_employee_id = w.project.owner_id = None
    db.commit()
    finalize(w)
    db.expire_all()
    expected_status = "done" if reviewer_available else "in_review"
    assert w.task.status == expected_status
    sources = completion_events(db, w)
    in_review = next(source for source in sources if source.type == "task.in_review")
    assert in_review.payload["fixture_review"] is reviewer_available
    assert in_review.payload["status"] == expected_status
    assert (
        any(source.type == "task.review_required" for source in sources) is not reviewer_available
    )
    if reviewer_available:
        passed = db.scalar(
            select(Event).where(Event.task_id == w.task.id, Event.type == "task.review_passed")
        )
        assert passed is not None and passed.id < sources[0].id


@pytest.mark.parametrize("task_status", ["in_progress", "blocked", "cancelled"])
def test_technical_failure_keeps_administrative_state_and_committed_release(
    db, finalizing_world, task_status
):
    w = finalizing_world
    w.task.status = task_status
    db.commit()
    Orchestrator()._finalize(
        w.task.id, False, [], "transport unavailable", 1.0, work_session_id=w.work.id
    )
    db.expire_all()
    assert w.work.status == "failed"
    assert w.task.status == ("blocked" if task_status == "in_progress" else task_status)
    assert [source.type for source in completion_events(db, w)] == ["employee.status_changed"]
    assert w.actor.status == "idle" and w.actor.current_task_id is None
    assert db.get(RuntimeArtifactCollection, w.recorder.attempt_id) is None
    blocked = list(
        db.scalars(select(Event).where(Event.task_id == w.task.id, Event.type == "task.blocked"))
    )
    assert len(blocked) == int(task_status == "in_progress")
