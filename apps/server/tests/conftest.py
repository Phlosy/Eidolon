"""Test fixtures: temp sqlite file + fast mock runtime. See docs/architecture.md §12."""

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="eidolon-test-")
os.environ["EIDOLON_DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ["EIDOLON_WORKSPACE_ROOT"] = f"{_TMP_DIR}/workspaces"
os.environ["EIDOLON_DATA_ROOT"] = f"{_TMP_DIR}/data"
os.environ["EIDOLON_MOCK_TASK_SECONDS"] = "0.1"
os.environ["EIDOLON_LOG_LEVEL"] = "WARNING"
os.environ["EIDOLON_UPDATE_CHECK_ENABLED"] = "false"
os.environ["EIDOLON_SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories import organization as org_repo  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def employees_by_slug(client):
    employees = client.get("/api/v1/employees").json()
    return {e["slug"]: e for e in employees}


@pytest.fixture()
def default_company_id(db):
    company = org_repo.get_default_company(db)
    assert company is not None
    return company.id


class FakeDockerService:
    """In-memory DockerService stand-in for unit tests (no daemon needed)."""

    def __init__(self) -> None:
        self.containers: dict[str, dict] = {}
        self.images: dict[str, dict] = {}
        self.networks: set[str] = set()
        self.fail_healthcheck_images: set[str] = set()  # image -> recreate fails health
        self._seq = 0

    # -- availability / networks --
    def available(self) -> bool:
        return True

    def ping(self) -> bool:
        return True

    def ensure_network(self, name: str):
        self.networks.add(name)
        return name

    # -- images --
    def pull_image(self, image: str):
        self.images.setdefault(image, {"Id": f"sha256:{abs(hash(image)) & 0xFFFFFFFFFFFF:x}"})
        return {"id": self.images[image]["Id"], "tags": [image]}

    def inspect_image(self, image: str):
        return self.images.get(image)

    def get_registry_digest(self, image: str):
        return (self.images.get(image) or {}).get("registry_digest")

    # -- containers --
    def create_container(
        self,
        *,
        name,
        image,
        environment=None,
        volumes=None,
        network=None,
        cpu_limit=None,
        memory_limit_mb=None,
        restart_policy="unless-stopped",
        labels=None,
        command=None,
    ):
        self._seq += 1
        cid = f"fake{self._seq:012d}"
        self.containers[cid] = {
            "name": name,
            "image": image,
            "environment": dict(environment or {}),
            "volumes": dict(volumes or {}),
            "network": network,
            "labels": dict(labels or {}),
            "command": command,
            "state": "created",
            "health": "none",
            "logs": [],
        }
        return cid

    def start_container(self, container_id: str) -> bool:
        c = self.containers.get(container_id)
        if c is None:
            return False
        if c["image"] in self.fail_healthcheck_images:
            return False  # simulated broken image: container never starts
        c["state"] = "running"
        c["health"] = "healthy"
        return True

    def stop_container(self, container_id: str, timeout: int = 10) -> bool:
        c = self.containers.get(container_id)
        if c is None:
            return False
        c["state"] = "exited"
        return True

    def restart_container(self, container_id: str, timeout: int = 10) -> bool:
        return self.stop_container(container_id) and self.start_container(container_id)

    def remove_container(self, container_id: str, force: bool = True) -> bool:
        self.containers.pop(container_id, None)
        return True

    def inspect_container(self, container_id: str):
        c = self.containers.get(container_id)
        if c is None:
            return None
        return {
            "Id": container_id,
            "Name": f"/{c['name']}",
            "Config": {"Image": c["image"]},
            "State": {"Status": c["state"], "Health": {"Status": c["health"]}},
        }

    def healthcheck_container(self, container_id: str):
        c = self.containers.get(container_id)
        if c is None:
            return None
        return c["state"], c["health"]

    def get_logs(self, container_id: str, tail: int = 200) -> list[str]:
        return list(self.containers.get(container_id, {}).get("logs", []))[-tail:]

    def stream_logs(self, container_id: str, follow: bool = False):
        yield from self.get_logs(container_id)

    def exec_run(self, container_id: str, cmd: list[str]):
        return 0, "fake-runtime 1.0.0"

    # -- test helpers --
    def crash(self, container_id: str) -> None:
        self.containers[container_id]["state"] = "exited"
        self.containers[container_id]["health"] = "none"


@pytest.fixture()
def fake_docker():
    return FakeDockerService()
