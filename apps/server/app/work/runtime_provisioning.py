"""Execute committed runtime intents with durable, owner-scoped claims.

The recruiting transaction only writes the plan. A separate session claims it;
filesystem/external work runs without a caller transaction or a database write lock.
Partial results await explicit retry. A lease recovers a process that died mid-job.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.redaction import redact
from app.models.base import utcnow
from app.models.enums import (
    LifecycleStatus,
    ProvisioningJobOwner,
    ProvisioningJobStatus,
    ProvisioningStepStatus,
)
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Employee
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import runtimes as runtime_repo
from app.work import readiness
from app.work.production import ProvisioningFailureReason as Reason

logger = get_logger(__name__)
OWNER = ProvisioningJobOwner.runtime.value
_FAILURE_REASONS = {
    readiness.STEP_WORKSPACE: Reason.workspace_unavailable.value,
    readiness.STEP_RUNTIME: Reason.start_failed.value,
    readiness.STEP_PROVIDER: Reason.provider_unavailable.value,
}


def _claimable():
    # Longer than the bounded three-step job; renewed before each step.
    lease = timedelta(seconds=max(30, 3 * settings.provisioning_step_timeout_seconds + 30))
    return or_(
        ProvisioningJob.status == ProvisioningJobStatus.pending.value,
        and_(
            ProvisioningJob.status == ProvisioningJobStatus.running.value,
            or_(
                ProvisioningJob.claimed_at.is_(None), ProvisioningJob.claimed_at <= utcnow() - lease
            ),
        ),
    )


def pending_job_ids() -> list[int]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ProvisioningJob.id)
                .where(ProvisioningJob.owner == OWNER, _claimable())
                .order_by(ProvisioningJob.id)
                .limit(100)
            )
        )


def claim_job(job_id: int) -> str | None:
    token = str(uuid4())
    with SessionLocal() as db:
        result = db.execute(
            update(ProvisioningJob)
            .where(ProvisioningJob.id == job_id, ProvisioningJob.owner == OWNER, _claimable())
            .values(
                status=ProvisioningJobStatus.running.value,
                claim_token=token,
                claimed_at=utcnow(),
                completed_at=None,
            )
        )
        if result.rowcount != 1:
            return None
        db.execute(
            update(ProvisioningStep)
            .where(
                ProvisioningStep.job_id == job_id,
                ProvisioningStep.status == ProvisioningStepStatus.running.value,
            )
            .values(status=ProvisioningStepStatus.pending.value, started_at=None, completed_at=None)
        )
        db.commit()
    return token


def _owned_job(db: Session, job_id: int, token: str) -> ProvisioningJob | None:
    # Acquire the short write transaction before touching receipts. A stale
    # process must not overwrite a successor's claim between a read and a write.
    result = db.execute(
        update(ProvisioningJob)
        .where(
            ProvisioningJob.id == job_id,
            ProvisioningJob.owner == OWNER,
            ProvisioningJob.status == ProvisioningJobStatus.running.value,
            ProvisioningJob.claim_token == token,
        )
        .values(claimed_at=utcnow())
    )
    return db.get(ProvisioningJob, job_id) if result.rowcount == 1 else None


def release_claim(job_id: int, token: str) -> None:
    with SessionLocal() as db:
        db.execute(
            update(ProvisioningJob)
            .where(
                ProvisioningJob.id == job_id,
                ProvisioningJob.owner == OWNER,
                ProvisioningJob.status == ProvisioningJobStatus.running.value,
                ProvisioningJob.claim_token == token,
            )
            .values(status=ProvisioningJobStatus.pending.value, claim_token=None, claimed_at=None)
        )
        db.commit()


async def _apply_step(
    resource_type: str,
    employee: Employee,
    policy: readiness.RuntimePolicy,
) -> tuple[bool, str]:
    if resource_type == readiness.STEP_WORKSPACE:
        # employee is detached; no database connection remains open during I/O.
        return await asyncio.to_thread(readiness.ensure_workspace, employee)
    with SessionLocal() as db:
        current = db.get(Employee, int(employee.id))
        if current is None or current.company_id != employee.company_id:
            raise readiness.ReadinessError("employee ownership changed during provisioning")
        if resource_type == readiness.STEP_RUNTIME:
            ok, detail, _ = readiness.ensure_runtime_instance(db, current, policy)
        elif resource_type == readiness.STEP_PROVIDER:
            ok, detail, binding_id = readiness.ensure_provider_binding(db, current, policy)
            instance = runtime_repo.get_instance_for_employee(db, int(current.id))
            if (
                binding_id is not None
                and instance is not None
                and instance.model_binding_id is None
            ):
                instance.model_binding_id = binding_id
        else:
            raise readiness.ReadinessError(f"unknown runtime provisioning step: {resource_type}")
        db.commit()
        return ok, detail


async def _run_step(job_id: int, token: str, step_id: int) -> None:
    with SessionLocal() as db:
        job = _owned_job(db, job_id, token)
        step = db.get(ProvisioningStep, step_id)
        if job is None or step is None or step.status != ProvisioningStepStatus.pending.value:
            return
        employee = db.get(Employee, job.employee_id)
        if employee is None:
            raise readiness.ReadinessError("employee no longer exists")
        if employee.lifecycle_status not in {
            LifecycleStatus.active.value,
            LifecycleStatus.onboarding.value,
        }:
            raise readiness.ReadinessError(f"employee is {employee.lifecycle_status}")
        metadata = job.metadata_json or {}
        if metadata.get("company_id", employee.company_id) != employee.company_id:
            raise readiness.ReadinessError("intent belongs to a different company")
        policy = readiness.RuntimePolicy(**metadata["policy"])
        resource_type = step.resource_type
        step.status = ProvisioningStepStatus.running.value
        step.started_at = utcnow()
        step.completed_at = None
        step.attempts += 1
        step.error = None
        step.result_json = {}
        job.claimed_at = utcnow()
        db.commit()
        db.expunge(employee)
    try:
        async with asyncio.timeout(settings.provisioning_step_timeout_seconds):
            ok, detail = await _apply_step(resource_type, employee, policy)
    except Exception as exc:
        ok, detail = False, str(exc) or type(exc).__name__
    detail = redact(detail)
    with SessionLocal() as db:
        job = _owned_job(db, job_id, token)
        if job is None:
            return
        step = db.get(ProvisioningStep, step_id)
        step.status = (
            ProvisioningStepStatus.done.value if ok else ProvisioningStepStatus.failed.value
        )
        step.error = None if ok else detail
        step.completed_at = utcnow()
        step.result_json = {
            "ok": ok,
            "detail": detail,
            "reason_code": None
            if ok
            else _FAILURE_REASONS.get(resource_type, Reason.provisioning_failed.value),
        }
        db.flush()
        job.done_steps = db.scalar(
            select(func.count())
            .select_from(ProvisioningStep)
            .where(
                ProvisioningStep.job_id == job_id,
                ProvisioningStep.status == ProvisioningStepStatus.done.value,
            )
        )
        db.commit()


def _fail_remaining(job_id: int, token: str, error: Exception) -> None:
    with SessionLocal() as db:
        if _owned_job(db, job_id, token) is None:
            return
        for step in lifecycle_repo.list_steps(db, job_id):
            if step.status in {
                ProvisioningStepStatus.pending.value,
                ProvisioningStepStatus.running.value,
            }:
                step.status = ProvisioningStepStatus.failed.value
                step.error = redact(str(error) or type(error).__name__)
                step.completed_at = utcnow()
                step.result_json = {
                    "ok": False,
                    "detail": step.error,
                    "reason_code": Reason.provisioning_failed.value,
                }
        db.commit()


def _finish_job(job_id: int, token: str) -> None:
    with SessionLocal() as db:
        job = _owned_job(db, job_id, token)
        if job is None:
            return
        steps = lifecycle_repo.list_steps(db, job_id)
        job.done_steps = sum(step.status == ProvisioningStepStatus.done.value for step in steps)
        if not steps or len(steps) != job.total_steps:
            job.status = ProvisioningJobStatus.failed.value
            job.reason = f"{Reason.provisioning_failed.value}: incomplete provisioning plan"
        else:
            job.status = (
                ProvisioningJobStatus.done.value
                if job.done_steps == len(steps)
                else ProvisioningJobStatus.partial.value
            )
        job.completed_at = utcnow()
        job.claim_token = None
        job.claimed_at = None
        db.commit()


async def run_job(job_id: int) -> bool:
    """Execute one committed intent only if this process obtains its claim."""
    token = claim_job(job_id)
    if token is None:
        return False
    try:
        with SessionLocal() as db:
            step_ids = [step.id for step in lifecycle_repo.list_steps(db, job_id)]
        try:
            for step_id in step_ids:
                await _run_step(job_id, token, step_id)
        except Exception as exc:
            _fail_remaining(job_id, token, exc)
        _finish_job(job_id, token)
    except asyncio.CancelledError:
        release_claim(job_id, token)
        raise
    return True


def retry_job(db: Session, job: ProvisioningJob) -> ProvisioningJob:
    """Explicit retry queues only failed steps; a live claim is never stolen."""
    if job.owner != OWNER:
        raise readiness.ReadinessError(f"job {job.id} is not owned by runtime")
    if job.status not in {ProvisioningJobStatus.partial.value, ProvisioningJobStatus.failed.value}:
        return job
    result = db.execute(
        update(ProvisioningJob)
        .where(
            ProvisioningJob.id == job.id,
            ProvisioningJob.owner == OWNER,
            ProvisioningJob.status.in_(
                (ProvisioningJobStatus.partial.value, ProvisioningJobStatus.failed.value)
            ),
        )
        .values(
            status=ProvisioningJobStatus.pending.value,
            completed_at=None,
            claim_token=None,
            claimed_at=None,
        )
    )
    if result.rowcount != 1:
        db.refresh(job)
        return job
    steps = lifecycle_repo.list_steps(db, int(job.id))
    for step in steps:
        if step.status == ProvisioningStepStatus.failed.value:
            step.status = ProvisioningStepStatus.pending.value
            step.started_at = None
            step.completed_at = None
    db.commit()
    db.refresh(job)
    return job


async def scan_once() -> int:
    completed = 0
    for job_id in pending_job_ids():
        try:
            completed += await run_job(job_id)
        except Exception:
            logger.exception("runtime provisioning job %s could not be processed", job_id)
    return completed


class RuntimeProvisioner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not settings.runtime_provisioning_enabled or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                await scan_once()
            except Exception:
                logger.exception("runtime provisioning scan failed")
            await asyncio.sleep(max(0.05, settings.runtime_provisioning_poll_seconds))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


runtime_provisioner = RuntimeProvisioner()
