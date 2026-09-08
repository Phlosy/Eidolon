"""Provisioning 弹性回归：撞 UNIQUE 后不会再卡 running；未安装资源可跳过。

实现保障：
  1. drive 目录创建用 savepoint 隔离并发（落败方回滚保存点、以已存在者为准）；
  2. engine._fail_step 先 rollback 再写状态 —— flush 失败(PendingRollback)后
     failed 状态才写得进去，step 不再永远 running（入职教程卡死根因）；
  3. SkippableStepError（git:gitea 未安装）→ step 置 skipped，job 照常 done。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.lifecycle.engine import ProvisioningEngine
from app.lifecycle.provisioners.base import SkippableStepError
from app.models.drive import DriveNode
from app.models.enums import (
    DriveNodeKind,
    DriveZone,
    ProvisioningJobKind,
    ProvisioningJobStatus,
    ProvisioningStepStatus,
)
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Company, Employee
from app.services.drive import ensure_zone_roots

engine = ProvisioningEngine()
_seq = 0


def _fresh(db) -> tuple[int, int]:
    global _seq
    _seq += 1
    company = Company(name=f"ResCo {_seq}", slug=f"res-co-{_seq}", description="")
    db.add(company)
    db.flush()
    employee = Employee(
        company_id=company.id,
        name=f"Res {_seq}",
        slug=f"res-{_seq}",
        workspace_path=f"/tmp/res-{_seq}-ws",
        memory_namespace=f"mem-res-{_seq}",
        lifecycle_status="active",
    )
    db.add(employee)
    db.commit()
    return int(company.id), int(employee.id)


def _job_with_steps(db, employee_id: int, steps: list[tuple[str, str]]) -> ProvisioningJob:
    job = ProvisioningJob(
        employee_id=employee_id,
        kind=ProvisioningJobKind.onboarding.value,
        status=ProvisioningJobStatus.pending.value,
        total_steps=len(steps),
        done_steps=0,
        reason="test",
    )
    db.add(job)
    db.flush()
    for seq, (key, action) in enumerate(steps, start=1):
        db.add(
            ProvisioningStep(
                job_id=job.id,
                seq=seq,
                resource_type="workspace"
                if "workspace" in key
                else "docs"
                if "docs" in key
                else "git",
                provider_key=key,
                action=action,
                description=key,
                status=ProvisioningStepStatus.pending.value,
            )
        )
    db.commit()
    return db.get(ProvisioningJob, job.id)


def test_flush_failure_marks_failed_not_stuck_and_retry_recovers(db, monkeypatch):
    company_id, employee_id = _fresh(db)
    ensure_zone_roots(db, company_id)
    db.expire_all()
    job = _job_with_steps(db, employee_id, [("docs:builtin", "provision")])

    async def boom_execute(db, job, step, employee, extras):
        # 真实复现并发创建：向已存在的 zone root 再插一行 → flush 撞 UNIQUE，
        # session 进入 PendingRollback。修复前 _fail_step 直接 commit 会抛
        # PendingRollbackError，failed 写不进去、step 永远 running。
        db.add(
            DriveNode(
                company_id=employee.company_id,
                kind=DriveNodeKind.folder.value,
                name="knowledge",
                path="drive/knowledge",
                zone=DriveZone.knowledge.value,
            )
        )
        db.flush()

    monkeypatch.setattr(engine, "_execute", boom_execute)
    asyncio_run(engine.run, db, job)

    db.expire_all()
    step = db.execute(
        sa.text("SELECT status, error FROM provisioning_steps WHERE job_id=:j"),
        {"j": job.id},
    ).first()
    assert step[0] == ProvisioningStepStatus.failed.value, "failed 必须写进去，不能再 running"
    assert "UNIQUE" in step[1]
    assert db.get(ProvisioningJob, job.id).status == ProvisioningJobStatus.partial.value

    # 会话已恢复：重试（恢复真实执行）→ done
    monkeypatch.undo()
    db.expire_all()
    asyncio_run(engine.retry, db, db.get(ProvisioningJob, job.id))
    db.expire_all()
    assert db.get(ProvisioningJob, job.id).status == ProvisioningJobStatus.done.value
    status = db.execute(
        sa.text("SELECT status FROM provisioning_steps WHERE job_id=:j"), {"j": job.id}
    ).scalar()
    assert status == ProvisioningStepStatus.done.value


def test_skippable_resource_marks_job_done(db, monkeypatch):
    company_id, employee_id = _fresh(db)
    job = _job_with_steps(
        db, employee_id, [("workspace:local", "provision"), ("git:gitea", "provision")]
    )

    original_execute = ProvisioningEngine._execute

    async def fake_execute(db, job, step, employee, extras):
        if step.provider_key == "git:gitea":
            raise SkippableStepError("builtin gitea is not running (status=not_installed)")
        await original_execute(engine, db, job, step, employee, extras)

    monkeypatch.setattr(engine, "_execute", fake_execute)
    asyncio_run(engine.run, db, job)

    db.expire_all()
    rows = {
        row[0]: row[1]
        for row in db.execute(
            sa.text("SELECT provider_key, status FROM provisioning_steps WHERE job_id=:j"),
            {"j": job.id},
        ).all()
    }
    assert rows["workspace:local"] == ProvisioningStepStatus.done.value
    assert rows["git:gitea"] == ProvisioningStepStatus.skipped.value, "未安装 → skipped"
    assert db.get(ProvisioningJob, job.id).status == ProvisioningJobStatus.done.value


def asyncio_run(fn, db, *args):
    import asyncio

    asyncio.run(fn(db, *args))


def test_step_timeout_marks_failed_quickly_not_stuck(db, monkeypatch):
    import asyncio
    import time

    from app.core.config import settings

    company_id, employee_id = _fresh(db)
    job = _job_with_steps(db, employee_id, [("docs:builtin", "provision")])
    monkeypatch.setattr(settings, "provisioning_step_timeout_seconds", 0.05)

    async def slow_execute(db, job, step, employee, extras):
        await asyncio.sleep(5)  # 外部资源挂起

    monkeypatch.setattr(engine, "_execute", slow_execute)
    start = time.monotonic()
    asyncio_run(engine.run, db, job)
    elapsed = time.monotonic() - start

    assert elapsed < 3, f"超时机制必须快速返回（实际 {elapsed:.2f}s）"
    db.expire_all()
    row = db.execute(
        sa.text("SELECT status, error FROM provisioning_steps WHERE job_id=:j"),
        {"j": job.id},
    ).first()
    assert row[0] == ProvisioningStepStatus.failed.value, "不能再无限 running"
    assert "timed out" in row[1]
    assert db.get(ProvisioningJob, job.id).status == ProvisioningJobStatus.partial.value
