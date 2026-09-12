"""M2.8 **Ready-to-Work**：公司运行时策略 + 逐项就绪事实 + 招募后的自动编排。

设计（docs/m2-implementation-plan.md §11，W31 / RD1–RD7）：

```text
招募得到的人 → Employee → PositionAssignment → RoleContext
             → 环境编排（工作区目录 / 运行时实例 / 供应商绑定）
             → READY_TO_WORK（**派生量**，不是一列）
```

三条边界：

1. **`READY_TO_WORK` 是派生量**（RD1）：它只是"四个事实项现在都成立"的缩写，
   所以系统永远能回答"差哪一项"（RD2）。不落列、不落枚举、不缓存。
2. **公司策略只配环境**（RD4/I6）：见 `contracts.RUNTIME_POLICY_KEYS` ——
   人格 / 提示词 / 工作流 / 技能 / 职位行为**一律拒绝**（W26）。
3. **编排失败不是招募失败**（RD5/I2）：环境步骤失败 ⇒ 员工还在、job 记 `partial` +
   明确原因、人就绪为 false；系统**绝不**把"没配好"四舍五入成"可以干活了"。

`orchestrate()` 刻意**不 commit**：招募路径把它放进**同一个事务**里
（招募失败 ⇒ 整笔回滚，E13/E14/E15 的既有纪律）；调用方自己决定事务边界。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import (
    DeploymentMode,
    HealthStatus,
    LifecycleStatus,
    ProvisioningJobStatus,
    RuntimeInstanceStatus,
    RuntimeType,
)
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Company, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.work import contracts as C

logger = logging.getLogger(__name__)

#: 编排步骤的 resource_type（PROVISIONING 表是既有表，值域是字符串）
STEP_WORKSPACE = "workspace"
STEP_RUNTIME = "runtime"
STEP_PROVIDER = "provider"

#: 运行时实例的"可用"状态集合（其余状态一律视为不可用，不做善意假设）
INSTANCE_OK_STATUSES: frozenset[str] = frozenset(
    {
        RuntimeInstanceStatus.created.value,
        RuntimeInstanceStatus.starting.value,
        RuntimeInstanceStatus.running.value,
        RuntimeInstanceStatus.idle.value,
        RuntimeInstanceStatus.stopped.value,
    }
)


class ReadinessError(C.WorkContractError):
    """就绪契约被违反（跨公司 / 策略键非法）。"""


# ---------------------------------------------------------------------------
# § 公司运行时策略（只配环境，不配工作方式）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimePolicy:
    """一个员工最终生效的**环境**配置（+ 每个值来自哪，便于解释）。"""

    runtime_type: str
    deployment_mode: str
    provider_id: int | None = None
    model: str = ""
    runtime_config: dict = field(default_factory=dict)
    source: str = C.RUNTIME_POLICY_SOURCES["platform_default"]

    @property
    def is_mock(self) -> bool:
        return self.runtime_type == RuntimeType.mock.value

    def as_dict(self) -> dict:
        return {
            "runtime_type": self.runtime_type,
            "deployment_mode": self.deployment_mode,
            "provider_id": self.provider_id,
            "model": self.model,
            "runtime_config": dict(self.runtime_config or {}),
            "source": self.source,
        }


def _company_settings(db: Session, company_id: int) -> dict:
    company = db.get(Company, int(company_id))
    return dict(getattr(company, "settings", None) or {})


def validate_policy_keys(payload: dict) -> None:
    """策略键校验（RD4/I6）：未知键与**禁止键**一律拒绝。

    未知键拒绝而不是忽略：静默忽略会让"我配了但它没生效"变成一个查不出来的谜。
    """
    forbidden = sorted(set(payload) & C.FORBIDDEN_RUNTIME_POLICY_KEYS)
    if forbidden:
        raise ReadinessError(
            f"runtime policy must not configure how an agent works: {forbidden} (W26)"
        )
    unknown = sorted(set(payload) - set(C.RUNTIME_POLICY_KEYS))
    if unknown:
        raise ReadinessError(
            f"unknown runtime policy keys: {unknown} (allowed: {list(C.RUNTIME_POLICY_KEYS)})"
        )


def company_runtime_defaults(db: Session, company_id: int) -> dict:
    """公司默认运行时策略（未配置 ⇒ 空 dict，由解析层落到平台默认）。"""
    return dict(_company_settings(db, company_id).get("runtime_defaults") or {})


def set_company_runtime_defaults(
    db: Session, company_id: int, payload: dict, *, commit: bool = True
) -> dict:
    """写公司默认运行时策略（只允许 `RUNTIME_POLICY_KEYS`；RD4/I6）。"""
    merged = {key: value for key, value in payload.items() if value is not None}
    validate_policy_keys(merged)
    company = db.get(Company, int(company_id))
    if company is None:
        raise ReadinessError(f"company {company_id} not found")
    current = dict(_company_settings(db, company_id))
    defaults = dict(current.get("runtime_defaults") or {})
    defaults.update(merged)
    current["runtime_defaults"] = defaults
    company.settings = current
    db.flush()
    if commit:
        db.commit()
    return defaults


def _normalize_runtime_type(runtime_type: str) -> str:
    try:
        return RuntimeType(runtime_type).value
    except ValueError as exc:
        raise ReadinessError(f"unknown runtime type: {runtime_type}") from exc


def resolve_runtime_policy(
    db: Session, *, company_id: int, employee: Employee | None = None
) -> RuntimePolicy:
    """解析一个员工的环境配置。

    **谁说了算**（刻意简单，避免"门禁算一种、执行算另一种"）：

    - `runtime_type`：**员工行权威**（`gateway.adapter_for(employee.runtime_type)` 就是读它）；
      没有员工时（招募前）用公司策略，再退到平台默认；
    - `runtime_config`：员工行优先；员工没配时继承公司策略的环境参数；
    - `provider_id` / `model`：只有公司策略有（员工行没有这两列）。
    """
    defaults = company_runtime_defaults(db, company_id)
    company_runtime_type = _normalize_runtime_type(
        str(defaults.get("runtime_type") or C.DEFAULT_RUNTIME_TYPE)
    )
    company_config = dict(defaults.get("runtime_config") or {})
    provider_id = int(defaults["provider_id"]) if defaults.get("provider_id") is not None else None
    if employee is None:
        return RuntimePolicy(
            runtime_type=company_runtime_type,
            deployment_mode=_deployment_for(company_runtime_type, defaults),
            provider_id=provider_id,
            model=str(defaults.get("model") or ""),
            runtime_config=company_config,
            source=C.RUNTIME_POLICY_SOURCES["company_default"],
        )
    runtime_type = _normalize_runtime_type(str(employee.runtime_type or company_runtime_type))
    employee_config = dict(employee.runtime_config or {})
    return RuntimePolicy(
        runtime_type=runtime_type,
        deployment_mode=_deployment_for(runtime_type, defaults),
        provider_id=provider_id,
        model=str(defaults.get("model") or ""),
        runtime_config=employee_config or company_config,
        source=(
            C.RUNTIME_POLICY_SOURCES["employee_override"]
            if employee_config or str(employee.runtime_type) != company_runtime_type
            else C.RUNTIME_POLICY_SOURCES["company_default"]
        ),
    )


def _deployment_for(runtime_type: str, defaults: dict) -> str:
    if defaults.get("deployment_mode"):
        return str(defaults["deployment_mode"])
    return (
        DeploymentMode.mock.value
        if runtime_type == RuntimeType.mock.value
        else DeploymentMode.docker.value
    )


# ---------------------------------------------------------------------------
# § 逐项就绪事实（RD1/RD2）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadinessItem:
    """一项就绪事实：`ready` + 一句**可核对**的解释。"""

    item: str
    ready: bool
    detail: str
    source: str
    facts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "item": self.item,
            "ready": self.ready,
            "detail": self.detail,
            "source": self.source,
            "facts": self.facts,
        }


@dataclass(frozen=True)
class ReadinessReport:
    employee_id: int
    company_id: int
    runtime_type: str
    deployment_mode: str
    ready_to_work: bool
    items: tuple[ReadinessItem, ...]
    gaps: tuple[str, ...]
    policy: RuntimePolicy

    def as_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "company_id": self.company_id,
            "runtime_type": self.runtime_type,
            "deployment_mode": self.deployment_mode,
            "ready_to_work": self.ready_to_work,
            "gaps": list(self.gaps),
            "policy": self.policy.as_dict(),
            "items": [item.as_dict() for item in self.items],
        }


def _position_item(db: Session, employee: Employee) -> ReadinessItem:
    """任职事实：有生效主职 → 经编制（slot）解析出职位定义。

    注意 `PositionAssignment`（物理表 `employments`）**没有** definition 列 ——
    定义是通过 `position_slot_id → position_slots.position_definition_id` 解析的
    （P4b 的单一真相：任职挂在编制上）。没有主职是**合法答案**（= AVAILABLE），
    不回落 `employees.role` 假装有职位。
    """
    assignments = [
        row
        for row in db.scalars(
            select(PositionAssignment).where(
                PositionAssignment.employee_id == int(employee.id),
                PositionAssignment.effective_to.is_(None),
            )
        )
    ]
    assignment = assignments[0] if assignments else None
    slot = (
        db.get(PositionSlot, int(assignment.position_slot_id))
        if assignment is not None and assignment.position_slot_id is not None
        else None
    )
    definition = (
        db.get(PositionDefinition, int(slot.position_definition_id)) if slot is not None else None
    )
    return ReadinessItem(
        item="position",
        ready=assignment is not None and definition is not None,
        detail=(
            f"在任职位 {definition.code}"
            if definition is not None
            else "没有生效职位（软契约：不阻止执行，但会缺少履职上下文）"
        ),
        source=C.READINESS_FACT_SOURCES["position"],
        facts={
            "assignment_id": int(assignment.id) if assignment is not None else None,
            "position_slot_id": (
                int(assignment.position_slot_id)
                if assignment is not None and assignment.position_slot_id is not None
                else None
            ),
            "position_definition_id": int(definition.id) if definition is not None else None,
        },
    )


def _workspace_item(employee: Employee) -> ReadinessItem:
    path = str(employee.workspace_path or "").strip()
    exists = bool(path) and Path(path).is_dir()
    return ReadinessItem(
        item="workspace",
        ready=bool(path) and exists,
        detail=(
            f"工作区目录存在：{path}"
            if exists
            else (f"工作区目录不存在：{path}" if path else "没有工作区路径")
        ),
        source=C.READINESS_FACT_SOURCES["workspace"],
        facts={"workspace_path": path, "dir_exists": exists},
    )


def _runtime_item(db: Session, employee: Employee, policy: RuntimePolicy) -> ReadinessItem:
    if policy.is_mock:
        # mock 代理由 MockAdapter 自带执行环境：**不需要**实例（事实，不是放水）
        return ReadinessItem(
            item="runtime",
            ready=True,
            detail="mock 运行时自带执行环境（不需要实例）",
            source=C.READINESS_FACT_SOURCES["runtime"],
            facts={"required": False, "runtime_type": policy.runtime_type},
        )
    instance = runtime_repo.get_instance_for_employee(db, int(employee.id))
    if instance is None:
        return ReadinessItem(
            item="runtime",
            ready=False,
            detail="没有运行时实例",
            source=C.READINESS_FACT_SOURCES["runtime"],
            facts={"required": True, "instance_id": None},
        )
    healthy = (
        str(instance.status) in INSTANCE_OK_STATUSES
        and str(instance.health_status) != HealthStatus.unhealthy.value
    )
    return ReadinessItem(
        item="runtime",
        ready=healthy,
        detail=f"实例状态 {instance.status} / 健康 {instance.health_status}",
        source=C.READINESS_FACT_SOURCES["runtime"],
        facts={
            "required": True,
            "instance_id": int(instance.id),
            "status": str(instance.status),
            "health_status": str(instance.health_status),
            "runtime_type": str(instance.runtime_type),
            "model_binding_id": (
                int(instance.model_binding_id) if instance.model_binding_id else None
            ),
        },
    )


def _provider_item(db: Session, employee: Employee, policy: RuntimePolicy) -> ReadinessItem:
    if policy.is_mock:
        return ReadinessItem(
            item="provider",
            ready=True,
            detail="mock 运行时不需要供应商绑定",
            source=C.READINESS_FACT_SOURCES["provider"],
            facts={"required": False},
        )
    binding = provider_repo.get_primary_binding(db, int(employee.id))
    if binding is None:
        return ReadinessItem(
            item="provider",
            ready=False,
            detail="没有主模型绑定",
            source=C.READINESS_FACT_SOURCES["provider"],
            facts={"required": True, "binding_id": None},
        )
    provider = provider_repo.get_provider(db, int(binding.provider_id))
    enabled = bool(provider is not None and provider.enabled)
    return ReadinessItem(
        item="provider",
        ready=enabled,
        detail=(
            f"主绑定 {binding.model}（provider {binding.provider_id}）"
            if enabled
            else f"绑定的 provider {binding.provider_id} 不存在或已停用"
        ),
        source=C.READINESS_FACT_SOURCES["provider"],
        facts={
            "required": True,
            "binding_id": int(binding.id),
            "provider_id": int(binding.provider_id),
            "model": binding.model,
            "provider_enabled": enabled,
        },
    )


def readiness_items(
    db: Session, employee: Employee, policy: RuntimePolicy | None = None
) -> tuple[ReadinessItem, ...]:
    """四项事实（顺序固定：position → workspace → runtime → provider）。"""
    resolved = policy or resolve_runtime_policy(
        db, company_id=int(employee.company_id or 0), employee=employee
    )
    return (
        _position_item(db, employee),
        _workspace_item(employee),
        _runtime_item(db, employee, resolved),
        _provider_item(db, employee, resolved),
    )


def required_for_execution(
    policy: RuntimePolicy, items: tuple[ReadinessItem, ...]
) -> tuple[str, ...]:
    """执行必须满足的项（mock ⇒ 空；真实运行时 ⇒ 工作区/运行时/供应商）。"""
    required = (
        C.READINESS_REQUIRED_FOR_EXECUTION["mock"]
        if policy.is_mock
        else C.READINESS_REQUIRED_FOR_EXECUTION["real"]
    )
    ready = {item.item for item in items if item.ready}
    return tuple(name for name in required if name not in ready)


def readiness_report(db: Session, employee: Employee) -> ReadinessReport:
    """逐项事实 + `READY_TO_WORK`（派生，RD1/RD2）。"""
    policy = resolve_runtime_policy(db, company_id=int(employee.company_id or 0), employee=employee)
    items = readiness_items(db, employee, policy)
    # gaps = 全部未就绪项（面向人：告诉你差什么）；ready_to_work 同样看**全部**四项
    gaps = tuple(item.item for item in items if not item.ready)
    return ReadinessReport(
        employee_id=int(employee.id),
        company_id=int(employee.company_id or 0),
        runtime_type=policy.runtime_type,
        deployment_mode=policy.deployment_mode,
        ready_to_work=not gaps,
        items=items,
        gaps=gaps,
        policy=policy,
    )


def blocking_gaps(db: Session, employee: Employee) -> tuple[str, ...]:
    """**执行门禁**用的缺口（比 `readiness_report().gaps` 窄，见契约 §8c）。"""
    policy = resolve_runtime_policy(db, company_id=int(employee.company_id or 0), employee=employee)
    return required_for_execution(policy, readiness_items(db, employee, policy))


# ---------------------------------------------------------------------------
# § 编排（招募之后自动跑；不 commit，事务由调用方持有）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestrationResult:
    """一次编排的**事实**结果（哪几步做了、哪几步失败）。"""

    employee_id: int
    job_id: int | None
    job_status: str
    steps: tuple[dict, ...]
    failed_steps: tuple[str, ...]
    report: ReadinessReport

    @property
    def ok(self) -> bool:
        return not self.failed_steps

    def as_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "job_id": self.job_id,
            "job_status": self.job_status,
            "steps": list(self.steps),
            "failed_steps": list(self.failed_steps),
            "ok": self.ok,
            "readiness": self.report.as_dict(),
        }


def _step_record(
    db: Session, job_id: int, *, seq: int, resource_type: str, provider_key: str, action: str
) -> ProvisioningStep:
    step = ProvisioningStep(
        job_id=int(job_id),
        seq=seq,
        resource_type=resource_type,
        provider_key=provider_key,
        action=action,
        description=f"{action} {resource_type}",
        status="pending",
    )
    db.add(step)
    db.flush()
    return step


def _finish_step(step: ProvisioningStep, *, ok: bool, error: str = "") -> None:
    step.status = "done" if ok else "failed"
    step.error = error or None
    step.attempts = int(step.attempts or 0) + 1


def ensure_workspace(employee: Employee) -> tuple[bool, str]:
    """确保工作区目录存在（幂等）。事实：目录在磁盘上真的存在（RD2）。"""
    path = str(employee.workspace_path or "").strip()
    if not path:
        return False, "employee has no workspace_path"
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - 磁盘问题
        return False, f"cannot create workspace dir: {exc}"
    return True, str(target)


def ensure_runtime_instance(
    db: Session, employee: Employee, policy: RuntimePolicy, *, model_binding_id: int | None = None
) -> tuple[bool, str, object | None]:
    """确保运行时实例存在（幂等）。失败 ⇒ 返回原因（不抛，交调用方记 `partial`）。"""
    existing = runtime_repo.get_instance_for_employee(db, int(employee.id))
    if existing is not None:
        if str(existing.runtime_type) != policy.runtime_type:
            existing.runtime_type = policy.runtime_type
            existing.deployment_mode = policy.deployment_mode
        if model_binding_id is not None and existing.model_binding_id is None:
            existing.model_binding_id = int(model_binding_id)
        if str(existing.workspace_path or "") != str(employee.workspace_path or ""):
            existing.workspace_path = str(employee.workspace_path or "")
        db.flush()
        return True, "runtime instance reused", existing
    if not str(employee.workspace_path or "").strip():
        return False, "employee has no workspace_path", None
    if settings.runtime_mode != "mock" and policy.is_mock is False:
        # 真实运行时：只登记实例（不在这里拉起容器）—— 拉起是运行时管理器的职责，
        # 本模块只保证"环境事实齐备"，`ready` 由实例状态决定。
        logger.info(
            "registering runtime instance for real runtime",
            extra={"employee_id": employee.id, "runtime_type": policy.runtime_type},
        )
    instance = runtime_repo.create_instance(
        db,
        employee_id=int(employee.id),
        runtime_type=policy.runtime_type,
        deployment_mode=policy.deployment_mode,
        status=RuntimeInstanceStatus.created.value,
        health_status=HealthStatus.unknown.value,
        workspace_path=str(employee.workspace_path or ""),
        data_path=str(employee.workspace_path or ""),
        model_binding_id=model_binding_id,
    )
    return True, "runtime instance created", instance


def ensure_provider_binding(
    db: Session, employee: Employee, policy: RuntimePolicy
) -> tuple[bool, str, int | None]:
    """确保主模型绑定存在（幂等）。

    公司策略里给了 provider/model ⇒ 按策略建；**没给** ⇒ 不猜：
    真实运行时缺绑定 ⇒ 就绪为 false（RD5 的"明确原因"），由管理层决定绑什么。
    """
    existing = provider_repo.get_primary_binding(db, int(employee.id))
    if existing is not None:
        return True, "primary binding reused", int(existing.id)
    if policy.is_mock:
        return True, "mock runtime needs no provider binding", None
    if policy.provider_id is None or not policy.model:
        return False, "runtime policy has no provider/model to bind", None
    provider = provider_repo.get_provider(db, int(policy.provider_id))
    if provider is None or not provider.enabled:
        return False, f"provider {policy.provider_id} is missing or disabled", None
    binding = provider_repo.create_binding(
        db,
        employee_id=int(employee.id),
        provider_id=int(policy.provider_id),
        model=str(policy.model),
        is_primary=True,
        position=0,
    )
    return True, "primary binding created", int(binding.id)


def orchestrate(
    db: Session,
    employee: Employee,
    *,
    policy: RuntimePolicy | None = None,
    reason: str = "ready_to_work",
    commit: bool = False,
) -> OrchestrationResult:
    """招募之后的**环境**编排：工作区 → 运行时 → 供应商 → 逐项事实。

    纪律（RD5/I2）：单步失败**不抛**，记在步骤与 job 状态里（`partial`），
    人就绪为 false 并给出原因。**不 commit**（事务由调用方持有，见模块文档）。
    """
    resolved = policy or resolve_runtime_policy(
        db, company_id=int(employee.company_id or 0), employee=employee
    )
    job = ProvisioningJob(
        employee_id=int(employee.id),
        kind="onboarding",
        status=ProvisioningJobStatus.running.value,
        reason=reason,
        metadata_json={"source": "app.work.readiness", "policy": resolved.as_dict()},
    )
    db.add(job)
    db.flush()

    steps: list[dict] = []
    failed: list[str] = []

    # ① 工作区
    step = _step_record(
        db,
        int(job.id),
        seq=1,
        resource_type=STEP_WORKSPACE,
        provider_key="local",
        action="provision",
    )
    ok, detail = ensure_workspace(employee)
    _finish_step(step, ok=ok, error="" if ok else detail)
    steps.append({"step": STEP_WORKSPACE, "ok": ok, "detail": detail})
    if not ok:
        failed.append(STEP_WORKSPACE)

    # ② 运行时（mock 也要登记实例：它是"这个人的执行环境"的事实载体）
    step = _step_record(
        db,
        int(job.id),
        seq=2,
        resource_type=STEP_RUNTIME,
        provider_key=resolved.runtime_type,
        action="start",
    )
    ok, detail, instance = ensure_runtime_instance(db, employee, resolved)
    _finish_step(step, ok=ok, error="" if ok else detail)
    steps.append({"step": STEP_RUNTIME, "ok": ok, "detail": detail})
    if not ok:
        failed.append(STEP_RUNTIME)

    # ③ 供应商
    step = _step_record(
        db,
        int(job.id),
        seq=3,
        resource_type=STEP_PROVIDER,
        provider_key=str(resolved.provider_id or "none"),
        action="provision",
    )
    ok, detail, binding_id = ensure_provider_binding(db, employee, resolved)
    _finish_step(step, ok=ok, error="" if ok else detail)
    steps.append({"step": STEP_PROVIDER, "ok": ok, "detail": detail})
    if not ok:
        failed.append(STEP_PROVIDER)
    if binding_id is not None and instance is not None and instance.model_binding_id is None:
        instance.model_binding_id = int(binding_id)
        db.flush()

    db.refresh(job)
    job.done_steps = len([item for item in steps if item["ok"]])
    job.total_steps = len(steps)
    job.status = (
        ProvisioningJobStatus.done.value if not failed else ProvisioningJobStatus.partial.value
    )
    job.reason = reason if not failed else f"{reason}: failed steps {', '.join(failed)}"
    if not failed:
        from app.models.base import utcnow

        job.completed_at = utcnow()
    db.flush()

    report = readiness_report(db, employee)
    if commit:
        db.commit()
    return OrchestrationResult(
        employee_id=int(employee.id),
        job_id=int(job.id),
        job_status=str(job.status),
        steps=tuple(steps),
        failed_steps=tuple(failed),
        report=report,
    )


# ---------------------------------------------------------------------------
# § 管理动作（人类/管理层把某个员工"配到位"）
# ---------------------------------------------------------------------------


def provision_employee(
    db: Session, employee: Employee, *, commit: bool = True
) -> OrchestrationResult:
    """对一个**已有**员工重跑环境编排（招募路径之外的重试入口）。

    典型场景：招募时环境还没配好（job `partial`），公司把供应商配好后再跑一次。
    """
    if str(employee.lifecycle_status) not in {
        LifecycleStatus.active.value,
        LifecycleStatus.onboarding.value,
    }:
        raise ReadinessError(
            f"employee {employee.id} is {employee.lifecycle_status}: cannot provision"
        )
    result = orchestrate(db, employee, reason="manual_provision", commit=False)
    if commit:
        db.commit()
    return result


__all__ = [
    "INSTANCE_OK_STATUSES",
    "OrchestrationResult",
    "ReadinessError",
    "ReadinessItem",
    "ReadinessReport",
    "RuntimePolicy",
    "blocking_gaps",
    "company_runtime_defaults",
    "ensure_provider_binding",
    "ensure_runtime_instance",
    "ensure_workspace",
    "orchestrate",
    "provision_employee",
    "readiness_items",
    "readiness_report",
    "required_for_execution",
    "resolve_runtime_policy",
    "set_company_runtime_defaults",
    "validate_policy_keys",
]
