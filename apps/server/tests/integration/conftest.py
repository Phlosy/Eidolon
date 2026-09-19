"""Integration test fixtures: real Docker daemon, isolated sqlite + data root.

These tests only run via `make test-integration` (pytest marker ``integration``;
the default ``make test`` excludes them via addopts).
"""

import asyncio
import json
import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker.errors import NotFound
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Base
from app.models.organization import Company, Employee
from app.models.provider import Provider
from app.providers.secrets.store import get_secret_store
from app.runtimes.docker.service import DockerService
from app.runtimes.openclaw.pairing import pair_controller
from app.runtimes.probe import probe_control


@pytest.fixture(scope="session")
def docker_service():
    service = DockerService()
    if not service.ping():
        pytest.skip("docker daemon not available")
    return service


@pytest.fixture()
def idb(tmp_path, monkeypatch, cleanup_containers):
    """Isolated sqlite DB + data root for one integration test."""
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspaces"))
    engine = create_engine(
        f"sqlite:///{tmp_path}/integration.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # the manager's healthcheck sweep opens its own sessions; the event bus
    # persists events with its own session factory too
    monkeypatch.setattr("app.runtimes.manager.docker_manager.SessionLocal", factory)
    monkeypatch.setattr("app.events.bus.SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def make_employee(idb):
    def _make():
        slug = f"it-{uuid.uuid4().hex[:8]}"
        with idb() as db:
            company = Company(
                name="IT", slug=f"it-{slug}", description="", industry="", settings={}
            )
            db.add(company)
            db.flush()
            employee = Employee(
                company_id=company.id,
                department_id=None,
                name=f"IT {slug}",
                slug=slug,
                role="engineer",
                title="",
                avatar="",
                status="idle",
                runtime_type="mock",
                runtime_config={},
                workspace_path=f"{settings.workspace_root}/{slug}",
                memory_namespace=f"emp_{slug}",
            )
            db.add(employee)
            db.commit()
            db.refresh(employee)
            return employee

    return _make


@pytest.fixture()
def runtime_network(docker_service, monkeypatch):
    name = f"eidolon-it-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "runtime_network", name)
    yield name
    try:
        docker_service._get_client().networks.get(name).remove()
    except NotFound:
        pass


@pytest.fixture()
def cleanup_containers(docker_service, runtime_network, monkeypatch):
    """Collect container ids and force-remove them after the test."""
    created: list[str] = []
    create = docker_service.create_container

    def tracked_create(**kwargs):
        container_id = create(**kwargs)
        if container_id is not None:
            created.append(container_id)
        return container_id

    # Track before a later manager failure can hide the returned container id.
    monkeypatch.setattr(docker_service, "create_container", tracked_create)
    yield created
    for container_id in dict.fromkeys(created):
        docker_service.remove_container(container_id)


@pytest.fixture()
def model_relay(docker_service, runtime_network, cleanup_containers, tmp_path):
    """An explicit paid gate: one tiny request through a disposable relay.

    The agent receives only the relay token. It cannot spend another request by
    retrying or falling back, even if a runtime ignores max_tokens or times out.
    """
    if os.environ.get("EIDOLON_REAL_MODEL_TESTS") != "1":
        pytest.skip("real model calls require EIDOLON_REAL_MODEL_TESTS=1")
    key = os.environ.get("EIDOLON_TEST_PROVIDER_KEY")
    if not key:
        pytest.skip("real model calls require EIDOLON_TEST_PROVIDER_KEY")
    budget = Decimal(os.environ["EIDOLON_TEST_MODEL_BUDGET_USD"])
    assert 0 < budget <= Decimal("0.05")
    valid_until = datetime.fromisoformat(os.environ["EIDOLON_TEST_PRICING_VALID_UNTIL"])
    assert valid_until.tzinfo and datetime.now(UTC) < valid_until <= datetime.now(UTC) + timedelta(
        days=1
    )
    image = docker_service.inspect_image("python:3.12-slim")
    if image is None:
        pytest.skip("budget relay requires local python:3.12-slim image")
    evidence = tmp_path / "model-evidence"
    evidence.mkdir(mode=0o700)
    name = f"eidolon-it-budget-{uuid.uuid4().hex[:12]}"
    token = secrets.token_hex(24)
    assert docker_service.ensure_network(runtime_network)
    cid = docker_service.create_container(
        name=name,
        image=image["Id"],
        network=runtime_network,
        environment={"REAL_PROVIDER_KEY": key, "RELAY_TOKEN": token, "MAX_COST_USD": str(budget)},
        volumes={
            str(Path(__file__).with_name("model_budget_proxy.py")): {
                "bind": "/relay.py",
                "mode": "ro",
            },
            str(evidence): {"bind": "/evidence", "mode": "rw"},
        },
        restart_policy="no",
        cpu_limit=0.5,
        memory_limit_mb=128,
        user=f"{os.getuid()}:{os.getgid()}",
        command=["python", "/relay.py"],
        labels={"eidolon.test": "model-budget-relay"},
    )
    assert cid and docker_service.start_container(cid)
    receipt = evidence / "receipt.json"
    deadline = time.monotonic() + 10
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert receipt.exists(), "budget relay did not start"
    relay = SimpleNamespace(
        url=f"http://{name}:8642/v1", token=token, model="deepseek-flash", receipt=receipt
    )
    yield relay
    print("MODEL_BUDGET_RECEIPT=" + json.dumps(json.loads(receipt.read_text()), sort_keys=True))


@pytest.fixture()
def wait_control():
    async def wait(manager, instance, token, device_key=None):
        base_url = f"http://{instance.internal_host}:{instance.internal_port}"
        async with asyncio.timeout(45):
            while True:
                facts = await probe_control(
                    instance.runtime_type, base_url, token, device_key=device_key
                )
                if facts["control"]["reason"] == "pairing_required" and device_key:
                    await pair_controller(manager._docker, instance, token, device_key)
                    continue
                if all(value["state"] == "passed" for value in facts.values()):
                    return facts
                await asyncio.sleep(0.5)

    return wait


@pytest.fixture()
def create_model_runtime(idb, make_employee, model_relay, wait_control):
    async def create(runtime_type, manager):
        employee = make_employee()
        with idb() as db:
            provider = Provider(
                company_id=employee.company_id,
                name="budgeted-real-model",
                provider_type="custom",
                base_url=model_relay.url,
                scope="company",
                enabled=True,
                credential_ref=get_secret_store().store(db, model_relay.token),
                metadata_json={},
            )
            db.add(provider)
            db.commit()
            instance = await manager.create_instance(
                db,
                employee,
                runtime_type=runtime_type,
                deployment_mode="docker",
                provider=provider,
                model=model_relay.model,
            )
            token = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
            device = manager._secrets.retrieve(db, instance.metadata_json.get("device_key_ref"))
        facts = await wait_control(manager, instance, token, device)
        return SimpleNamespace(
            instance=instance,
            token=token,
            device=device,
            facts=facts,
            base_url=f"http://{instance.internal_host}:{instance.internal_port}",
        )

    return create


@pytest.fixture()
def record_model_evidence(model_relay):
    def record(runtime, *, output_matches):
        receipt = json.loads(model_relay.receipt.read_text())
        assert receipt["attempts"] == 1
        assert receipt["http_status"] == 200
        assert receipt["usage"]["output_tokens"] > 0
        assert Decimal(receipt["cost_usd"]) <= Decimal(receipt["max_cost_usd"])
        assert output_matches, "runtime did not return the actual minimal model response"
        print(
            "REAL_MODEL_EVIDENCE="
            + json.dumps(
                {
                    "runtime": runtime.instance.runtime_type,
                    "image_id": runtime.instance.image_digest,
                    "version": runtime.instance.runtime_version,
                    "container_id": runtime.instance.container_id,
                    "authenticated_control": True,
                    "capabilities": runtime.facts["capabilities"],
                    "response_matches": output_matches,
                    **receipt,
                },
                sort_keys=True,
            )
        )

    return record
