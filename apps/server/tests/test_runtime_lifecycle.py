"""Runtime lifecycle with a fake DockerService (v0.2).

create → start → crash-detect → restart → destroy, all without a real daemon.
"""

import pytest

from app.core.config import settings
from app.models.enums import DeploymentMode, ProviderType, RuntimeInstanceStatus, RuntimeType
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager.docker_manager import (
    DockerRuntimeInstanceManager,
    RuntimeManagerError,
    runtime_user_ids,
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
        workspace_path=f"{settings.workspace_root}/{slug}",
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
    assert container["environment"]["PUID"] == str(runtime_user_ids("hermes")[0])
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


async def test_runtime_intent_fills_an_existing_empty_instance(
    db, manager, fake_docker, docker_employee, monkeypatch
):
    from sqlalchemy import select

    from app.models.lifecycle import ProvisioningStep
    from app.runtimes.manager import docker_manager
    from app.work import readiness, runtime_provisioning

    monkeypatch.setattr(docker_manager, "_manager", manager)
    docker_employee.runtime_type = "hermes"
    instance = runtime_repo.create_instance(
        db,
        employee_id=docker_employee.id,
        runtime_type="hermes",
        deployment_mode="docker",
        workspace_path=docker_employee.workspace_path,
        status="created",
    )
    db.commit()
    queued = readiness.orchestrate(
        db, docker_employee, policy=readiness.RuntimePolicy("hermes", "docker"), commit=True
    )
    assert await runtime_provisioning.run_job(queued.job_id)
    db.refresh(instance)
    runtime_step = db.scalar(
        select(ProvisioningStep).where(
            ProvisioningStep.job_id == queued.job_id,
            ProvisioningStep.resource_type == "runtime",
        )
    )
    assert runtime_step.status == "done", runtime_step.result_json
    assert instance.container_id in fake_docker.containers, (
        "successful runtime step has no container"
    )
    assert instance.status == "running"


async def test_concurrent_ensure_uses_one_row_container_and_workspace(
    db, manager, fake_docker, docker_employee
):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import select

    from app.models.runtime import RuntimeInstance

    barrier = Barrier(2)

    def provision():
        barrier.wait(timeout=5)
        return asyncio.run(
            manager.ensure_instance(
                docker_employee.id, runtime_type="hermes", deployment_mode="docker"
            )
        )

    def race():
        with ThreadPoolExecutor(max_workers=2) as threads:
            futures = [threads.submit(provision) for _ in range(2)]
            return [future.result(timeout=15) for future in futures]

    instances = await asyncio.to_thread(race)
    assert instances[0].id == instances[1].id
    assert instances[0].container_id == instances[1].container_id
    assert instances[0].workspace_path == docker_employee.workspace_path
    assert len(fake_docker.containers) == 1
    assert (
        len(
            list(
                db.scalars(
                    select(RuntimeInstance).where(RuntimeInstance.employee_id == docker_employee.id)
                )
            )
        )
        == 1
    )
    assert runtime_repo.get_instance_for_employee(db, docker_employee.id).provisioning_token is None


async def test_healthy_ensure_does_not_restart_recreate_or_overwrite_runtime_files(
    db, manager, fake_docker, docker_employee, monkeypatch
):
    from pathlib import Path

    instance = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    profile = Path(instance.data_path) / "runtime" / "hermes" / "eidolon" / "behavior.md"
    profile.write_text("runtime-owned current file")
    row = runtime_repo.get_instance_for_employee(db, docker_employee.id)
    row.metadata_json = {**row.metadata_json, "behavior_revision": "previously-applied"}
    db.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("healthy environment must not be recreated or restarted")

    for operation in (
        "create_container",
        "start_container",
        "restart_container",
        "remove_container",
    ):
        monkeypatch.setattr(fake_docker, operation, forbidden)
    again = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    assert again.id == instance.id and again.container_id == instance.container_id
    assert profile.read_text() == "runtime-owned current file"
    assert again.metadata_json["behavior_revision"] == "previously-applied"


async def test_interrupted_container_writeback_is_adopted_without_a_second_create(
    db, manager, fake_docker, docker_employee, monkeypatch
):
    from app.runtimes.manager import ensure

    class ProcessInterrupted(BaseException):
        pass

    save = ensure._save

    def interrupted(*args, **fields):
        if "container_id" in fields:
            raise ProcessInterrupted()
        return save(*args, **fields)

    monkeypatch.setattr(ensure, "_save", interrupted)
    with pytest.raises(ProcessInterrupted):
        await manager.ensure_instance(
            docker_employee.id, runtime_type="hermes", deployment_mode="docker"
        )
    assert len(fake_docker.containers) == 1
    original_id = next(iter(fake_docker.containers))
    row = runtime_repo.get_instance_for_employee(db, docker_employee.id)
    assert row.container_id is None and row.resource_key and row.metadata_json["api_key_ref"]
    monkeypatch.setattr(ensure, "_save", save)
    again = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    assert again.container_id == original_id
    assert len(fake_docker.containers) == 1


async def test_docker_create_runs_without_a_database_write_lock(
    db, manager, fake_docker, docker_employee, monkeypatch
):
    from app.core.database import SessionLocal

    create = fake_docker.create_container

    def independent_write(**kwargs):
        with SessionLocal() as other:
            row = runtime_repo.get_instance_for_employee(other, docker_employee.id)
            assert row.resource_key and row.provisioning_token and row.metadata_json["api_key_ref"]
            other.get(Employee, docker_employee.id).title = "written during Docker I/O"
            other.commit()
        return create(**kwargs)

    monkeypatch.setattr(fake_docker, "create_container", independent_write)
    await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    db.refresh(docker_employee)
    assert docker_employee.title == "written during Docker I/O"


async def test_create_does_not_commit_the_callers_changes(
    db, manager, fake_docker, docker_employee
):
    from app.core.database import SessionLocal

    docker_employee.title = "uncommitted"
    with pytest.raises(RuntimeManagerError, match="commit caller changes"):
        await manager.create_instance(
            db, docker_employee, runtime_type="hermes", deployment_mode="docker"
        )
    with SessionLocal() as other:
        assert other.get(Employee, docker_employee.id).title != "uncommitted"
    assert fake_docker.containers == {}


@pytest.mark.parametrize("sdk_fails", [False, True])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancellation_drains_docker_call_before_releasing_instance_claim(
    db, manager, fake_docker, docker_employee, monkeypatch, sdk_fails, cancel_count
):
    import asyncio
    from threading import Event

    create = fake_docker.create_container
    started, finish = Event(), Event()

    def paused_create(**kwargs):
        started.set()
        assert finish.wait(5)
        if sdk_fails:
            raise RuntimeError("SDK failed while cancellation was pending")
        return create(**kwargs)

    monkeypatch.setattr(fake_docker, "create_container", paused_create)
    task = asyncio.create_task(
        manager.ensure_instance(docker_employee.id, runtime_type="hermes", deployment_mode="docker")
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        for _ in range(cancel_count):
            task.cancel()
            # The SDK call is held by an event, so cancellation must stay pending,
            # including when shutdown cancels an already timed-out operation.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
        db.expire_all()
        assert runtime_repo.get_instance_for_employee(db, docker_employee.id).provisioning_token
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    db.expire_all()
    assert runtime_repo.get_instance_for_employee(db, docker_employee.id).provisioning_token is None
    monkeypatch.setattr(fake_docker, "create_container", create)
    recovered = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    assert recovered.container_id in fake_docker.containers
    assert len(fake_docker.containers) == 1


def test_runtime_claim_expiry_and_stale_owner_fencing(db, manager, docker_employee, monkeypatch):
    from datetime import timedelta

    from app.core.database import SessionLocal
    from app.runtimes.manager import ensure

    def claim(token):
        return ensure._claim(
            SessionLocal, manager, docker_employee.id, "hermes", "docker", token, 2.0, 4096
        )

    first = claim("first-owner")
    assert first is not None
    assert claim("competing-owner") is None
    later = ensure.utcnow() + timedelta(hours=1)
    monkeypatch.setattr(ensure, "utcnow", lambda: later)
    replacement = claim("replacement-owner")
    assert replacement.id == first.id
    with pytest.raises(RuntimeManagerError, match="superseded"):
        ensure._save(SessionLocal, first.id, "first-owner", status="error")
    ensure._release(SessionLocal, docker_employee.id, "first-owner")
    db.expire_all()
    row = runtime_repo.get_instance_for_employee(db, docker_employee.id)
    assert row.provisioning_token == "replacement-owner" and row.status != "error"


async def test_runtime_backup_includes_the_employee_workspace(manager, docker_employee):
    import tarfile
    from pathlib import Path

    instance = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    (Path(instance.workspace_path) / "retained.txt").write_text("work survives replacement")
    archive = manager.backup_data_path(instance)
    with tarfile.open(archive) as backup:
        entry = backup.extractfile(f"{Path(instance.data_path).name}/workspace/retained.txt")
        assert entry.read() == b"work survives replacement"


async def test_unhealthy_ensure_has_a_persisted_recovery_cooldown(
    manager, fake_docker, docker_employee, monkeypatch
):
    instance = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    fake_docker.containers[instance.container_id]["health"] = "unhealthy"
    attempts = []

    def remains_unhealthy(container_id):
        attempts.append(container_id)
        return True

    async def unavailable(*args):
        raise RuntimeManagerError("health check still fails")

    monkeypatch.setattr(fake_docker, "restart_container", remains_unhealthy)
    monkeypatch.setattr(manager, "_wait_healthy", unavailable)
    with pytest.raises(RuntimeManagerError, match="health check still fails"):
        await manager.ensure_instance(
            docker_employee.id, runtime_type="hermes", deployment_mode="docker"
        )
    with pytest.raises(RuntimeManagerError, match="cooldown"):
        await manager.ensure_instance(
            docker_employee.id, runtime_type="hermes", deployment_mode="docker"
        )
    assert attempts == [instance.container_id]


@pytest.mark.parametrize("mismatch", ["ownership", "mount"])
async def test_ensure_refuses_mismatched_external_resource_without_touching_it(
    manager, fake_docker, docker_employee, monkeypatch, mismatch
):
    instance = await manager.ensure_instance(
        docker_employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    container = fake_docker.containers[instance.container_id]
    if mismatch == "ownership":
        container["labels"]["eidolon.resource_key"] = "another-owner"
    else:
        container["volumes"] = {}

    def forbidden(*args, **kwargs):
        pytest.fail("mismatched external resources must be preserved")

    for operation in (
        "create_container",
        "start_container",
        "restart_container",
        "remove_container",
    ):
        monkeypatch.setattr(fake_docker, operation, forbidden)
    with pytest.raises(RuntimeManagerError, match=mismatch):
        await manager.ensure_instance(
            docker_employee.id, runtime_type="hermes", deployment_mode="docker"
        )
    assert instance.container_id in fake_docker.containers


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
