"""Actual process death across the existing runtime provisioning boundaries (U16)."""

import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select
from test_m3_provisioning import worker_employee as _worker_employee

from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.provider import ModelBinding
from app.models.runtime import RuntimeInstance
from app.services.seed import ensure_employee_runtime_state
from app.work import readiness

worker_employee = _worker_employee

_CRASH = """
import asyncio
import os
import sys
from pathlib import Path
from app.main import app
from app.core.database import SessionLocal
from app.models.organization import Employee
from app.work import readiness, runtime_provisioning as worker

employee_id, window = int(sys.argv[1]), sys.argv[2]
runtime_type = sys.argv[3] if len(sys.argv) > 3 else 'mock'
with SessionLocal() as db:
    employee = db.get(Employee, employee_id)
    result = readiness.orchestrate(
        db, employee, policy=readiness.RuntimePolicy(
            runtime_type, 'mock' if runtime_type == 'mock' else 'docker'
        )
    )
    db.commit()
    job_id = result.job_id
if window == 'intent':
    os._exit(23)
if window == 'claim':
    claim = worker.claim_job
    def crash_claim(identifier):
        assert claim(identifier)
        os._exit(23)
    worker.claim_job = crash_claim
if window in {'workspace', 'runtime'}:
    apply = worker._apply_step
    async def crash_effect(resource_type, employee, policy):
        result = await apply(resource_type, employee, policy)
        assert result[0], result
        if resource_type == window:
            Path(employee.workspace_path, 'preserved.txt').write_text('original resource')
            os._exit(23)
        return result
    worker._apply_step = crash_effect
if window == 'receipts':
    worker._finish_job = lambda *args: os._exit(23)
asyncio.run(worker.run_job(job_id))
raise AssertionError('crash window was not reached')
"""

_START = """
import json
import os
import sys
from datetime import timedelta
from threading import Event
from fastapi.testclient import TestClient
from app.main import app
from app.work import runtime_provisioning as worker

hours, crash = int(sys.argv[1]), sys.argv[2] == 'crash'
if hours:
    now = worker.utcnow() + timedelta(hours=hours)
    worker.utcnow = lambda: now
if crash:
    worker._finish_job = lambda *args: os._exit(24)
finished = Event()
scans = []
scan = worker.scan_once
async def observe_scan():
    scans.append(await scan())
    finished.set()
    return scans[-1]
worker.scan_once = observe_scan
with TestClient(app):
    assert finished.wait(15), 'startup scan did not finish'
print(json.dumps({'completed': scans[0]}))
"""


def _process(script, *args, expected=0):
    result = subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        env={
            **os.environ,
            "EIDOLON_RUNTIME_PROVISIONING_ENABLED": "true",
            "EIDOLON_RUNTIME_PROVISIONING_POLL_SECONDS": "60",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        return json.loads(result.stdout.strip().splitlines()[-1])


def _facts(db, employee_id):
    db.expire_all()
    job = db.scalars(
        select(ProvisioningJob).where(ProvisioningJob.employee_id == employee_id)
    ).one()
    steps = db.scalars(
        select(ProvisioningStep)
        .where(ProvisioningStep.job_id == job.id)
        .order_by(ProvisioningStep.seq)
    ).all()
    runtimes = db.scalars(
        select(RuntimeInstance).where(RuntimeInstance.employee_id == employee_id)
    ).all()
    bindings = db.scalars(select(ModelBinding).where(ModelBinding.employee_id == employee_id))
    return {
        "job": {
            "id": job.id,
            "owner": job.owner,
            "status": job.status,
            "claim_token": job.claim_token,
            "claimed_at": job.claimed_at,
            "completed_at": job.completed_at,
            "done_steps": job.done_steps,
            "metadata": job.metadata_json,
        },
        "steps": [
            {
                "id": step.id,
                "resource": step.resource_type,
                "status": step.status,
                "attempts": step.attempts,
                "result": step.result_json,
                "started_at": step.started_at,
                "completed_at": step.completed_at,
            }
            for step in steps
        ],
        "runtimes": [
            {
                "id": row.id,
                "resource_key": row.resource_key,
                "workspace": row.workspace_path,
                "started_at": row.started_at,
                "type": row.runtime_type,
                "deployment": row.deployment_mode,
                "status": row.status,
                "provisioning_token": row.provisioning_token,
            }
            for row in runtimes
        ],
        "bindings": [row.id for row in bindings],
    }


@pytest.mark.parametrize("window", ["intent", "claim", "workspace", "runtime", "receipts"])
def test_process_crashes_reuse_original_provisioning_facts(
    db, worker_employee, tmp_path, window, record_property
):
    employee_id = worker_employee.id
    workspace = tmp_path / "owned-workspace"
    worker_employee.workspace_path = str(workspace)
    db.commit()

    _process(_CRASH, employee_id, window, expected=23)
    before = _facts(db, employee_id)
    assert before["job"]["owner"] == "runtime"
    assert before["job"]["metadata"]["policy"]["runtime_type"] == "mock"
    assert before["bindings"] == []
    if window == "intent":
        assert before["job"]["status"] == "pending"
        assert before["job"]["claim_token"] is None
    else:
        assert before["job"]["status"] == "running"
        assert before["job"]["claim_token"]
        # A real startup cannot infer death from a running row or steal its lease.
        assert _process(_START, 0, "finish") == {"completed": 0}
        assert _facts(db, employee_id) == before
    expected_attempts = {
        "intent": [0, 0, 0],
        "claim": [0, 0, 0],
        "workspace": [1, 0, 0],
        "runtime": [1, 1, 0],
        "receipts": [1, 1, 1],
    }
    assert [step["attempts"] for step in before["steps"]] == expected_attempts[window]
    if window in {"workspace", "runtime"}:
        assert before["steps"][0 if window == "workspace" else 1]["status"] == "running"
        assert (workspace / "preserved.txt").read_text() == "original resource"
    if window in {"runtime", "receipts"}:
        assert len(before["runtimes"]) == 1
    if window == "receipts":
        assert before["job"]["done_steps"] == 3

    # Only the worker's lease clock moves; persisted timestamps and claim guards stay real.
    _process(_START, 1, "crash", expected=24)
    recovered = _facts(db, employee_id)
    assert recovered["job"]["id"] == before["job"]["id"]
    assert recovered["job"]["status"] == "running"
    assert recovered["job"]["claim_token"] != before["job"]["claim_token"]
    assert recovered["job"]["done_steps"] == 3
    assert all(step["status"] == "done" for step in recovered["steps"])
    assert [step["id"] for step in recovered["steps"]] == [s["id"] for s in before["steps"]]
    for old, new in zip(before["steps"], recovered["steps"], strict=True):
        if old["status"] == "done":
            assert new == old, "completed receipts must not be re-executed or rewritten"
        else:
            assert new["attempts"] == old["attempts"] + 1
    assert len(recovered["runtimes"]) == 1
    if before["runtimes"]:
        assert recovered["runtimes"] == before["runtimes"]
    assert recovered["runtimes"][0]["type"] == "mock"
    assert recovered["runtimes"][0]["deployment"] == "mock"
    assert recovered["runtimes"][0]["provisioning_token"] is None

    assert _process(_START, 2, "finish") == {"completed": 1}
    final = _facts(db, employee_id)
    assert final["job"]["status"] == "done"
    assert final["job"]["claim_token"] is None
    assert final["job"]["claimed_at"] is None
    assert final["job"]["completed_at"] is not None
    assert final["steps"] == recovered["steps"]
    assert final["runtimes"] == recovered["runtimes"]
    assert final["bindings"] == []
    for _ in range(2):
        assert _process(_START, 3, "finish") == {"completed": 0}
        assert _facts(db, employee_id) == final
    assert workspace.is_dir()
    if window in {"workspace", "runtime"}:
        assert (workspace / "preserved.txt").read_text() == "original resource"
    record_property(
        "process_facts",
        json.dumps(
            {
                "window": window,
                "exit_codes": [23, 24],
                "before": before,
                "recovered": recovered,
                "final": final,
            },
            default=str,
        ),
    )


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
def test_restart_does_not_seed_mock_over_a_claimed_real_intent(
    db, worker_employee, tmp_path, runtime_type, record_property
):
    employee_id = worker_employee.id
    worker_employee.runtime_type = runtime_type
    worker_employee.workspace_path = str(tmp_path / "owned-real-workspace")
    db.commit()
    _process(_CRASH, employee_id, "claim", runtime_type, expected=23)
    before = _facts(db, employee_id)
    assert before["runtimes"] == []
    assert before["job"]["metadata"]["policy"]["runtime_type"] == runtime_type
    assert before["job"]["claim_token"]
    # Do not expire the real intent or invoke any Docker/Provider operation.
    assert _process(_START, 0, "finish") == {"completed": 0}
    assert _facts(db, employee_id) == before
    record_property(
        "process_facts", json.dumps({"exit_code": 23, "before_and_after": before}, default=str)
    )


@pytest.mark.parametrize("status", ["partial", "failed", "done"])
def test_seed_does_not_replace_missing_resource_of_an_existing_intent(db, worker_employee, status):
    # A failed/finished history is not permission to invent a successful mock resource.
    result = readiness.orchestrate(db, worker_employee)
    job = db.get(ProvisioningJob, result.job_id)
    job.status = status
    db.commit()
    before = _facts(db, worker_employee.id)
    ensure_employee_runtime_state(db)
    assert _facts(db, worker_employee.id) == before


def test_legacy_lifecycle_owner_still_gets_one_seed_runtime(db, worker_employee):
    db.add(
        ProvisioningJob(
            employee_id=worker_employee.id, owner="lifecycle", kind="onboarding", status="done"
        )
    )
    db.commit()
    ensure_employee_runtime_state(db)
    original = _facts(db, worker_employee.id)
    assert len(original["runtimes"]) == 1
    assert original["runtimes"][0]["type"] == "mock"
    ensure_employee_runtime_state(db)
    assert _facts(db, worker_employee.id) == original
