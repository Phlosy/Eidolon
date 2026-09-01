"""Thin wrapper over docker-py (v0.2).

Hard safety rules for runtime containers:
- never privileged, never host PID/network mode;
- never mount ``/`` or ``/var/run/docker.sock`` into a runtime container;
- no host port publishing — the backend reaches containers over the private
  ``eidolon-runtime-net`` docker network via container-name DNS.

Every method degrades gracefully when the Docker daemon is unavailable: the
process must keep booting and runtime types simply report docker_available=false.
All methods are synchronous (docker-py is sync); async callers should use
``asyncio.to_thread``.
"""

from collections.abc import Iterator
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound

from app.core.logging import get_logger
from app.core.redaction import redact

logger = get_logger(__name__)

FORBIDDEN_BIND_SOURCES = ("/", "/var/run/docker.sock", "/etc", "/root")


class DockerUnavailableError(RuntimeError):
    """Raised when the Docker daemon cannot be reached."""


class DockerService:
    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None
        self._available: bool | None = None

    # ---- availability ----

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
            except DockerException as exc:
                raise DockerUnavailableError(str(exc)) from exc
        return self._client

    def available(self) -> bool:
        if self._available is None:
            self._available = self.ping()
        return self._available

    def ping(self) -> bool:
        try:
            ok = bool(self._get_client().ping())
        except Exception:
            ok = False
        self._available = ok
        if ok and self._client is None:
            self._client = docker.from_env()
        return ok

    # ---- networks ----

    def ensure_network(self, name: str) -> str | None:
        try:
            client = self._get_client()
            try:
                return client.networks.get(name).id
            except NotFound:
                return client.networks.create(name, driver="bridge").id
        except DockerException as exc:
            logger.warning("ensure_network(%s) failed: %s", name, exc)
            return None

    # ---- images ----

    def pull_image(self, image: str) -> dict[str, Any] | None:
        try:
            pulled = self._get_client().images.pull(image)
            return {"id": pulled.id, "tags": list(pulled.tags)}
        except DockerException as exc:
            logger.warning("pull_image(%s) failed: %s", image, exc)
            return None

    def inspect_image(self, image: str) -> dict[str, Any] | None:
        try:
            img = self._get_client().images.get(image)
            return dict(img.attrs)
        except ImageNotFound:
            return None
        except DockerException as exc:
            logger.warning("inspect_image(%s) failed: %s", image, exc)
            return None

    def get_registry_digest(self, image: str) -> str | None:
        """Remote digest without pulling (registry HEAD via distribution inspect)."""
        try:
            data = self._get_client().images.get_registry_data(image)
            return data.id
        except DockerException as exc:
            logger.warning("get_registry_digest(%s) failed: %s", image, exc)
            return None

    # ---- containers ----

    def create_container(
        self,
        *,
        name: str,
        image: str,
        environment: dict[str, str] | None = None,
        volumes: dict[str, dict[str, str]] | None = None,
        network: str | None = None,
        cpu_limit: float | None = None,
        memory_limit_mb: int | None = None,
        restart_policy: str = "unless-stopped",
        labels: dict[str, str] | None = None,
        command: str | list[str] | None = None,
    ) -> str | None:
        """Create (not start) a hardened runtime container. Returns container id."""
        self._validate_volumes(volumes or {})
        kwargs: dict[str, Any] = {
            "name": name,
            "image": image,
            "environment": environment or {},
            "volumes": volumes or {},
            "detach": True,
            "privileged": False,
            "network_mode": None,
            "restart_policy": {"Name": restart_policy},
            "labels": {"eidolon.managed": "true", **(labels or {})},
        }
        if command is not None:
            kwargs["command"] = command
        if cpu_limit:
            kwargs["nano_cpus"] = int(cpu_limit * 1e9)
        if memory_limit_mb:
            kwargs["mem_limit"] = f"{memory_limit_mb}m"
        try:
            container = self._get_client().containers.create(**kwargs)
            if network:
                try:
                    self._get_client().networks.get(network).connect(container.id)
                except DockerException as exc:
                    logger.warning("connect to network %s failed: %s", network, exc)
            return container.id
        except DockerException as exc:
            logger.warning("create_container(%s) failed: %s", name, exc)
            return None

    def start_container(self, container_id: str) -> bool:
        return self._op(container_id, "start")

    def stop_container(self, container_id: str, timeout: int = 10) -> bool:
        try:
            self._get_client().containers.get(container_id).stop(timeout=timeout)
            return True
        except DockerException as exc:
            logger.warning("stop_container(%s) failed: %s", container_id[:12], exc)
            return False

    def restart_container(self, container_id: str, timeout: int = 10) -> bool:
        try:
            self._get_client().containers.get(container_id).restart(timeout=timeout)
            return True
        except DockerException as exc:
            logger.warning("restart_container(%s) failed: %s", container_id[:12], exc)
            return False

    def remove_container(self, container_id: str, force: bool = True) -> bool:
        try:
            self._get_client().containers.get(container_id).remove(force=force)
            return True
        except NotFound:
            return True
        except DockerException as exc:
            logger.warning("remove_container(%s) failed: %s", container_id[:12], exc)
            return False

    def inspect_container(self, container_id: str) -> dict[str, Any] | None:
        try:
            container = self._get_client().containers.get(container_id)
            container.reload()
            return dict(container.attrs)
        except NotFound:
            return None
        except DockerException as exc:
            logger.warning("inspect_container(%s) failed: %s", container_id[:12], exc)
            return None

    def healthcheck_container(self, container_id: str) -> tuple[str, str] | None:
        """Return (state_status, health_status) from container attrs.

        state_status: created|running|exited|dead|... ; health_status:
        healthy|unhealthy|starting|none (none = image has no HEALTHCHECK).
        """
        attrs = self.inspect_container(container_id)
        if attrs is None:
            return None
        state = attrs.get("State", {})
        status = state.get("Status", "unknown")
        health = (state.get("Health") or {}).get("Status", "none")
        return status, health

    def get_logs(self, container_id: str, tail: int = 200) -> list[str]:
        """Last ``tail`` log lines, redacted."""
        try:
            raw = (
                self._get_client()
                .containers.get(container_id)
                .logs(tail=tail, stdout=True, stderr=True)
            )
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            return [redact(line) for line in text.splitlines()]
        except DockerException as exc:
            logger.warning("get_logs(%s) failed: %s", container_id[:12], exc)
            return []

    def stream_logs(self, container_id: str, follow: bool = False) -> Iterator[str]:
        """Yield redacted log lines (blocking generator — offload to a thread)."""
        try:
            stream = (
                self._get_client()
                .containers.get(container_id)
                .logs(stream=True, follow=follow, stdout=True, stderr=True)
            )
            for chunk in stream:
                line = (
                    chunk.decode("utf-8", errors="replace")
                    if isinstance(chunk, bytes)
                    else str(chunk)
                )
                yield redact(line.rstrip("\n"))
        except DockerException as exc:
            logger.warning("stream_logs(%s) failed: %s", container_id[:12], exc)

    def exec_run(self, container_id: str, cmd: list[str]) -> tuple[int, str]:
        try:
            exit_code, output = self._get_client().containers.get(container_id).exec_run(cmd)
            text = (
                output.decode("utf-8", errors="replace")
                if isinstance(output, bytes)
                else str(output)
            )
            return exit_code, redact(text)
        except DockerException as exc:
            logger.warning("exec_run(%s) failed: %s", container_id[:12], exc)
            return 1, ""

    # ---- helpers ----

    def _op(self, container_id: str, op: str) -> bool:
        try:
            getattr(self._get_client().containers.get(container_id), op)()
            return True
        except DockerException as exc:
            logger.warning("%s(%s) failed: %s", op, container_id[:12], exc)
            return False

    @staticmethod
    def _validate_volumes(volumes: dict[str, dict[str, str]]) -> None:
        for source in volumes:
            normalized = source.rstrip("/") or "/"
            if normalized in FORBIDDEN_BIND_SOURCES:
                raise ValueError(f"forbidden bind-mount source: {source}")


_service: DockerService | None = None


def get_docker_service() -> DockerService:
    global _service
    if _service is None:
        _service = DockerService()
    return _service
