"""Durable per-instance ensure; database phases never span Docker calls."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, timedelta
from uuid import uuid4

from docker.utils import parse_repository_tag
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.redaction import redact
from app.models.base import utcnow
from app.models.enums import (
    DeploymentMode,
    HealthStatus,
    LifecycleStatus,
    RuntimeInstanceStatus,
    RuntimeType,
)
from app.models.organization import Employee
from app.models.runtime import RuntimeInstance
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo

from .connection import reserve_connection
from .docker_manager import RUNTIME_PORTS, RuntimeManagerError, employee_dirs, runtime_user_ids


async def blocking(operation, *args, **kwargs):
    """Drain a finite SDK/database call before releasing its resource claim."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling to_thread does not stop its thread. Releasing now could let
        # another process race a still-running create/start operation.
        while not task.done():
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        with suppress(Exception, asyncio.CancelledError):
            task.result()
        raise


def _lease_seconds() -> float:
    return max(180, settings.provisioning_step_timeout_seconds + 60)


def _claim(
    factory, manager, employee_id, runtime_type, deployment_mode, token, cpu_limit, memory_limit_mb
):
    with factory() as db:
        employee = db.get(Employee, employee_id)
        if employee is None or employee.lifecycle_status not in {
            LifecycleStatus.active.value,
            LifecycleStatus.onboarding.value,
        }:
            raise RuntimeManagerError("employee is missing or inactive")
        if not employee.workspace_path:
            raise RuntimeManagerError("employee has no workspace_path")
        instance = runtime_repo.get_instance_for_employee(db, employee_id)
        if instance is None:
            try:
                instance = runtime_repo.create_instance(
                    db,
                    employee_id=employee_id,
                    runtime_type=runtime_type,
                    deployment_mode=deployment_mode,
                    workspace_path=employee.workspace_path,
                    data_path=str(employee_dirs(employee_id, runtime_type)["base"]),
                    cpu_limit=cpu_limit,
                    memory_limit_mb=memory_limit_mb,
                )
            except IntegrityError:
                # The employee/person unique constraints chose another creator.
                db.rollback()
                return None
        if instance.employee_id != employee_id:
            raise RuntimeManagerError("runtime belongs to another employment")
        if (instance.runtime_type, instance.deployment_mode) != (runtime_type, deployment_mode):
            raise RuntimeManagerError(
                "runtime configuration differs; use the explicit runtime change action"
            )
        result = db.execute(
            update(RuntimeInstance)
            .where(
                RuntimeInstance.id == instance.id,
                or_(
                    RuntimeInstance.provisioning_token.is_(None),
                    RuntimeInstance.provisioning_claimed_at.is_(None),
                    RuntimeInstance.provisioning_claimed_at
                    <= utcnow() - timedelta(seconds=_lease_seconds()),
                ),
            )
            .values(provisioning_token=token, provisioning_claimed_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        db.refresh(instance)
        instance.resource_key = instance.resource_key or str(uuid4())
        instance.workspace_path = employee.workspace_path
        instance.data_path = str(employee_dirs(employee_id, runtime_type)["base"])
        if deployment_mode == DeploymentMode.docker.value:
            instance.container_name = instance.container_name or (
                f"eidolon-{runtime_type}-{employee.slug[:48]}-{instance.resource_key[:12]}"
            )
            metadata = dict(instance.metadata_json or {})
            if not metadata.get("api_key_ref"):
                if instance.container_id:
                    raise RuntimeManagerError(
                        "recorded container has no control credential; repair configuration first"
                    )
                metadata["api_key_ref"] = manager._secrets.store(db, uuid4().hex + uuid4().hex)
            if runtime_type == RuntimeType.openclaw.value and not metadata.get("device_key_ref"):
                from app.runtimes.openclaw.identity import new_device_key

                metadata["device_key_ref"] = manager._secrets.store(db, new_device_key())
            instance.metadata_json = metadata
            reserve_connection(db, instance)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent instance may have reserved this loopback port first.
            db.rollback()
            return None
        db.expunge(instance)
        return instance


def _save(factory, instance_id, token, **fields):
    with factory() as db:
        result = db.execute(
            update(RuntimeInstance)
            .where(RuntimeInstance.id == instance_id, RuntimeInstance.provisioning_token == token)
            .values(**fields, provisioning_claimed_at=utcnow())
        )
        if result.rowcount != 1:
            raise RuntimeManagerError("runtime provisioning claim was superseded")
        db.commit()


def _release(factory, employee_id, token):
    with factory() as db:
        db.execute(
            update(RuntimeInstance)
            .where(
                RuntimeInstance.employee_id == employee_id,
                RuntimeInstance.provisioning_token == token,
            )
            .values(provisioning_token=None, provisioning_claimed_at=None)
        )
        db.commit()


def _prepare(factory, manager, instance_id, token, provider_id, model, write_files):
    # Read-only database session while rendering files; its ORM mutations are
    # discarded. Only the fenced _save below persists the resulting metadata.
    with factory() as db:
        instance = db.get(RuntimeInstance, instance_id)
        if instance is None or instance.provisioning_token != token:
            raise RuntimeManagerError("runtime provisioning claim was superseded")
        employee = db.get(Employee, instance.employee_id)
        provider = None
        if instance.model_binding_id:
            provider, model = manager._current_binding(db, instance)
        elif provider_id is not None:
            provider = provider_repo.get_provider_visible(db, provider_id, employee.id)
            if provider is None:
                raise RuntimeManagerError("provider is not visible to this employee")
        if provider is not None and not provider.enabled:
            raise RuntimeManagerError("provider is disabled")
        api_token = manager._secrets.retrieve(db, instance.metadata_json.get("api_key_ref"))
        if not api_token:
            raise RuntimeManagerError("runtime control credential is unavailable")
        spec = manager._container_spec(
            db, instance, employee, provider, model, api_token, write_files=write_files
        )
        metadata = dict(instance.metadata_json or {})
    _save(factory, instance_id, token, metadata_json=metadata)
    return spec


def _matches(attrs, instance, spec):
    labels = attrs.get("Config", {}).get("Labels") or {}
    legacy_recorded = attrs.get("Id") == instance.container_id and not labels.get(
        "eidolon.resource_key"
    )
    if not (
        labels.get("eidolon.managed") == "true"
        and labels.get("eidolon.employee_id") == str(instance.employee_id)
        and labels.get("eidolon.runtime_type") == instance.runtime_type
        and (labels.get("eidolon.resource_key") == instance.resource_key or legacy_recorded)
    ):
        raise RuntimeManagerError("container ownership does not match the persisted runtime")
    mounts = {mount.get("Destination"): mount for mount in attrs.get("Mounts", [])}
    for source, volume in spec["volumes"].items():
        mount = mounts.get(volume["bind"], {})
        if mount.get("Source") != source or not mount.get("RW"):
            raise RuntimeManagerError(f"container mount does not match runtime: {volume['bind']}")
    if settings.runtime_network not in attrs.get("NetworkSettings", {}).get("Networks", {}):
        raise RuntimeManagerError("container is not on the configured runtime network")
    if spec.get("ports"):
        bindings = attrs.get("HostConfig", {}).get("PortBindings") or {}
        for container_port, (host, port) in spec["ports"].items():
            if bindings.get(container_port) != [{"HostIp": host, "HostPort": str(port)}]:
                raise RuntimeManagerError("container control port does not match runtime")


async def ensure_instance(
    manager,
    factory,
    employee_id,
    *,
    runtime_type,
    deployment_mode,
    provider_id=None,
    model=None,
    cpu_limit=2.0,
    memory_limit_mb=4096,
):
    if runtime_type == RuntimeType.mock.value or deployment_mode == DeploymentMode.mock.value:
        runtime_type, deployment_mode = RuntimeType.mock.value, DeploymentMode.mock.value
    elif runtime_type not in RUNTIME_PORTS or deployment_mode != DeploymentMode.docker.value:
        raise RuntimeManagerError(
            f"unsupported runtime environment: {runtime_type}/{deployment_mode}"
        )
    token = str(uuid4())
    instance = None
    deadline = asyncio.get_running_loop().time() + settings.provisioning_step_timeout_seconds
    try:
        while instance is None:
            instance = await blocking(
                _claim,
                factory,
                manager,
                employee_id,
                runtime_type,
                deployment_mode,
                token,
                cpu_limit,
                memory_limit_mb,
            )
            if instance is None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeManagerError("runtime provisioning is already in progress")
                await asyncio.sleep(0.05)
        await blocking(manager._prepare_directories, instance)
        if deployment_mode == DeploymentMode.mock.value:
            await blocking(
                _save,
                factory,
                instance.id,
                token,
                status=RuntimeInstanceStatus.running.value,
                health_status=HealthStatus.healthy.value,
                image="mock-runtime",
                started_at=instance.started_at or utcnow(),
            )
        else:
            if not await blocking(manager._docker.ping):
                raise RuntimeManagerError("docker daemon is not available")
            attrs = await blocking(manager._docker.inspect_container, instance.container_name)
            spec = await blocking(
                _prepare, factory, manager, instance.id, token, provider_id, model, attrs is None
            )
            existing_container = attrs is not None
            if attrs is None:
                await manager._ensure_image(spec["image"])
                if not await blocking(manager._docker.ensure_network, settings.runtime_network):
                    raise RuntimeManagerError("runtime network is unavailable")
                await blocking(
                    _save, factory, instance.id, token, status=RuntimeInstanceStatus.starting.value
                )
                container_id = await blocking(
                    manager._docker.create_container,
                    name=instance.container_name,
                    image=spec["image"],
                    environment=spec["env"],
                    volumes=spec["volumes"],
                    network=settings.runtime_network,
                    cpu_limit=instance.cpu_limit,
                    memory_limit_mb=instance.memory_limit_mb,
                    restart_policy=instance.restart_policy,
                    labels={
                        "eidolon.employee_id": str(employee_id),
                        "eidolon.runtime_type": runtime_type,
                        "eidolon.resource_key": instance.resource_key,
                    },
                    command=spec.get("command"),
                    user=spec.get("user"),
                    ports=spec.get("ports"),
                )
                # A timed-out create may have succeeded. Inspect the stable name
                # and validate its ownership before adopting the external fact.
                attrs = await blocking(
                    manager._docker.inspect_container, container_id or instance.container_name
                )
                if attrs is None:
                    raise RuntimeManagerError("runtime container creation failed")
            _matches(attrs, instance, spec)
            instance.container_id = attrs["Id"]
            await blocking(
                _save,
                factory,
                instance.id,
                token,
                container_id=instance.container_id,
            )
            state = attrs.get("State", {})
            unhealthy = (
                state.get("Status") == "running"
                and (state.get("Health") or {}).get("Status") == "unhealthy"
            )
            if existing_container and (unhealthy or state.get("Status") != "running"):
                metadata = await blocking(_get_metadata, factory, instance.id)
                previous = metadata.get("ensure_recovery_at")
                now = utcnow().replace(tzinfo=UTC).timestamp()
                if previous and now - float(previous) < 300:
                    raise RuntimeManagerError(
                        "runtime remains unhealthy; recovery cooldown is active"
                    )
                metadata["ensure_recovery_at"] = now
                await blocking(_save, factory, instance.id, token, metadata_json=metadata)
            if unhealthy:
                if not await blocking(manager._docker.restart_container, instance.container_id):
                    raise RuntimeManagerError("runtime recovery failed")
            elif state.get("Status") != "running":
                if not await blocking(manager._docker.start_container, instance.container_id):
                    raise RuntimeManagerError("runtime start failed")
            # Detached instance: health/version I/O has no database transaction.
            await manager._wait_healthy(None, instance)
            await blocking(_probe_workspace, manager, instance, spec["workspace_mount"])
            version = instance.runtime_version or await manager._detect_version(instance)
            actual_image = (
                attrs.get("Image") or attrs.get("Config", {}).get("Image") or spec["image"]
            )
            image = await blocking(manager._docker.inspect_image, actual_image)
            repository, tag = parse_repository_tag(
                attrs.get("Config", {}).get("Image") or spec["image"]
            )
            metadata = await blocking(_get_metadata, factory, instance.id)
            metadata.pop("ensure_error", None)
            await blocking(
                _save,
                factory,
                instance.id,
                token,
                status=instance.status,
                health_status=instance.health_status,
                last_healthcheck_at=utcnow(),
                started_at=instance.started_at or utcnow(),
                stopped_at=None,
                runtime_version=version,
                image_digest=(image or {}).get("Id"),
                image=repository,
                image_tag=tag or "latest",
                metadata_json=metadata,
            )
        return await blocking(_get_instance, factory, instance.id)
    except Exception as exc:
        if instance is not None:
            metadata = await blocking(_get_metadata, factory, instance.id)
            metadata["ensure_error"] = redact(str(exc))
            try:
                await blocking(
                    _save,
                    factory,
                    instance.id,
                    token,
                    status=RuntimeInstanceStatus.error.value,
                    metadata_json=metadata,
                )
            except RuntimeManagerError:
                pass  # A successor owns the receipt now.
        raise
    finally:
        await blocking(_release, factory, employee_id, token)


def _get_instance(factory, instance_id):
    with factory() as db:
        instance = db.get(RuntimeInstance, instance_id)
        db.expunge(instance)
        return instance


def _get_metadata(factory, instance_id):
    return dict(_get_instance(factory, instance_id).metadata_json or {})


def _probe_workspace(manager, instance, mount):
    if instance.runtime_type == RuntimeType.hermes.value:
        command = [
            "python3",
            "-c",
            "import sys,tempfile; f=tempfile.TemporaryFile(dir=sys.argv[1]); f.close()",
            mount,
        ]
    else:
        command = [
            "node",
            "-e",
            (
                "const fs=require('node:fs'),p=require('node:path');"
                "const f=p.join(process.argv[1],'.eidolon-probe-'+process.pid+'-'+Date.now());"
                "fs.writeFileSync(f,'',{flag:'wx'});fs.unlinkSync(f)"
            ),
            mount,
        ]
    uid, gid = runtime_user_ids(instance.runtime_type)
    code, _ = manager._docker.exec_run(instance.container_id, command, user=f"{uid}:{gid}")
    if code != 0:
        raise RuntimeManagerError("runtime user cannot write the employee workspace")
