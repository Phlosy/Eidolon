"""Project request allowance is shared by real sessions, never a dollar cap."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_m3_management_runner import source
from test_m3_management_runner import world as _world
from test_m3_runtime_admission import original as _original
from test_m3_runtime_admission import resumed
from test_m3_runtime_conversation import ScriptedAdapter, finish, read, run
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import prepare
from test_m3_telemetry import worker_world as _worker_world
from test_m3_worker_recovery import recovering as _recovering

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.models.base import utcnow
from app.models.project import Project, Task, WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeProtocolError, RuntimeSession
from app.work import dispatch, management_dispatch, request_budget, telemetry
from app.workflow.orchestrator import Orchestrator

worker_world = _worker_world
bound = _bound
world = _world
original = _original
recovering = _recovering


def limit(monkeypatch, value):
    # Before implementation this injects an unused setting, so red proves the
    # missing behavioral guard rather than failing to import a proposed module.
    monkeypatch.setitem(settings.__dict__, "runtime_project_request_limit", value)


@pytest.mark.parametrize("state", ["prepared", "accepted", "unknown", "completed", "failed"])
def test_each_original_request_consumes_allowance_regardless_of_outcome(
    db, bound, monkeypatch, state
):
    limit(monkeypatch, 1)
    first = prepare(bound)
    if state != "prepared":
        first.accepted("original-run", bound.runtime.id)
    if state == "unknown":
        first.finish_host("failed", "stream_ended")
    elif state in {"completed", "failed"}:
        first.terminal(state, "hermes.status")
    assert prepare(bound).attempt_id == first.attempt_id
    with pytest.raises(RuntimeProtocolError, match="execution_request_budget_exhausted"):
        prepare(bound, "another-request")
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1
    assert bound.task.rework_count == 0


def test_explicit_technical_attempt_consumes_same_project_allowance(db, bound, monkeypatch):
    limit(monkeypatch, 2)
    first = prepare(bound)
    first.accepted("original-run", bound.runtime.id)
    first.terminal("failed", "hermes.status")
    second = prepare(bound, retry_of=first.attempt_id)
    second.accepted("retried-run", bound.runtime.id)
    second.terminal("failed", "hermes.status")
    with pytest.raises(RuntimeProtocolError, match="execution_request_budget_exhausted"):
        prepare(bound, retry_of=second.attempt_id)
    db.expire_all()
    assert bound.work.technical_retry_count == 1
    assert bound.task.rework_count == 0
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 2


def next_session(db, bound, *, new_project=False):
    bound.work.status, bound.work.ended_at = "failed", utcnow()
    project = bound.project
    if new_project:
        project = Project(
            company_id=project.company_id, name="Separate allowance", status="in_progress"
        )
        db.add(project)
        db.flush()
    task = Task(
        project_id=project.id,
        title="Next execution",
        status="in_progress",
        assignee_id=bound.actor.id,
    )
    db.add(task)
    db.flush()
    work = project_repo.create_work_session(
        db,
        task_id=task.id,
        employee_id=bound.actor.id,
        runtime_type="hermes",
        runtime_instance_id=bound.row.id,
        provider_id=bound.provider.id,
        model="original-model",
        started_at=utcnow(),
    )
    current = RuntimeSession(f"new-session-{work.id}", bound.runtime.instance, task.id)
    work.runtime_session_ref = current.id
    db.commit()
    return work, current


@pytest.mark.parametrize("new_project", [False, True])
def test_new_session_cannot_reset_project_allowance(db, bound, monkeypatch, new_project):
    limit(monkeypatch, 1)
    first = prepare(bound)
    first.accepted("original-run", bound.runtime.id)
    first.terminal("completed", "hermes.status")
    work, current = next_session(db, bound, new_project=new_project)
    if new_project:
        telemetry.prepare_attempt(work.id, current, "next", request_key="next", kind="controlled")
    else:
        with pytest.raises(RuntimeProtocolError, match="execution_request_budget_exhausted"):
            telemetry.prepare_attempt(
                work.id, current, "next", request_key="next", kind="controlled"
            )
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1 + int(new_project)


def test_competing_request_admissions_share_atomic_limit(db, bound, monkeypatch):
    limit(monkeypatch, 2)
    work_id, runtime = bound.work.id, bound.runtime
    db.rollback()

    def submit(index):
        try:
            telemetry.prepare_attempt(
                work_id,
                replace(runtime),
                "input",
                request_key=f"parallel-{index}",
                kind="controlled",
            )
            return "admitted"
        except RuntimeProtocolError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(submit, range(4)))
    assert outcomes.count("admitted") == 2
    assert outcomes.count("execution_request_budget_exhausted") == 2
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 2


@pytest.mark.parametrize("value", [0, -1, 4097, True, float("inf"), None])
def test_invalid_request_policy_cannot_admit(db, bound, monkeypatch, value):
    limit(monkeypatch, value)
    with pytest.raises(RuntimeProtocolError, match="execution_request_budget_unavailable"):
        prepare(bound)
    assert db.scalar(select(RuntimeAttempt)) is None


def test_fresh_process_observes_consumed_project_allowance(db, bound, monkeypatch):
    limit(monkeypatch, 1)
    first = prepare(bound)
    first.accepted("original-run", bound.runtime.id)
    script = """
import json, sys
from app.runtimes.base import RuntimeSession, RuntimeProtocolError
from app.work.telemetry import reconstruct_attempt, prepare_attempt
_, instance, reference = reconstruct_attempt(int(sys.argv[1]))
current = RuntimeSession(reference.local_session_ref, instance, int(sys.argv[3]))
try:
    prepare_attempt(int(sys.argv[2]), current, "new", request_key="new", kind="controlled")
    result = "admitted"
except RuntimeProtocolError as exc:
    result = str(exc)
print(json.dumps(result))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(first.attempt_id),
            str(bound.work.id),
            str(bound.task.id),
        ],
        env={**os.environ, "EIDOLON_RUNTIME_PROJECT_REQUEST_LIMIT": "1"},
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(child.stdout) == "execution_request_budget_exhausted"
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1


@pytest.mark.parametrize("target", ["same_task", "other_task", "reassigned"])
async def test_worker_dispatch_explains_exhaustion_without_a_new_session(
    db, original, monkeypatch, target
):
    limit(monkeypatch, 1)
    old, task = resumed(db, original, target=target)
    old.recorder.terminal(
        "completed", "hermes.status" if original.kind == "hermes" else "openclaw.wait"
    )
    view = dispatch.evaluate_dispatch(db, task)
    assert not view.dispatchable
    assert view.reasons == ("runtime_unavailable",)
    assert view.readiness["request_budget"] == {
        "unit": "host_request",
        "limit": 1,
        "recorded_requests": 1,
        "remaining": 0,
        "reason": "execution_request_budget_exhausted",
    }
    await Orchestrator()._run_task(task.id)
    assert original.starts == original.sends == original.calls == []
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1
    assert task.status == "todo"


@pytest.mark.parametrize("review", [False, True])
def test_management_and_review_share_worker_request_allowance(db, original, monkeypatch, review):
    limit(monkeypatch, 1)
    old = original.history()
    old.recorder.terminal(
        "completed", "hermes.status" if original.kind == "hermes" else "openclaw.wait"
    )
    source_id = source(original.world, review=review)
    if review:
        source_id = source_id[0]
    claim = management_dispatch.claim_source(source_id, enabled=True, budget_available=True)
    assert not claim.created
    assert claim.dispatch.waiting_reason == "budget_unavailable"
    assert claim.dispatch.details["request_budget"]["remaining"] == 0
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 1


async def test_controlled_loop_stops_before_next_model_turn_on_request_exhaustion(
    db, bound, monkeypatch
):
    limit(monkeypatch, 1)
    adapter = ScriptedAdapter(read(bound), finish())
    result = await run(bound, adapter)
    assert result["reason"] == "budget_denied"
    assert len(adapter.prompts) == 1
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1
    assert bound.task.status == "in_progress"


def test_failed_commit_does_not_spend_or_publish_an_attempt(db, bound, monkeypatch):
    limit(monkeypatch, 1)

    @contextmanager
    def storage_failure():
        with SessionLocal() as inner:

            def fail(_):
                raise OperationalError("offline-commit", {}, None)

            event.listen(inner, "before_commit", fail)
            yield inner

    with pytest.raises(OperationalError):
        prepare(bound, session_factory=storage_failure)
    assert bound.runtime.attempt is None
    assert db.scalar(select(RuntimeAttempt)) is None
    first = prepare(bound)
    assert first.attempt_id is not None
    assert (
        request_budget.project_allowance(
            db, company_id=bound.project.company_id, project_id=bound.project.id
        )["remaining"]
        == 0
    )


def test_policy_change_preserves_counter_instead_of_resetting_it(db, bound, monkeypatch):
    limit(monkeypatch, 1)
    prepare(bound)
    limit(monkeypatch, 2)
    view = request_budget.project_allowance(
        db, company_id=bound.project.company_id, project_id=bound.project.id
    )
    assert view["recorded_requests"] == 1 and view["remaining"] == 1
    prepare(bound, "new-policy-request")
    with pytest.raises(RuntimeProtocolError, match="execution_request_budget_exhausted"):
        prepare(bound, "still-limited")


def test_historical_request_stays_with_captured_project(db, bound, monkeypatch):
    limit(monkeypatch, 1)
    prepare(bound)
    original_id = bound.project.id
    another = Project(company_id=bound.project.company_id, name="New current task scope")
    db.add(another)
    db.flush()
    bound.task.project_id = another.id
    db.commit()
    original_view = request_budget.project_allowance(
        db, company_id=bound.project.company_id, project_id=original_id
    )
    current_view = request_budget.project_allowance(
        db, company_id=bound.project.company_id, project_id=another.id
    )
    assert original_view["recorded_requests"] == 1 and original_view["remaining"] == 0
    assert current_view["recorded_requests"] == 0 and current_view["remaining"] == 1


@pytest.mark.parametrize("change", ["foreign_company", "foreign_request", "missing_project"])
def test_request_allowance_read_refuses_foreign_or_missing_scope(db, bound, change):
    company_id, project_id = bound.project.company_id, bound.project.id
    token = None
    if change == "foreign_company":
        company_id += 1000
    elif change == "missing_project":
        project_id += 1000
    else:
        token = set_request_identity(RequestIdentity(1, company_id + 1000, "owner", 1))
    try:
        with pytest.raises(RuntimeProtocolError, match="execution_request_budget_scope"):
            request_budget.project_allowance(db, company_id=company_id, project_id=project_id)
    finally:
        if token is not None:
            reset_request_identity(token)


def test_mock_requests_do_not_spend_real_allowance(db, bound, monkeypatch):
    limit(monkeypatch, 1)
    bound.work.status, bound.work.ended_at = "failed", utcnow()
    work = project_repo.create_work_session(
        db,
        task_id=bound.task.id,
        employee_id=bound.actor.id,
        runtime_type="mock",
        started_at=utcnow(),
    )
    current = RuntimeSession("mock-budget-session", bound.runtime.instance, bound.task.id)
    work.runtime_session_ref = current.id
    db.commit()
    first = telemetry.prepare_attempt(
        work.id, current, "mock input", request_key="worker", kind="worker"
    )
    first.terminal("failed", "mock.stream")
    telemetry.prepare_attempt(
        work.id,
        current,
        "mock input",
        request_key="worker",
        kind="worker",
        retry_of=first.attempt_id,
    )
    view = request_budget.project_allowance(
        db, company_id=bound.project.company_id, project_id=bound.project.id
    )
    assert view["recorded_requests"] == 0 and view["remaining"] == 1


async def test_read_recovery_uses_original_reserved_request_at_limit(
    db, bound, recovering, monkeypatch
):
    limit(monkeypatch, 1)
    await recovering.runner._run_task(bound.task.id)
    db.expire_all()
    assert bound.task.status == "in_review"
    assert len(recovering.sends) == 1 and len(recovering.queries) == 1
    assert db.scalar(select(func.count()).select_from(RuntimeAttempt)) == 1
    view = request_budget.project_allowance(
        db, company_id=bound.project.company_id, project_id=bound.project.id
    )
    assert view["remaining"] == 0
