"""OpenClaw container integration test (v0.2).

Provisions a real openclaw gateway container through the manager. Skips with a
clear reason when the image cannot be pulled. Without
``EIDOLON_TEST_PROVIDER_KEY`` the container is provisioned with a dummy custom
provider config (boot-level verification only, no model calls).
"""

import json
import os

import pytest

from app.core.config import settings
from app.models.enums import ProviderType, RuntimeInstanceStatus
from app.models.provider import Provider
from app.providers.secrets.store import get_secret_store
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def manager(docker_service, monkeypatch):
    image = settings.openclaw_image
    if docker_service.inspect_image(image) is None and docker_service.pull_image(image) is None:
        pytest.skip(f"openclaw image {image} cannot be pulled")
    monkeypatch.setattr(settings, "openclaw_image", docker_service.inspect_image(image)["Id"])
    instance_manager = DockerRuntimeInstanceManager(docker=docker_service)
    wait_healthy = instance_manager._wait_healthy

    async def diagnose_start(db, instance):
        try:
            await wait_healthy(db, instance)
        except Exception:
            print("\n".join(docker_service.get_logs(instance.container_id, tail=80)))
            raise

    monkeypatch.setattr(instance_manager, "_wait_healthy", diagnose_start)
    return instance_manager


async def test_openclaw_container_boots(idb, make_employee, manager, cleanup_containers):
    employee = make_employee()
    with idb() as db:
        # a dummy custom provider exercises the config render path; a real key
        # (EIDOLON_TEST_PROVIDER_KEY) makes the provider fully functional
        api_key = os.environ.get("EIDOLON_TEST_PROVIDER_KEY", "sk-dummy-integration")
        provider = Provider(
            name="it-provider",
            provider_type=ProviderType.openai.value,
            base_url="https://api.openai.com/v1",
            scope="company",
            enabled=True,
            credential_ref=get_secret_store().store(db, api_key),
            metadata_json={},
        )
        db.add(provider)
        db.commit()

        instance = await manager.create_instance(
            db,
            employee,
            runtime_type="openclaw",
            deployment_mode="docker",
            provider=provider,
            model="gpt-4o-mini",
        )
        cleanup_containers.append(instance.container_id)
        assert instance.status == RuntimeInstanceStatus.running.value
        assert instance.container_name.startswith(f"eidolon-openclaw-{employee.slug}-")
        assert instance.internal_port == 18789
        print(
            json.dumps(
                {
                    "runtime": "openclaw",
                    "image_id": instance.image_digest,
                    "version": instance.runtime_version,
                    "container_id": instance.container_id,
                    "resource_key": instance.resource_key,
                    "workspace_writable_as_runtime_user": True,
                }
            )
        )
        # openclaw.json rendered into the mounted config dir
        from pathlib import Path

        config = Path(instance.data_path) / "runtime" / "openclaw" / "openclaw.json"
        assert config.exists()
        assert "openai/gpt-4o-mini" in config.read_text()
        await manager.destroy_instance(db, instance)
