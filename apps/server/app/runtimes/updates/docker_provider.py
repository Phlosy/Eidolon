"""Docker-backed RuntimeUpdateProvider (v0.2).

Installed state comes from local image inspect (RepoDigests + OCI version
label); latest state from the registry digest (no pull needed for detection).
"""

from app.runtimes.docker.service import DockerService, get_docker_service
from app.runtimes.updates.base import RuntimeUpdateProvider

_VERSION_LABEL = "org.opencontainers.image.version"


class DockerUpdateProvider(RuntimeUpdateProvider):
    def __init__(self, docker: DockerService | None = None) -> None:
        self._docker = docker or get_docker_service()

    def get_installed(self, image: str) -> dict | None:
        attrs = self._docker.inspect_image(image)
        if attrs is None:
            return None
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        repo_digests = attrs.get("RepoDigests") or []
        return {
            "digest": repo_digests[0].split("@", 1)[-1] if repo_digests else attrs.get("Id"),
            "version": labels.get(_VERSION_LABEL),
        }

    def get_latest_digest(self, image: str) -> str | None:
        digest = self._docker.get_registry_digest(image)
        return digest.split("@", 1)[-1] if digest and "@" in digest else digest

    def pull(self, image: str) -> bool:
        return self._docker.pull_image(image) is not None
