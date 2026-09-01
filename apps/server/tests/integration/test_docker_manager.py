"""Real-Docker integration tests for DockerRuntimeInstanceManager (v0.2).

Uses the pre-pulled ``python:3.12-slim`` image (no runtime image download
needed): two "employee" containers write distinct files into their bind-mounted
runtime dirs, proving per-employee workspace isolation.
"""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.enums import RuntimeInstanceStatus
from app.models.event import Event
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


@pytest.fixture()
def manager(docker_service, monkeypatch):
    monkeypatch.setattr(settings, "hermes_image", IMAGE)
    if docker_service.inspect_image(IMAGE) is None and docker_service.pull_image(IMAGE) is None:
        pytest.skip(f"cannot pull {IMAGE}")
    return DockerRuntimeInstanceManager(
        docker=docker_service,
        command_overrides={"hermes": ["sleep", "3600"]},
        version_commands={"hermes": ["python3", "--version"]},
    )


async def test_manager_lifecycle_real_docker(idb, make_employee, manager, cleanup_containers):
    employee = make_employee()
    with idb() as db:
        instance = await manager.create_instance(
            db, employee, runtime_type="hermes", deployment_mode="docker"
        )
        cleanup_containers.append(instance.container_id)
        assert instance.status == RuntimeInstanceStatus.running.value
        assert instance.runtime_version and "Python 3.12" in instance.runtime_version
        assert instance.container_name.startswith(f"eidolon-hermes-{employee.slug}-")

        state = await manager.inspect_instance(instance)
        assert state["container"]["status"] == "running"

        await manager.stop_instance(db, instance)
        assert instance.status == RuntimeInstanceStatus.stopped.value

        await manager.start_instance(db, instance)
        assert instance.status == RuntimeInstanceStatus.running.value

        await manager.destroy_instance(db, instance)
        assert runtime_repo.get_instance_for_employee(db, employee.id) is None


async def test_crash_detection_real_docker(idb, make_employee, manager, cleanup_containers):
    employee = make_employee()
    with idb() as db:
        instance = await manager.create_instance(
            db, employee, runtime_type="hermes", deployment_mode="docker"
        )
        cleanup_containers.append(instance.container_id)
        # simulate a crash: kill the container behind the manager's back
        docker = manager._docker
        assert docker.stop_container(instance.container_id, timeout=1)
        await manager.healthcheck_once()
        db.refresh(instance)
        assert instance.status == RuntimeInstanceStatus.crashed.value
        events = list(db.scalars(select(Event).where(Event.type == "runtime.crashed")))
        assert any(e.payload.get("instance_id") == instance.id for e in events)


async def test_workspace_isolation_between_employees(
    idb, make_employee, manager, cleanup_containers
):
    emp_a, emp_b = make_employee(), make_employee()
    with idb() as db:
        inst_a = await manager.create_instance(
            db, emp_a, runtime_type="hermes", deployment_mode="docker"
        )
        inst_b = await manager.create_instance(
            db, emp_b, runtime_type="hermes", deployment_mode="docker"
        )
        cleanup_containers.extend([inst_a.container_id, inst_b.container_id])
        assert inst_a.container_name != inst_b.container_name
        assert inst_a.data_path != inst_b.data_path

        # each container writes a file into its own /opt/data (bind-mounted)
        for instance, marker in ((inst_a, "alpha"), (inst_b, "bravo")):
            code, out = manager._docker.exec_run(
                instance.container_id, ["sh", "-c", f"echo {marker} > /opt/data/marker.txt"]
            )
            assert code == 0, out

        from pathlib import Path

        file_a = Path(inst_a.data_path) / "runtime" / "hermes" / "marker.txt"
        file_b = Path(inst_b.data_path) / "runtime" / "hermes" / "marker.txt"
        assert file_a.read_text().strip() == "alpha"
        assert file_b.read_text().strip() == "bravo"

        await manager.destroy_instance(db, inst_a)
        await manager.destroy_instance(db, inst_b)
        # data dirs survive container destruction
        assert file_a.exists() and file_b.exists()
