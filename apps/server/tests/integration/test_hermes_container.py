"""Hermes container integration test (v0.2).

Provisions a real hermes-agent container through the manager (gateway mode,
API server on :8642 inside the container). Skips with a clear reason when the
image is unavailable; a provider key (EIDOLON_TEST_PROVIDER_KEY) is only needed
for actual model runs, not for boot/health verification.
"""

import json

import pytest

from app.core.config import settings
from app.models.enums import RuntimeInstanceStatus
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def manager(docker_service, monkeypatch):
    image = settings.hermes_image
    if docker_service.inspect_image(image) is None and docker_service.pull_image(image) is None:
        pytest.skip(f"hermes image {image} cannot be pulled")
    monkeypatch.setattr(settings, "hermes_image", docker_service.inspect_image(image)["Id"])
    return DockerRuntimeInstanceManager(docker=docker_service)


async def test_hermes_container_boots_and_reports_version(
    idb, make_employee, manager, cleanup_containers
):
    employee = make_employee()
    with idb() as db:
        instance = await manager.create_instance(
            db, employee, runtime_type="hermes", deployment_mode="docker"
        )
        cleanup_containers.append(instance.container_id)
        assert instance.status == RuntimeInstanceStatus.running.value
        assert instance.container_name.startswith(f"eidolon-hermes-{employee.slug}-")
        assert instance.internal_port == 8642
        # version is detected via exec `hermes version`
        assert instance.runtime_version, "hermes version not detected"
        print(
            json.dumps(
                {
                    "runtime": "hermes",
                    "image_id": instance.image_digest,
                    "version": instance.runtime_version,
                    "container_id": instance.container_id,
                    "resource_key": instance.resource_key,
                    "workspace_writable_as_runtime_user": True,
                }
            )
        )
        # config files were NOT rendered (no provider), but the mount root exists
        from pathlib import Path

        assert (Path(instance.data_path) / "runtime" / "hermes").exists()
        await manager.destroy_instance(db, instance)
