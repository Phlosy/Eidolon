"""Cheap, bounded control probes. These endpoints never start an agent/model run."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx

from app.providers.base import effective_base_url
from app.runtimes.openclaw.rpc import GatewayClient, GatewayError

CONTROL_TTL_SECONDS = 90
MODEL_TTL_SECONDS = 3600
RUNTIME_FACT_NAMES = ("container", "process", "application_health", "control", "capabilities")
HERMES_REQUIRED = frozenset({"run_submission", "run_status", "run_events_sse", "run_stop"})
OPENCLAW_REQUIRED = frozenset({"chat.send", "chat.abort", "agent.wait"})


def fact(state: str, reason: str, *, ttl: int = CONTROL_TTL_SECONDS, **details) -> dict:
    now = datetime.now(UTC)
    return {
        "state": state,
        "reason": reason,
        "checked_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        **details,
    }


def current_fact(value: dict | None, *, now: datetime | None = None) -> dict:
    """Expired/malformed/missing evidence can never be used as success."""
    if not isinstance(value, dict):
        return {"state": "unknown", "reason": "not_probed", "checked_at": None, "expires_at": None}
    value = dict(value)
    try:
        checked = datetime.fromisoformat(value["checked_at"])
        expires = datetime.fromisoformat(value["expires_at"])
        now = now or datetime.now(UTC)
        if checked.tzinfo is None or expires.tzinfo is None or checked > now or checked >= expires:
            raise ValueError
        if expires <= now:
            value.update(state="unknown", reason="probe_expired")
    except (ValueError, TypeError, KeyError):
        value.update(
            state="unknown", reason="probe_timestamp_invalid", checked_at=None, expires_at=None
        )
    if value.get("state") not in {"passed", "failed", "unknown"}:
        value.update(state="unknown", reason="probe_state_invalid")
    return value


def model_fingerprint(provider, model: str | None) -> str | None:
    if provider is None or not model:
        return None
    configuration = {
        "provider_id": provider.id,
        "provider_type": provider.provider_type,
        "base_url": effective_base_url(provider),
        "credential_ref": provider.credential_ref,
        "model": model,
    }
    return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()


def instance_fingerprint(instance) -> str:
    metadata = instance.metadata_json or {}
    identity = [
        instance.container_id,
        instance.resource_key,
        instance.runtime_type,
        instance.internal_host,
        instance.internal_port,
        metadata.get("api_key_ref"),
        metadata.get("device_key_ref"),
        metadata.get("applied_model_fingerprint"),
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def runtime_facts(instance) -> dict[str, dict]:
    report = (instance.metadata_json or {}).get("runtime_probe") or {}
    facts = report.get("facts") or {}
    matches = report.get("instance_fingerprint") == instance_fingerprint(instance)
    return {name: current_fact(facts.get(name) if matches else None) for name in RUNTIME_FACT_NAMES}


async def probe_control(
    runtime_type: str, base_url: str | None, token: str | None, *, device_key: str | None = None
) -> dict[str, dict]:
    facts = {
        "application_health": fact("unknown", "not_probed"),
        "control": fact("unknown", "not_probed"),
        "capabilities": fact("unknown", "not_probed"),
    }
    if not base_url or not token:
        facts["control"] = fact("failed", "control_configuration_missing")
        return facts
    try:
        async with asyncio.timeout(8):
            if runtime_type == "hermes":
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    response = await client.get(f"{base_url}/health")
                    response.raise_for_status()
                    payload = response.json()
                    healthy = (
                        payload.get("status") == "ok" and payload.get("platform") == "hermes-agent"
                    )
                    facts["application_health"] = fact(
                        "passed" if healthy else "failed",
                        "application_healthy" if healthy else "application_unhealthy",
                    )
                    response = await client.get(
                        f"{base_url}/v1/capabilities", headers={"Authorization": f"Bearer {token}"}
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("object") != "hermes.api_server.capabilities":
                        facts["control"] = fact("failed", "control_protocol_mismatch")
                        return facts
                    if (payload.get("auth") or {}).get("required") is not True:
                        facts["control"] = fact("failed", "control_authentication_not_enforced")
                        return facts
                    features = payload.get("features") or {}
                    present = {key for key in HERMES_REQUIRED if features.get(key) is True}
                    missing = HERMES_REQUIRED - present
            elif runtime_type == "openclaw":
                url = base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
                async with GatewayClient(url, token, device_key=device_key, timeout=6) as control:
                    health = await control.call("health", {}, timeout=5)
                    healthy = isinstance(health, dict) and health.get("ok") is True
                    facts["application_health"] = fact(
                        "passed" if healthy else "failed",
                        "application_healthy" if healthy else "application_unhealthy",
                    )
                    methods = (control.hello.get("features") or {}).get("methods") or []
                    present = OPENCLAW_REQUIRED.intersection(methods)
                    missing = OPENCLAW_REQUIRED - present
            else:
                facts["control"] = fact("failed", "runtime_unsupported")
                return facts
        facts["control"] = fact("passed", "authenticated_control")
        facts["capabilities"] = fact(
            "failed" if missing else "passed",
            "capability_missing" if missing else "capabilities_present",
            present=sorted(present),
            missing=sorted(missing),
        )
    except GatewayError as exc:
        facts["control"] = fact("failed", exc.code)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        facts["control"] = fact(
            "failed",
            "authentication_rejected" if status in {401, 403} else "control_http_error",
            http_status=status,
        )
    except (TimeoutError, httpx.TimeoutException):
        facts["control"] = fact("failed", "control_timeout")
    except Exception:
        # No exception/body text in facts: an upstream service may echo its credential.
        facts["control"] = fact("failed", "control_unreachable_or_invalid")
    return facts
