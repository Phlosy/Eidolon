"""Real-Docker integration tests for DockerRuntimeInstanceManager (v0.2).

Uses the pre-pulled ``python:3.12-slim`` image (no runtime image download
needed): two "employee" containers write distinct files into their actual
workspaces, proving per-employee isolation.
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.base import utcnow
from app.models.enums import RuntimeInstanceStatus
from app.models.event import Event
from app.repositories import runtimes as runtime_repo
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager, runtime_user_ids

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


@pytest.fixture()
def manager(docker_service, monkeypatch):
    if docker_service.inspect_image(IMAGE) is None and docker_service.pull_image(IMAGE) is None:
        pytest.skip(f"cannot pull {IMAGE}")
    monkeypatch.setattr(settings, "hermes_image", docker_service.inspect_image(IMAGE)["Id"])
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
            uid, gid = runtime_user_ids("hermes")
            code, out = manager._docker.exec_run(
                instance.container_id,
                [
                    "python3",
                    "-c",
                    "import pathlib,sys;"
                    "pathlib.Path('/opt/data/workspace/marker.txt').write_text(sys.argv[1])",
                    marker,
                ],
                user=f"{uid}:{gid}",
            )
            assert code == 0, out

        file_a = Path(inst_a.workspace_path) / "marker.txt"
        file_b = Path(inst_b.workspace_path) / "marker.txt"
        assert file_a.read_text().strip() == "alpha"
        assert file_b.read_text().strip() == "bravo"

        await manager.destroy_instance(db, inst_a)
        await manager.destroy_instance(db, inst_b)
        # data dirs survive container destruction
        assert file_a.exists() and file_b.exists()


async def test_process_exit_after_create_reuses_the_same_real_container(
    idb, make_employee, manager, cleanup_containers, tmp_path
):
    employee = make_employee()
    marker = tmp_path / "created-resource.json"
    script = """import asyncio,json,os
from pathlib import Path
from app.runtimes.manager import ensure
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager

save = ensure._save
def exit_before_writeback(factory, instance_id, token, **fields):
    if "container_id" in fields:
        Path(os.environ["TEST_CRASH_MARKER"]).write_text(json.dumps(fields))
        os._exit(23)
    return save(factory, instance_id, token, **fields)
ensure._save = exit_before_writeback
manager = DockerRuntimeInstanceManager(command_overrides={"hermes":["sleep","3600"]},
    version_commands={"hermes":["python3","--version"]})
asyncio.run(manager.ensure_instance(int(os.environ["TEST_EMPLOYEE_ID"]),
    runtime_type="hermes", deployment_mode="docker"))
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "EIDOLON_DATABASE_URL": str(idb.kw["bind"].url),
            "EIDOLON_DATA_ROOT": settings.data_root,
            "EIDOLON_WORKSPACE_ROOT": settings.workspace_root,
            "EIDOLON_RUNTIME_NETWORK": settings.runtime_network,
            "EIDOLON_HERMES_IMAGE": settings.hermes_image,
            "TEST_EMPLOYEE_ID": str(employee.id),
            "TEST_CRASH_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    with idb() as db:
        row = runtime_repo.get_instance_for_employee(db, employee.id)
        assert row is not None, result.stderr
        attrs = manager._docker.inspect_container(row.container_name)
        # The child created this exact resource key in our private database.
        if (
            attrs
            and attrs.get("Config", {}).get("Labels", {}).get("eidolon.resource_key")
            == row.resource_key
        ):
            cleanup_containers.append(attrs["Id"])
        assert result.returncode == 23, result.stdout + result.stderr
        created = json.loads(marker.read_text())
        assert row.container_id is None and row.provisioning_token
        assert attrs["Id"] == created["container_id"]
        row.provisioning_claimed_at = utcnow() - timedelta(hours=1)
        db.commit()
    recovered = await manager.ensure_instance(
        employee.id, runtime_type="hermes", deployment_mode="docker"
    )
    assert recovered.container_id == created["container_id"]
    assert recovered.status == "running"
    matches = manager._docker._get_client().containers.list(
        all=True, filters={"label": f"eidolon.resource_key={recovered.resource_key}"}
    )
    assert [container.id for container in matches] == [recovered.container_id]
    with idb() as db:
        assert runtime_repo.get_instance_for_employee(db, employee.id).provisioning_token is None


async def test_concurrent_ensure_reuses_one_real_container(
    idb, make_employee, manager, cleanup_containers
):
    employee = make_employee()
    first, second = await asyncio.gather(
        *(
            manager.ensure_instance(employee.id, runtime_type="hermes", deployment_mode="docker")
            for _ in range(2)
        )
    )
    assert first.id == second.id
    assert first.container_id == second.container_id
    assert first.workspace_path == second.workspace_path == employee.workspace_path
    matches = manager._docker._get_client().containers.list(
        all=True, filters={"label": f"eidolon.resource_key={first.resource_key}"}
    )
    assert [container.id for container in matches] == [first.container_id]
    with idb() as db:
        row = runtime_repo.get_instance_for_employee(db, employee.id)
        assert row.container_id == first.container_id and row.provisioning_token is None
