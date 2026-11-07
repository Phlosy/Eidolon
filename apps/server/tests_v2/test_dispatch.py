"""Dispatch, run execution and review (R3-d): the worker-side components drive a
real one-task project through the replayed engineer and reviewer runs."""

import json
from uuid import uuid4

from loop_support import replay_provider
from project_support import ENGINEER, REVIEWER, components, drive, set_up_project, svc

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.providers import Completed, TextDelta, Usage
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.agent.scheduler import RunLauncher, parse_verdict
from eidolon.db import SessionLocal
from eidolon.institution import RECORDED as DECISION_RECORDED
from eidolon.institution import SIGNED
from eidolon.kernel.consumers import process
from eidolon.kernel.store import FactStore
from eidolon.work import TaskRow, WorkProjection
from eidolon.work import facts as F
from eidolon.work.dispatch import request_runs
from eidolon.worker import Worker

store = FactStore()


def test_request_runs_is_idempotent_and_only_for_dispatchable_tasks(db):
    company, pid, task = set_up_project(db)
    first = request_runs(db, svc, pid)
    db.commit()
    assert [f.kind for f in first] == [F.RUN_REQUESTED] and first[0].payload["assignee"] == ENGINEER
    assert request_runs(db, svc, pid) == []  # pending request: not dispatchable again
    state = svc.project(db, pid)
    assert state.tasks[task].pending_run_id is not None and state.tasks[task].dispatchable is False
    # the launcher starts exactly that run; a second launch is a no-op
    process(SessionLocal, RunLauncher(svc))
    state = svc.project(db, pid)
    t = state.tasks[task]
    assert (
        t.status == "in_progress"
        and str(t.current_run_id) == first[0].payload["run_id"]
        and t.pending_run_id is None
    )
    assert request_runs(db, svc, pid) == []  # running: not dispatchable


def test_parse_verdict():
    assert parse_verdict('{"verdict": "PASS", "reason": "good"}') == ("pass", "good")
    assert parse_verdict('Sure:\n```json\n{"verdict": "rework", "reason": "x"}\n```') == (
        "rework",
        "x",
    )
    assert parse_verdict('{"verdict": "maybe"}') is None and parse_verdict("no json") is None


def test_project_runs_submits_reviews_and_lands_done_from_replayed_fixtures(db, tmp_path):
    company, pid, task = set_up_project(db)
    provider = replay_provider(SessionLocal)
    runs, reviews = components(
        SessionLocal, provider, BlobStore(tmp_path / "blobs"), tmp_path / "ws"
    )
    trail = drive(SessionLocal, db, pid, runs, reviews)
    assert trail[-1] == "done", trail

    state = svc.project(db, pid)
    t = state.tasks[task]
    assert (
        t.assignee == ENGINEER
        and t.reviews[-1]["verdict"] == "pass"
        and t.reviews[-1]["reviewer"] == REVIEWER
    )
    paths = sorted(a.path for a in state.artifacts.values())
    assert "hello.py" in paths and "test_hello.py" in paths
    for art in state.artifacts.values():
        assert art.task_id == task and art.run_id == t.runs[0]  # H2
    # the files really are the artifacts' blobs
    blobs = BlobStore(tmp_path / "blobs")
    hello = next(a for a in state.artifacts.values() if a.path == "hello.py")
    assert b"Hello, Eidolon!" in blobs.get_bytes(hello.blob_ref)
    assert (
        tmp_path / "ws" / str(company) / str(pid) / str(task) / "hello.py"
    ).read_bytes() == blobs.get_bytes(hello.blob_ref)

    # the fact trail of the task in order
    kinds = [
        f.kind
        for f in store.by_correlation(db, pid)
        if f.kind.startswith("work.task") or f.kind == F.RUN_REQUESTED
    ]
    assert kinds[-4:] == [F.RUN_REQUESTED, F.TASK_STARTED, F.TASK_SUBMITTED, F.TASK_REVIEWED]
    reviewed = next(f for f in store.by_correlation(db, pid) if f.kind == F.TASK_REVIEWED)
    decision = store.by_correlation(db, reviewed.decision_id)
    assert [f.kind for f in decision][-2:] == [SIGNED, DECISION_RECORDED]
    signed = next(f for f in decision if f.kind == SIGNED)
    assert (
        signed.actor.id == REVIEWER
        and signed.payload["role"] == "qa"
        and signed.payload["verdict"] == "approve"
    )
    assert decision[0].payload["resolution"]["separation"] == ["author!=reviewer"]
    # two agent runs happened (engineer, reviewer), each a full run with model calls
    engineer_run = store.by_correlation(db, t.runs[0])
    assert engineer_run[0].kind == R.RUN_STARTED and R.RUN_COMPLETED in [
        f.kind for f in engineer_run
    ]
    assert any(f.kind == MODEL_CALLED for f in engineer_run)
    reviewer_runs = [
        f
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=10)
        if f.payload["person_id"] == REVIEWER
    ]
    assert len(reviewer_runs) == 1
    reviewer_tools = [
        f.payload["name"]
        for f in store.by_correlation(db, reviewer_runs[0].correlation_id)
        if f.kind == R.TOOL_COMPLETED
    ]
    assert all(name in ("read_file", "list_dir", "run_tests", "shell") for name in reviewer_tools)
    # projection agrees
    process(SessionLocal, WorkProjection())
    row = db.get(TaskRow, task)
    assert row.status == "done" and row.rework_count == 0 and row.current_run_id is None

    # everything is settled: another cycle does nothing
    assert drive(SessionLocal, db, pid, runs, reviews, max_cycles=1) == ["done"]
    assert request_runs(db, svc, pid) == []


def test_review_waits_when_the_reviewer_gives_no_verdict(db, tmp_path):
    """RV1/RV6: an unparsable reviewer answer is not a verdict; the task stays in review."""
    company, pid, task = set_up_project(db)

    class Mute:
        name = "mute"

        async def complete(self, request):
            yield TextDelta(
                "I am not sure." if "Review task" in request.messages[0].content else "done"
            )
            yield Completed("end_turn", Usage(1, 1), request.model, "mute", request_id=str(uuid4()))

        async def embed(self, texts, *, model=None):
            raise NotImplementedError

    runs, reviews = components(SessionLocal, Mute(), BlobStore(tmp_path / "b"), tmp_path / "ws")
    trail = drive(SessionLocal, db, pid, runs, reviews, max_cycles=2)
    assert trail == ["in_review", "in_review"]
    kinds = [f.kind for f in store.by_correlation(db, pid)]
    assert F.TASK_REVIEWED not in kinds
    # the decision was opened once and never signed
    requested = [
        f
        for f in store.stream(
            db, kinds=["institution.decision.requested"], company_id=company, limit=20
        )
        if f.payload["gate"] == "task.review"
    ]
    assert len(requested) == 1
    assert (
        store.by_correlation(db, requested[0].correlation_id)[-1].kind
        == "institution.decision.requested"
    )


def test_failed_run_fails_the_task_and_nothing_retries(db, tmp_path):
    company, pid, task = set_up_project(db)

    class Broken:
        name = "broken"

        async def complete(self, request):
            yield Completed(
                "max_tokens", Usage(1, 1), request.model, "broken", request_id=str(uuid4())
            )

        async def embed(self, texts, *, model=None):
            raise NotImplementedError

    runs, reviews = components(SessionLocal, Broken(), BlobStore(tmp_path / "b"), tmp_path / "ws")
    trail = drive(SessionLocal, db, pid, runs, reviews, max_cycles=3)
    assert trail[0] == "failed" and len(trail) == 1
    failed = next(f for f in store.by_correlation(db, pid) if f.kind == F.TASK_FAILED)
    assert "max_tokens" in failed.payload["reason"]
    assert request_runs(db, svc, pid) == []  # R5: no automatic reassignment or retry


def test_worker_runs_jobs_after_draining(db, tmp_path):
    class Counting:
        name = "counting"

        def __init__(self):
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            return 1 if self.ticks == 1 else 0

    class Failing:
        name = "failing"

        def tick(self):
            raise RuntimeError("boom")

    from eidolon.kernel.consumers import Registry
    from eidolon.kernel.notify import listening

    job, bad = Counting(), Failing()
    with listening() as listener:
        worker = Worker(Registry(), SessionLocal, listener, poll_interval=0.1, jobs=[job, bad])
        worker.start()
        assert (
            worker.run_jobs() == 1
            and worker.stats.jobs == 1
            and worker.stats.failures["failing"] == 1
        )
        assert worker.run_jobs() == 0 and worker.stats.failures["failing"] == 2
        worker.close()


def test_engineer_fixture_requests_do_not_contain_ids(tmp_path):
    """The recorded prompts carry task text only, so replay matches any new ids."""
    from eidolon.agent.providers import fixture_dir
    from eidolon.config import settings

    seen = 0
    for path in fixture_dir(settings).glob("chat-*.json"):
        req = json.loads(path.read_text())["request"]
        first = req["messages"][0]["content"]
        if first.startswith("Task: Greeting CLI") or first.startswith("Review task: Greeting CLI"):
            seen += 1
            assert "-" not in first.split("Greeting CLI")[0]
    assert seen >= 2, "R3-d fixtures missing: run `make v2-record-fixtures`"
