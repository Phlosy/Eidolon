"""Runtime management business logic (v0.2): instances, brains, types, images."""

import asyncio

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.brain import BRAIN_EDITABLE_FIELDS, merge_traits, write_traits_to_brain
from app.brain import policy_for as behavior_policy_for
from app.brain.projection import project_brain
from app.brain.traits import TraitOutOfRange, UnknownTrait
from app.core.config import settings
from app.events.bus import bus
from app.models.enums import DeploymentMode, RuntimeType
from app.models.organization import Employee
from app.models.runtime import EmployeeBrain, RuntimeInstance
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import RuntimeCapabilities
from app.runtimes.gateway import gateway
from app.runtimes.manager.docker_manager import (
    DockerRuntimeInstanceManager,
    RuntimeManagerError,
    get_manager,
)
from app.runtimes.updates.service import get_update_service
from app.schemas.runtime import (
    EmployeeBrainOut,
    EmployeeBrainPatch,
    EmployeeRuntimeCreate,
    EmployeeRuntimeProviderPatch,
    RuntimeCapabilitiesOut,
    RuntimeImageOut,
    RuntimeInstanceOut,
    RuntimeTypeInfoOut,
)


def instance_out(db: Session, instance: RuntimeInstance) -> RuntimeInstanceOut:
    employee = org_repo.get_employee(db, instance.employee_id)
    provider_name = None
    model = None
    if instance.model_binding_id:
        from app.models.provider import ModelBinding

        binding = db.get(ModelBinding, instance.model_binding_id)
        if binding is not None:
            model = binding.model
            provider = provider_repo.get_provider(db, binding.provider_id)
            provider_name = provider.name if provider else None
    return RuntimeInstanceOut(
        id=instance.id,
        employee_id=instance.employee_id,
        runtime_type=instance.runtime_type,
        deployment_mode=instance.deployment_mode,
        container_name=instance.container_name,
        image=instance.image or None,
        image_tag=instance.image_tag,
        runtime_version=instance.runtime_version,
        status=instance.status,
        health_status=instance.health_status,
        internal_host=instance.internal_host,
        internal_port=instance.internal_port,
        workspace_path=instance.workspace_path,
        data_path=instance.data_path,
        model_binding_id=instance.model_binding_id,
        cpu_limit=instance.cpu_limit,
        memory_limit_mb=instance.memory_limit_mb,
        last_healthcheck_at=instance.last_healthcheck_at,
        started_at=instance.started_at,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
        employee_name=employee.name if employee else None,
        employee_slug=employee.slug if employee else None,
        provider_name=provider_name,
        model=model,
    )


def _get_instance_or_404(db: Session, instance_id: int) -> RuntimeInstance:
    instance = runtime_repo.get_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="runtime instance not found")
    return instance


def list_instances(db: Session) -> list[RuntimeInstanceOut]:
    return [instance_out(db, i) for i in runtime_repo.list_instances(db)]


def get_instance(db: Session, instance_id: int) -> RuntimeInstanceOut:
    return instance_out(db, _get_instance_or_404(db, instance_id))


def get_employee_runtime(db: Session, employee: Employee) -> RuntimeInstanceOut | None:
    instance = runtime_repo.get_instance_for_employee(db, employee.id)
    return instance_out(db, instance) if instance else None


async def create_employee_runtime(
    db: Session,
    employee: Employee,
    payload: EmployeeRuntimeCreate,
    manager: DockerRuntimeInstanceManager | None = None,
) -> RuntimeInstanceOut:
    manager = manager or get_manager()
    if runtime_repo.get_instance_for_employee(db, employee.id) is not None:
        raise HTTPException(status_code=409, detail="employee already has a runtime instance")

    provider = None
    if payload.provider_id is not None:
        # scope check: employee can only use company-scope or their own providers
        provider = provider_repo.get_provider_visible(db, payload.provider_id, employee.id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        if not provider.enabled:
            raise HTTPException(status_code=422, detail="provider is disabled")
    if (
        payload.runtime_type != RuntimeType.mock
        and payload.deployment_mode == DeploymentMode.docker.value
        and (provider is None or not payload.model)
    ):
        raise HTTPException(status_code=422, detail="docker runtimes require provider_id and model")

    try:
        instance = await manager.create_instance(
            db,
            employee,
            runtime_type=payload.runtime_type.value,
            deployment_mode=payload.deployment_mode,
            provider=provider,
            model=payload.model,
            cpu_limit=payload.cpu_limit,
            memory_limit_mb=payload.memory_limit_mb,
        )
    except RuntimeManagerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if provider is not None and payload.model:
        provider_repo.clear_primary_flags(db, employee.id)
        binding = provider_repo.create_binding(
            db,
            employee_id=employee.id,
            provider_id=provider.id,
            model=payload.model,
            is_primary=True,
            position=0,
        )
        instance.model_binding_id = binding.id
    # keep the v0.1 runtime pointer in sync (Task dispatch reads it)
    employee.runtime_type = payload.runtime_type.value
    db.commit()
    gateway.drop_instance(employee.id)
    return instance_out(db, instance)


async def change_runtime_provider(
    db: Session,
    employee: Employee,
    payload: EmployeeRuntimeProviderPatch,
    manager: DockerRuntimeInstanceManager | None = None,
) -> RuntimeInstanceOut:
    """Re-render provider config + restart the runtime if needed.

    Identity/memory/skills live in the mounted data dirs and are untouched.
    """
    manager = manager or get_manager()
    instance = runtime_repo.get_instance_for_employee(db, employee.id)
    if instance is None:
        raise HTTPException(status_code=404, detail="employee has no runtime instance")
    provider = provider_repo.get_provider_visible(db, payload.provider_id, employee.id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")

    provider_repo.clear_primary_flags(db, employee.id)
    binding = provider_repo.create_binding(
        db,
        employee_id=employee.id,
        provider_id=provider.id,
        model=payload.model,
        is_primary=True,
        position=0,
    )
    instance.model_binding_id = binding.id
    db.commit()

    if instance.deployment_mode == DeploymentMode.docker.value:
        try:
            # env vars are fixed at container create → recreate with same mounts
            await manager.recreate_container(db, instance)
        except RuntimeManagerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.commit()
    bus.publish(
        "runtime.provider_changed",
        {
            "instance_id": instance.id,
            "employee_id": employee.id,
            "provider_id": provider.id,
            "model": payload.model,
        },
        company_id=employee.company_id,
        actor_employee_id=employee.id,
    )
    return instance_out(db, instance)


async def change_employee_runtime(
    db: Session,
    employee: Employee,
    payload: EmployeeRuntimeCreate,
    manager: DockerRuntimeInstanceManager | None = None,
) -> RuntimeInstanceOut:
    """切换员工的运行时类型（同类型时最多顺手换 provider/model）。

    换类型 = 拆掉旧实例再按新类型开一个；工作区/记忆/技能所在的数据目录在
    destroy 时保留，所以身份不会丢。Mock 入职后想换成 Hermes/OpenClaw
    只能走这条路径 —— 没有它，Mock 就是一个不可逆的选择。
    """
    manager = manager or get_manager()
    instance = runtime_repo.get_instance_for_employee(db, employee.id)
    if instance is None:
        raise HTTPException(status_code=404, detail="employee has no runtime instance")

    same_type = instance.runtime_type == payload.runtime_type.value
    same_mode = instance.deployment_mode == payload.deployment_mode
    if same_type and same_mode:
        if payload.provider_id is not None and payload.model:
            return await change_runtime_provider(
                db,
                employee,
                EmployeeRuntimeProviderPatch(provider_id=payload.provider_id, model=payload.model),
                manager,
            )
        return instance_out(db, instance)

    await manager.destroy_instance(db, instance)
    gateway.drop_instance(employee.id)
    # 旧实例已删除并提交；create 路径会重新校验 provider/docker 并开出新实例
    return await create_employee_runtime(db, employee, payload, manager)


async def delete_employee_runtime(
    db: Session, employee: Employee, manager: DockerRuntimeInstanceManager | None = None
) -> None:
    """Destroy the container/instance row; data dirs are KEPT."""
    manager = manager or get_manager()
    instance = runtime_repo.get_instance_for_employee(db, employee.id)
    if instance is None:
        raise HTTPException(status_code=404, detail="employee has no runtime instance")
    await manager.destroy_instance(db, instance)
    gateway.drop_instance(employee.id)


async def instance_action(db: Session, instance_id: int, action: str) -> RuntimeInstanceOut:
    manager = get_manager()
    instance = _get_instance_or_404(db, instance_id)
    try:
        if action == "start":
            await manager.start_instance(db, instance)
        elif action == "stop":
            await manager.stop_instance(db, instance)
        elif action == "restart":
            await manager.restart_instance(db, instance)
        else:  # pragma: no cover - guarded by the router
            raise HTTPException(status_code=400, detail=f"unknown action {action}")
    except RuntimeManagerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return instance_out(db, instance)


async def instance_logs(db: Session, instance_id: int, tail: int) -> list[str]:
    instance = _get_instance_or_404(db, instance_id)
    return await get_manager().get_logs(instance, tail)


# ---- runtime types ----


async def runtime_types() -> list[RuntimeTypeInfoOut]:
    docker = get_manager()._docker
    docker_available = await asyncio.to_thread(docker.ping)
    adapters = gateway.adapters()
    result = []
    for rt in RuntimeType:
        adapter = adapters.get(rt)
        implemented = bool(adapter and adapter.implemented)
        if rt == RuntimeType.mock:
            modes = [DeploymentMode.mock.value]
        elif implemented:
            modes = [DeploymentMode.docker.value]
        else:
            modes = []
        capabilities = adapter.get_capabilities() if adapter else RuntimeCapabilities()
        result.append(
            RuntimeTypeInfoOut(
                type=rt.value,
                implemented=implemented,
                docker_available=docker_available
                if modes == [DeploymentMode.docker.value]
                else False,
                deployment_modes=modes,
                capabilities=RuntimeCapabilitiesOut(**vars(capabilities)),
                supported_providers=adapter.supported_providers() if adapter else [],
            )
        )
    return result


# ---- brains ----


def get_brain(db: Session, employee: Employee) -> EmployeeBrainOut:
    return _brain_out(db, employee)


def _brain_out(
    db: Session, employee: Employee, brain: EmployeeBrain | None = None
) -> EmployeeBrainOut:
    """brain + BehaviorPolicy 摘要（工作方式额度/风格）。永不含 confidence / 结果判定。"""
    brain = brain if brain is not None else runtime_repo.ensure_brain(db, employee.id)
    policy = behavior_policy_for(db, employee.id, getattr(employee, "company", None))
    out = EmployeeBrainOut.model_validate(brain)
    out.behavior = policy.as_dict()
    return out


def patch_brain(db: Session, employee: Employee, payload: EmployeeBrainPatch) -> EmployeeBrainOut:
    brain = runtime_repo.ensure_brain(db, employee.id)
    data = payload.model_dump(exclude_unset=True)
    # 白名单来自 app.brain（契约层），不是“payload 里有什么就 setattr 什么”（§4.4）。
    traits_patch = data.pop("traits", None)
    curiosity_patch = data.pop("curiosity", None)
    for name, value in data.items():
        if name not in BRAIN_EDITABLE_FIELDS:
            raise HTTPException(status_code=422, detail=f"不可编辑的 brain 字段：{name}")
        setattr(brain, name, value)
    if traits_patch is not None or curiosity_patch is not None:
        patch: dict = dict(traits_patch or {})
        if curiosity_patch is not None:
            patch.setdefault("curiosity", curiosity_patch)  # 显式 traits 优先
        try:
            merged = merge_traits(brain, patch)
        except (UnknownTrait, TraitOutOfRange, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        write_traits_to_brain(brain, merged)
    db.commit()
    db.refresh(brain)
    project_brain(db, employee, brain)
    return _brain_out(db, employee, brain)


# ---- images ----


def list_images(db: Session) -> list[RuntimeImageOut]:
    rows = runtime_repo.list_images(db)
    if not rows:
        # seed rows for known docker runtimes (repository from settings)
        for rt, image in {
            RuntimeType.hermes.value: settings.hermes_image,
            RuntimeType.openclaw.value: settings.openclaw_image,
        }.items():
            repository, _, tag = image.rpartition(":")
            runtime_repo.upsert_image(db, rt, repository=repository or image, tag=tag or "latest")
        db.commit()
        rows = runtime_repo.list_images(db)
    return [
        RuntimeImageOut(
            runtime_type=row.runtime_type,
            repository=row.repository,
            tag=row.tag,
            installed_version=row.installed_version,
            latest_version=row.latest_version,
            update_available=row.update_available,
            compatibility_status=row.compatibility_status,
            update_status=row.update_status,
            last_checked_at=row.last_checked_at,
            used_by=runtime_repo.count_instances_using(db, row.runtime_type),
        )
        for row in rows
    ]


async def check_updates(db: Session) -> list[RuntimeImageOut]:
    await get_update_service().check_updates()
    return list_images(db)


async def trigger_update(db: Session, runtime_type: str) -> RuntimeImageOut:
    if runtime_type not in (RuntimeType.hermes.value, RuntimeType.openclaw.value):
        raise HTTPException(status_code=404, detail=f"unknown runtime type: {runtime_type}")
    list_images(db)  # ensure the row exists
    service = get_update_service()
    # runs in the background; progress is observable via runtime.update_* events
    asyncio.create_task(service.managed_update(runtime_type), name=f"runtime-update-{runtime_type}")
    row = runtime_repo.get_image(db, runtime_type)
    return RuntimeImageOut(
        runtime_type=row.runtime_type,
        repository=row.repository,
        tag=row.tag,
        installed_version=row.installed_version,
        latest_version=row.latest_version,
        update_available=row.update_available,
        compatibility_status=row.compatibility_status,
        update_status=row.update_status,
        last_checked_at=row.last_checked_at,
        used_by=runtime_repo.count_instances_using(db, row.runtime_type),
    )
