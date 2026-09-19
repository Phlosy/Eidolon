"""OpenClaw container integration test (v0.2).

Provisions a real openclaw gateway container through the manager. Skips with a
clear reason when the image cannot be pulled. Without
Boot verification always uses a dummy key. A separate, explicitly gated test
uses the one-request model budget relay.
"""

import asyncio
import json
import uuid

import pytest

from app.core.config import settings
from app.models.enums import ProviderType, RuntimeInstanceStatus
from app.models.provider import Provider
from app.providers.secrets.store import get_secret_store
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.openclaw.pairing import pair_controller
from app.runtimes.openclaw.rpc import GatewayClient
from app.runtimes.probe import probe_control

pytestmark = pytest.mark.integration


@pytest.fixture()
def manager(docker_service, monkeypatch):
    image = settings.openclaw_image
    info = docker_service.inspect_image(image)
    if info is None:
        if docker_service.pull_image(image) is None:
            pytest.skip(f"openclaw image {image} cannot be pulled")
        info = docker_service.inspect_image(image)
    monkeypatch.setattr(settings, "openclaw_image", info["Id"])
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
        api_key = "sk-dummy-integration"
        provider = Provider(
            company_id=employee.company_id,
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
        assert instance.internal_host == "127.0.0.1"
        assert 26900 <= instance.internal_port <= 26989
        token = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
        device_key = manager._secrets.retrieve(db, instance.metadata_json["device_key_ref"])
        await pair_controller(manager._docker, instance, token, device_key)
        async with GatewayClient(
            f"ws://{instance.internal_host}:{instance.internal_port}", token, device_key=device_key
        ) as control:
            health = await control.call("health", {})
            assert health["ok"] is True
        base_url = f"http://{instance.internal_host}:{instance.internal_port}"
        facts = await probe_control("openclaw", base_url, token, device_key=device_key)
        assert all(f["state"] == "passed" for f in facts.values()), facts
        rejected = await probe_control("openclaw", base_url, "invalid-token", device_key=device_key)
        assert rejected["control"]["reason"] == "authentication_rejected"
        print(
            json.dumps(
                {
                    "runtime": "openclaw",
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
        # openclaw.json rendered into the mounted config dir
        from pathlib import Path

        config = Path(instance.data_path) / "runtime" / "openclaw" / "openclaw.json"
        assert config.exists()
        assert "openai/gpt-4o-mini" in config.read_text()
        await manager.destroy_instance(db, instance)


async def test_openclaw_real_minimal_model_call(
    manager, create_model_runtime, record_model_evidence
):
    runtime = await create_model_runtime("openclaw", manager)
    session_key = "agent:main:eidolon-probe-" + uuid.uuid4().hex
    async with (
        asyncio.timeout(60),
        GatewayClient(
            runtime.base_url.replace("http://", "ws://"), runtime.token, device_key=runtime.device
        ) as control,
    ):
        started = await control.call(
            "chat.send",
            {
                "sessionKey": session_key,
                "message": "Reply only OK. Do not use any tools.",
                "idempotencyKey": uuid.uuid4().hex,
                "thinking": "off",
                "timeoutMs": 45000,
            },
        )
        assert started.get("runId")
        terminal = await control.call(
            "agent.wait", {"runId": started["runId"], "timeoutMs": 45000}, timeout=50
        )
        assert terminal.get("status") == "ok", terminal
        history = await control.call("chat.history", {"sessionKey": session_key, "limit": 10})
        texts = [
            block.get("text", "")
            for message in history.get("messages", [])
            if message.get("role") == "assistant"
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        record_model_evidence(
            runtime, output_matches=any(text.strip() in {"OK", "OK."} for text in texts)
        )
