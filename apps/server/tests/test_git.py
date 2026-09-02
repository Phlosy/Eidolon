"""Git integration (v0.3 phase 2): connection CRUD + masking, probe, builtin Gitea."""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.models.provider import Secret
from app.services.git import GITEA_CONTAINER_NAME, GitService

TOKEN = "glpat-test-abcdef1234567890TOKEN"
TOKEN2 = "glpat-rotated-zzzz9999SECOND"


def _create_connection(client, **overrides):
    payload = {
        "name": "Company GitLab",
        "platform_type": "gitlab",
        "base_url": "https://gitlab.example.com",
        "token": TOKEN,
    }
    payload.update(overrides)
    return client.post("/api/v1/git/connections", json=payload)


def _secret_count(db) -> int:
    return int(db.scalar(select(func.count(Secret.id))) or 0)


# ---- CRUD + masking ----


def test_create_connection_masks_token(client):
    response = _create_connection(client)
    assert response.status_code == 201
    body = response.json()
    assert body["has_credential"] is True
    assert body["credential_mask"] is not None
    assert body["credential_mask"].endswith("OKEN")
    assert body["platform_type"] == "gitlab"
    assert body["enabled"] is True
    # plaintext must never appear in the API response
    assert TOKEN not in json.dumps(body)
    assert "credential_ref" not in body
    assert "token" not in body


def test_connection_list_is_scope_free_and_masked(client):
    created = _create_connection(client, name="scope-free").json()
    overview = client.get("/api/v1/git").json()
    assert "builtin" in overview
    assert set(overview["builtin"]) == {"docker_available", "status", "url", "version"}
    ids = [c["id"] for c in overview["connections"]]
    assert created["id"] in ids
    # no employee scoping: connections list never takes/needs a scope context
    assert TOKEN not in json.dumps(overview)


def test_patch_keeps_credential_when_token_absent_or_empty(client):
    created = _create_connection(client, name="patch-keep").json()
    mask = created["credential_mask"]

    renamed = client.patch(
        f"/api/v1/git/connections/{created['id']}", json={"name": "renamed"}
    ).json()
    assert renamed["name"] == "renamed"
    assert renamed["credential_mask"] == mask

    emptied = client.patch(f"/api/v1/git/connections/{created['id']}", json={"token": ""}).json()
    assert emptied["credential_mask"] == mask


def test_patch_rotates_token(client, db):
    before = _secret_count(db)
    created = _create_connection(client, name="patch-rotate").json()
    assert _secret_count(db) == before + 1

    rotated = client.patch(
        f"/api/v1/git/connections/{created['id']}", json={"token": TOKEN2}
    ).json()
    assert rotated["credential_mask"].endswith("COND")
    assert TOKEN2 not in json.dumps(rotated)
    # old ciphertext dropped, exactly one secret still held
    assert _secret_count(db) == before + 1


def test_delete_connection_drops_secret(client, db):
    before = _secret_count(db)
    created = _create_connection(client, name="to-delete").json()
    assert _secret_count(db) == before + 1
    assert client.delete(f"/api/v1/git/connections/{created['id']}").status_code == 204
    assert _secret_count(db) == before
    ids = [c["id"] for c in client.get("/api/v1/git").json()["connections"]]
    assert created["id"] not in ids
    assert client.delete(f"/api/v1/git/connections/{created['id']}").status_code == 404


def test_connection_events_carry_no_plaintext(client):
    created = _create_connection(client, name="event-check").json()
    events = client.get("/api/v1/events?limit=200").json()
    git_events = [
        e
        for e in events
        if e["type"].startswith("git.") and e["payload"].get("id") == created["id"]
    ]
    assert git_events, "expected git.* events"
    assert TOKEN not in json.dumps(git_events)


# ---- test-connection probe (mocked httpx) ----


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeHttpxClient:
    """AsyncClient stand-in: url-substring → response or exception."""

    responses: dict = {}
    requests: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        type(self).requests.append({"url": url, "headers": dict(headers or {})})
        for key, value in type(self).responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise httpx.ConnectError(f"no fake route for {url}")


@pytest.fixture()
def fake_httpx(monkeypatch):
    _FakeHttpxClient.responses = {}
    _FakeHttpxClient.requests = []
    monkeypatch.setattr("app.git.probe.httpx.AsyncClient", _FakeHttpxClient)
    return _FakeHttpxClient


def test_test_connection_gitlab(client, fake_httpx):
    fake_httpx.responses = {
        "/api/v4/version": _FakeResponse(200, {"version": "16.4.0-ee", "revision": "abc"})
    }
    created = _create_connection(client, name="probe-gitlab").json()
    result = client.post(f"/api/v1/git/connections/{created['id']}/test").json()
    assert result["ok"] is True
    assert result["version"] == "16.4.0-ee"
    assert isinstance(result["latency_ms"], int)
    assert result["error"] is None
    # gitlab auth style: PRIVATE-TOKEN header against /api/v4/version
    request = fake_httpx.requests[-1]
    assert request["url"] == "https://gitlab.example.com/api/v4/version"
    assert request["headers"].get("PRIVATE-TOKEN") == TOKEN


def test_test_connection_gitea(client, fake_httpx):
    fake_httpx.responses = {"/api/v1/version": _FakeResponse(200, {"version": "1.22.0"})}
    created = _create_connection(
        client,
        name="probe-gitea",
        platform_type="gitea",
        base_url="https://gitea.example.com/",
    ).json()
    result = client.post(f"/api/v1/git/connections/{created['id']}/test").json()
    assert result["ok"] is True
    assert result["version"] == "1.22.0"
    request = fake_httpx.requests[-1]
    assert request["url"] == "https://gitea.example.com/api/v1/version"
    assert request["headers"].get("Authorization") == f"token {TOKEN}"


def test_test_connection_failure_is_redacted(client, fake_httpx):
    fake_httpx.responses = {
        "/api/v4/version": httpx.ConnectError(f"connection refused for token {TOKEN}")
    }
    created = _create_connection(client, name="probe-fail").json()
    result = client.post(f"/api/v1/git/connections/{created['id']}/test").json()
    assert result["ok"] is False
    assert result["version"] is None
    assert result["error"]
    assert TOKEN not in json.dumps(result)
    # events must not carry the plaintext either
    events = client.get("/api/v1/events?limit=50").json()
    tested = [e for e in events if e["type"] == "git.connection_tested"]
    assert TOKEN not in json.dumps(tested)


def test_test_connection_unknown_id(client):
    assert client.post("/api/v1/git/connections/99999/test").status_code == 404


# ---- builtin gitea (FakeDockerService) ----


class _UnavailableDocker:
    """DockerService stand-in with the daemon down."""

    def available(self):
        return False

    def ping(self):
        return False

    def inspect_container(self, name):
        return None


def _stub_wait_ready(monkeypatch, service, version="1.22.0"):
    async def fake_wait_ready(timeout):
        return version

    monkeypatch.setattr(service, "_wait_ready", fake_wait_ready)


def test_builtin_status_not_installed(fake_docker):
    service = GitService(docker=fake_docker)
    status = service.builtin_status()
    assert status.status == "not_installed"
    assert status.docker_available is True
    assert status.url is None
    assert status.version is None


def test_builtin_status_error_after_failed_install(db, fake_docker, monkeypatch):
    monkeypatch.setattr(fake_docker, "pull_image", lambda image: None)
    service = GitService(docker=fake_docker)
    import asyncio

    asyncio.run(service.install_builtin())
    status = service.builtin_status()
    assert status.status == "error"
    from app.models.event import Event

    failed = db.scalars(select(Event).where(Event.type == "git.builtin_failed")).all()
    assert failed, "expected git.builtin_failed event"


async def test_builtin_install_orchestration(db, fake_docker, monkeypatch):
    service = GitService(docker=fake_docker)
    _stub_wait_ready(monkeypatch, service, version="1.22.3")

    await service.install_builtin()

    assert len(fake_docker.containers) == 1
    container = next(iter(fake_docker.containers.values()))
    assert container["name"] == GITEA_CONTAINER_NAME
    assert container["image"] == "gitea/gitea:1"
    # loopback-only publishing: HTTP 26990 → 3000, SSH 26922 → 22
    assert container["ports"] == {
        "3000/tcp": ("127.0.0.1", 26990),
        "22/tcp": ("127.0.0.1", 26922),
    }
    assert all(binding[0] == "127.0.0.1" for binding in container["ports"].values())
    assert container["environment"]["GITEA__security__INSTALL_LOCK"] == "true"
    assert any(v.get("bind") == "/data" for v in container["volumes"].values())
    assert container["network"] == "eidolon-runtime-net"
    assert "eidolon-runtime-net" in fake_docker.networks

    status = service.builtin_status()
    assert status.status == "running"
    assert status.url == "http://127.0.0.1:26990"
    assert status.version == "1.22.3"

    from app.models.event import Event

    types = [e.type for e in db.scalars(select(Event).where(Event.type.like("git.%"))).all()]
    assert "git.builtin_install_started" in types
    assert "git.builtin_installed" in types


async def test_builtin_stop_start_cycle(db, fake_docker, monkeypatch):
    service = GitService(docker=fake_docker)
    _stub_wait_ready(monkeypatch, service)
    await service.install_builtin()
    assert service.builtin_status().status == "running"

    stopped = await service.stop_builtin()
    assert stopped.status == "stopped"
    assert stopped.url is None

    started = await service.start_builtin()
    assert started.status == "running"

    from app.models.event import Event

    types = [e.type for e in db.scalars(select(Event).where(Event.type.like("git.%"))).all()]
    assert "git.builtin_stopped" in types
    assert "git.builtin_started" in types


def test_builtin_api_409_when_docker_unavailable(client, monkeypatch):
    service = GitService(docker=_UnavailableDocker())
    monkeypatch.setattr("app.api.v1.git.git_service", service)
    assert client.post("/api/v1/git/builtin/install").status_code == 409
    assert client.post("/api/v1/git/builtin/start").status_code == 409
    assert client.post("/api/v1/git/builtin/stop").status_code == 409


def test_builtin_api_install_flow(client, fake_docker, monkeypatch):
    service = GitService(docker=fake_docker)
    _stub_wait_ready(monkeypatch, service, version="1.22.0")
    monkeypatch.setattr("app.api.v1.git.git_service", service)

    overview = client.get("/api/v1/git").json()
    assert overview["builtin"]["status"] == "not_installed"

    response = client.post("/api/v1/git/builtin/install")
    assert response.status_code == 200
    assert response.json() == {"status": "installing"}

    # install task runs in the background; poll until done
    import time

    deadline = time.monotonic() + 10
    status = ""
    while time.monotonic() < deadline:
        status = client.get("/api/v1/git").json()["builtin"]["status"]
        if status != "installing":
            break
        time.sleep(0.1)
    assert status == "running"
    overview = client.get("/api/v1/git").json()["builtin"]
    assert overview["url"] == "http://127.0.0.1:26990"
    assert overview["version"] == "1.22.0"

    assert client.post("/api/v1/git/builtin/stop").json() == {"status": "stopped"}
    assert client.post("/api/v1/git/builtin/start").json() == {"status": "running"}
