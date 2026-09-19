"""Fixed-image, budgeted real responses; protocol proof precedes business bridge proof."""

import json
import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from app.runtimes.base import RuntimeInstance, TaskContext
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.openclaw.adapter import OpenClawAdapter

pytestmark = pytest.mark.integration

FIXED_IMAGES = {
    "hermes": "sha256:33a938c54ec8f58cd3128901b378c226b2846fde5f217533bdae2b1501bd26e5",
    "openclaw": "sha256:e7849cb6c1ef1ead39ab4be7d85edb2df89611f486e283284c7cf35ce39a20d4",
}


@pytest.fixture()
def manager(docker_service, monkeypatch, runtime_type):
    image = FIXED_IMAGES[runtime_type]
    if docker_service.inspect_image(image) is None:
        pytest.skip(f"required fixed {runtime_type} image is unavailable: {image}")
    monkeypatch.setattr(settings, f"{runtime_type}_image", image)
    return DockerRuntimeInstanceManager(docker=docker_service)


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
@pytest.mark.parametrize("model_relay", [{"max_requests": 2, "output_limit": 128}], indirect=True)
async def test_fixed_image_complete_response_and_conversation_continuation(
    runtime_type, manager, create_model_runtime, model_relay
):
    runtime = await create_model_runtime(runtime_type, manager)
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type]()
    connection = RuntimeInstance(
        employee_id=runtime.instance.employee_id,
        profile="real-protocol-probe",
        home_path=runtime.instance.workspace_path,
        details={
            "base_url": runtime.base_url,
            "api_key": runtime.token,
            "device_key": runtime.device,
        },
    )
    # Transport characterization has no business WorkSession or business mutation.
    current = await adapter.create_session(
        connection, TaskContext(task_id=0, project_id=0, title="protocol", kind="general")
    )
    nonce = uuid.uuid4().hex[:12]
    first = await adapter.complete_turn(
        current,
        "Protocol check. Do not use tools. Return only a JSON object with exactly "
        f'these fields: {{"kind":"read","nonce":"{nonce}"}}. Remember the nonce for the next turn.',
        request_id="protocol-first",
        timeout=50,
    )
    assert json.loads(first.text) == {"kind": "read", "nonce": nonce}
    second = await adapter.complete_turn(
        current,
        'The read receipt is {"ok":true,"fact":"RECEIPT_ACCEPTED"}. Return only a JSON object '
        'with exactly kind="finish", nonce=the nonce from the previous turn, '
        "receipt=the fact in this receipt. Do not use tools.",
        request_id="protocol-second",
        timeout=50,
    )
    assert json.loads(second.text) == {
        "kind": "finish",
        "nonce": nonce,
        "receipt": "RECEIPT_ACCEPTED",
    }
    assert first.session_id == second.session_id == current.id
    assert first.run_id != second.run_id
    receipt = json.loads(model_relay.receipt.read_text())
    assert receipt["attempts"] == 2
    assert receipt["cost_usd"] is not None
    assert Decimal(receipt["cost_usd"]) <= Decimal(receipt["max_cost_usd"])
    print(
        "REAL_PROTOCOL_EVIDENCE="
        + json.dumps(
            {
                "runtime": runtime_type,
                "image_id": runtime.instance.image_digest,
                "version": runtime.instance.runtime_version,
                "session_id": current.id,
                "run_ids": [first.run_id, second.run_id],
                "responses": [json.loads(first.text), json.loads(second.text)],
                "model_receipt": receipt,
            },
            sort_keys=True,
        )
    )
