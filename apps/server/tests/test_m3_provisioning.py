"""Runtime intent transaction boundaries, ownership and durable recovery (U2)."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select
from test_recruitment import _market_talent

from app.core.config import settings
from app.core.database import SessionLocal
from app.lifecycle.engine import ProvisioningEngine
from app.lifecycle.provisioners.base import ProvisionerError
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Employee
from app.models.person import Person
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.services.recruitment import RecruitmentService
from app.work import readiness
from app.work import runtime_provisioning as worker
from app.workforce import access as workforce_access


@pytest.fixture
def worker_employee(db, default_company_id):
    person = Person(name="Runtime intent", slug="runtime-intent")
    db.add(person)
    db.flush()
    employee = org_repo.create_employee(
        db,
        person_id=person.id,
        company_id=default_company_id,
        name=person.name,
        slug=person.slug,
        role="engineer",
        title="Engineer",
        status="idle",
        lifecycle_status="active",
        runtime_type="mock",
        runtime_config={},
        workspace_path=str(Path(settings.workspace_root) / person.slug),
        memory_namespace="runtime-intent",
    )
    db.commit()
    return employee


def _queue(db, employee):
    result = readiness.orchestrate(db, employee)
    db.commit()
    return int(result.job_id)


def _job(db, job_id):
    db.expire_all()
    return db.get(ProvisioningJob, job_id)


def test_recruitment_rollback_leaves_no_intent_employee_or_workspace(
    client, db, default_company_id
):
    talent = _market_talent(client)
    result = RecruitmentService().recruit_existing_person(
        db,
        listing_id=talent["listing"]["listing_id"],
        company_id=default_company_id,
        commit=False,
    )
    workspace = Path(db.get(Employee, result.employee_id).workspace_path)
    assert result.readiness["job_status"] == "pending"
    assert result.readiness["ok"] is False
    assert worker.pending_job_ids() == []
    assert not workspace.exists(), "external work ran before recruitment committed"
    db.rollback()
    assert db.get(Employee, result.employee_id) is None
    assert (
        db.scalar(select(ProvisioningJob).where(ProvisioningJob.employee_id == result.employee_id))
        is None
    )
    assert not workspace.exists()


def test_intent_is_visible_only_after_commit(db, worker_employee, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("intent creation must not provision resources")

    for operation in ("ensure_workspace", "ensure_runtime_instance", "ensure_provider_binding"):
        monkeypatch.setattr(readiness, operation, forbidden)
    result = readiness.orchestrate(db, worker_employee)
    assert result.job_status == "pending" and not result.ok
    assert worker.pending_job_ids() == []
    assert runtime_repo.get_instance_for_employee(db, worker_employee.id) is None
    db.commit()
    assert worker.pending_job_ids() == [result.job_id]
    job = db.get(ProvisioningJob, result.job_id)
    assert job.owner == "runtime" and job.source == "app.work.readiness"
    assert [item["status"] for item in result.steps] == ["pending"] * 3


def test_new_process_startup_finishes_committed_intent(db, worker_employee):
    job_id = _queue(db, worker_employee)
    # A fresh interpreter imports the application and runs its real lifespan.
    # Only persisted facts cross the process boundary; there is no in-memory wakeup.
    script = """import json
from threading import Event
from fastapi.testclient import TestClient
from app.core.database import SessionLocal
from app.main import app
from app.models.lifecycle import ProvisioningJob
from app.work import runtime_provisioning as worker

finished = Event()
scan = worker.scan_once
async def observe_scan():
    result = await scan()
    finished.set()
    return result
worker.scan_once = observe_scan
with TestClient(app):
    assert finished.wait(15), "startup scan did not finish"
    with SessionLocal() as db:
        job = db.get(ProvisioningJob, JOB_ID)
        print(json.dumps({"status": job.status, "done_steps": job.done_steps}))
""".replace("JOB_ID", str(job_id))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "EIDOLON_RUNTIME_PROVISIONING_ENABLED": "true"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"status": "done", "done_steps": 3}
    assert _job(db, job_id).claim_token is None
    assert Path(worker_employee.workspace_path).is_dir()
    assert runtime_repo.get_instance_for_employee(db, worker_employee.id) is not None


@pytest.mark.asyncio
async def test_external_step_sees_committed_employee_without_write_lock(
    db, worker_employee, monkeypatch
):
    job_id = _queue(db, worker_employee)
    original = readiness.ensure_workspace

    def inspect_boundary(employee):
        with SessionLocal() as independent:
            assert independent.get(Employee, employee.id) is not None
            job = independent.get(ProvisioningJob, job_id)
            assert job.status == "running" and job.claim_token
            # A real independent write must not wait for the worker's transaction.
            independent.get(Employee, employee.id).title = "independent writer"
            independent.commit()
        return original(employee)

    monkeypatch.setattr(readiness, "ensure_workspace", inspect_boundary)
    assert await worker.run_job(job_id)
    assert _job(db, job_id).status == "done"
    assert db.get(Employee, worker_employee.id).title == "independent writer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,reason",
    [
        ("ensure_workspace", "WORKSPACE_UNAVAILABLE"),
        ("ensure_runtime_instance", "START_FAILED"),
        ("ensure_provider_binding", "PROVIDER_UNAVAILABLE"),
    ],
)
async def test_failure_is_partial_keeps_employee_and_has_structured_reason(
    db, worker_employee, monkeypatch, operation, reason
):
    job_id = _queue(db, worker_employee)

    def fail(*args, **kwargs):
        raise RuntimeError("provisioning unavailable")

    monkeypatch.setattr(readiness, operation, fail)
    assert await worker.run_job(job_id)
    job = _job(db, job_id)
    result = readiness.orchestration_result(db, job)
    assert job.status == "partial" and not result.ok
    assert db.get(Employee, worker_employee.id) is not None
    assert [step["reason_code"] for step in result.steps if not step["ok"]] == [reason]
    assert worker.pending_job_ids() == [], "partial jobs require explicit retry"


def test_concurrent_claim_has_exactly_one_owner(db, worker_employee):
    job_id = _queue(db, worker_employee)
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return worker.claim_job(job_id)

    with ThreadPoolExecutor(max_workers=2) as threads:
        futures = [threads.submit(claim) for _ in range(2)]
        tokens = [future.result(timeout=10) for future in futures]
    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1
    assert _job(db, job_id).claim_token == winners[0]


def test_expired_claim_recovers_and_old_owner_cannot_overwrite(db, worker_employee, monkeypatch):
    job_id = _queue(db, worker_employee)
    before = worker.utcnow()
    original = worker.claim_job(job_id)
    assert original is not None
    assert worker.claim_job(job_id) is None
    monkeypatch.setattr(worker, "utcnow", lambda: before + timedelta(hours=1))
    assert worker.pending_job_ids() == [job_id]
    replacement = worker.claim_job(job_id)
    assert replacement and replacement != original
    worker.release_claim(job_id, original)
    worker._finish_job(job_id, original)
    assert _job(db, job_id).status == "running"
    assert _job(db, job_id).claim_token == replacement


def test_stale_retry_request_cannot_reset_a_new_claim(db, worker_employee):
    job_id = _queue(db, worker_employee)
    job = db.get(ProvisioningJob, job_id)
    job.status = "partial"
    db.commit()
    with SessionLocal() as independent:
        worker.retry_job(independent, independent.get(ProvisioningJob, job_id))
    token = worker.claim_job(job_id)
    assert token is not None and job.status == "partial"
    retried = worker.retry_job(db, job)
    assert retried.status == "running" and retried.claim_token == token


@pytest.mark.asyncio
async def test_cancel_releases_claim_and_later_scan_resumes(db, worker_employee, monkeypatch):
    job_id = _queue(db, worker_employee)
    started = asyncio.Event()
    original = worker._apply_step

    async def wait(resource_type, employee, policy):
        if resource_type == readiness.STEP_WORKSPACE:
            return await original(resource_type, employee, policy)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_apply_step", wait)
    task = asyncio.create_task(worker.run_job(job_id))
    await asyncio.wait_for(started.wait(), timeout=5)
    assert _job(db, job_id).done_steps == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _job(db, job_id).status == "pending"
    assert _job(db, job_id).claim_token is None
    monkeypatch.setattr(worker, "_apply_step", original)
    assert await worker.scan_once() == 1
    assert _job(db, job_id).status == "done"
    steps = db.scalars(
        select(ProvisioningStep)
        .where(
            ProvisioningStep.job_id == job_id,
        )
        .order_by(ProvisioningStep.seq)
    )
    assert [step.attempts for step in steps] == [1, 2, 1]


@pytest.mark.asyncio
async def test_owner_routes_sweeper_and_rejects_wrong_executor(db, worker_employee):
    runtime_id = _queue(db, worker_employee)
    legacy = ProvisioningJob(
        employee_id=worker_employee.id, kind="permission_change", status="pending"
    )
    db.add(legacy)
    db.commit()
    assert worker.pending_job_ids() == [runtime_id]
    assert worker.claim_job(legacy.id) is None
    healed = workforce_access.sweep_stale_provisioning_jobs()
    assert [entry["job_id"] for entry in healed] == [legacy.id]
    with pytest.raises(ProvisionerError, match="belongs to runtime"):
        await ProvisioningEngine().run(db, _job(db, runtime_id))
    with pytest.raises(ProvisionerError, match="belongs to runtime"):
        await ProvisioningEngine().retry(db, _job(db, runtime_id))
    assert _job(db, runtime_id).status == "pending"


def test_retry_api_uses_runtime_owner_and_preserves_successful_receipts(
    client, db, worker_employee, monkeypatch
):
    job_id = _queue(db, worker_employee)
    original = readiness.ensure_workspace
    monkeypatch.setattr(readiness, "ensure_workspace", lambda employee: (False, "offline"))
    client.portal.call(worker.run_job, job_id)
    assert _job(db, job_id).status == "partial"
    response = client.get(f"/api/v1/provisioning-jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["owner"] == "runtime"
    assert response.json()["steps"][0]["result_json"]["reason_code"] == "WORKSPACE_UNAVAILABLE"
    retried = client.post(f"/api/v1/provisioning-jobs/{job_id}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "pending"
    monkeypatch.setattr(readiness, "ensure_workspace", original)
    client.portal.call(worker.run_job, job_id)
    job = _job(db, job_id)
    assert job.status == "done"
    steps = list(
        db.scalars(
            select(ProvisioningStep)
            .where(ProvisioningStep.job_id == job_id)
            .order_by(ProvisioningStep.seq)
        )
    )
    assert [step.attempts for step in steps] == [2, 1, 1]
