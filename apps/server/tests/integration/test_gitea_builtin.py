"""Integration: install the optional builtin Gitea against a real Docker daemon.

Run via `make test-integration` (pytest marker ``integration``; excluded from
`make test`). Pulls ``gitea/gitea:1`` — if the registry/network is unavailable
the test SKIPs with a clear reason. Cleans up the container afterwards.
"""

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.services.git import GITEA_CONTAINER_NAME, GITEA_HTTP_URL, GitService

pytestmark = pytest.mark.integration


def _remove_leftover(docker_service) -> None:
    attrs = docker_service.inspect_container(GITEA_CONTAINER_NAME)
    if attrs:
        docker_service.remove_container(attrs["Id"])


def test_gitea_builtin_install(docker_service, idb, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "data"))
    if docker_service.pull_image(settings.gitea_image) is None:
        pytest.skip(f"could not pull {settings.gitea_image} (registry/network unavailable)")

    _remove_leftover(docker_service)  # clean slate: install must create the container
    service = GitService(docker=docker_service)
    try:
        asyncio.run(service.install_builtin())
        status = service.builtin_status()
        assert status.status == "running", f"install failed: {service._last_error}"
        assert status.url == GITEA_HTTP_URL
        assert status.version is not None

        # the container actually answers its API over the loopback binding
        response = httpx.get(f"{GITEA_HTTP_URL}/api/v1/version", timeout=10)
        assert response.status_code == 200
        assert response.json().get("version")

        asyncio.run(service.stop_builtin())
        assert service.builtin_status().status == "stopped"
    finally:
        _remove_leftover(docker_service)
