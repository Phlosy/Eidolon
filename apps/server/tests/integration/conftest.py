"""Integration test fixtures: real Docker daemon, isolated sqlite + data root.

These tests only run via `make test-integration` (pytest marker ``integration``;
the default ``make test`` excludes them via addopts).
"""

import uuid

import pytest
from docker.errors import NotFound
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Base
from app.models.organization import Company, Employee
from app.runtimes.docker.service import DockerService


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
