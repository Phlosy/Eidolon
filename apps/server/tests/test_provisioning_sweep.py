"""Provisioning 中断自愈（启动补收敛）回归。

进程死在 engine.run 中途 ⇒ step 永远 running、job 卡 running、教程无限轮询。
`workforce_access.sweep_stale_provisioning_jobs` + `rerun_stale_provisioning_jobs`
把它们重置并幂等重跑（docs:builtin / workspace:local 本地幂等，无外部依赖）。
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa

from app.lifecycle.engine import ProvisioningEngine
from app.models.enums import (
    ProvisioningJobKind,
    ProvisioningJobStatus,
    ProvisioningStepStatus,
)
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Company, Employee
from app.workforce import access as workforce_access

engine = ProvisioningEngine()
_seq = 0


def _fresh(db) -> tuple[int, int]:
    global _seq
    _seq += 1
    company = Company(name=f"SweepCo {_seq}", slug=f"sweep-co-{_seq}", description="")
    db.add(company)
    db.flush()
    employee = Employee(
        company_id=company.id,
        name=f"Sweep {_seq}",
        slug=f"sweep-{_seq}",
        workspace_path=f"/tmp/sweep-{_seq}-ws",
        memory_namespace=f"mem-sweep-{_seq}",
        lifecycle_status="active",
    )
    db.add(employee)
    db.commit()
    return int(company.id), int(employee.id)


def _interrupted_job(db, employee_id: int) -> ProvisioningJob:
    """与生产一致：job running、第 1 步 done、第 2 步卡 running、第 3 步 pending。"""
    job = ProvisioningJob(
        employee_id=employee_id,
        kind=ProvisioningJobKind.onboarding.value,
        status=ProvisioningJobStatus.running.value,
        total_steps=3,
        done_steps=1,
        reason="test",
    )
    db.add(job)
    db.flush()
    for seq, key, status in (
        (1, "workspace:local", ProvisioningStepStatus.done.value),
        (2, "docs:builtin", ProvisioningStepStatus.running.value),
        (3, "workspace:local", ProvisioningStepStatus.pending.value),
    ):
        db.add(
            ProvisioningStep(
                job_id=job.id,
                seq=seq,
                resource_type="workspace" if "workspace" in key else "docs",
                provider_key=key,
                action="provision",
                description=key,
                status=status,
                attempts=1 if status == "running" else 0,
            )
        )
    db.commit()
    return db.get(ProvisioningJob, job.id)


def _steps(db, job_id: int) -> list[dict]:
    rows = db.execute(
        sa.text(
            "SELECT seq, provider_key, status, error FROM provisioning_steps "
            "WHERE job_id=:j ORDER BY seq"
        ),
        {"j": job_id},
    ).all()
    return [dict(r._mapping) for r in rows]


def test_sweep_resets_interrupted_steps_and_rerun_completes(db):
    _company_id, employee_id = _fresh(db)
    job = _interrupted_job(db, employee_id)

    healed = workforce_access.sweep_stale_provisioning_jobs()
    # sweep 是仓库级动作：共享测试库里其它用例的 job 也会被扫到，只断言本用例 job 在其中
    assert job.id in [h["job_id"] for h in healed]

    db.expire_all()  # sweep 走的是 SessionLocal，fixture session 需重新读取
    steps = _steps(db, job.id)
    assert steps[1]["status"] == ProvisioningStepStatus.pending.value, "stale running → pending"
    assert steps[1]["error"] is None
    job = db.get(ProvisioningJob, job.id)
    assert job.status == ProvisioningJobStatus.pending.value, "job running → pending"

    # 幂等重跑：全部步骤走完（docs/workspace 本地幂等），不再卡死
    asyncio.run(engine.run(db, job))
    db.expire_all()
    assert db.get(ProvisioningJob, job.id).status in (
        ProvisioningJobStatus.done.value,
        ProvisioningJobStatus.partial.value,
    )
    finished = _steps(db, job.id)
    assert all(s["status"] == ProvisioningStepStatus.done.value for s in finished)


def test_sweep_heals_job_that_never_started(db):
    _company_id, employee_id = _fresh(db)
    job = ProvisioningJob(
        employee_id=employee_id,
        kind=ProvisioningJobKind.onboarding.value,
        status=ProvisioningJobStatus.pending.value,
        total_steps=1,
        done_steps=0,
        reason="test",
    )
    db.add(job)
    db.flush()
    db.add(
        ProvisioningStep(
            job_id=job.id,
            seq=1,
            resource_type="docs",
            provider_key="docs:builtin",
            action="provision",
            description="docs",
            status=ProvisioningStepStatus.pending.value,
        )
    )
    db.commit()

    healed = workforce_access.sweep_stale_provisioning_jobs()
    assert job.id in [h["job_id"] for h in healed]

    asyncio.run(engine.run(db, db.get(ProvisioningJob, job.id)))
    db.expire_all()
    assert db.get(ProvisioningJob, job.id).status == ProvisioningJobStatus.done.value


def test_sweep_ignores_finished_jobs(db):
    _company_id, employee_id = _fresh(db)
    job = ProvisioningJob(
        employee_id=employee_id,
        kind=ProvisioningJobKind.onboarding.value,
        status=ProvisioningJobStatus.done.value,
        total_steps=1,
        done_steps=1,
        reason="test",
    )
    db.add(job)
    db.commit()
    db.expire_all()
    healed = workforce_access.sweep_stale_provisioning_jobs()
    assert job.id not in [h["job_id"] for h in healed], "done 的 job 不应被重新扫出"
