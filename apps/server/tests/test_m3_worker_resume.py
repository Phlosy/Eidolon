"""Lost host ownership resumes the original Worker, never a second request.

The process/files/domain/credit transactions are real. Native transport responses
are scripted here; this is not a paid model or native container acceptance test.
"""

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_artifact_workspace_identity import conversation_world as _conversation_world
from test_m3_artifact_workspace_identity import workspace_world as _workspace_world

from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.drive import DriveNode
from app.models.economy import ComputeUsage
from app.models.event import Event
from app.models.knowledge import LearningRecord
from app.models.project import WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import accept_remote_run
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.workspace_artifacts import output_directory
from app.work import recovery, telemetry, worker_resume
from app.workflow import orchestrator as orchestration

conversation_world = _conversation_world
workspace_world = _workspace_world


def write_output(session):
    target = Path(session.instance.home_path) / output_directory(session)
    target.mkdir(parents=True, exist_ok=True)
    body = b"print('original process output')\n"
    (target / "result.py").write_bytes(body)
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "protocol": "eidolon.artifacts.v1",
                "local_session_ref": session.id,
                "request_ref": session.attempt.idempotency_key,
                "complete": True,
                "artifacts": [
                    {
                        "path": "result.py",
                        "title": "Original result",
                        "type": "source_code",
                        "size_bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                ],
            }
        )
    )


@pytest.fixture()
def orphan(db, workspace_world):
    w = workspace_world
    w.actor.status, w.actor.current_task_id = "working", w.task.id
    db.commit()
    return w


def prepare(w, *, seconds=30, accepted=True):
    w.recorder = telemetry.prepare_attempt(
        w.work.id,
        w.runtime,
        "Build original source",
        request_key="worker",
        kind="worker",
        deadline_at=None if seconds is None else utcnow() + timedelta(seconds=seconds),
    )
    if accepted:
        accept_remote_run(w.runtime, "original-run", w.runtime.id)
    write_output(w.runtime)
    return w.recorder


def transport(monkeypatch, handler):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    adapter = HermesAdapter()
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: adapter)
    return adapter


def facts(db, w):
    db.expire_all()
    attempt = db.scalar(select(RuntimeAttempt).where(RuntimeAttempt.work_session_id == w.work.id))
    return {
        "work": [w.work.status, w.work.error, w.work.cost],
        "task": [w.task.status, w.task.rework_count],
        "actor": [w.actor.status, w.actor.current_task_id],
        "attempt": None
        if attempt is None
        else [attempt.id, attempt.status, attempt.host_outcome, attempt.stop_reason],
        "collections": list(db.scalars(select(RuntimeArtifactCollection.attempt_id))),
        "nodes": list(
            db.scalars(select(DriveNode.id).where(DriveNode.work_session_id == w.work.id))
        ),
        "costs": list(
            db.scalars(select(ComputeUsage.id).where(ComputeUsage.work_session_id == w.work.id))
        ),
        "events": list(db.scalars(select(Event.id).where(Event.task_id == w.task.id))),
        "learning": list(
            db.scalars(select(LearningRecord.id).where(LearningRecord.task_id == w.task.id))
        ),
    }


PROCESS = """
import asyncio, json, os, sys
from datetime import timedelta
import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session
sys.path.insert(0, 'apps/server/tests')
from app.core.database import SessionLocal
from app.models.base import utcnow
from app.models.project import WorkSession
from app.runtimes.base import RuntimeSession, accept_remote_run
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.work import telemetry
from app.workflow import orchestrator as module
from test_m3_worker_resume import write_output

work_id, stage = int(sys.argv[1]), sys.argv[2]
if stage in {'before_send', 'accepted', 'artifacts'}:
    with SessionLocal() as db:
        work = db.get(WorkSession, work_id)
        runtime = RuntimeSession(work.runtime_session_ref,
            resolve_container_instance(work.employee_id, 'hermes'), work.task_id)
    recorder = telemetry.prepare_attempt(work_id, runtime, 'Build original source',
        request_key='worker', kind='worker', deadline_at=utcnow()+timedelta(seconds=30))
    if stage != 'before_send':
        accept_remote_run(runtime, 'original-run', runtime.id)
    write_output(runtime)
    if stage == 'artifacts':
        recorder.terminal('completed', 'hermes.status')
    os._exit(23)

requests = []
def respond(req):
    requests.append([req.method, req.url.path])
    assert req.method == 'GET' and req.url.path == '/v1/runs/original-run'
    return httpx.Response(200, json={'run_id': 'original-run', 'status': 'completed'})
original_client = httpx.AsyncClient
httpx.AsyncClient = lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw)
module.gateway.adapter_for = lambda _: HermesAdapter()
if stage == 'recover_crash':
    def die_after_result(db):
        if not db.in_nested_transaction() and any(
            isinstance(row, WorkSession) and row.id == work_id and row.status == 'completed'
            for row in db.identity_map.values()
        ):
            os._exit(24)
    event.listen(Session, 'after_commit', die_after_result)

async def run():
    owner = module.Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(8):
            while True:
                with SessionLocal() as db:
                    if db.get(WorkSession, work_id).status != 'running':
                        break
                await asyncio.sleep(.02)
        await owner._handle({'type': 'sweep'})
    finally:
        await owner.stop()
asyncio.run(run())
print(json.dumps(requests))
"""


def process(w, stage):
    return subprocess.run(
        [sys.executable, "-c", PROCESS, str(w.work.id), stage],
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("stage", ["before_send", "accepted", "artifacts"])
def test_actual_process_exit_resumes_same_worker_and_repeated_restart_has_no_effect(
    db, orphan, stage
):
    w = orphan
    crashed = process(w, stage)
    assert crashed.returncode == 23, crashed.stderr
    before = facts(db, w)
    assert before["work"][0] == "running" and not before["costs"]
    db.rollback()
    recovered = process(w, "recover")
    assert recovered.returncode == 0, recovered.stderr
    after = facts(db, w)
    assert after["attempt"][0] == before["attempt"][0]
    if stage == "before_send":
        assert after["work"][:2] == ["failed", "execution_reference_unknown"]
        assert after["task"][0] == "blocked" and after["attempt"][1] == "unknown"
        assert not after["nodes"] and json.loads(recovered.stdout) == []
    else:
        assert after["work"][0] == "completed" and after["task"][0] == "in_review"
        assert after["attempt"][1:3] == ["completed", "completed"]
        assert len(after["nodes"]) == len(after["collections"]) == 1
    assert after["work"][2]["duration_sec"] is None
    assert len(after["costs"]) == 1 and not after["learning"]
    assert after["actor"] == ["idle", None]
    db.rollback()
    for _ in range(2):
        repeated = process(w, "recover")
        assert repeated.returncode == 0, repeated.stderr
        assert json.loads(repeated.stdout) == []
        assert facts(db, w) == after
        db.rollback()


def test_actual_exit_after_recovered_domain_commit_preserves_host_receipt(db, orphan):
    w = orphan
    assert process(w, "artifacts").returncode == 23
    crashed = process(w, "recover_crash")
    assert crashed.returncode == 24, crashed.stderr
    saved = facts(db, w)
    assert saved["attempt"][1:3] == ["completed", "completed"]
    assert len(saved["nodes"]) == len(saved["costs"]) == 1
    db.rollback()
    assert process(w, "recover").returncode == 0
    assert facts(db, w) == saved


async def test_active_run_is_adopted_without_another_send_or_premature_failure(
    db, orphan, monkeypatch
):
    w = orphan
    prepare(w)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def respond(req):
        requests.append(req.method)
        assert req.method == "GET"
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"run_id": "original-run", "status": "completed"})

    transport(monkeypatch, respond)
    owner = orchestration.Orchestrator()
    owner.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert facts(db, w)["work"][0] == "running"
        assert list(owner._workers) == [w.work.id]
        await owner._handle({"type": "sweep"})
        assert requests == ["GET"]
        release.set()
        await asyncio.wait_for(asyncio.gather(*owner._workers.values()), 3)
        assert facts(db, w)["work"][0] == "completed"
    finally:
        release.set()
        await owner.stop()


async def test_expired_deadline_stops_original_run_without_guessing_remote_terminal(
    db, orphan, monkeypatch
):
    w = orphan
    prepare(w, seconds=-1)
    requests = []

    def respond(req):
        requests.append([req.method, req.url.path])
        return httpx.Response(200, json={"run_id": "original-run", "status": "running"})

    transport(monkeypatch, respond)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    result = facts(db, w)
    assert result["work"][:2] == ["failed", "execution_deadline_expired"]
    assert result["task"][0] == "blocked" and result["attempt"][1] == "unknown"
    assert requests == [["POST", "/v1/runs/original-run/stop"], ["GET", "/v1/runs/original-run"]]


def test_running_worker_page_is_bounded_and_company_scoped(db, orphan):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )

    w = orphan
    through = recovery.work_session_boundary()
    page = recovery.running_workers(through_id=through, limit=1)
    assert [item.work_session_id for item in page.items] == [w.work.id]
    assert page.complete and page.cursor == w.work.id
    token = set_request_identity(RequestIdentity(1, 999999, "owner", 1))
    try:
        assert recovery.running_workers(through_id=through).items == ()
    finally:
        reset_request_identity(token)


@pytest.mark.parametrize(
    "damage", ["no_attempt", "unknown_deadline", "failed_remote", "missing_run"]
)
async def test_missing_or_failed_execution_never_becomes_delivery(db, orphan, monkeypatch, damage):
    w = orphan
    if damage != "no_attempt":
        prepare(w, seconds=None if damage == "unknown_deadline" else 30)
    calls = []

    def respond(req):
        calls.append([req.method, req.url.path])
        assert req.url.path != "/v1/runs"
        if damage == "missing_run":
            return httpx.Response(404)
        return httpx.Response(200, json={"run_id": "original-run", "status": "failed"})

    transport(monkeypatch, respond)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    saved = facts(db, w)
    assert saved["work"][0] == "failed" and saved["task"][0] == "blocked"
    assert not saved["nodes"] and not saved["collections"]
    if damage == "no_attempt":
        assert saved["work"][1] == "execution_attempt_unknown"
        assert saved["attempt"] is None and calls == []
    elif damage == "unknown_deadline":
        assert saved["work"][1] == "execution_deadline_unknown"
        assert calls[0] == ["POST", "/v1/runs/original-run/stop"]
    elif damage == "missing_run":
        assert saved["attempt"][1] == "unknown"
        assert db.get(RuntimeAttempt, w.recorder.attempt_id).ended_at is None
    else:
        assert saved["attempt"][1:3] == ["failed", "failed"]
        assert saved["work"][1] == "remote_failed"


async def test_unknown_remote_wait_expires_at_original_limit(db, orphan, monkeypatch):
    w = orphan
    prepare(w, seconds=0.15)
    calls = []

    def respond(req):
        calls.append(req.method)
        return httpx.Response(200, json={"run_id": "original-run", "status": "unknown"})

    transport(monkeypatch, respond)
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(orchestration.Orchestrator()._resume_worker(w.work.id), 2)
    assert asyncio.get_running_loop().time() - started < 1
    saved = facts(db, w)
    assert saved["work"][:2] == ["failed", "execution_deadline_expired"]
    assert saved["attempt"][1] == "unknown" and "POST" in calls
    assert db.get(RuntimeAttempt, w.recorder.attempt_id).ended_at is None


async def test_transient_query_failure_uses_durable_read_retry_without_new_attempt(
    db, orphan, monkeypatch
):
    w = orphan
    prepare(w)
    calls = []

    def respond(req):
        assert req.method == "GET"
        calls.append(req.url.path)
        if len(calls) == 1:
            raise httpx.ReadError("sensitive-transport-detail")
        with SessionLocal() as independent:
            assert independent.get(WorkSession, w.work.id).technical_retry_count == 1
        return httpx.Response(200, json={"run_id": "original-run", "status": "completed"})

    transport(monkeypatch, respond)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    assert facts(db, w)["work"][0] == "completed"
    assert len(calls) == 2 and w.work.technical_retry_count == 1
    assert len(list(db.scalars(select(RuntimeAttempt)))) == 1


@pytest.mark.parametrize("change", ["blocked", "cancelled", "rebound", "stop"])
async def test_original_authority_is_rechecked_after_native_query(db, orphan, monkeypatch, change):
    w = orphan
    prepare(w)
    calls = []

    def respond(req):
        calls.append(req.method)
        if len(calls) == 1:
            with SessionLocal() as independent:
                if change in {"blocked", "cancelled"}:
                    task = independent.get(type(w.task), w.task.id)
                    task.status = change
                elif change == "rebound":
                    row = independent.get(type(w.row), w.row.id)
                    row.image_digest = "sha256:replacement"
                else:
                    telemetry.record_stop_intent(independent, w.recorder.attempt_id, "reassigned")
                independent.commit()
        return httpx.Response(200, json={"run_id": "original-run", "status": "completed"})

    transport(monkeypatch, respond)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    result = facts(db, w)
    assert result["work"][0] == "failed" and not result["nodes"]
    assert result["task"][0] == (change if change in {"blocked", "cancelled"} else "blocked")
    if change == "rebound":
        assert calls == ["GET"] and result["attempt"][1] == "unknown"
    if change == "stop":
        assert result["attempt"][3] == "reassigned"


async def test_stop_during_artifact_read_cannot_accept_late_output(db, orphan, monkeypatch):
    w = orphan
    prepare(w)
    adapter = transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"run_id": "original-run", "status": "completed"}),
    )
    collect = adapter.get_artifacts

    async def late(session):
        outputs = await collect(session)
        w.recorder.request_stop("cancelled")
        return outputs

    monkeypatch.setattr(adapter, "get_artifacts", late)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    result = facts(db, w)
    assert result["work"][0] == "failed" and not result["nodes"]
    assert result["attempt"][1:4] == ["completed", "failed", "cancelled"]


@pytest.mark.parametrize("damage", ["partial", "hash", "workspace"])
async def test_recovered_completion_still_requires_original_complete_artifacts(
    db, orphan, monkeypatch, damage
):
    w = orphan
    prepare(w)
    root = Path(w.row.workspace_path)
    manifest = root / output_directory(w.runtime) / "manifest.json"
    if damage == "workspace":
        root.rename(root.with_name("lost-workspace"))
        root.mkdir()
    else:
        value = json.loads(manifest.read_text())
        if damage == "partial":
            value["complete"] = False
        else:
            value["artifacts"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(value))
    transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"run_id": "original-run", "status": "completed"}),
    )
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    saved = facts(db, w)
    assert saved["work"][0] == "failed" and not saved["nodes"] and not saved["collections"]
    assert saved["attempt"][1:3] == ["completed", "failed"]


async def test_real_storage_failure_rolls_back_recovered_domain_and_host_then_retries(
    db, orphan, monkeypatch
):
    w = orphan
    prepare(w)
    transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"run_id": "original-run", "status": "completed"}),
    )
    db.execute(
        text(
            "CREATE TRIGGER reject_recovery_host BEFORE UPDATE ON runtime_attempts "
            "WHEN NEW.host_outcome = 'completed' "
            "BEGIN SELECT RAISE(ABORT, 'injected recovery host failure'); END"
        )
    )
    db.commit()
    try:
        with pytest.raises(IntegrityError, match="injected recovery host failure"):
            await orchestration.Orchestrator()._resume_worker(w.work.id)
        failed = facts(db, w)
        assert failed["work"][0] == "running" and failed["task"][0] == "in_progress"
        assert failed["attempt"][1:3] == ["completed", None]
        assert not failed["collections"] and not failed["nodes"] and not failed["costs"]
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER reject_recovery_host"))
        db.commit()
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    saved = facts(db, w)
    assert saved["work"][0] == "completed" and len(saved["costs"]) == 1
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    assert facts(db, w) == saved


async def test_recovery_does_not_change_other_sessions_skill_results(db, orphan, monkeypatch):
    from app.repositories import knowledge

    w = orphan
    prepare(w)
    old_work = db.scalar(select(WorkSession).where(WorkSession.id != w.work.id))
    usages = []
    for source, label in [(old_work, "previous"), (w.work, "original")]:
        skill = knowledge.create_skill(db, employee_id=w.actor.id, name=f"resume-{label}")
        usages.append(
            knowledge.create_skill_usage(
                db,
                employee_id=w.actor.id,
                task_id=w.task.id,
                skill_id=skill.id,
                work_session_id=source.id,
                success=False,
            )
        )
    db.commit()
    transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"run_id": "original-run", "status": "completed"}),
    )
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    db.expire_all()
    assert [item.success for item in usages] == [False, True]


async def test_shutdown_joins_recovery_and_original_remote_stop(db, orphan, monkeypatch):
    w = orphan
    prepare(w)
    waiting, stop_entered, stop_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(req):
        calls.append(req.method)
        if req.method == "POST":
            stop_entered.set()
            await stop_release.wait()
            return httpx.Response(200)
        if len(calls) == 1:
            waiting.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"run_id": "original-run", "status": "failed"})

    transport(monkeypatch, respond)
    owner = orchestration.Orchestrator()
    owner.start()
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        stopping = asyncio.create_task(owner.stop())
        await asyncio.wait_for(stop_entered.wait(), 2)
        assert not stopping.done() and facts(db, w)["work"][0] == "running"
        stop_release.set()
        await asyncio.wait_for(stopping, 2)
        assert not owner._workers
        result = facts(db, w)
        assert result["work"][0] == "failed" and result["attempt"][3] == "shutdown"
        assert result["attempt"][2] == "cancelled"
        assert result["attempt"][1] == "failed" and calls == ["GET", "POST", "GET"]
    finally:
        stop_release.set()
        await owner.stop()


@pytest.mark.parametrize("limit", [True, 0, 101])
def test_invalid_running_recovery_page_is_rejected_before_read(limit):
    with pytest.raises(ValueError, match="invalid recovery page"):
        recovery.running_workers(
            through_id=0, limit=limit, session_factory=lambda: pytest.fail("read")
        )


async def test_foreign_company_cannot_resume_or_stop_original_worker(db, orphan, monkeypatch):
    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )
    from app.runtimes.base import RuntimeProtocolError

    w = orphan
    prepare(w)
    transport(monkeypatch, lambda req: pytest.fail("foreign I/O"))
    token = set_request_identity(RequestIdentity(1, 99999, "owner", 1))
    try:
        with pytest.raises(RuntimeProtocolError, match="execution_scope_unavailable"):
            await orchestration.Orchestrator()._resume_worker(w.work.id)
    finally:
        reset_request_identity(token)
    assert facts(db, w)["work"][0] == "running"


async def test_scanner_excludes_new_cohort_and_limits_owned_recovery(db, orphan, monkeypatch):
    from app.repositories import project as projects

    w = orphan
    works = [w.work]
    for _ in range(5):
        works.append(
            projects.create_work_session(
                db,
                task_id=w.task.id,
                employee_id=w.actor.id,
                runtime_type="hermes",
                runtime_instance_id=w.row.id,
                provider_id=w.provider.id,
                model="original-model",
                started_at=utcnow(),
            )
        )
    db.commit()
    owner = orchestration.Orchestrator()
    owner.start()
    entered, release = [], asyncio.Event()

    async def hold(work_id):
        entered.append(work_id)
        await release.wait()

    monkeypatch.setattr(owner, "_resume_worker", hold)
    later = projects.create_work_session(
        db,
        task_id=w.task.id,
        employee_id=w.actor.id,
        runtime_type="hermes",
        runtime_instance_id=w.row.id,
        provider_id=w.provider.id,
        model="original-model",
        started_at=utcnow(),
    )
    db.commit()
    try:
        await owner._handle({"type": "sweep"})
        await asyncio.sleep(0)
        assert entered == [work.id for work in works[:4]]
        await owner._handle({"type": "sweep"})
        assert len(owner._workers) == 4
        release.set()
        await asyncio.gather(*owner._workers.values())
        await asyncio.sleep(0)
        await owner._handle({"type": "sweep"})
        await asyncio.sleep(0)
        assert entered == [work.id for work in works] and later.id not in entered
        await asyncio.gather(*owner._workers.values())
    finally:
        release.set()
        await owner.stop()


async def test_storage_failed_owner_rewinds_page_for_retry(db, orphan, monkeypatch):
    w = orphan
    prepare(w)
    actual = worker_resume.resume_worker
    calls = []

    async def fail_once(work_id, **kwargs):
        calls.append(work_id)
        if len(calls) == 1:
            raise IntegrityError("private statement", {}, Exception("private storage text"))
        return await actual(work_id, **kwargs)

    monkeypatch.setattr(worker_resume, "resume_worker", fail_once)
    transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"run_id": "original-run", "status": "completed"}),
    )
    owner = orchestration.Orchestrator()
    owner.start()
    try:
        async with asyncio.timeout(3):
            while facts(db, w)["work"][0] == "running":
                await asyncio.sleep(0.02)
        assert calls == [w.work.id, w.work.id]
        assert facts(db, w)["work"][0] == "completed"
    finally:
        await owner.stop()


async def test_current_live_owner_is_excluded_from_startup_recovery(db, orphan, monkeypatch):
    w = orphan
    prepare(w)
    owner = orchestration.Orchestrator()
    owner.start()
    current = asyncio.create_task(asyncio.Event().wait())
    owner._workers[w.work.id] = current
    monkeypatch.setattr(owner, "_resume_worker", lambda _: pytest.fail("stole live owner"))
    try:
        await owner._handle({"type": "sweep"})
        await asyncio.sleep(0)
        assert not current.done() and owner._workers[w.work.id] is current
        assert facts(db, w)["work"][0] == "running"
    finally:
        await owner.stop()


@pytest.mark.parametrize("state", ["completed", "unknown"])
async def test_openclaw_recovery_uses_original_native_run_and_manifest(
    db, orphan, monkeypatch, state
):
    from app.repositories import project as projects
    from app.runtimes.base import RuntimeSession
    from app.runtimes.container_common import resolve_container_instance
    from app.runtimes.openclaw.adapter import OpenClawAdapter

    w = orphan
    w.work.status, w.work.ended_at = "failed", utcnow()
    w.row.runtime_type = "openclaw"
    w.actor.runtime_type = "openclaw"
    w.work = projects.create_work_session(
        db,
        task_id=w.task.id,
        employee_id=w.actor.id,
        runtime_type="openclaw",
        runtime_instance_id=w.row.id,
        provider_id=w.provider.id,
        model="original-model",
        runtime_session_ref="original-openclaw-local",
        started_at=utcnow(),
    )
    db.commit()
    w.runtime = RuntimeSession(
        w.work.runtime_session_ref,
        resolve_container_instance(w.actor.id, "openclaw"),
        w.task.id,
    )
    prepare(w, seconds=30 if state == "completed" else 0.15, accepted=False)
    accept_remote_run(w.runtime, "original-openclaw-run", "agent:main:main")
    calls = []

    class Gateway:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call(self, method, params, **kwargs):
            calls.append([method, params])
            if method == "agent.wait":
                assert params["runId"] == "original-openclaw-run"
                return {"runId": params["runId"], "status": state}
            if method == "chat.abort":
                assert params == {"sessionKey": "agent:main:main", "runId": "original-openclaw-run"}
                return {"aborted": True}
            if method == "artifacts.list":
                return {"artifacts": []}
            pytest.fail(f"unexpected native operation {method}")

    adapter = OpenClawAdapter()
    monkeypatch.setattr(adapter, "_connect", lambda _: Gateway())
    monkeypatch.setattr(orchestration.gateway, "adapter_for", lambda _: adapter)
    await orchestration.Orchestrator()._resume_worker(w.work.id)
    saved = facts(db, w)
    if state == "completed":
        assert saved["work"][0] == "completed" and len(saved["nodes"]) == 1
    else:
        assert saved["work"][0] == "failed" and saved["attempt"][1] == "unknown"
        assert any(call[0] == "chat.abort" for call in calls)
    assert all(call[0] != "chat.send" for call in calls)
