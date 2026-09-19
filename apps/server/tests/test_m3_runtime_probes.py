"""M3 U4: observed control facts, expiry and authenticated fixed-image protocol."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.runtimes import probe
from app.runtimes.openclaw import rpc
from app.runtimes.openclaw.identity import new_device_key


class GatewaySocket:
    def __init__(self, *, scopes=rpc.CONTROLLER_SCOPES, reject=False, hang=False):
        self.messages = asyncio.Queue()
        self.messages.put_nowait(
            json.dumps(
                {"type": "event", "event": "connect.challenge", "payload": {"nonce": "challenge"}}
            )
        )
        self.scopes, self.reject, self.hang = scopes, reject, hang
        self.closed = False
        self.sent = []

    async def send(self, raw):
        request = json.loads(raw)
        self.sent.append(request)
        if self.hang:
            return
        if request["method"] == "connect":
            self.messages.put_nowait(
                json.dumps(
                    {
                        "type": "res",
                        "id": request["id"],
                        "ok": not self.reject,
                        "payload": {
                            "type": "hello-ok",
                            "protocol": 4,
                            "auth": {"scopes": list(self.scopes)},
                            "features": {"methods": ["health", "chat.send"]},
                        },
                        "error": {"code": "INVALID_REQUEST", "message": "secret-fixture-token"}
                        if self.reject
                        else None,
                    }
                )
            )
        else:
            self.messages.put_nowait(
                json.dumps(
                    {"type": "res", "id": request["id"], "ok": True, "payload": {"ok": True}}
                )
            )

    async def recv(self):
        return await self.messages.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.messages.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self):
        self.closed = True


def use_socket(monkeypatch, socket):
    async def connect(*args, **kwargs):
        assert kwargs["proxy"] is None
        return socket

    monkeypatch.setattr(rpc.websockets, "connect", connect)


async def test_gateway_v4_signed_handshake_and_payload_response(monkeypatch):
    socket = GatewaySocket()
    use_socket(monkeypatch, socket)
    async with rpc.GatewayClient(
        "ws://runtime", "secret-fixture-token", device_key=new_device_key()
    ) as client:
        assert await client.call("health", {}) == {"ok": True}
        params = socket.sent[0]["params"]
        assert params["minProtocol"] == params["maxProtocol"] == 4
        assert params["client"]["id"] == "gateway-client"
        assert "name" not in params["client"]
        proof = params["device"]
        message = "|".join(
            [
                "v3",
                proof["id"],
                "gateway-client",
                "backend",
                "operator",
                "operator.read,operator.write",
                str(proof["signedAt"]),
                "secret-fixture-token",
                "challenge",
                "python",
                "",
            ]
        )
        public = base64.urlsafe_b64decode(proof["publicKey"] + "==")
        signature = base64.urlsafe_b64decode(proof["signature"] + "==")
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message.encode())
    assert socket.closed


@pytest.mark.parametrize("reason", ["authentication_rejected", "scope_missing"])
async def test_gateway_rejects_ungranted_access_and_closes_socket(monkeypatch, reason):
    socket = GatewaySocket(reject=reason == "authentication_rejected", scopes=())
    use_socket(monkeypatch, socket)
    with pytest.raises(rpc.GatewayError, match=reason) as error:
        async with rpc.GatewayClient("ws://runtime", "secret-fixture-token"):
            pytest.fail("ungranted access was accepted")
    assert "secret-fixture-token" not in str(error.value)
    assert socket.closed


async def test_gateway_handshake_deadline_closes_socket(monkeypatch):
    socket = GatewaySocket(hang=True)
    use_socket(monkeypatch, socket)
    with pytest.raises(TimeoutError):
        async with rpc.GatewayClient("ws://runtime", "token", timeout=0.01):
            pytest.fail("unfinished handshake was accepted")
    assert socket.closed


async def test_gateway_eof_fails_pending_call_without_waiting_for_rpc_deadline(monkeypatch):
    socket = GatewaySocket()
    use_socket(monkeypatch, socket)
    async with rpc.GatewayClient("ws://runtime", "token") as client:
        socket.hang = True
        call = asyncio.create_task(client.call("health", {}, timeout=60))
        await asyncio.sleep(0)
        socket.messages.put_nowait(None)
        with pytest.raises(rpc.GatewayError, match="connection_closed"):
            await asyncio.wait_for(call, 0.2)
        assert not client._pending


@pytest.mark.parametrize(
    "mode, reason",
    [
        ("healthy", "authenticated_control"),
        ("forbidden", "authentication_rejected"),
        ("timeout", "control_timeout"),
        ("auth_disabled", "control_authentication_not_enforced"),
        ("wrong_protocol", "control_protocol_mismatch"),
        ("missing_capability", "authenticated_control"),
    ],
)
async def test_hermes_control_distinguishes_failures_without_calling_a_model(
    monkeypatch, mode, reason
):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-fixture-token", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "platform": "hermes-agent"})
        assert request.headers["Authorization"] == "Bearer secret-fixture-token"
        if mode == "forbidden":
            return httpx.Response(403, text="secret-fixture-token")
        return httpx.Response(
            200,
            json={
                "object": "bad" if mode == "wrong_protocol" else "hermes.api_server.capabilities",
                "auth": {"required": mode != "auth_disabled"},
                "features": {
                    k: not (mode == "missing_capability" and k == "run_stop")
                    for k in probe.HERMES_REQUIRED
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(probe.httpx, "AsyncClient", lambda **kwargs: client)
    report = await probe.probe_control("hermes", "http://runtime", "secret-fixture-token")
    assert report["control"]["reason"] == reason
    assert "secret-fixture-token" not in json.dumps(report)
    assert set(paths) <= {"/health", "/v1/capabilities"}
    if mode == "missing_capability":
        assert report["capabilities"]["state"] == "failed"
        assert report["capabilities"]["missing"] == ["run_stop"]


def test_expired_or_malformed_probe_is_unknown():
    value = probe.fact("passed", "fixture")
    assert probe.current_fact(value)["state"] == "passed"
    assert (
        probe.current_fact(value, now=datetime.now(UTC) + timedelta(seconds=91))["reason"]
        == "probe_expired"
    )
    for invalid in ({}, {**value, "checked_at": "yesterday"}, {**value, "expires_at": None}):
        assert probe.current_fact(invalid)["state"] == "unknown"
    assert probe.current_fact(None)["reason"] == "not_probed"


@pytest.fixture()
async def runtime_environment(db, default_company_id, fake_docker, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.models.organization import Employee
    from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
    from app.work import runtime_reconciliation as reconcile

    monkeypatch.setattr(settings, "runtime_control_mode", "docker")
    employee = Employee(
        company_id=default_company_id,
        name="Probe",
        slug="probe-employee",
        role="engineer",
        runtime_type="hermes",
        workspace_path=str(tmp_path / "workspace"),
        memory_namespace="probe-memory",
    )
    db.add(employee)
    db.commit()
    manager = DockerRuntimeInstanceManager(docker=fake_docker)
    instance = await manager.create_instance(
        db, employee, runtime_type="hermes", deployment_mode="docker"
    )

    async def healthy(*args, **kwargs):
        return {
            name: probe.fact("passed", "fixture_control")
            for name in ("application_health", "control", "capabilities")
        }

    monkeypatch.setattr(reconcile, "probe_control", healthy)
    return SimpleNamespace(
        manager=manager,
        instance=instance,
        employee=employee,
        docker=fake_docker,
        reconcile=reconcile,
    )


async def test_reconciliation_checks_real_control_and_invalidates_replaced_container(
    db, runtime_environment
):
    env = runtime_environment
    await env.reconcile.probe_instance(env.manager, env.instance.id)
    db.refresh(env.instance)
    assert all(f["state"] == "passed" for f in probe.runtime_facts(env.instance).values())
    env.instance.container_id = "replacement"
    assert all(f["state"] == "unknown" for f in probe.runtime_facts(env.instance).values())


@pytest.mark.parametrize(
    "case, reason, state",
    [
        ("empty", "container_not_recorded", "failed"),
        ("missing", "container_missing", "failed"),
        ("offline", "docker_unavailable", "unknown"),
        ("foreign", "container_ownership_mismatch", "failed"),
    ],
)
async def test_reconciliation_distinguishes_missing_resources_from_unavailable_daemon(
    db, runtime_environment, monkeypatch, case, reason, state
):
    env = runtime_environment
    if case == "empty":
        env.instance.container_id = None
        db.commit()
    elif case == "missing":
        env.docker.containers.clear()
    elif case == "offline":
        monkeypatch.setattr(env.docker, "ping", lambda: False)
    else:
        env.docker.containers[env.instance.container_id]["labels"]["eidolon.employee_id"] = "99999"
    await env.reconcile.probe_instance(env.manager, env.instance.id)
    db.refresh(env.instance)
    result = probe.runtime_facts(env.instance)
    assert result["container"]["reason"] == reason
    assert result["container"]["state"] == state
    assert result["control"]["state"] != "passed"


async def test_running_with_unknown_health_is_not_certified(db, runtime_environment, monkeypatch):
    env = runtime_environment
    env.docker.containers[env.instance.container_id]["health"] = "none"

    async def unreachable(*args, **kwargs):
        return {
            name: probe.fact("unknown", "fixture_unreachable")
            for name in ("application_health", "control", "capabilities")
        }

    monkeypatch.setattr(env.reconcile, "probe_control", unreachable)
    await env.reconcile.probe_instance(env.manager, env.instance.id)
    db.refresh(env.instance)
    assert probe.runtime_facts(env.instance)["process"]["reason"] == "process_health_unknown"


async def test_probe_does_not_overwrite_a_concurrent_instance_change(
    db, runtime_environment, monkeypatch
):
    env = runtime_environment

    async def changed(*args, **kwargs):
        env.instance.status = "stopped"
        db.commit()
        return {
            name: probe.fact("passed", "fixture")
            for name in ("application_health", "control", "capabilities")
        }

    monkeypatch.setattr(env.reconcile, "probe_control", changed)
    assert not await env.reconcile.probe_instance(env.manager, env.instance.id)
    db.refresh(env.instance)
    assert env.instance.status == "stopped"
    assert "runtime_probe" not in env.instance.metadata_json


async def test_reconciliation_reports_orphan_once_and_preserves_all_resources(
    db, runtime_environment
):
    from sqlalchemy import select

    from app.models.event import Event

    env = runtime_environment
    orphan = env.docker.create_container(
        name="orphan", image="fixture", labels={"eidolon.runtime_type": "hermes"}
    )
    unrelated = env.docker.create_container(
        name="unrelated", image="fixture", labels={"eidolon.managed": "false"}
    )
    # A crash between create and pointer writeback still has a durable key/name.
    env.instance.container_id = None
    db.commit()
    before = set(env.docker.containers)
    await env.reconcile.reconcile_once(env.manager)
    await env.reconcile.reconcile_once(env.manager)
    events = list(db.scalars(select(Event).where(Event.type == "runtime.orphan_detected")))
    assert [event.payload["container_id"] for event in events] == [orphan]
    assert set(env.docker.containers) == before
    assert unrelated in before


async def test_provisioned_row_without_observed_control_is_not_ready(db, runtime_environment):
    from app.work import readiness

    env = runtime_environment
    report = readiness.readiness_report(db, env.employee)
    runtime = next(item for item in report.items if item.item == "runtime")
    assert env.instance.status == "running"
    assert not runtime.ready
    assert runtime.facts["probes"]["control"]["reason"] == "not_probed"


@pytest.fixture()
def configured_model(db, runtime_environment):
    from app.providers.secrets.store import get_secret_store
    from app.repositories import providers as repo

    env = runtime_environment
    provider = repo.create_provider(
        db,
        company_id=env.employee.company_id,
        name="Probe provider",
        provider_type="custom",
        base_url="https://provider.invalid/v1",
        credential_ref=get_secret_store().store(db, "probe-test-key"),
    )
    binding = repo.create_binding(
        db, employee_id=env.employee.id, provider_id=provider.id, model="selected", is_primary=True
    )
    env.instance.model_binding_id = binding.id
    fingerprint = probe.model_fingerprint(provider, binding.model)
    env.instance.metadata_json = {
        **env.instance.metadata_json,
        "applied_model_fingerprint": fingerprint,
        "provider_connectivity_probe": {
            "fingerprint": fingerprint,
            "fact": probe.fact("passed", "fixture_model_list_readable"),
        },
    }
    db.commit()
    return provider, binding, fingerprint


async def test_readable_model_list_does_not_certify_selected_model(
    db, runtime_environment, configured_model
):
    from app.work import readiness
    from app.work.runtime_readiness import provider_facts

    env = runtime_environment
    facts = provider_facts(db, env.employee, env.instance)
    assert facts["provider_connectivity"]["state"] == "passed"
    assert facts["selected_model"]["state"] == "unknown"
    assert "provider" in readiness.blocking_gaps(db, env.employee)
    provider, binding, fingerprint = configured_model
    for state in ("failed", "passed"):
        env.instance.metadata_json = {
            **env.instance.metadata_json,
            "selected_model_probe": {
                "fingerprint": fingerprint,
                "fact": probe.fact(state, "fixture_model_permission"),
            },
        }
        db.commit()
        assert ("provider" in readiness.blocking_gaps(db, env.employee)) is (state == "failed")


@pytest.mark.parametrize(
    "change", ["model", "credential", "disabled", "foreign_company", "expired"]
)
async def test_provider_evidence_cannot_survive_configuration_changes(
    db, runtime_environment, configured_model, change
):
    from app.work import readiness

    env = runtime_environment
    provider, binding, fingerprint = configured_model
    receipt = probe.fact("passed", "fixture_success")
    if change == "expired":
        receipt["checked_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        receipt["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    env.instance.metadata_json = {
        **env.instance.metadata_json,
        "selected_model_probe": {"fingerprint": fingerprint, "fact": receipt},
    }
    if change == "model":
        binding.model = "different"
    elif change == "credential":
        provider.credential_ref = "different-key-ref"
    elif change == "disabled":
        provider.enabled = False
    elif change == "foreign_company":
        provider.company_id = None
    db.commit()
    assert "provider" in readiness.blocking_gaps(db, env.employee)


async def test_reconciliation_loop_starts_without_waiting_for_remote_work(
    runtime_environment, monkeypatch
):
    from app.core.config import settings

    env = runtime_environment
    began = asyncio.Event()

    async def waiting(manager):
        began.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(settings, "runtime_reconciliation_enabled", True)
    monkeypatch.setattr(env.reconcile, "reconcile_once", waiting)
    await asyncio.wait_for(env.manager.start_healthcheck_loop(), 0.2)
    await asyncio.wait_for(began.wait(), 0.2)
    await asyncio.wait_for(env.manager.stop_healthcheck_loop(), 0.2)
    assert env.manager._loop_task is None


@pytest.fixture()
def model_limits(db, configured_model):
    from app.schemas.runtime import RuntimeModelProbeRequest

    provider, _, _ = configured_model
    provider.metadata_json = {
        "probe_pricing": {
            "selected": {
                "input_usd_per_million": "1",
                "output_usd_per_million": "2",
                "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "source": "unit-test fixture",
            }
        }
    }
    db.commit()
    return RuntimeModelProbeRequest(allow_paid=True, idempotency_key="first", max_cost_usd="0.01")


@pytest.mark.parametrize("response_kind", ["accepted", "rejected", "timeout", "no_usage"])
async def test_explicit_model_validation_calls_selected_model_once_and_persists_receipt(
    db, runtime_environment, model_limits, monkeypatch, response_kind
):
    from app.models.runtime import RuntimeModelProbe
    from app.providers import probe as provider_probe
    from app.runtimes.manager import docker_manager
    from app.work.model_validation import validate_model
    from app.work.runtime_readiness import provider_facts

    env = runtime_environment
    requests = []

    def handle(request):
        requests.append(request)
        # An independent connection sees the attempt before any paid request.
        with docker_manager.SessionLocal() as other:
            from sqlalchemy import select

            attempt = other.scalar(select(RuntimeModelProbe))
            assert attempt.state == "running"
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "selected"
        assert payload["max_tokens"] == 32
        assert "tools" not in payload
        if response_kind == "timeout":
            raise httpx.ReadTimeout("secret-fixture-token", request=request)
        if response_kind == "rejected":
            return httpx.Response(403, text="secret-fixture-token")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": None
                if response_kind == "no_usage"
                else {"prompt_tokens": 9, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(provider_probe.httpx, "AsyncClient", lambda **kwargs: client)
    receipt = await validate_model(env.manager, env.instance.id, model_limits)
    assert len(requests) == 1
    assert (
        receipt["state"]
        == {"accepted": "passed", "no_usage": "passed", "rejected": "failed", "timeout": "unknown"}[
            response_kind
        ]
    )
    assert "secret-fixture-token" not in json.dumps(receipt)
    again = await validate_model(env.manager, env.instance.id, model_limits)
    assert again == receipt
    assert len(requests) == 1
    db.refresh(env.instance)
    assert (
        provider_facts(db, env.employee, env.instance)["selected_model"]["state"]
        == receipt["state"]
    )
    if response_kind == "no_usage":
        assert receipt["result"]["usage"] is None and receipt["result"]["cost_usd"] is None
    elif response_kind == "accepted":
        assert receipt["result"]["cost_usd"] == "0.000011"


@pytest.mark.parametrize("problem", ["missing_quote", "insufficient_budget", "expired_quote"])
async def test_model_probe_requires_known_price_within_explicit_budget_before_network(
    db, runtime_environment, model_limits, configured_model, monkeypatch, problem
):
    from fastapi import HTTPException

    from app.providers import probe as provider_probe
    from app.work.model_validation import validate_model

    env = runtime_environment
    provider, _, _ = configured_model
    if problem == "missing_quote":
        provider.metadata_json = {}
    elif problem == "expired_quote":
        provider.metadata_json = {
            "probe_pricing": {
                "selected": {
                    **provider.metadata_json["probe_pricing"]["selected"],
                    "valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                }
            }
        }
    else:
        model_limits = model_limits.model_copy(update={"max_cost_usd": Decimal("0.000001")})
    db.commit()

    def forbidden(**kwargs):
        pytest.fail("unauthorized probe reached the network")

    monkeypatch.setattr(provider_probe.httpx, "AsyncClient", forbidden)
    with pytest.raises(HTTPException) as error:
        await validate_model(env.manager, env.instance.id, model_limits)
    assert error.value.status_code == 422


async def test_model_probe_idempotency_and_single_active_request_bound(
    runtime_environment, model_limits, monkeypatch
):
    from fastapi import HTTPException

    from app.providers import probe as provider_probe
    from app.work.model_validation import validate_model

    env = runtime_environment
    began, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def waiting(*args):
        calls.append(args[2])
        began.set()
        await finish.wait()
        return {"fact": probe.fact("passed", "fixture_success"), "usage": None, "cost_usd": None}

    monkeypatch.setattr(provider_probe, "validate_selected_model", waiting)
    first = asyncio.create_task(validate_model(env.manager, env.instance.id, model_limits))
    try:
        await asyncio.wait_for(began.wait(), 1)
        duplicate = await validate_model(env.manager, env.instance.id, model_limits)
        assert duplicate["state"] == "running"
        for changed in ({"max_output_tokens": 10}, {"idempotency_key": "second"}):
            with pytest.raises(HTTPException) as error:
                await validate_model(
                    env.manager, env.instance.id, model_limits.model_copy(update=changed)
                )
            assert error.value.status_code == 409
        assert calls == ["selected"]
    finally:
        finish.set()
        await first


async def test_cancelled_model_probe_keeps_unknown_cost_and_never_retries(
    runtime_environment, model_limits, monkeypatch
):
    from app.providers import probe as provider_probe
    from app.work.model_validation import validate_model

    env = runtime_environment
    began = asyncio.Event()

    async def waiting(*args):
        began.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider_probe, "validate_selected_model", waiting)
    task = asyncio.create_task(validate_model(env.manager, env.instance.id, model_limits))
    await asyncio.wait_for(began.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    receipt = await validate_model(env.manager, env.instance.id, model_limits)
    assert receipt["state"] == "unknown"
    assert receipt["result"]["fact"]["reason"] == "model_probe_cancelled"
    assert receipt["result"]["cost_usd"] is None


async def test_periodic_probe_reuses_current_provider_receipt_but_explicit_probe_refreshes(
    db, runtime_environment, configured_model, monkeypatch
):
    from app.providers import probe as provider_probe

    env = runtime_environment
    calls = []

    async def connectivity(*args):
        calls.append(True)
        return {"ok": True}

    monkeypatch.setattr(provider_probe, "test_provider", connectivity)
    await env.reconcile.probe_instance(env.manager, env.instance.id, reuse_provider=True)
    assert calls == []
    await env.reconcile.probe_instance(env.manager, env.instance.id)
    assert len(calls) == 1
    db.refresh(env.instance)
    metadata = dict(env.instance.metadata_json)
    metadata["provider_connectivity_probe"] = {
        "fingerprint": configured_model[2],
        "fact": probe.fact("passed", "old", ttl=-1),
    }
    env.instance.metadata_json = metadata
    db.commit()
    await env.reconcile.probe_instance(env.manager, env.instance.id, reuse_provider=True)
    assert len(calls) == 2


async def test_port_reservation_skips_existing_reservations_listeners_and_gitea(
    db, runtime_environment, monkeypatch
):
    from app.core.config import settings
    from app.runtimes.manager import connection

    env = runtime_environment
    env.instance.internal_host, env.instance.internal_port = "127.0.0.1", 26920
    db.commit()
    monkeypatch.setattr(settings, "runtime_control_mode", "loopback")
    monkeypatch.setattr(settings, "runtime_debug_port_start", 26920)
    monkeypatch.setattr(settings, "runtime_debug_port_end", 26924)
    attempted = []

    class Listener:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            attempted.append(address[1])
            if address[1] == 26921:
                raise OSError("already used by another process")

    monkeypatch.setattr(connection.socket, "socket", Listener)
    candidate = SimpleNamespace(
        id=999, internal_host=None, internal_port=None, runtime_type="hermes"
    )
    connection.reserve_connection(db, candidate)
    assert candidate.internal_host == "127.0.0.1" and candidate.internal_port == 26923
    assert attempted == [26921, 26923]
    monkeypatch.setattr(settings, "runtime_debug_port_end", 26922)
    candidate.internal_host = candidate.internal_port = None
    with pytest.raises(connection.RuntimeManagerError, match="exhausted"):
        connection.reserve_connection(db, candidate)


@pytest.mark.parametrize(
    "problem",
    ["foreign_container", "foreign_device", "extra_scope", "wrong_client", "two_requests"],
)
async def test_pairing_never_approves_an_unowned_or_overprivileged_device(monkeypatch, problem):
    from app.runtimes.openclaw import pairing
    from app.runtimes.openclaw.identity import device_id

    key = new_device_key()
    instance = SimpleNamespace(
        internal_host="127.0.0.1",
        internal_port=26900,
        employee_id=1,
        container_id="owned-container",
        resource_key="owned-key",
    )
    pending = {
        "deviceId": device_id(key),
        "requestId": "11111111-1111-1111-1111-111111111111",
        "roles": ["operator"],
        "scopes": list(rpc.CONTROLLER_SCOPES),
        "clientId": "gateway-client",
        "clientMode": "backend",
    }
    labels = {
        "eidolon.managed": "true",
        "eidolon.runtime_type": "openclaw",
        "eidolon.employee_id": "1",
        "eidolon.resource_key": "owned-key",
    }
    if problem == "foreign_container":
        labels["eidolon.resource_key"] = "other-key"
    elif problem == "foreign_device":
        pending["deviceId"] = device_id(new_device_key())
    elif problem == "extra_scope":
        pending["scopes"].append("operator.admin")
    elif problem == "wrong_client":
        pending["clientId"] = "different-client"

    class NeedsPairing:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise rpc.GatewayError("pairing_required")

        async def __aexit__(self, *args):
            pass

    commands = []

    def cli(container_id, command):
        commands.append(command)
        assert "approve" not in command
        return 0, json.dumps({"pending": [pending] * (2 if problem == "two_requests" else 1)})

    monkeypatch.setattr(pairing, "GatewayClient", NeedsPairing)
    docker = SimpleNamespace(
        inspect_container=lambda cid: {"Config": {"Labels": labels}}, exec_run=cli
    )
    with pytest.raises(rpc.GatewayError, match="pairing_"):
        await pairing.pair_controller(docker, instance, "token", key)
    assert len(commands) == (0 if problem == "foreign_container" else 1)


async def test_interrupted_model_probe_reuses_unknown_receipt_without_calling_again(
    db, runtime_environment, model_limits, monkeypatch
):
    from app.models.runtime import RuntimeModelProbe
    from app.providers import probe as provider_probe
    from app.work.model_validation import _claim, validate_model

    env = runtime_environment
    receipt, _ = _claim(env.instance.id, model_limits, env.manager)
    row = db.get(RuntimeModelProbe, receipt["id"])
    row.created_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    async def forbidden(*args):
        pytest.fail("crash recovery repeated a paid request")

    monkeypatch.setattr(provider_probe, "validate_selected_model", forbidden)
    result = await validate_model(env.manager, env.instance.id, model_limits)
    assert result["state"] == "unknown"
    assert result["result"]["fact"]["reason"] == "model_probe_interrupted"
    assert result["result"]["cost_usd"] is None


async def test_model_validation_reauthorizes_company_in_its_own_database_phase(
    runtime_environment, model_limits, monkeypatch
):
    from fastapi import HTTPException

    from app.core.request_context import (
        RequestIdentity,
        reset_request_identity,
        set_request_identity,
    )
    from app.providers import probe as provider_probe
    from app.work.model_validation import validate_model

    env = runtime_environment

    async def forbidden(*args):
        pytest.fail("another company reached a paid model request")

    monkeypatch.setattr(provider_probe, "validate_selected_model", forbidden)
    token = set_request_identity(
        RequestIdentity(user_id=99, company_id=99999, membership_role="owner", session_id=99)
    )
    try:
        with pytest.raises(HTTPException) as error:
            await validate_model(env.manager, env.instance.id, model_limits)
        assert error.value.status_code == 404
    finally:
        reset_request_identity(token)


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
def test_rendered_provider_configuration_preserves_explicit_endpoint(runtime_type):
    import yaml

    from app.models.provider import Provider
    from app.providers.hermes import HermesProviderConfigurator
    from app.providers.openclaw import OpenClawProviderConfigurator

    provider = Provider(
        provider_type="deepseek",
        base_url="https://private-provider.invalid/v1",
        credential_ref="ref",
    )
    renderer = {"hermes": HermesProviderConfigurator, "openclaw": OpenClawProviderConfigurator}[
        runtime_type
    ]()
    result = renderer.render_provider_config(provider, "selected", "fixture-key")
    if runtime_type == "hermes":
        assert yaml.safe_load(result.files["config.yaml"])["model"]["base_url"] == provider.base_url
    else:
        assert (
            json.loads(result.files["openclaw.json"])["models"]["providers"]["deepseek"]["baseUrl"]
            == provider.base_url
        )


def test_openclaw_custom_provider_declares_selected_model_and_uses_an_env_reference():
    from app.models.provider import Provider
    from app.providers.openclaw import OpenClawProviderConfigurator

    provider = Provider(
        provider_type="custom", base_url="http://private-runtime:8642/v1", credential_ref="ref"
    )
    result = OpenClawProviderConfigurator().render_provider_config(
        provider, "selected", "fixture-key"
    )
    config = json.loads(result.files["openclaw.json"])
    custom = config["models"]["providers"]["custom"]
    assert custom["models"] == [{"id": "selected", "name": "selected"}]
    assert custom["apiKey"] == "${OPENAI_API_KEY}"
    assert result.env["OPENAI_API_KEY"] == "fixture-key"
    assert "fixture-key" not in result.files["openclaw.json"]


async def test_keyless_custom_provider_uses_its_public_model_list(monkeypatch):
    from app.models.provider import Provider
    from app.providers import probe as provider_probe

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "local"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(provider_probe.httpx, "AsyncClient", lambda **kwargs: client)
    result = await provider_probe.test_provider(
        Provider(provider_type="custom", base_url="http://local-model/v1"), None
    )
    assert result["ok"]
    assert len(requests) == 1 and "Authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "change",
    [
        {"allow_paid": False},
        {"max_requests": 2},
        {"timeout_seconds": 31},
        {"max_cost_usd": "0"},
        {"max_output_tokens": 129},
        {"prompt": "arbitrary"},
    ],
)
def test_model_validation_api_rejects_missing_opt_in_or_unbounded_requests(
    client, monkeypatch, change
):
    from app.services import runtimes

    async def forbidden(*args):
        pytest.fail("invalid paid request reached the service")

    monkeypatch.setattr(runtimes, "validate_runtime_model", forbidden)
    response = client.post(
        "/api/v1/runtimes/1/validate-model",
        json={
            "allow_paid": True,
            "idempotency_key": "explicit",
            "max_cost_usd": "0.01",
            **change,
        },
    )
    assert response.status_code == 422


async def test_runtime_read_api_exposes_only_sanitized_facts_and_never_calls_a_model(
    db, runtime_environment, configured_model, monkeypatch
):
    from app.providers import probe as provider_probe
    from app.services.runtimes import get_instance

    env = runtime_environment
    await env.reconcile.probe_instance(env.manager, env.instance.id, reuse_provider=True)
    db.refresh(env.instance)
    report = dict(env.instance.metadata_json)
    report["runtime_probe"]["facts"]["control"]["api_key"] = "must-not-leak"
    env.instance.metadata_json = report
    db.commit()

    async def forbidden(*args):
        pytest.fail("a read reached a paid model call")

    monkeypatch.setattr(provider_probe, "validate_selected_model", forbidden)
    output = get_instance(db, env.instance.id).model_dump_json()
    assert "must-not-leak" not in output and "api_key_ref" not in output
    assert json.loads(output)["probes"]["control"]["state"] == "passed"


async def test_model_receipt_cannot_be_reused_by_a_replacement_with_the_same_database_id(
    db, runtime_environment, model_limits
):
    from fastapi import HTTPException

    from app.models.runtime import RuntimeInstance
    from app.work.model_validation import _claim, _finish, validate_model

    env = runtime_environment
    receipt, _ = _claim(env.instance.id, model_limits, env.manager)
    _finish(receipt["id"], {"fact": probe.fact("passed", "fixture_success")})
    db.refresh(env.instance)
    instance_id = env.instance.id
    binding_id = env.instance.model_binding_id
    fingerprint = env.instance.metadata_json["applied_model_fingerprint"]
    db.delete(env.instance)
    db.commit()
    # SQLite can reuse the last deleted rowid. An audit receipt intentionally
    # outlives its deleted runtime and must not become this new runtime's receipt.
    replacement = RuntimeInstance(
        employee_id=env.employee.id,
        runtime_type="hermes",
        deployment_mode="docker",
        resource_key="replacement-resource",
        container_id="replacement-container",
        model_binding_id=binding_id,
        metadata_json={"applied_model_fingerprint": fingerprint},
    )
    db.add(replacement)
    db.commit()
    assert replacement.id == instance_id
    with pytest.raises(HTTPException) as error:
        await validate_model(env.manager, replacement.id, model_limits)
    assert error.value.status_code == 409
