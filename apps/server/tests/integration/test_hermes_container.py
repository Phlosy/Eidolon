"""Hermes container integration test (v0.2).

Provisions a real hermes-agent container through the manager (gateway mode,
API server on :8642 inside the container). Skips with a clear reason when the
image is unavailable; a provider key (EIDOLON_TEST_PROVIDER_KEY) is only needed
for actual model runs, not for boot/health verification.
"""

import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.models.enums import RuntimeInstanceStatus
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.probe import probe_control

pytestmark = pytest.mark.integration


@pytest.fixture()
def manager(docker_service, monkeypatch):
    image = settings.hermes_image
    info = docker_service.inspect_image(image)
    if info is None:
        if docker_service.pull_image(image) is None:
            pytest.skip(f"hermes image {image} cannot be pulled")
        info = docker_service.inspect_image(image)
    monkeypatch.setattr(settings, "hermes_image", info["Id"])
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
        assert instance.internal_host == "127.0.0.1"
        assert 26900 <= instance.internal_port <= 26989
        # version is detected via exec `hermes version`
        assert instance.runtime_version, "hermes version not detected"
        base_url = f"http://{instance.internal_host}:{instance.internal_port}"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            try:
                async with asyncio.timeout(45):
                    while True:
                        try:
                            health = await client.get(f"{base_url}/health")
                            if health.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.5)
            except TimeoutError:
                print("\n".join(manager._docker.get_logs(instance.container_id, tail=100)))
                raise
            assert health.status_code == 200
            token = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
            capabilities = await client.get(
                f"{base_url}/v1/capabilities", headers={"Authorization": f"Bearer {token}"}
            )
            assert capabilities.status_code == 200
            assert capabilities.json()["features"]["run_submission"] is True
            rejected = await client.get(
                f"{base_url}/v1/capabilities", headers={"Authorization": "Bearer invalid-token"}
            )
            assert rejected.status_code in {401, 403}
        facts = await probe_control("hermes", base_url, token)
        assert all(f["state"] == "passed" for f in facts.values()), facts
        print(
            json.dumps(
                {
                    "runtime": "hermes",
                    "image_id": instance.image_digest,
                    "version": instance.runtime_version,
                    "container_id": instance.container_id,
                    "resource_key": instance.resource_key,
                    "workspace_writable_as_runtime_user": True,
                    "authenticated_control": True,
                    "required_capabilities_present": True,
                }
            )
        )
        # config files were NOT rendered (no provider), but the mount root exists
        from pathlib import Path

        assert (Path(instance.data_path) / "runtime" / "hermes").exists()
        await manager.destroy_instance(db, instance)


async def test_hermes_real_minimal_model_call(manager, create_model_runtime, record_model_evidence):
    runtime = await create_model_runtime("hermes", manager)
    async with asyncio.timeout(60), httpx.AsyncClient(timeout=50, trust_env=False) as client:
        response = await client.post(
            f"{runtime.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {runtime.token}"},
            json={
                "model": "hermes",
                "messages": [{"role": "user", "content": "Reply only OK. Do not use any tools."}],
                "stream": False,
                "max_tokens": 32,
            },
        )
        assert response.status_code == 200
        content = response.json()["choices"][0]["message"]["content"]
        record_model_evidence(runtime, output_matches=content.strip() in {"OK", "OK."})
