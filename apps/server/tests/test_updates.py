"""Runtime update detection + managed update + rollback with fakes (v0.2)."""

import pytest

from app.models.enums import ImageUpdateStatus
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.updates.base import RuntimeUpdateProvider
from app.runtimes.updates.service import (
    RuntimeUpdateError,
    RuntimeUpdateService,
    compatibility_for,
)


class FakeUpdateProvider(RuntimeUpdateProvider):
    def __init__(self, installed_digest="sha256:old", latest_digest="sha256:new", pull_ok=True):
        self.installed_digest = installed_digest
        self.latest_digest = latest_digest
        self.pull_ok = pull_ok
        self.pulled: list[str] = []

    def get_installed(self, image: str):
        return {"digest": self.installed_digest, "version": "2026.8.31"}

    def get_latest_digest(self, image: str):
        return self.latest_digest

    def pull(self, image: str) -> bool:
        self.pulled.append(image)
        return self.pull_ok


@pytest.fixture()
def update_employee(db, default_company_id) -> Employee:
    import uuid

    slug = f"update-tester-{uuid.uuid4().hex[:8]}"
    employee = org_repo.create_employee(
        db,
        company_id=default_company_id,
        department_id=None,
        name="Update Tester",
        slug=slug,
        role="engineer",
        title="",
        avatar="",
        status="idle",
        runtime_type="mock",
        runtime_config={},
        workspace_path=f"/tmp/eidolon-test-{slug}",
        memory_namespace=f"emp_{slug}",
    )
    db.commit()
    return employee


@pytest.fixture()
def setup(fake_docker, db):
    manager = DockerRuntimeInstanceManager(docker=fake_docker)
    provider = FakeUpdateProvider()
    service = RuntimeUpdateService(
        provider=provider, manager=manager, docker_available=lambda: True
    )
    return manager, provider, service


def test_compatibility_check():
    assert compatibility_for("hermes", "2026.8.31") == "verified"
    assert compatibility_for("hermes", "2026.7.0") == "unverified"  # below tested_min 2026.8.0
    assert compatibility_for("hermes", None) == "unknown"
    assert compatibility_for("hermes", "not-a-version") == "unknown"


async def test_check_updates_detects_update(db, setup, default_company_id):
    manager, provider, service = setup
    rows = await service.check_updates()
    by_type = {r.runtime_type: r for r in rows}
    hermes = by_type["hermes"]
    assert hermes.update_available is True
    assert hermes.update_status == ImageUpdateStatus.available.value
    assert hermes.latest_digest == "sha256:new"
    assert hermes.compatibility_status == "verified"  # installed 2026.8.31 in tested range


async def test_check_updates_no_update_when_digests_match(db, setup):
    manager, provider, service = setup
    provider.latest_digest = "sha256:old"
    rows = await service.check_updates()
    hermes = next(r for r in rows if r.runtime_type == "hermes")
    assert hermes.update_available is False
    assert hermes.update_status == ImageUpdateStatus.idle.value


async def test_check_updates_graceful_without_docker(db, fake_docker):
    manager = DockerRuntimeInstanceManager(docker=fake_docker)
    service = RuntimeUpdateService(
        provider=FakeUpdateProvider(), manager=manager, docker_available=lambda: False
    )
    rows = await service.check_updates()  # must not raise
    assert rows  # rows still ensured
    assert all(r.update_status == ImageUpdateStatus.idle.value for r in rows)


async def test_managed_update_success(db, setup, fake_docker, update_employee):
    manager, provider, service = setup
    instance = await manager.create_instance(
        db, update_employee, runtime_type="hermes", deployment_mode="docker"
    )
    old_container_id = instance.container_id

    row = await service.managed_update("hermes")
    assert row.update_status == ImageUpdateStatus.completed.value
    assert row.update_available is False
    assert provider.pulled == ["nousresearch/hermes-agent:latest"]
    db.refresh(instance)
    assert instance.container_id != old_container_id  # recreated
    assert instance.status == "running"


async def test_managed_update_failure_rolls_back(db, setup, fake_docker, update_employee):
    manager, provider, service = setup
    instance = await manager.create_instance(
        db, update_employee, runtime_type="hermes", deployment_mode="docker"
    )
    old_digest = instance.image_digest

    # after the pull, the "new" image never becomes healthy → update must roll back
    fake_docker.fail_healthcheck_images.add("nousresearch/hermes-agent:latest")
    with pytest.raises(RuntimeUpdateError):
        await service.managed_update("hermes")

    row = runtime_repo.get_image(db, "hermes")
    assert row.update_status == ImageUpdateStatus.rolled_back.value

    # rollback recreated the container from the previous image digest
    db.refresh(instance)
    container = fake_docker.containers[instance.container_id]
    assert container["image"] == old_digest

    # rollback event persisted
    from sqlalchemy import select

    from app.models.event import Event

    events = list(db.scalars(select(Event).where(Event.type == "runtime.update_rolled_back")))
    assert events


async def test_managed_update_refused_when_employee_busy(db, setup, update_employee):
    manager, provider, service = setup
    await manager.create_instance(
        db, update_employee, runtime_type="hermes", deployment_mode="docker"
    )
    update_employee.status = "working"
    db.commit()
    with pytest.raises(RuntimeUpdateError, match="busy"):
        await service.managed_update("hermes")
