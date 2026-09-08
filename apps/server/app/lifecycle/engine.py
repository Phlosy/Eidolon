"""ProvisioningEngine (v0.4 §5): Desired State → ProvisioningJob + ordered
ProvisioningSteps, executed step-by-step through the ProvisionerRegistry.

- Every step is idempotent (provisioners no-op on converged resources).
- Failures record error + attempts and never abort sibling resources; the job
  ends ``partial`` and the employee keeps their previous lifecycle state.
- ``retry`` re-runs ONLY failed steps.
- Default compensation policy: keep already-provisioned resources (a failed
  account step never deletes the workspace).
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.redaction import redact
from app.events.bus import bus
from app.lifecycle.access import AccessDiff, EffectiveEntitlement
from app.lifecycle.provisioners.base import (
    ProvisionContext,
    ProvisionerError,
    SkippableStepError,
)
from app.lifecycle.provisioners.registry import ProvisionerRegistry, get_registry
from app.models.base import utcnow
from app.models.enums import (
    DeploymentMode,
    LifecycleStatus,
    ProvisioningJobKind,
    ProvisioningJobStatus,
    ProvisioningStepStatus,
    ResourceAccountStatus,
    RuntimeInstanceStatus,
)
from app.models.lifecycle import Entitlement, ProvisioningJob, ResourceProvider
from app.models.organization import Employee
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.gateway import gateway

logger = get_logger(__name__)

# resource types provision accounts in this order
RESOURCE_ORDER = ("workspace", "docs", "git")

# engine-internal actions (no provisioner; orchestrated by lifecycle, §4/§7)
_ENGINE_ACTIONS = ("stop_runtime", "start_runtime")

# job kind → lifecycle status applied when every step succeeds
_TARGET_STATUS = {
    ProvisioningJobKind.onboarding.value: LifecycleStatus.active.value,
    ProvisioningJobKind.transfer.value: LifecycleStatus.active.value,
    ProvisioningJobKind.permission_change.value: None,  # unchanged
    ProvisioningJobKind.suspension.value: LifecycleStatus.suspended.value,
    ProvisioningJobKind.resumption.value: LifecycleStatus.active.value,
    ProvisioningJobKind.offboarding.value: LifecycleStatus.offboarded.value,
}

_COMPLETION_EVENT = {
    ProvisioningJobKind.onboarding.value: "employee.onboarding_completed",
    ProvisioningJobKind.transfer.value: "employee.transferred",
    ProvisioningJobKind.suspension.value: "employee.suspended",
    ProvisioningJobKind.resumption.value: "employee.resumed",
    ProvisioningJobKind.offboarding.value: "employee.offboarded",
}


@dataclass
class PlanStep:
    resource_type: str
    provider_key: str
    action: str
    description: str
    entitlement: Entitlement | None = None
    skipped: bool = False


class ProvisioningEngine:
    def __init__(self, registry: ProvisionerRegistry | None = None) -> None:
        self.registry = registry or get_registry()

    # ------------------------------------------------------------------ plans

    def plan_onboarding(
        self, entitlements: list[EffectiveEntitlement], *, slug: str = "new employee"
    ) -> list[PlanStep]:
        """One provision step per involved provider, then one grant step per
        entitlement (dedupe happened upstream)."""
        present = {e.entitlement.resource_type for e in entitlements}
        steps: list[PlanStep] = []
        for resource_type in RESOURCE_ORDER:
            if resource_type not in present:
                continue
            provider_key = self.registry.default_provider_key(resource_type)
            steps.append(
                PlanStep(
                    resource_type=resource_type,
                    provider_key=provider_key,
                    action="provision",
                    description=f"provision {resource_type} account for {slug}",
                )
            )
        ordered = sorted(
            entitlements,
            key=lambda e: (RESOURCE_ORDER.index(e.entitlement.resource_type), e.entitlement.id),
        )
        for entry in ordered:
            entitlement = entry.entitlement
            provider_key = self.registry.default_provider_key(entitlement.resource_type)
            steps.append(
                PlanStep(
                    resource_type=entitlement.resource_type,
                    provider_key=provider_key,
                    action="grant",
                    description=f"grant {entitlement.key}",
                    entitlement=entitlement,
                )
            )
        return steps

    def plan_transfer(self, diff: AccessDiff) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for entitlement in diff.keep:
            steps.append(
                PlanStep(
                    resource_type=entitlement.resource_type,
                    provider_key=self.registry.default_provider_key(entitlement.resource_type),
                    action="keep",
                    description=f"KEEP {entitlement.key} (already granted)",
                    entitlement=entitlement,
                    skipped=True,
                )
            )
        for entitlement in diff.add:
            steps.append(
                PlanStep(
                    resource_type=entitlement.resource_type,
                    provider_key=self.registry.default_provider_key(entitlement.resource_type),
                    action="grant",
                    description=f"ADD {entitlement.key}",
                    entitlement=entitlement,
                )
            )
        for entitlement in diff.remove:
            steps.append(
                PlanStep(
                    resource_type=entitlement.resource_type,
                    provider_key=self.registry.default_provider_key(entitlement.resource_type),
                    action="revoke",
                    description=f"REMOVE {entitlement.key}",
                    entitlement=entitlement,
                )
            )
        return steps

    def plan_suspension(self, db: Session, employee: Employee) -> list[PlanStep]:
        steps = [
            PlanStep(
                resource_type="workspace",
                provider_key="runtime:local",
                action="stop_runtime",
                description=f"stop runtime for {employee.slug} (workspace preserved)",
            )
        ]
        for account in lifecycle_repo.list_accounts(db, employee.id):
            if account.status == ResourceAccountStatus.active.value:
                provider_key = self.account_provider_key(db, account)
                steps.append(
                    PlanStep(
                        resource_type=account.resource_type,
                        provider_key=provider_key,
                        action="suspend",
                        description=f"suspend {account.resource_type} account {account.username}",
                    )
                )
        return steps

    def plan_resumption(self, db: Session, employee: Employee) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for account in lifecycle_repo.list_accounts(db, employee.id):
            if account.status == ResourceAccountStatus.suspended.value:
                provider_key = self.account_provider_key(db, account)
                steps.append(
                    PlanStep(
                        resource_type=account.resource_type,
                        provider_key=provider_key,
                        action="resume",
                        description=f"resume {account.resource_type} account {account.username}",
                    )
                )
        steps.append(
            PlanStep(
                resource_type="workspace",
                provider_key="runtime:local",
                action="start_runtime",
                description=f"start runtime for {employee.slug}",
            )
        )
        return steps

    def plan_offboarding(self, db: Session, employee: Employee) -> list[PlanStep]:
        """§7: stop runtime → suspend external accounts → transfer assets →
        archive workspace → deprovision accounts (credential revocation folds
        into deprovision). Employee data is never deleted."""
        steps = [
            PlanStep(
                resource_type="workspace",
                provider_key="runtime:local",
                action="stop_runtime",
                description=f"stop runtime for {employee.slug}",
            )
        ]
        accounts = [
            a
            for a in lifecycle_repo.list_accounts(db, employee.id)
            if a.status != ResourceAccountStatus.deprovisioned.value
        ]
        for account in accounts:
            if account.status == ResourceAccountStatus.active.value:
                steps.append(
                    PlanStep(
                        resource_type=account.resource_type,
                        provider_key=self.account_provider_key(db, account),
                        action="suspend",
                        description=f"suspend {account.resource_type} account {account.username}",
                    )
                )
        provider_keys = {self.account_provider_key(db, a) for a in accounts}
        provider_keys |= {a.provider_key for a in lifecycle_repo.list_assets(db, employee.id)}
        for provider_key in sorted(provider_keys):
            provisioner = self.registry.provisioner_for(provider_key)
            steps.append(
                PlanStep(
                    resource_type=provisioner.resource_type,
                    provider_key=provider_key,
                    action="transfer_assets",
                    description=f"transfer {provisioner.resource_type} assets of {employee.slug}",
                )
            )
        steps.append(
            PlanStep(
                resource_type="workspace",
                provider_key=self.registry.default_provider_key("workspace"),
                action="archive",
                description=f"archive workspace of {employee.slug} to data/archive/",
            )
        )
        for account in accounts:
            steps.append(
                PlanStep(
                    resource_type=account.resource_type,
                    provider_key=self.account_provider_key(db, account),
                    action="deprovision",
                    description=f"deprovision {account.resource_type} account {account.username}",
                )
            )
        return steps

    # ------------------------------------------------------------- job create

    def create_job(
        self,
        db: Session,
        *,
        employee_id: int,
        kind: str,
        plan: list[PlanStep],
        reason: str = "",
        metadata: dict | None = None,
    ) -> ProvisioningJob:
        job = lifecycle_repo.create_job(
            db,
            employee_id=employee_id,
            kind=kind,
            status=ProvisioningJobStatus.pending.value,
            total_steps=len(plan),
            done_steps=sum(1 for s in plan if s.skipped),
            reason=reason,
            metadata_json=metadata or {},
        )
        for seq, plan_step in enumerate(plan, start=1):
            lifecycle_repo.create_step(
                db,
                job_id=job.id,
                seq=seq,
                resource_type=plan_step.resource_type,
                provider_key=plan_step.provider_key,
                action=plan_step.action,
                description=plan_step.description,
                status=ProvisioningStepStatus.skipped.value
                if plan_step.skipped
                else ProvisioningStepStatus.pending.value,
                entitlement_id=plan_step.entitlement.id if plan_step.entitlement else None,
            )
        db.flush()
        return job

    # -------------------------------------------------------------- execution

    async def run(
        self, db: Session, job: ProvisioningJob, *, extras: dict | None = None
    ) -> ProvisioningJob:
        """Execute all pending steps in order; failed steps are recorded and
        execution continues with independent resources."""
        employee = org_repo.get_employee(db, job.employee_id)
        if employee is None:
            raise ProvisionerError(f"employee {job.employee_id} not found for job {job.id}")
        job.status = ProvisioningJobStatus.running.value
        db.commit()
        for step in lifecycle_repo.list_steps(db, job.id):
            if step.status != ProvisioningStepStatus.pending.value:
                continue
            await self._run_step(db, job, step, employee, extras or {})
        return self._finalize(db, job, employee)

    async def retry(self, db: Session, job: ProvisioningJob) -> ProvisioningJob:
        """Re-run ONLY failed steps (§5)."""
        steps = lifecycle_repo.list_steps(db, job.id)
        if not any(s.status == ProvisioningStepStatus.failed.value for s in steps):
            return job
        employee = org_repo.get_employee(db, job.employee_id)
        if employee is None:
            raise ProvisionerError(f"employee {job.employee_id} not found for job {job.id}")
        for step in steps:
            if step.status == ProvisioningStepStatus.failed.value:
                step.status = ProvisioningStepStatus.pending.value
                step.error = None
        job.status = ProvisioningJobStatus.running.value
        db.commit()
        for step in lifecycle_repo.list_steps(db, job.id):
            if step.status != ProvisioningStepStatus.pending.value:
                continue
            await self._run_step(db, job, step, employee, {})
        return self._finalize(db, job, employee)

    async def _run_step(
        self, db: Session, job: ProvisioningJob, step, employee: Employee, extras: dict
    ) -> None:
        step.status = ProvisioningStepStatus.running.value
        step.attempts += 1
        step.started_at = utcnow()
        db.commit()
        if step.action == "provision":
            bus.publish(
                "resource.provisioning_started",
                {
                    "employee_id": employee.id,
                    "job_id": job.id,
                    "provider_key": step.provider_key,
                    "resource_type": step.resource_type,
                },
                company_id=employee.company_id,
                actor_employee_id=employee.id,
            )
        # flush 失败后 session 处于 DEACTIVE：此刻任何 ORM 属性访问（连 step.id）
        # 都会触发刷新 SELECT 并抛 PendingRollback —— 所以 id 必须在执行前预取。
        step_id, job_id, provider_key = step.id, job.id, step.provider_key
        try:
            await self._execute(db, job, step, employee, extras)
        except SkippableStepError as exc:
            # git:gitea 等未安装：跳过步骤而不是堵死整个 job（入职教程不被卡住）
            self._skip_step(db, job, step, employee, str(exc))
            return
        except ProvisionerError as exc:
            self._fail_step(db, job, step, employee, str(exc))
            return
        except Exception as exc:  # never let one resource 500 the whole job
            logger.exception(
                "provisioning step %s crashed (job %s, provider %s)", step_id, job_id, provider_key
            )
            self._fail_step(db, job, step, employee, redact(str(exc)[:300]))
            return
        step.status = ProvisioningStepStatus.done.value
        step.error = None
        step.completed_at = utcnow()
        job.done_steps = self._count_finished(db, job.id)
        db.commit()
        self._publish_step_event(db, job, step, employee)

    async def _execute(
        self, db: Session, job: ProvisioningJob, step, employee: Employee, extras: dict
    ) -> None:
        if step.action in _ENGINE_ACTIONS:
            if step.action == "stop_runtime":
                await self._stop_runtime(db, employee)
            else:
                await self._start_runtime(db, employee)
            return
        provisioner = self.registry.provisioner_for(step.provider_key)
        ctx = ProvisionContext(db=db, job=job, step=step, extras=extras)
        entitlement = (
            lifecycle_repo.get_entitlement(db, step.entitlement_id) if step.entitlement_id else None
        )
        if step.action == "provision":
            await provisioner.provision_employee(employee, entitlement, ctx)
        elif step.action == "grant":
            if entitlement is None:
                raise ProvisionerError("grant step missing entitlement")
            await provisioner.grant_entitlement(employee, entitlement, ctx)
        elif step.action == "revoke":
            if entitlement is None:
                raise ProvisionerError("revoke step missing entitlement")
            await provisioner.revoke_entitlement(employee, entitlement, ctx)
        elif step.action == "suspend":
            await provisioner.suspend_employee(employee, entitlement, ctx)
        elif step.action == "resume":
            await provisioner.resume_employee(employee, entitlement, ctx)
        elif step.action == "deprovision":
            await provisioner.deprovision_employee(employee, entitlement, ctx)
        elif step.action == "transfer_assets":
            target = extras.get("transfer_target") or (job.metadata_json or {}).get(
                "transfer_target"
            )
            if not target:
                raise ProvisionerError("transfer_assets step missing target")
            await provisioner.transfer_assets(employee, target, ctx)
        elif step.action == "archive":
            archive = getattr(provisioner, "archive", None)
            if archive is None:
                raise ProvisionerError(f"{step.provider_key} does not support archive")
            await archive(employee, ctx)
        else:
            raise ProvisionerError(f"unknown step action: {step.action}")

    def _fail_step(
        self, db: Session, job: ProvisioningJob, step, employee: Employee, error: str
    ) -> None:
        # 会话恢复：flush 失败（如 UNIQUE 冲突）后 session 处于 PendingRollback，
        # 必须先 rollback 才能继续写 —— 否则 failed 状态写不进去，step 永远 running。
        db.rollback()
        step.status = ProvisioningStepStatus.failed.value
        step.error = error
        step.completed_at = utcnow()
        if step.action == "provision":
            account = lifecycle_repo.get_account_by_provider_key(db, employee.id, step.provider_key)
            if account is not None and account.status != ResourceAccountStatus.active.value:
                account.status = ResourceAccountStatus.failed.value
                account.provisioning_state = "failed"
        db.commit()
        bus.publish(
            "resource.provisioning_failed",
            {
                "employee_id": employee.id,
                "job_id": job.id,
                "step_id": step.id,
                "provider_key": step.provider_key,
                "action": step.action,
                "error": error,
            },
            company_id=employee.company_id,
            actor_employee_id=employee.id,
        )

    def _skip_step(
        self, db: Session, job: ProvisioningJob, step, employee: Employee, reason: str
    ) -> None:
        db.rollback()
        step.status = ProvisioningStepStatus.skipped.value
        step.error = reason
        step.completed_at = utcnow()
        if step.action == "provision":
            # 账户留痕：不是 active 也不是 failed，而是"本次未开通"（装上后可重跑）
            account = lifecycle_repo.get_account_by_provider_key(
                db, employee.id, step.provider_key
            )
            if account is not None and account.provisioning_state != "skipped":
                account.provisioning_state = "skipped"
        job.done_steps = self._count_finished(db, job.id)
        db.commit()

    def _publish_step_event(
        self, db: Session, job: ProvisioningJob, step, employee: Employee
    ) -> None:
        event_by_action = {
            "provision": "resource.provisioned",
            "grant": "employee.access_granted",
            "revoke": "employee.access_revoked",
            "suspend": "resource.suspended",
            "deprovision": "resource.deprovisioned",
            "transfer_assets": "asset.transferred",
        }
        event_type = event_by_action.get(step.action)
        if event_type is None:
            return
        payload = {
            "employee_id": employee.id,
            "job_id": job.id,
            "provider_key": step.provider_key,
            "resource_type": step.resource_type,
        }
        if step.entitlement_id:
            entitlement = lifecycle_repo.get_entitlement(db, step.entitlement_id)
            payload["entitlement"] = entitlement.key if entitlement else None
        bus.publish(
            event_type, payload, company_id=employee.company_id, actor_employee_id=employee.id
        )

    def _finalize(self, db: Session, job: ProvisioningJob, employee: Employee) -> ProvisioningJob:
        steps = lifecycle_repo.list_steps(db, job.id)
        failed = [s for s in steps if s.status == ProvisioningStepStatus.failed.value]
        job.done_steps = self._count_finished(db, job.id)
        if failed:
            job.status = ProvisioningJobStatus.partial.value
        else:
            job.status = ProvisioningJobStatus.done.value
            job.completed_at = utcnow()
            target = _TARGET_STATUS.get(job.kind)
            if target is not None and employee.lifecycle_status != target:
                employee.lifecycle_status = target
            event_type = _COMPLETION_EVENT.get(job.kind)
            if event_type is not None:
                bus.publish(
                    event_type,
                    {"employee_id": employee.id, "job_id": job.id},
                    company_id=employee.company_id,
                    actor_employee_id=employee.id,
                )
        db.commit()
        db.refresh(job)
        return job

    # --------------------------------------------------------------- runtime

    async def _stop_runtime(self, db: Session, employee: Employee) -> None:
        """Stop the employee runtime; workspace/assets/history are preserved (§7)."""
        gateway.drop_instance(employee.id)
        instance = runtime_repo.get_instance_for_employee(db, employee.id)
        if instance is None:
            return
        if instance.deployment_mode == DeploymentMode.docker.value:
            try:
                from app.runtimes.manager import get_manager

                await get_manager().stop_instance(db, instance)
            except Exception as exc:
                logger.warning("docker runtime stop failed: %s", redact(str(exc)[:200]))
                instance.status = RuntimeInstanceStatus.stopped.value
                instance.stopped_at = utcnow()
        else:
            instance.status = RuntimeInstanceStatus.stopped.value
            instance.stopped_at = utcnow()
        db.flush()

    async def _start_runtime(self, db: Session, employee: Employee) -> None:
        gateway.drop_instance(employee.id)
        instance = runtime_repo.get_instance_for_employee(db, employee.id)
        if instance is None:
            return
        if instance.deployment_mode == DeploymentMode.docker.value:
            try:
                from app.runtimes.manager import get_manager

                await get_manager().start_instance(db, instance)
            except Exception as exc:
                logger.warning("docker runtime start failed: %s", redact(str(exc)[:200]))
                instance.status = RuntimeInstanceStatus.running.value
                instance.started_at = utcnow()
        else:
            instance.status = RuntimeInstanceStatus.running.value
            instance.started_at = utcnow()
        db.flush()

    # --------------------------------------------------------------- helpers

    @staticmethod
    def account_provider_key(db: Session, account) -> str:
        metadata_key = (account.metadata_json or {}).get("provider_key")
        if metadata_key:
            return metadata_key
        if account.provider_id:
            provider = db.get(ResourceProvider, account.provider_id)
            if provider is not None:
                return provider.key
        raise ProvisionerError(f"resource account {account.id} has no provider")

    @staticmethod
    def _count_finished(db: Session, job_id: int) -> int:
        return sum(
            1
            for s in lifecycle_repo.list_steps(db, job_id)
            if s.status in (ProvisioningStepStatus.done.value, ProvisioningStepStatus.skipped.value)
        )
