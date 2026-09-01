"""RuntimeUpdateService (v0.2).

Detects image updates (registry digest vs local digest) and performs managed
updates: verify employees idle → backup data dirs → pull → stop → recreate with
the same mounts → healthcheck → verify; any failure rolls back to the previous
image and emits runtime.update_rolled_back.

Policy (EIDOLON_UPDATE_POLICY): ``notify_only`` (default) only flags
update_available; ``managed`` allows API-triggered updates; ``automatic``
(reserved) additionally applies compatible updates after checks.
"""

import asyncio
import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.models.enums import (
    DeploymentMode,
    EmployeeStatus,
    ImageCompatibility,
    ImageUpdateStatus,
    RuntimeType,
)
from app.models.runtime import RuntimeImage
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager, get_manager
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.runtimes.updates.base import RuntimeUpdateProvider
from app.runtimes.updates.docker_provider import DockerUpdateProvider

logger = get_logger(__name__)

# adapter classes carry tested_min_version / tested_max_version
_TESTED_RANGES: dict[str, tuple[str | None, str | None]] = {
    RuntimeType.hermes.value: (HermesAdapter.tested_min_version, HermesAdapter.tested_max_version),
    RuntimeType.openclaw.value: (
        OpenClawAdapter.tested_min_version,
        OpenClawAdapter.tested_max_version,
    ),
}

_ACTIVE_UPDATE_STATES = (
    ImageUpdateStatus.checking.value,
    ImageUpdateStatus.downloading.value,
    ImageUpdateStatus.updating.value,
    ImageUpdateStatus.verifying.value,
)


class RuntimeUpdateError(RuntimeError):
    pass


def _parse_version(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    match = re.search(r"(\d+(?:\.\d+)+)", version)
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def compatibility_for(runtime_type: str, version: str | None) -> str:
    """verified | unverified | unknown for a runtime version."""
    parsed = _parse_version(version)
    if parsed is None:
        return ImageCompatibility.unknown.value
    min_v, max_v = _TESTED_RANGES.get(runtime_type, (None, None))
    if min_v and parsed < _parse_version(min_v):
        return ImageCompatibility.unverified.value
    if max_v and parsed > _parse_version(max_v):
        return ImageCompatibility.unverified.value
    return ImageCompatibility.verified.value


class RuntimeUpdateService:
    def __init__(
        self,
        provider: RuntimeUpdateProvider | None = None,
        manager: DockerRuntimeInstanceManager | None = None,
        docker_available=None,
    ) -> None:
        self._provider = provider or DockerUpdateProvider()
        self._manager = manager or get_manager()
        self._docker_available = docker_available  # callable override for tests
        self._loop_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---- detection ----

    def _known_images(self) -> dict[str, str]:
        return {
            RuntimeType.hermes.value: settings.hermes_image,
            RuntimeType.openclaw.value: settings.openclaw_image,
        }

    def _is_docker_available(self) -> bool:
        if self._docker_available is not None:
            return bool(self._docker_available())
        return self._manager._docker.available()

    async def check_updates(self) -> list[RuntimeImage]:
        """Refresh runtime_images rows from the registry. Never raises."""
        bus.publish("runtime.update_check_started", {"images": list(self._known_images())})
        if not self._is_docker_available():
            with SessionLocal() as db:
                return self._ensure_rows(db)
        with SessionLocal() as db:
            rows = self._ensure_rows(db)
            for row in rows:
                image = f"{row.repository}:{row.tag}"
                row.update_status = ImageUpdateStatus.checking.value
                db.commit()
                try:
                    installed = await asyncio.to_thread(self._provider.get_installed, image)
                    latest_digest = await asyncio.to_thread(self._provider.get_latest_digest, image)
                    if installed:
                        row.digest = installed["digest"]
                        row.installed_version = installed["version"]
                    row.latest_digest = latest_digest
                    row.last_checked_at = datetime.now(UTC)
                    was_available = row.update_available
                    row.update_available = bool(
                        latest_digest and installed and installed["digest"] != latest_digest
                    )
                    row.compatibility_status = compatibility_for(
                        row.runtime_type, row.installed_version
                    )
                    row.update_status = (
                        ImageUpdateStatus.available.value
                        if row.update_available
                        else ImageUpdateStatus.idle.value
                    )
                    db.commit()
                    if row.update_available and not was_available:
                        bus.publish(
                            "runtime.update_available",
                            {"runtime_type": row.runtime_type, "latest_digest": latest_digest},
                        )
                        if settings.update_policy == "automatic":
                            asyncio.create_task(self._guarded_auto_update(row.runtime_type))
                except Exception:
                    logger.exception("update check failed for %s", image)
                    row.update_status = ImageUpdateStatus.failed.value
                    db.commit()
            return rows

    def _ensure_rows(self, db: Session) -> list[RuntimeImage]:
        rows = []
        for runtime_type, image in self._known_images().items():
            repository, _, tag = image.rpartition(":")
            rows.append(
                runtime_repo.upsert_image(
                    db, runtime_type, repository=repository or image, tag=tag or "latest"
                )
            )
        db.commit()
        return rows

    async def _guarded_auto_update(self, runtime_type: str) -> None:
        """Automatic policy: only apply compatible (verified) updates."""
        try:
            with SessionLocal() as db:
                row = runtime_repo.get_image(db, runtime_type)
                if row is None or row.compatibility_status != ImageCompatibility.verified.value:
                    return  # unverified versions are never auto-applied
            await self.managed_update(runtime_type)
        except Exception:
            logger.exception("automatic update failed for %s", runtime_type)

    # ---- managed update ----

    async def managed_update(self, runtime_type: str) -> RuntimeImage:
        """Pull + recreate every docker instance of this runtime type, with
        rollback to the previous image on any failure."""
        async with self._lock:
            if not self._is_docker_available():
                raise RuntimeUpdateError("docker daemon is not available")
            with SessionLocal() as db:
                row = self._ensure_rows(db)
                image_row = next(r for r in row if r.runtime_type == runtime_type)
                if image_row.update_status in _ACTIVE_UPDATE_STATES:
                    raise RuntimeUpdateError(f"update already in progress for {runtime_type}")
                self._set_status(db, image_row, ImageUpdateStatus.downloading.value)
                bus.publish("runtime.update_started", {"runtime_type": runtime_type})

                instances = [
                    i
                    for i in runtime_repo.list_instances_by_runtime_type(db, runtime_type)
                    if i.deployment_mode == DeploymentMode.docker.value
                ]
                # verify all affected employees are idle before touching anything
                from app.models.organization import Employee

                for instance in instances:
                    emp = db.get(Employee, instance.employee_id)
                    busy_session = project_repo.get_running_session_for_employee(
                        db, instance.employee_id
                    )
                    if busy_session or (
                        emp
                        and emp.status
                        not in (EmployeeStatus.idle.value, EmployeeStatus.offline.value)
                    ):
                        self._set_status(db, image_row, ImageUpdateStatus.failed.value)
                        bus.publish(
                            "runtime.update_failed",
                            {"runtime_type": runtime_type, "reason": "employee busy"},
                        )
                        raise RuntimeUpdateError(
                            f"employee {instance.employee_id} is busy; update aborted"
                        )

                image = f"{image_row.repository}:{image_row.tag}"
                previous = {i.id: i.image_digest for i in instances}
                bus.publish("runtime.update_downloading", {"runtime_type": runtime_type})
                if not await asyncio.to_thread(self._provider.pull, image):
                    self._set_status(db, image_row, ImageUpdateStatus.failed.value)
                    bus.publish(
                        "runtime.update_failed",
                        {"runtime_type": runtime_type, "reason": "pull failed"},
                    )
                    raise RuntimeUpdateError(f"failed to pull {image}")

                try:
                    for instance in instances:
                        await asyncio.to_thread(self._manager.backup_data_path, instance)
                        self._set_status(db, image_row, ImageUpdateStatus.updating.value)
                        bus.publish(
                            "runtime.update_restarting",
                            {"runtime_type": runtime_type, "employee_id": instance.employee_id},
                        )
                        await self._manager.recreate_container(db, instance, image=image)
                    self._set_status(db, image_row, ImageUpdateStatus.verifying.value)
                    bus.publish("runtime.update_verifying", {"runtime_type": runtime_type})
                    installed = await asyncio.to_thread(self._provider.get_installed, image)
                except Exception as exc:
                    await self._rollback(db, image_row, instances, previous, str(exc))
                    raise RuntimeUpdateError(f"update failed, rolled back: {exc}") from exc

                image_row.digest = (installed or {}).get("digest") or image_row.digest
                image_row.installed_version = (installed or {}).get("version")
                image_row.update_available = False
                image_row.compatibility_status = compatibility_for(
                    runtime_type, image_row.installed_version
                )
                self._set_status(db, image_row, ImageUpdateStatus.completed.value)
                bus.publish("runtime.update_completed", {"runtime_type": runtime_type})
                db.refresh(image_row)
                return image_row

    async def _rollback(
        self,
        db: Session,
        image_row: RuntimeImage,
        instances,
        previous: dict[int, str | None],
        reason: str,
    ) -> None:
        logger.error("rolling back %s update: %s", image_row.runtime_type, reason)
        for instance in instances:
            old_image = previous.get(instance.id)
            if not old_image:
                continue
            try:
                await self._manager.recreate_container(db, instance, image=old_image)
            except Exception:
                logger.exception("rollback recreate failed for instance %s", instance.id)
        self._set_status(db, image_row, ImageUpdateStatus.rolled_back.value)
        bus.publish(
            "runtime.update_rolled_back",
            {"runtime_type": image_row.runtime_type, "reason": reason[:300]},
        )

    @staticmethod
    def _set_status(db: Session, row: RuntimeImage, status: str) -> None:
        row.update_status = status
        row.last_checked_at = datetime.now(UTC)
        db.commit()

    # ---- background loop ----

    async def start_update_loop(self) -> None:
        if not settings.update_check_enabled:
            return
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._update_loop(), name="runtime-update-check")

    async def stop_update_loop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None

    async def _update_loop(self) -> None:
        while True:
            try:
                await self.check_updates()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("update check sweep failed")
            await asyncio.sleep(max(settings.update_check_interval, 60))


_update_service: RuntimeUpdateService | None = None


def get_update_service() -> RuntimeUpdateService:
    global _update_service
    if _update_service is None:
        _update_service = RuntimeUpdateService()
    return _update_service
