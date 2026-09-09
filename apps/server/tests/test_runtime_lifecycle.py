"""Runtime lifecycle with a fake DockerService (v0.2).

create → start → crash-detect → restart → destroy, all without a real daemon.
"""

import pytest

from app.models.enums import DeploymentMode, ProviderType, RuntimeInstanceStatus, RuntimeType
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager.docker_manager import (
    DockerRuntimeInstanceManager,
    RuntimeManagerError,
)
from app.schemas.provider import EmployeeProviderCreate
from app.schemas.runtime import EmployeeRuntimeCreate
from app.services import runtimes as runtime_service
from app.services.providers import provider_service


@pytest.fixture()
def manager(fake_docker):
    return DockerRuntimeInstanceManager(docker=fake_docker)


@pytest.fixture()
def docker_employee(db, default_company_id) -> Employee:
    import uuid

    slug = f"runtime-tester-{uuid.uuid4().hex[:8]}"
    employee = org_repo.create_employee(
        db,
        company_id=default_company_id,
        department_id=None,
        name="Runtime Tester",
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


async def test_create_and_start_docker_instance(db, manager, fake_docker, docker_employee):
    instance = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    assert instance.status == RuntimeInstanceStatus.running.value
    assert instance.health_status == "healthy"
    assert instance.container_name.startswith(f"eidolon-hermes-{docker_employee.slug}-")
    assert instance.internal_host == instance.container_name
    assert instance.internal_port == 8642
    assert instance.runtime_version == "fake-runtime 1.0.0"
    assert instance.image_digest

    container = fake_docker.containers[instance.container_id]
    # hard rules: no published ports, env carries the control-plane key, network attached
    assert container["environment"]["API_SERVER_ENABLED"] == "true"
    assert container["environment"]["API_SERVER_HOST"] == "0.0.0.0"
    assert container["environment"]["PUID"] == "10000"
    assert container["network"] is not None

    # duplicate create is rejected (one instance per employee)
    with pytest.raises(RuntimeManagerError):
        await manager.create_instance(
            db, docker_employee, runtime_type="hermes", deployment_mode="docker"
        )


async def test_healthcheck_detects_crash_and_restart(db, manager, fake_docker, docker_employee):
    instance = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    fake_docker.crash(instance.container_id)

    await manager.healthcheck_once()
    db.refresh(instance)
    assert instance.status == RuntimeInstanceStatus.crashed.value
    assert instance.health_status == "unhealthy"

    # crash event persisted
    from sqlalchemy import select

    from app.models.event import Event

    events = list(
        db.scalars(
            select(Event).where(
                Event.type == "runtime.crashed",
                Event.actor_employee_id == docker_employee.id,
            )
        )
    )
    assert events, "expected runtime.crashed event"

    # restart recovers
    await manager.start_instance(db, instance)
    db.refresh(instance)
    assert instance.status == RuntimeInstanceStatus.running.value


async def test_unhealthy_transition(db, manager, fake_docker, docker_employee):
    instance = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    fake_docker.containers[instance.container_id]["health"] = "unhealthy"
    await manager.healthcheck_once()
    db.refresh(instance)
    assert instance.status == RuntimeInstanceStatus.unhealthy.value


async def test_destroy_keeps_data_dirs(db, manager, fake_docker, docker_employee):
    from pathlib import Path

    instance = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    data_path = Path(instance.data_path)
    assert data_path.exists()
    container_id = instance.container_id

    await manager.destroy_instance(db, instance)
    assert container_id not in fake_docker.containers
    assert runtime_repo.get_instance_for_employee(db, docker_employee.id) is None
    assert data_path.exists(), "data dirs must survive destroy"


async def test_mock_instance_lifecycle(db, manager, docker_employee):
    instance = await manager.create_instance(
        db, docker_employee, runtime_type="mock", deployment_mode="mock"
    )
    assert instance.deployment_mode == DeploymentMode.mock.value
    assert instance.status == RuntimeInstanceStatus.running.value
    assert instance.container_id is None
    logs = await manager.get_logs(instance)
    assert logs and "mock" in logs[0]


async def test_change_runtime_type_from_mock_to_docker_keeps_data(
    db, manager, fake_docker, docker_employee
):
    """Mock 入职后必须能换成真实运行时：拆旧开新，数据目录（身份/记忆）保留。"""
    mock = await manager.create_instance(
        db, docker_employee, runtime_type="mock", deployment_mode="mock"
    )
    data_path = mock.data_path
    provider = provider_service.create_for_employee(
        db,
        docker_employee.id,
        EmployeeProviderCreate(
            name="switch-provider",
            provider_type=ProviderType.custom,
            base_url="https://api.example.com/v1",
            api_key="sk-switch-test-key-123456",
            model="gpt-4o-mini",
        ),
    )

    switched = await runtime_service.change_employee_runtime(
        db,
        docker_employee,
        EmployeeRuntimeCreate(
            runtime_type=RuntimeType.hermes,
            deployment_mode="docker",
            provider_id=provider.id,
            model="gpt-4o-mini",
        ),
        manager,
    )

    assert switched.runtime_type == RuntimeType.hermes.value
    assert switched.deployment_mode == DeploymentMode.docker.value
    assert switched.data_path == data_path, "数据目录必须保留"
    raw = runtime_repo.get_instance_for_employee(db, docker_employee.id)
    assert raw is not None and raw.container_id in fake_docker.containers
    db.refresh(docker_employee)
    assert docker_employee.runtime_type == RuntimeType.hermes.value


async def test_change_runtime_type_back_to_mock_removes_container(
    db, manager, fake_docker, docker_employee
):
    original = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    container_id = original.container_id
    data_path = original.data_path

    switched = await runtime_service.change_employee_runtime(
        db,
        docker_employee,
        EmployeeRuntimeCreate(runtime_type=RuntimeType.mock, deployment_mode="mock"),
        manager,
    )

    assert switched.runtime_type == RuntimeType.mock.value
    assert switched.data_path == data_path
    raw = runtime_repo.get_instance_for_employee(db, docker_employee.id)
    assert raw is not None and raw.container_id is None
    assert container_id not in fake_docker.containers
    db.refresh(docker_employee)
    assert docker_employee.runtime_type == RuntimeType.mock.value


async def test_logs_are_redacted(db, manager, fake_docker, docker_employee):
    instance = await manager.create_instance(
        db, docker_employee, runtime_type="hermes", deployment_mode="docker"
    )
    secret = "sk-container-log-secret-5555"
    from app.core import redaction

    redaction.register_secret(secret)
    try:
        fake_docker.containers[instance.container_id]["logs"].append(f"using key {secret}")
        logs = await manager.get_logs(instance)
        assert any(secret not in line for line in logs)
        assert not any(secret in line for line in logs)
    finally:
        redaction.unregister_secret(secret)
