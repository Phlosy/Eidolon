"""DockerRuntimeInstanceManager (v0.2).

Provisions one container per employee on the private ``eidolon-runtime-net``
network (no host port publishing — the backend reaches containers via docker
network DNS). Per-employee persistent dirs live under
``{data_root}/employees/{employee_id}/{workspace,brain,runtime/<type>}``.

Mock instances (deployment_mode=mock) are virtual rows: no container, status
always running — they let mock-mode employees participate in the same
management surface. All docker calls are wrapped in asyncio.to_thread and
degrade gracefully when the daemon is unavailable.
"""

import asyncio
import secrets
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import redaction
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.models.enums import (
    DeploymentMode,
    HealthStatus,
    RuntimeInstanceStatus,
    RuntimeType,
)
from app.models.organization import Employee
from app.models.provider import Provider
from app.models.runtime import RuntimeInstance
from app.providers import registry as provider_registry
from app.providers.secrets.store import LocalEncryptedSecretStore, get_secret_store
from app.repositories import runtimes as runtime_repo
from app.runtimes.docker.service import DockerService, get_docker_service
from app.runtimes.manager.base import RuntimeInstanceManager

logger = get_logger(__name__)

# Container-internal control-plane ports (never published to the host).
RUNTIME_PORTS = {RuntimeType.hermes.value: 8642, RuntimeType.openclaw.value: 18789}

# Default commands: the bare hermes image is an interactive chat, so the
# documented headless mode is `gateway run` (research §1); openclaw's image
# entrypoint already starts the gateway.
DEFAULT_COMMANDS: dict[str, list[str]] = {RuntimeType.hermes.value: ["gateway", "run"]}

DEFAULT_VERSION_COMMANDS: dict[str, list[str]] = {
    RuntimeType.hermes.value: ["hermes", "--version"],
    RuntimeType.openclaw.value: ["openclaw", "--version"],
}

# In-container mount targets per runtime type, relative to the runtime dir.
HERMES_MOUNT = "/opt/data"
OPENCLAW_CONFIG_MOUNT = "/home/node/.openclaw"
OPENCLAW_AUTH_MOUNT = "/home/node/.config/openclaw"

_HEALTH_WAIT_SECONDS = 45
_ACTIVE_STATUSES = (
    RuntimeInstanceStatus.created.value,
    RuntimeInstanceStatus.starting.value,
    RuntimeInstanceStatus.running.value,
    RuntimeInstanceStatus.idle.value,
    RuntimeInstanceStatus.unhealthy.value,
)


class RuntimeManagerError(RuntimeError):
    pass


def employee_dirs(employee_id: int, runtime_type: str) -> dict[str, Path]:
    base = Path(settings.data_root) / "employees" / str(employee_id)
    return {
        "base": base,
        "workspace": base / "workspace",
        "brain": base / "brain",
        "runtime": base / "runtime" / runtime_type,
    }


def container_name(runtime_type: str, employee_slug: str) -> str:
    return f"eidolon-{runtime_type}-{employee_slug}-{secrets.token_hex(3)}"


class DockerRuntimeInstanceManager(RuntimeInstanceManager):
    def __init__(
        self,
        docker: DockerService | None = None,
        secret_store: LocalEncryptedSecretStore | None = None,
        command_overrides: dict[str, list[str]] | None = None,
        version_commands: dict[str, list[str]] | None = None,
    ) -> None:
        self._docker = docker or get_docker_service()
        self._secrets = secret_store or get_secret_store()
        self._commands = {**DEFAULT_COMMANDS, **(command_overrides or {})}
        self._version_commands = {**DEFAULT_VERSION_COMMANDS, **(version_commands or {})}
        self._loop_task: asyncio.Task | None = None

    # ---- provisioning ----

    async def create_instance(
        self,
        db: Session,
        employee: Employee,
        *,
        runtime_type: str,
        deployment_mode: str,
        provider: Provider | None = None,
        model: str | None = None,
        cpu_limit: float = 2.0,
        memory_limit_mb: int = 4096,
    ) -> RuntimeInstance:
        existing = runtime_repo.get_instance_for_employee(db, employee.id)
        if existing is not None:
            raise RuntimeManagerError(f"employee {employee.id} already has a runtime instance")

        dirs = employee_dirs(employee.id, runtime_type)
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)

        if runtime_type == RuntimeType.mock.value or deployment_mode == DeploymentMode.mock.value:
            instance = runtime_repo.create_instance(
                db,
                employee_id=employee.id,
                runtime_type=RuntimeType.mock.value,
                deployment_mode=DeploymentMode.mock.value,
                image="mock-runtime",
                image_tag="latest",
                status=RuntimeInstanceStatus.running.value,
                health_status=HealthStatus.healthy.value,
                workspace_path=str(dirs["workspace"]),
                data_path=str(dirs["base"]),
                cpu_limit=cpu_limit,
                memory_limit_mb=memory_limit_mb,
                started_at=datetime.now(UTC),
            )
            db.commit()
            self._publish(db, "runtime.instance_created", instance)
            self._publish(db, "runtime.started", instance)
            return instance

        if deployment_mode != DeploymentMode.docker.value:
            raise RuntimeManagerError(f"unsupported deployment mode: {deployment_mode}")
        if not await asyncio.to_thread(self._docker.available):
            raise RuntimeManagerError("docker daemon is not available")

        instance = runtime_repo.create_instance(
            db,
            employee_id=employee.id,
            runtime_type=runtime_type,
            deployment_mode=DeploymentMode.docker.value,
            image=self._image_for(runtime_type).rsplit(":", 1)[0],
            image_tag=self._image_for(runtime_type).rsplit(":", 1)[-1],
            status=RuntimeInstanceStatus.created.value,
            health_status=HealthStatus.unknown.value,
            workspace_path=str(dirs["workspace"]),
            data_path=str(dirs["base"]),
            cpu_limit=cpu_limit,
            memory_limit_mb=memory_limit_mb,
        )
        db.commit()
        self._publish(db, "runtime.instance_created", instance)
        try:
            await self._provision_container(db, instance, employee, provider, model)
        except Exception:
            # never leak a half-provisioned container
            if instance.container_id:
                await asyncio.to_thread(self._docker.remove_container, instance.container_id)
                instance.container_id = None
            instance.status = RuntimeInstanceStatus.error.value
            db.commit()
            self._publish(db, "runtime.crashed", instance, extra={"stage": "create"})
            raise
        return instance

    async def _provision_container(
        self,
        db: Session,
        instance: RuntimeInstance,
        employee: Employee,
        provider: Provider | None,
        model: str | None,
    ) -> None:
        # per-employee control-plane credential, kept in the secret store
        api_token = secrets.token_hex(32)
        redaction.register_secret(api_token)
        token_ref = self._secrets.store(db, api_token)
        spec = self._container_spec(db, instance, employee, provider, model, api_token)

        instance.status = RuntimeInstanceStatus.starting.value
        db.commit()
        self._publish(db, "runtime.starting", instance)

        await self._ensure_image(spec["image"])
        await asyncio.to_thread(self._docker.ensure_network, settings.runtime_network)
        instance.container_name = container_name(instance.runtime_type, employee.slug)
        await self._create_and_start(db, instance, employee, spec)
        instance.metadata_json = {**instance.metadata_json, "api_key_ref": token_ref}
        db.commit()
        await self._finish_start(db, instance)

    def _container_spec(
        self,
        db: Session,
        instance: RuntimeInstance,
        employee: Employee,
        provider: Provider | None,
        model: str | None,
        api_token: str,
    ) -> dict:
        """Env + volumes + image for the employee's container (shared by create
        and recreate — provider keys are re-rendered from the secret store each
        time, so no plaintext credential is ever persisted in the DB)."""
        runtime_type = instance.runtime_type
        dirs = employee_dirs(employee.id, runtime_type)
        runtime_dir = dirs["runtime"]

        env: dict[str, str] = {}
        credential = self._secrets.retrieve(db, provider.credential_ref) if provider else None
        if provider is not None and model:
            configurator = provider_registry.configurator_for(runtime_type)
            if configurator is None:
                raise RuntimeManagerError(f"no provider configurator for {runtime_type}")
            errors = configurator.validate_provider(provider)
            if errors:
                raise RuntimeManagerError(f"invalid provider config: {'; '.join(errors)}")
            rendered = configurator.render_provider_config(provider, model, credential)
            env.update(rendered.env)
            for rel_path, content in rendered.files.items():
                target = runtime_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                if target.name == ".env" or (credential and credential in content):
                    target.chmod(0o600)

        image = self._image_for(runtime_type)
        if runtime_type == RuntimeType.hermes.value:
            env.update(
                {
                    "API_SERVER_ENABLED": "true",
                    "API_SERVER_HOST": "0.0.0.0",
                    "API_SERVER_KEY": api_token,
                    "PUID": "10000",
                    "PGID": "10000",
                }
            )
            volumes = {str(runtime_dir.resolve()): {"bind": HERMES_MOUNT, "mode": "rw"}}
        elif runtime_type == RuntimeType.openclaw.value:
            auth_dir = runtime_dir.parent / "openclaw-auth"
            auth_dir.mkdir(parents=True, exist_ok=True)
            # container runs as uid 1000; keep bind mounts writable (research §9)
            for path in (runtime_dir, auth_dir):
                path.chmod(0o777)
            env["OPENCLAW_GATEWAY_TOKEN"] = api_token
            # the gateway refuses to boot without a config carrying
            # gateway.mode=local + auth — render it even without a provider
            configurator = provider_registry.configurator_for(runtime_type)
            rendered = configurator.render_with_context(
                provider if (provider and model) else None,
                model,
                credential,
                gateway_token=api_token,
            )
            env.update(rendered.env)
            for rel_path, content in rendered.files.items():
                target = runtime_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                if target.name == ".env" or api_token in content:
                    target.chmod(0o600)
            volumes = {
                str(runtime_dir.resolve()): {"bind": OPENCLAW_CONFIG_MOUNT, "mode": "rw"},
                str(auth_dir.resolve()): {"bind": OPENCLAW_AUTH_MOUNT, "mode": "rw"},
            }
        else:
            raise RuntimeManagerError(f"unsupported runtime type: {runtime_type}")
        return {
            "env": env,
            "volumes": volumes,
            "image": image,
            "runtime_dir": runtime_dir,
            "command": self._commands.get(runtime_type),
        }

    async def _ensure_image(self, image: str) -> None:
        if await asyncio.to_thread(self._docker.inspect_image, image) is None:
            if await asyncio.to_thread(self._docker.pull_image, image) is None:
                raise RuntimeManagerError(f"failed to pull image {image}")

    async def _create_and_start(
        self, db: Session, instance: RuntimeInstance, employee: Employee, spec: dict
    ) -> None:
        if not instance.container_name:
            raise RuntimeManagerError("container_name must be set before create")
        container_id = await asyncio.to_thread(
            self._docker.create_container,
            name=instance.container_name,
            image=spec["image"],
            environment=spec["env"],
            volumes=spec["volumes"],
            network=settings.runtime_network,
            cpu_limit=instance.cpu_limit,
            memory_limit_mb=instance.memory_limit_mb,
            restart_policy=instance.restart_policy,
            labels={
                "eidolon.employee_id": str(employee.id),
                "eidolon.runtime_type": instance.runtime_type,
            },
            command=spec.get("command"),
        )
        if container_id is None:
            raise RuntimeManagerError("docker create_container failed")

        instance.container_id = container_id
        instance.internal_host = instance.container_name  # docker network DNS
        instance.internal_port = RUNTIME_PORTS.get(instance.runtime_type)
        if not spec["image"].startswith("sha256:"):
            # rollback recreates by digest; keep the human-readable image/tag
            instance.image = spec["image"].rsplit(":", 1)[0]
            instance.image_tag = spec["image"].rsplit(":", 1)[-1]
        image_attrs = await asyncio.to_thread(self._docker.inspect_image, spec["image"])
        instance.image_digest = (image_attrs or {}).get("Id")
        db.commit()

        if not await asyncio.to_thread(self._docker.start_container, container_id):
            raise RuntimeManagerError("failed to start container")
        instance.started_at = datetime.now(UTC)
        db.commit()
        self._publish(db, "runtime.started", instance)

    async def _finish_start(self, db: Session, instance: RuntimeInstance) -> None:
        instance.runtime_version = await self._detect_version(instance)
        await self._wait_healthy(db, instance)
        db.commit()
        self._publish(db, "runtime.ready", instance)

    async def recreate_container(
        self, db: Session, instance: RuntimeInstance, *, image: str | None = None
    ) -> None:
        """Remove + recreate the container with the same mounts/limits.

        Used by provider changes (env is re-rendered) and managed updates
        (image override). Identity/memory/skills live in the mounted dirs and
        are untouched.
        """
        employee = db.get(Employee, instance.employee_id)
        if employee is None:
            raise RuntimeManagerError(f"employee {instance.employee_id} not found")
        api_token = self._secrets.retrieve(db, (instance.metadata_json or {}).get("api_key_ref"))
        if not api_token:
            api_token = secrets.token_hex(32)
            redaction.register_secret(api_token)
            instance.metadata_json = {
                **instance.metadata_json,
                "api_key_ref": self._secrets.store(db, api_token),
            }
        provider, model = self._current_binding(db, instance)
        spec = self._container_spec(db, instance, employee, provider, model, api_token)
        if image:
            spec["image"] = image

        if instance.container_id:
            await asyncio.to_thread(self._docker.remove_container, instance.container_id)
            instance.container_id = None
        await asyncio.to_thread(self._docker.ensure_network, settings.runtime_network)
        await self._create_and_start(db, instance, employee, spec)
        await self._finish_start(db, instance)

    @staticmethod
    def _current_binding(db: Session, instance: RuntimeInstance):
        """(provider, model) from the instance's model binding, if any."""
        from app.models.provider import ModelBinding

        if not instance.model_binding_id:
            return None, None
        binding = db.get(ModelBinding, instance.model_binding_id)
        if binding is None:
            return None, None
        return db.get(Provider, binding.provider_id), binding.model

    # ---- lifecycle ----

    async def start_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance:
        if instance.deployment_mode == DeploymentMode.mock.value:
            instance.status = RuntimeInstanceStatus.running.value
            instance.health_status = HealthStatus.healthy.value
            db.commit()
            self._publish(db, "runtime.started", instance)
            return instance
        instance.status = RuntimeInstanceStatus.starting.value
        db.commit()
        self._publish(db, "runtime.starting", instance)
        if not await asyncio.to_thread(self._docker.start_container, instance.container_id):
            instance.status = RuntimeInstanceStatus.error.value
            db.commit()
            raise RuntimeManagerError("failed to start container")
        instance.started_at = datetime.now(UTC)
        instance.stopped_at = None
        await self._wait_healthy(db, instance)
        db.commit()
        self._publish(db, "runtime.started", instance)
        self._publish(db, "runtime.ready", instance)
        return instance

    async def stop_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance:
        if instance.deployment_mode == DeploymentMode.mock.value:
            instance.status = RuntimeInstanceStatus.stopped.value
            db.commit()
            self._publish(db, "runtime.stopped", instance)
            return instance
        instance.status = RuntimeInstanceStatus.stopping.value
        db.commit()
        ok = await asyncio.to_thread(self._docker.stop_container, instance.container_id)
        if not ok:
            instance.status = RuntimeInstanceStatus.error.value
            db.commit()
            raise RuntimeManagerError("failed to stop container")
        instance.status = RuntimeInstanceStatus.stopped.value
        instance.health_status = HealthStatus.unknown.value
        instance.stopped_at = datetime.now(UTC)
        db.commit()
        self._publish(db, "runtime.stopped", instance)
        return instance

    async def restart_instance(self, db: Session, instance: RuntimeInstance) -> RuntimeInstance:
        await self.stop_instance(db, instance)
        await self.start_instance(db, instance)
        self._publish(db, "runtime.restarted", instance)
        return instance

    async def destroy_instance(self, db: Session, instance: RuntimeInstance) -> None:
        """Remove the container; data dirs (identity/memory/workspace) are kept."""
        was_running = instance.status in (
            RuntimeInstanceStatus.running.value,
            RuntimeInstanceStatus.idle.value,
        )
        instance.status = RuntimeInstanceStatus.deleting.value
        db.commit()
        if instance.deployment_mode == DeploymentMode.docker.value and instance.container_id:
            await asyncio.to_thread(self._docker.remove_container, instance.container_id)
        runtime_repo.delete_instance(db, instance)
        db.commit()
        if was_running:
            bus.publish("runtime.stopped", {"employee_id": instance.employee_id, "destroyed": True})

    async def inspect_instance(self, instance: RuntimeInstance) -> dict:
        if instance.deployment_mode == DeploymentMode.mock.value or not instance.container_id:
            return {"deployment_mode": instance.deployment_mode, "container": None}
        attrs = await asyncio.to_thread(self._docker.inspect_container, instance.container_id)
        if attrs is None:
            return {"deployment_mode": instance.deployment_mode, "container": None}
        state = attrs.get("State", {})
        return {
            "deployment_mode": instance.deployment_mode,
            "container": {
                "id": attrs.get("Id", "")[:12],
                "status": state.get("Status"),
                "health": (state.get("Health") or {}).get("Status"),
                "started_at": state.get("StartedAt"),
                "image": attrs.get("Config", {}).get("Image"),
            },
        }

    async def get_logs(self, instance: RuntimeInstance, tail: int = 200) -> list[str]:
        if instance.deployment_mode == DeploymentMode.mock.value or not instance.container_id:
            return [
                f"[mock] runtime instance for employee {instance.employee_id} — no container logs"
            ]
        lines = await asyncio.to_thread(self._docker.get_logs, instance.container_id, tail)
        # defense in depth: DockerService already redacts, never trust it blindly
        return [redaction.redact(line) for line in lines]

    def get_connection_info(self, instance: RuntimeInstance) -> dict:
        info = {
            "deployment_mode": instance.deployment_mode,
            "container_name": instance.container_name,
            "network": settings.runtime_network,
            "host": instance.internal_host,
            "port": instance.internal_port,
        }
        if instance.internal_host and instance.internal_port:
            info["base_url"] = f"http://{instance.internal_host}:{instance.internal_port}"
        return info

    # ---- healthcheck ----

    async def start_healthcheck_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(
                self._healthcheck_loop(), name="runtime-healthcheck"
            )

    async def stop_healthcheck_loop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None

    async def healthcheck_once(self) -> None:
        with SessionLocal() as db:
            rows = [
                row
                for row in runtime_repo.list_instances_by_status(db, list(_ACTIVE_STATUSES))
                if row.deployment_mode == DeploymentMode.docker.value and row.container_id
            ]
            for row in rows:
                await self._check_row(db, row)
            db.commit()

    async def _check_row(self, db: Session, instance: RuntimeInstance) -> None:
        result = await asyncio.to_thread(self._docker.healthcheck_container, instance.container_id)
        instance.last_healthcheck_at = datetime.now(UTC)
        previous = instance.status
        if result is None:
            state, health = "missing", "none"
        else:
            state, health = result
        if state in ("exited", "dead", "missing"):
            if previous in _ACTIVE_STATUSES:
                instance.status = RuntimeInstanceStatus.crashed.value
                instance.health_status = HealthStatus.unhealthy.value
                instance.stopped_at = datetime.now(UTC)
                db.commit()
                self._publish(db, "runtime.crashed", instance, extra={"container_state": state})
            return
        if state == "running":
            if health == "unhealthy":
                if previous != RuntimeInstanceStatus.unhealthy.value:
                    instance.status = RuntimeInstanceStatus.unhealthy.value
                    instance.health_status = HealthStatus.unhealthy.value
                    db.commit()
                    self._publish(db, "runtime.unhealthy", instance)
            else:
                instance.status = RuntimeInstanceStatus.running.value
                instance.health_status = (
                    HealthStatus.healthy.value
                    if health == "healthy"
                    else HealthStatus.unknown.value
                )

    async def _healthcheck_loop(self) -> None:
        while True:
            try:
                await self.healthcheck_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("runtime healthcheck sweep failed")
            await asyncio.sleep(max(settings.runtime_healthcheck_interval, 1.0))

    # ---- update support ----

    def backup_data_path(self, instance: RuntimeInstance) -> Path | None:
        """Tar the instance data dir (pre-update backup). Returns archive path."""
        data_path = Path(instance.data_path)
        if not data_path.exists():
            return None
        backup_dir = data_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        archive = backup_dir / f"{instance.runtime_type}-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(data_path, arcname=data_path.name)
        return archive

    # ---- helpers ----

    async def _wait_healthy(self, db: Session, instance: RuntimeInstance) -> None:
        deadline = asyncio.get_running_loop().time() + _HEALTH_WAIT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            result = await asyncio.to_thread(
                self._docker.healthcheck_container, instance.container_id
            )
            if result is not None:
                state, health = result
                if state == "running" and health in ("healthy", "none"):
                    instance.status = RuntimeInstanceStatus.running.value
                    instance.health_status = (
                        HealthStatus.healthy.value
                        if health == "healthy"
                        else HealthStatus.unknown.value
                    )
                    instance.last_healthcheck_at = datetime.now(UTC)
                    return
                if state in ("exited", "dead"):
                    raise RuntimeManagerError(f"container exited during startup ({state})")
            await asyncio.sleep(1.0)
        raise RuntimeManagerError("container did not become healthy in time")

    async def _detect_version(self, instance: RuntimeInstance) -> str | None:
        cmd = self._version_commands.get(instance.runtime_type)
        if cmd is None:
            return None
        for _ in range(10):
            exit_code, output = await asyncio.to_thread(
                self._docker.exec_run, instance.container_id, cmd
            )
            if exit_code == 0 and output.strip():
                return output.strip().splitlines()[0][:100]
            await asyncio.sleep(2.0)
        return None

    def _image_for(self, runtime_type: str) -> str:
        return {
            RuntimeType.hermes.value: settings.hermes_image,
            RuntimeType.openclaw.value: settings.openclaw_image,
        }.get(runtime_type, runtime_type)

    def _publish(
        self, db: Session, event_type: str, instance: RuntimeInstance, extra: dict | None = None
    ) -> None:
        employee = db.get(Employee, instance.employee_id)
        bus.publish(
            event_type,
            {
                "instance_id": instance.id,
                "employee_id": instance.employee_id,
                "runtime_type": instance.runtime_type,
                "status": instance.status,
                **(extra or {}),
            },
            company_id=employee.company_id if employee else None,
            actor_employee_id=instance.employee_id,
        )


_manager: DockerRuntimeInstanceManager | None = None


def get_manager() -> DockerRuntimeInstanceManager:
    global _manager
    if _manager is None:
        _manager = DockerRuntimeInstanceManager()
    return _manager
