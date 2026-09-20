"""Real native cancellation with an offline held Provider, never a paid model.

Opt in with EIDOLON_REMOTE_CANCEL_TESTS=1. This characterizes actual fixed-image
control protocols and durable attempts; model/business delivery gates are separate.
"""

import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import select
from test_m3_management_bridge import manager as manager_fixture
from test_m3_real_runtime_artifacts import make_employee as person_employee_fixture

from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project, Task
from app.models.provider import Provider
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeRunReference, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.runtimes.openclaw.rpc import GatewayClient
from app.work import budget_monitor, telemetry

pytestmark = pytest.mark.integration
manager = manager_fixture
make_employee = person_employee_fixture


@pytest.fixture()
def model_relay(docker_service, runtime_network, cleanup_containers, tmp_path):
    if os.environ.get("EIDOLON_REMOTE_CANCEL_TESTS") != "1":
        pytest.skip("native cancellation requires EIDOLON_REMOTE_CANCEL_TESTS=1; no paid model")
    info = docker_service.inspect_image("python:3.12-slim")
    if info is None:
        pytest.skip("offline Provider requires a local python:3.12-slim image")
    evidence = tmp_path / "held-provider"
    evidence.mkdir(mode=0o700)
    token = secrets.token_hex(24)
    name = f"eidolon-it-held-{uuid.uuid4().hex[:12]}"
    assert docker_service.ensure_network(runtime_network)
    container = docker_service.create_container(
        name=name,
        image=info["Id"],
        network=runtime_network,
        environment={"TEST_TOKEN": token},
        volumes={
            str(Path(__file__).with_name("held_model_server.py")): {
                "bind": "/server.py",
                "mode": "ro",
            },
            str(evidence): {"bind": "/evidence", "mode": "rw"},
        },
        restart_policy="no",
        cpu_limit=0.5,
        memory_limit_mb=128,
        user=f"{os.getuid()}:{os.getgid()}",
        command=["python", "/server.py"],
        labels={"eidolon.test": "offline-held-provider"},
    )
    assert container and docker_service.start_container(container)
    receipt = evidence / "receipt.json"
    deadline = time.monotonic() + 10
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert receipt.exists(), "offline Provider did not start"
    relay = SimpleNamespace(
        url=f"http://{name}:8642/v1",
        token=token,
        model="held-test-model",
        receipt=receipt,
        release=evidence / "release",
    )
    try:
        yield relay
    finally:
        relay.release.touch()
        print("OFFLINE_PROVIDER_RECEIPT=" + receipt.read_text())


def drop_stop(monkeypatch, runtime_type, calls):
    """Fault only the outgoing stop; queries still use the actual native server."""
    if runtime_type == "hermes":
        original = httpx.AsyncClient.post

        async def post(client, url, *args, **kwargs):
            if str(url).endswith("/stop"):
                calls.append("hermes.stop")
                raise httpx.ConnectError("injected stop transport loss")
            return await original(client, url, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
    else:
        original = GatewayClient.call

        async def call(client, method, *args, **kwargs):
            if method == "chat.abort":
                calls.append("openclaw.abort")
                raise ConnectionError("injected stop transport loss")
            return await original(client, method, *args, **kwargs)

        monkeypatch.setattr(GatewayClient, "call", call)


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
@pytest.mark.parametrize(
    "reason,fault", [("cancelled", False), ("budget_denied", False), ("cancelled", True)]
)
async def test_native_stop_and_unconfirmed_original_run(
    runtime_type, reason, fault, manager, create_model_runtime, model_relay, idb, monkeypatch
):
    runtime = await create_model_runtime(runtime_type, manager)
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type]()
    with idb() as db:
        employee = db.get(Employee, runtime.instance.employee_id)
        provider = db.scalar(select(Provider).where(Provider.company_id == employee.company_id))
        project = Project(
            company_id=employee.company_id, name="Native cancellation", status="in_progress"
        )
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            title="Await held response",
            assignee_id=employee.id,
            status="in_progress",
        )
        db.add(task)
        db.flush()
        context = TaskContext(
            task_id=task.id, project_id=project.id, title=task.title, kind="coding"
        )
        connection = resolve_container_instance(employee.id, runtime_type, session_factory=idb)
        current = await adapter.create_session(connection, context)
        work = project_repo.create_work_session(
            db,
            task_id=task.id,
            employee_id=employee.id,
            runtime_type=runtime_type,
            runtime_instance_id=runtime.instance.id,
            provider_id=provider.id,
            model=model_relay.model,
            started_at=utcnow(),
            runtime_session_ref=current.id,
        )
        db.commit()
    attempt = telemetry.prepare_attempt(
        work.id,
        current,
        "Wait for the response.",
        request_key="native-cancel",
        kind="worker",
        session_factory=idb,
    )
    evidence = {
        "runtime": runtime_type,
        "image_id": runtime.instance.image_digest,
        "version": runtime.instance.runtime_version,
        "container_id": runtime.instance.container_id,
        "attempt_id": attempt.attempt_id,
        "reason": reason,
        "stop_fault_injected": fault,
        "paid_model_requests": 0,
    }
    try:
        await adapter.send_task(
            current,
            "Wait for the response.",
            {"task_context": context, "runtime_config": {"run_timeout_seconds": 90}},
        )
        async with asyncio.timeout(25):
            while (
                not current.remote_run_id
                or not json.loads(model_relay.receipt.read_text())["requests"]
            ):
                if current.background.done():
                    current.background.result()
                    pytest.fail("native run ended before reaching offline Provider")
                await asyncio.sleep(0.1)
        reference = RuntimeRunReference(
            current.id, current.remote_run_id, current.remote_session_ref
        )
        before = await adapter.query_run(connection, reference)
        assert before.status not in {"completed", "failed"}
        evidence.update(
            remote_run_id=current.remote_run_id,
            remote_session_ref=current.remote_session_ref,
            before=before.status,
        )
        if reason == "budget_denied":

            async def denied(_):
                return False

            assert (
                await budget_monitor.stop_reason(
                    work.id, denied, is_current=lambda: True, session_factory=idb
                )
                == "budget_denied"
            )
        current.stop_reason = reason
        faults = []
        with monkeypatch.context() as patch:
            if fault:
                drop_stop(patch, runtime_type, faults)
            await adapter.cancel_task(current)
        observed = await adapter.query_run(connection, reference)
        # Hermes can acknowledge stopping while an in-flight Provider response
        # is still pending. Preserve that initial unknown, then let the offline
        # response return and inspect whether the native run actually terminates.
        if runtime_type == "hermes" and not fault:
            with idb() as db:
                initial = db.get(RuntimeAttempt, attempt.attempt_id)
                evidence["immediate_stop_state"] = initial.stop_state
                evidence["immediate_cancel_confirmed"] = current.cancel_confirmed
            evidence["immediate_query_status"] = observed.status
            model_relay.release.touch()
            deadline = time.monotonic() + 8
            while observed.status not in {"completed", "failed"} and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                observed = await adapter.query_run(connection, reference)
            if observed.status in {"completed", "failed"}:
                # Exercise the existing durable reconstruction/stop/query path;
                # do not mark a terminal directly from this test's assumptions.
                from app.work.runtime_stop import stop_attempt

                evidence["recheck"] = await stop_attempt(
                    attempt.attempt_id, reason=reason, adapter=adapter, session_factory=idb
                )
        if runtime_type == "hermes":
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(
                    connection.details["base_url"]
                    + "/v1/runs/"
                    + quote(reference.remote_run_id, safe=""),
                    headers=adapter._headers(connection),
                )
                response.raise_for_status()
                payload = response.json()
                evidence["native_status"] = payload.get("status")
        with idb() as db:
            row = db.get(RuntimeAttempt, attempt.attempt_id)
            evidence.update(
                after=observed.status,
                query_source=observed.source,
                stop_state=row.stop_state,
                saved_stop_reason=row.stop_reason,
                cancel_confirmed=current.cancel_confirmed,
                producer_drained=current.background.done(),
                provider=json.loads(model_relay.receipt.read_text()),
                fault_calls=faults,
            )
            print("REAL_NATIVE_CANCEL=" + json.dumps(evidence, sort_keys=True))
            assert row.stop_reason == reason and current.background.done()
            assert row.remote_run_id == reference.remote_run_id
            if fault:
                assert len(faults) == 1
                assert observed.status not in {"completed", "failed"}
                assert row.stop_state == "unconfirmed" and current.cancel_confirmed is False
                assert any(r["state"] == "held" for r in evidence["provider"]["requests"])
            else:
                assert observed.status == "failed", "native run must confirm interruption"
                assert row.stop_state == "terminal_observed"
                if runtime_type == "openclaw":
                    assert current.cancel_confirmed is True
            assert db.get(Task, task.id).status == "in_progress", (
                "telemetry cannot change business state"
            )
    finally:
        # Restore the real transport before cleanup; release the bounded fixture
        # even if the native runtime cannot cancel an in-flight Provider request.
        model_relay.release.touch()
        await adapter.cancel_task(current)
        if current.background is not None:
            await asyncio.gather(current.background, return_exceptions=True)
