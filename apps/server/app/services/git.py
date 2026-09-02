"""Git integration service (v0.3 phase 2).

Two concerns:

1. **GitConnection CRUD** — external self-hosted platforms (GitLab / Gitea /
   GitHub Enterprise / custom). Eidolon only *connects*; it never installs or
   manages external platforms. Tokens go to the SecretStore, responses only
   ever carry a mask.

2. **Builtin Gitea** — optional, manually triggered one-click install. The
   DockerService manages a container named ``eidolon-gitea`` (image from
   ``EIDOLON_GITEA_IMAGE``), persistent dir ``data/gitea`` → ``/data``, with
   loopback-only port publishing (127.0.0.1:26990 → 3000 HTTP,
   127.0.0.1:26922 → 22 SSH) so the host-dev backend can reach it, plus
   membership in the runtime network for container-mode deployments.

Status is derived from Docker on every read (the container is the source of
truth); ``installing`` and ``error`` are tracked in-process.
"""

import asyncio
import time
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redaction import mask_secret, redact
from app.events.bus import bus
from app.git import probe
from app.models.enums import GitBuiltinStatus
from app.models.git import GitConnection
from app.providers.secrets.store import LocalEncryptedSecretStore, get_secret_store
from app.repositories import git as git_repo
from app.runtimes.docker.service import DockerService, get_docker_service
from app.schemas.git import (
    GitBuiltinOut,
    GitConnectionCreate,
    GitConnectionOut,
    GitConnectionPatch,
    GitConnectionTestOut,
)

logger = get_logger(__name__)

GITEA_CONTAINER_NAME = "eidolon-gitea"
GITEA_HTTP_PORT = 26990  # 127.0.0.1:26990 → container :3000 (docs/ports.md)
GITEA_SSH_PORT = 26922  # 127.0.0.1:26922 → container :22
GITEA_HTTP_URL = f"http://127.0.0.1:{GITEA_HTTP_PORT}"
_INSTALL_READY_TIMEOUT = 120.0  # seconds polling /api/v1/version after start


def connection_out(connection: GitConnection) -> GitConnectionOut:
    metadata = dict(connection.metadata_json or {})
    return GitConnectionOut(
        id=connection.id,
        name=connection.name,
        platform_type=connection.platform_type,
        base_url=connection.base_url,
        has_credential=connection.credential_ref is not None,
        credential_mask=metadata.get("credential_mask"),
        enabled=connection.enabled,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


class GitService:
    def __init__(
        self,
        docker: DockerService | None = None,
        secrets: LocalEncryptedSecretStore | None = None,
    ) -> None:
        self._docker = docker or get_docker_service()
        self._secrets = secrets or get_secret_store()
        self._install_task: asyncio.Task | None = None
        self._version: str | None = None  # recorded after a successful install
        self._last_error: str | None = None  # last install failure, redacted

    # ---- connections ----

    def list_connections(self, db: Session) -> list[GitConnectionOut]:
        return [connection_out(c) for c in git_repo.list_connections(db)]

    def create_connection(self, db: Session, payload: GitConnectionCreate) -> GitConnectionOut:
        credential_ref = None
        mask = None
        if payload.token:
            credential_ref = self._secrets.store(db, payload.token)
            mask = mask_secret(payload.token)
        connection = git_repo.create_connection(
            db,
            name=payload.name,
            platform_type=payload.platform_type.value,
            base_url=payload.base_url,
            enabled=payload.enabled,
            credential_ref=credential_ref,
            metadata_json={"credential_mask": mask} if mask else {},
        )
        db.commit()
        bus.publish(
            "git.connection_created",
            {
                "id": connection.id,
                "name": connection.name,
                "platform_type": connection.platform_type,
            },
        )
        return connection_out(connection)

    def update_connection(
        self, db: Session, connection_id: int, payload: GitConnectionPatch
    ) -> GitConnectionOut:
        connection = git_repo.get_connection(db, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="git connection not found")
        data = payload.model_dump(exclude_unset=True)
        token = data.pop("token", None)
        for field, value in data.items():
            setattr(connection, field, value.value if hasattr(value, "value") else value)
        if token:
            # rotate: replace the stored credential, drop the old ciphertext
            self._secrets.delete(db, connection.credential_ref)
            connection.credential_ref = self._secrets.store(db, token)
            metadata = dict(connection.metadata_json or {})
            metadata["credential_mask"] = mask_secret(token)
            connection.metadata_json = metadata
        db.commit()
        bus.publish("git.connection_updated", {"id": connection.id, "name": connection.name})
        return connection_out(connection)

    def delete_connection(self, db: Session, connection_id: int) -> None:
        connection = git_repo.get_connection(db, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="git connection not found")
        self._secrets.delete(db, connection.credential_ref)
        git_repo.delete_connection(db, connection)
        db.commit()
        bus.publish("git.connection_deleted", {"id": connection_id, "name": connection.name})

    async def test_connection(self, db: Session, connection_id: int) -> GitConnectionTestOut:
        connection = git_repo.get_connection(db, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="git connection not found")
        token = self._secrets.retrieve(db, connection.credential_ref)
        result = await probe.test_connection(connection.platform_type, connection.base_url, token)
        bus.publish(
            "git.connection_tested",
            {
                "id": connection.id,
                "name": connection.name,
                "ok": result["ok"],
                "error": result["error"],
            },
        )
        return GitConnectionTestOut(**result)

    # ---- builtin gitea ----

    def docker_available(self) -> bool:
        return self._docker.ping()

    def _container_attrs(self) -> dict | None:
        if not self._docker.available():
            return None
        return self._docker.inspect_container(GITEA_CONTAINER_NAME)

    def builtin_status(self) -> GitBuiltinOut:
        docker_available = self._docker.ping()
        if self._install_task is not None and not self._install_task.done():
            return GitBuiltinOut(
                docker_available=docker_available,
                status=GitBuiltinStatus.installing.value,
                url=None,
                version=None,
            )
        attrs = self._container_attrs()
        if attrs is None:
            status = (
                GitBuiltinStatus.error.value
                if self._last_error and docker_available
                else GitBuiltinStatus.not_installed.value
            )
            return GitBuiltinOut(
                docker_available=docker_available, status=status, url=None, version=None
            )
        state = (attrs.get("State") or {}).get("Status", "unknown")
        if state == "running":
            status = GitBuiltinStatus.running.value
        elif state in ("created", "exited", "paused"):
            status = GitBuiltinStatus.stopped.value
        else:
            status = GitBuiltinStatus.error.value
        return GitBuiltinOut(
            docker_available=docker_available,
            status=status,
            url=GITEA_HTTP_URL if state == "running" else None,
            version=self._version if state == "running" else None,
        )

    def request_install(self) -> GitBuiltinOut:
        """Kick off the background install. Caller checks docker first (409)."""
        current = self.builtin_status()
        if current.status in (GitBuiltinStatus.installing.value, GitBuiltinStatus.running.value):
            return current
        self._install_task = asyncio.create_task(self.install_builtin(), name="gitea-install")
        return GitBuiltinOut(
            docker_available=current.docker_available,
            status=GitBuiltinStatus.installing.value,
            url=None,
            version=None,
        )

    async def install_builtin(self) -> None:
        """Pull → create → start → wait for /api/v1/version. Never raises;
        failures set status=error and publish git.builtin_failed."""
        self._last_error = None
        self._version = None
        bus.publish("git.builtin_install_started", {"image": settings.gitea_image})
        try:
            await asyncio.to_thread(self._docker.ensure_network, settings.runtime_network)
            if await asyncio.to_thread(self._docker.pull_image, settings.gitea_image) is None:
                raise RuntimeError(f"failed to pull image {settings.gitea_image}")
            data_dir = Path(settings.data_root).resolve() / "gitea"
            data_dir.mkdir(parents=True, exist_ok=True)
            container_id = self._container_id_from_attrs()
            if container_id is None:
                container_id = await asyncio.to_thread(
                    self._docker.create_container,
                    name=GITEA_CONTAINER_NAME,
                    image=settings.gitea_image,
                    environment={
                        # skip the first-run install wizard so the API is up immediately
                        "GITEA__security__INSTALL_LOCK": "true",
                    },
                    volumes={str(data_dir): {"bind": "/data", "mode": "rw"}},
                    ports={
                        "3000/tcp": ("127.0.0.1", GITEA_HTTP_PORT),
                        "22/tcp": ("127.0.0.1", GITEA_SSH_PORT),
                    },
                    network=settings.runtime_network,
                    labels={"eidolon.builtin": "gitea"},
                )
                if container_id is None:
                    raise RuntimeError("failed to create gitea container")
            if not await asyncio.to_thread(self._docker.start_container, container_id):
                raise RuntimeError("failed to start gitea container")
            self._version = await self._wait_ready(_INSTALL_READY_TIMEOUT)
            bus.publish(
                "git.builtin_installed",
                {"version": self._version, "url": GITEA_HTTP_URL},
            )
            logger.info("builtin gitea installed (version=%s)", self._version)
        except Exception as exc:
            self._last_error = redact(str(exc)[:300])
            logger.warning("builtin gitea install failed: %s", self._last_error)
            bus.publish("git.builtin_failed", {"reason": self._last_error})

    async def start_builtin(self) -> GitBuiltinOut:
        if not self.docker_available():
            raise HTTPException(status_code=409, detail="docker daemon is not available")
        container_id = self._container_id_from_attrs()
        if container_id is None:
            raise HTTPException(status_code=409, detail="builtin gitea is not installed")
        if not await asyncio.to_thread(self._docker.start_container, container_id):
            raise HTTPException(status_code=409, detail="failed to start builtin gitea")
        bus.publish("git.builtin_started", {})
        return self.builtin_status()

    async def stop_builtin(self) -> GitBuiltinOut:
        if not self.docker_available():
            raise HTTPException(status_code=409, detail="docker daemon is not available")
        container_id = self._container_id_from_attrs()
        if container_id is None:
            raise HTTPException(status_code=409, detail="builtin gitea is not installed")
        if not await asyncio.to_thread(self._docker.stop_container, container_id):
            raise HTTPException(status_code=409, detail="failed to stop builtin gitea")
        bus.publish("git.builtin_stopped", {})
        return self.builtin_status()

    # ---- helpers ----

    def _container_id_from_attrs(self) -> str | None:
        attrs = self._container_attrs()
        return attrs.get("Id") if attrs else None

    async def _wait_ready(self, timeout: float) -> str | None:
        """Poll the gitea version endpoint until ready; returns the version."""
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"{GITEA_HTTP_URL}/api/v1/version")
                    if response.status_code == 200:
                        return (response.json() or {}).get("version")
                except Exception:
                    pass
                await asyncio.sleep(2.0)
        raise RuntimeError(f"gitea did not become ready within {int(timeout)}s")


git_service = GitService()
