"""Pair only Eidolon's persisted controller, using the owned container's local CLI."""

import json
import re

from app.runtimes.manager.ensure import blocking

from .identity import device_id
from .rpc import CONTROLLER_SCOPES, GatewayClient, GatewayError


async def pair_controller(docker, instance, token: str, device_key: str) -> None:
    url = f"ws://{instance.internal_host}:{instance.internal_port}"
    try:
        async with GatewayClient(url, token, device_key=device_key):
            return  # A healthy paired controller is reused without changing approvals.
    except GatewayError as exc:
        if exc.code != "pairing_required":
            raise
    attrs = await blocking(docker.inspect_container, instance.container_id)
    labels = ((attrs or {}).get("Config") or {}).get("Labels") or {}
    if not (
        labels.get("eidolon.managed") == "true"
        and labels.get("eidolon.runtime_type") == "openclaw"
        and labels.get("eidolon.employee_id") == str(instance.employee_id)
        and labels.get("eidolon.resource_key") == instance.resource_key
        and instance.resource_key
    ):
        raise GatewayError("pairing_ownership_mismatch")

    async def command(*args):
        code, output = await blocking(
            docker.exec_run,
            instance.container_id,
            [
                "timeout",
                "30s",
                "node",
                "/app/openclaw.mjs",
                "devices",
                *args,
                "--json",
                "--timeout",
                "5000",
            ],
        )
        if code != 0:
            raise GatewayError("pairing_cli_failed")
        try:
            return json.loads(output)
        except ValueError:
            raise GatewayError("pairing_cli_invalid") from None

    listing = await command("list")
    identifier = device_id(device_key)
    pending = [p for p in listing.get("pending", []) if p.get("deviceId") == identifier]
    if len(pending) != 1:
        raise GatewayError("pairing_request_missing")
    request = pending[0]
    request_id = request.get("requestId", "")
    roles = set(request.get("roles") or [request.get("role")])
    if not (
        re.fullmatch(r"[a-f0-9-]{36}", request_id)
        and roles == {"operator"}
        and set(request.get("scopes") or []) == set(CONTROLLER_SCOPES)
        and request.get("clientId") == "gateway-client"
        and request.get("clientMode") == "backend"
    ):
        raise GatewayError("pairing_request_mismatch")
    result = await command("approve", request_id)
    if (result.get("device") or {}).get("deviceId") != identifier:
        raise GatewayError("pairing_receipt_mismatch")
    # Confirm effective permissions through the external path, not the CLI receipt alone.
    async with GatewayClient(url, token, device_key=device_key):
        return
