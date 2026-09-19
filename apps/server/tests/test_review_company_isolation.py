"""Company boundaries for the activity feed, provisioning jobs and live events."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.events.bus import bus
from app.models.event import Event
from app.models.git import GitConnection
from app.models.lifecycle import ProvisioningJob, ProvisioningStep
from app.models.organization import Employee
from app.models.person import Person
from app.models.provider import ModelBinding, Provider
from app.repositories import events as event_repo
from app.repositories import lifecycle as lifecycle_repo


@pytest.fixture
def companies(client, db, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    accounts = []
    for label in ("a", "b"):
        slug = f"review-scope-{label}-{uuid4().hex}"
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": f"{slug}@example.com", "password": "company boundary password1"},
        )
        assert registered.status_code == 201
        verified = client.post(
            "/api/v1/auth/verify-email",
            json={"token": registered.json()["development_verification_token"]},
        )
        assert verified.status_code == 200
        account = verified.json()
        person = Person(slug=slug, name=f"Employee {label}")
        db.add(person)
        db.flush()
        employee = Employee(
            company_id=account["company"]["id"],
            person_id=person.id,
            name=person.name,
            slug=slug,
            workspace_path=f"{settings.workspace_root}/{slug}",
            memory_namespace=slug,
        )
        db.add(employee)
        db.commit()
        session_cookie = client.cookies.get(settings.session_cookie_name)
        csrf = client.cookies.get("eidolon_csrf")
        accounts.append(
            {
                "company_id": employee.company_id,
                "employee_id": employee.id,
                "person_id": person.id,
                "user_id": account["user"]["id"],
                "headers": {
                    "Cookie": (
                        f"{settings.session_cookie_name}={session_cookie}; eidolon_csrf={csrf}"
                    ),
                    "X-CSRF-Token": csrf,
                },
            }
        )
    client.cookies.clear()
    try:
        yield accounts
    finally:
        # The app fixture shares a database: do not leave synthetic employees/jobs
        # for later workforce and lifecycle tests to discover.
        db.rollback()
        company_ids = [account["company_id"] for account in accounts]
        employee_ids = [account["employee_id"] for account in accounts]
        job_ids = select(ProvisioningJob.id).where(ProvisioningJob.employee_id.in_(employee_ids))
        db.execute(delete(ProvisioningStep).where(ProvisioningStep.job_id.in_(job_ids)))
        db.execute(delete(ProvisioningJob).where(ProvisioningJob.employee_id.in_(employee_ids)))
        db.execute(delete(ModelBinding).where(ModelBinding.employee_id.in_(employee_ids)))
        db.execute(delete(Provider).where(Provider.company_id.in_(company_ids)))
        db.execute(delete(GitConnection).where(GitConnection.company_id.in_(company_ids)))
        db.execute(
            delete(Event).where(
                Event.company_id.in_(company_ids) | Event.actor_employee_id.in_(employee_ids)
            )
        )
        db.execute(delete(Employee).where(Employee.id.in_(employee_ids)))
        db.execute(delete(Person).where(Person.id.in_([a["person_id"] for a in accounts])))
        db.commit()


@pytest.fixture
def company_records(companies, db):
    for account in companies:
        event = Event(
            type="employee.review_private",
            company_id=account["company_id"],
            actor_employee_id=account["employee_id"],
            actor_person_id=account["person_id"],
            payload={"detail": "private activity"},
        )
        job = ProvisioningJob(
            employee_id=account["employee_id"],
            kind="onboard",
            status="failed",
            total_steps=1,
            reason="private provisioning reason",
        )
        db.add_all([event, job])
        db.flush()
        db.add(
            ProvisioningStep(
                job_id=job.id,
                seq=1,
                resource_type="git",
                provider_key="git:gitea",
                action="provision",
                description="private operation",
                status="failed",
                error="private provisioning error",
            )
        )
        account.update(event_id=event.id, job_id=job.id)
    unscoped = Event(
        type="employee.review_unscoped",
        actor_employee_id=companies[0]["employee_id"],
        actor_person_id=companies[0]["person_id"],
        payload={"detail": "unscoped private activity"},
    )
    db.add(unscoped)
    db.commit()
    return companies, unscoped.id


def test_authenticated_event_reads_filter_company_before_limit(client, company_records):
    accounts, _ = company_records
    for account in accounts:
        response = client.get("/api/v1/events?limit=1000", headers=account["headers"])
        assert response.status_code == 200
        assert [event["id"] for event in response.json()] == [account["event_id"]]
        latest = client.get("/api/v1/events?limit=1", headers=account["headers"])
        assert [event["id"] for event in latest.json()] == [account["event_id"]]


@pytest.mark.parametrize("operation", ["list", "employee_filter", "detail"])
def test_authenticated_provisioning_jobs_are_company_scoped(client, company_records, operation):
    accounts, _ = company_records
    for own, foreign in (accounts, reversed(accounts)):
        if operation == "list":
            response = client.get("/api/v1/provisioning-jobs", headers=own["headers"])
            assert response.status_code == 200
            assert [job["id"] for job in response.json()] == [own["job_id"]]
        elif operation == "employee_filter":
            path = "/api/v1/provisioning-jobs"
            own_jobs = client.get(
                path, params={"employee_id": own["employee_id"]}, headers=own["headers"]
            )
            assert [job["id"] for job in own_jobs.json()] == [own["job_id"]]
            foreign_jobs = client.get(
                path, params={"employee_id": foreign["employee_id"]}, headers=own["headers"]
            )
            assert foreign_jobs.json() == []
        else:
            own_job = client.get(
                f"/api/v1/provisioning-jobs/{own['job_id']}", headers=own["headers"]
            )
            assert own_job.status_code == 200
            assert own_job.json()["steps"][0]["error"] == "private provisioning error"
            foreign_path = f"/api/v1/provisioning-jobs/{foreign['job_id']}"
            assert client.get(foreign_path, headers=own["headers"]).status_code == 404
            assert client.post(f"{foreign_path}/retry", headers=own["headers"]).status_code == 404


def test_repository_actor_filter_keeps_company_scope_and_internal_reads(db, company_records):
    (own, foreign), unscoped_id = company_records
    token = set_request_identity(
        RequestIdentity(
            user_id=own["user_id"],
            company_id=own["company_id"],
            membership_role="OWNER",
            session_id=0,
        )
    )
    try:
        events = event_repo.list_events(db, actor_employee_id=own["employee_id"])
        assert [event.id for event in events] == [own["event_id"]]
        assert event_repo.list_events(db, actor_employee_id=foreign["employee_id"]) == []
        assert lifecycle_repo.get_job(db, foreign["job_id"]) is None
    finally:
        reset_request_identity(token)
    assert {own["event_id"], foreign["event_id"], unscoped_id}.issubset(
        {event.id for event in event_repo.list_events(db, limit=1000)}
    )
    assert lifecycle_repo.get_job(db, foreign["job_id"]).id == foreign["job_id"]


def test_auth_off_anonymous_reads_remain_available(client, company_records, monkeypatch):
    accounts, unscoped_id = company_records
    monkeypatch.setattr(settings, "auth_required", False)
    events = client.get("/api/v1/events?limit=1000")
    assert events.status_code == 200
    assert {unscoped_id, *(a["event_id"] for a in accounts)}.issubset(
        {event["id"] for event in events.json()}
    )
    jobs = client.get("/api/v1/provisioning-jobs").json()
    assert {a["job_id"] for a in accounts}.issubset({job["id"] for job in jobs})
    for account in accounts:
        assert client.get(f"/api/v1/provisioning-jobs/{account['job_id']}").status_code == 200


def _receive_until_marker(socket, marker):
    messages = []
    while True:
        message = socket.receive_json()
        messages.append(message)
        if message["type"] == "review.marker" and message["data"]["marker"] == marker:
            return messages


def test_authenticated_websockets_filter_unscoped_and_foreign_configuration(client, companies):
    own, foreign = companies
    with (
        client.websocket_connect("/ws/events", headers=own["headers"]) as own_socket,
        client.websocket_connect("/ws/events", headers=foreign["headers"]) as foreign_socket,
    ):
        created = client.post(
            "/api/v1/providers",
            headers=foreign["headers"],
            json={"name": "Private provider B", "provider_type": "openai"},
        )
        assert created.status_code == 201
        binding = client.post(
            f"/api/v1/employees/{foreign['employee_id']}/bindings",
            headers=foreign["headers"],
            json={"provider_id": created.json()["id"], "model": "private-model"},
        )
        assert binding.status_code == 201
        git = client.post(
            "/api/v1/git/connections",
            headers=foreign["headers"],
            json={
                "name": "Private Git B",
                "platform_type": "gitea",
                "base_url": "https://git.example.com",
            },
        )
        assert git.status_code == 201
        bus.publish("provider.updated", {"name": "legacy unscoped private provider"})
        bus.publish("review.unknown_private", {"detail": "unknown event is not public"})
        for account, marker in ((own, "a"), (foreign, "b")):
            bus.publish("review.marker", {"marker": marker}, company_id=account["company_id"])
        own_messages = _receive_until_marker(own_socket, "a")
        foreign_messages = _receive_until_marker(foreign_socket, "b")

    assert [message["type"] for message in own_messages] == ["review.marker"]
    assert [message["type"] for message in foreign_messages] == [
        "provider.created",
        "provider.binding_added",
        "git.connection_created",
        "review.marker",
    ]
    assert all(message["company_id"] == foreign["company_id"] for message in foreign_messages)


def test_websocket_requires_session_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect("/ws/events"):
        pass
    assert error.value.code == 4401


def test_auth_off_websocket_preserves_unscoped_delivery(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    with client.websocket_connect("/ws/events") as socket:
        bus.publish("review.marker", {"marker": "anonymous"})
        messages = _receive_until_marker(socket, "anonymous")
    assert messages[-1]["company_id"] is None


def test_configuration_events_keep_the_resource_company(client, db, companies, monkeypatch):
    async def provider_probe(*args):
        return {"ok": True, "latency_ms": 1, "error": None, "models_count": 1}

    async def git_probe(*args):
        return {"ok": True, "latency_ms": 1, "error": None, "version": "test"}

    monkeypatch.setattr("app.services.providers.probe.test_provider", provider_probe)
    monkeypatch.setattr("app.services.git.probe.test_connection", git_probe)
    account = companies[1]
    headers = account["headers"]
    before = set(db.scalars(select(Event.id)))
    provider = client.post(
        "/api/v1/providers",
        headers=headers,
        json={"name": "Company configuration", "provider_type": "openai"},
    )
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    assert (
        client.patch(
            f"/api/v1/providers/{provider_id}", headers=headers, json={"name": "Updated provider"}
        ).status_code
        == 200
    )
    binding_path = f"/api/v1/employees/{account['employee_id']}/bindings"
    binding = client.post(
        binding_path, headers=headers, json={"provider_id": provider_id, "model": "test-model"}
    )
    assert binding.status_code == 201
    assert (
        client.post(f"{binding_path}/{binding.json()['id']}/primary", headers=headers).status_code
        == 200
    )
    assert client.post(f"/api/v1/providers/{provider_id}/test", headers=headers).status_code == 200

    async def provider_failure(*args):
        return {"ok": False, "latency_ms": 1, "error": "private error", "models_count": None}

    monkeypatch.setattr("app.services.providers.probe.test_provider", provider_failure)
    assert client.post(f"/api/v1/providers/{provider_id}/test", headers=headers).status_code == 200
    git = client.post(
        "/api/v1/git/connections",
        headers=headers,
        json={
            "name": "Company Git",
            "platform_type": "gitea",
            "base_url": "https://git.example.com",
        },
    )
    assert git.status_code == 201
    git_path = f"/api/v1/git/connections/{git.json()['id']}"
    assert client.patch(git_path, headers=headers, json={"name": "Updated Git"}).status_code == 200
    assert client.post(f"{git_path}/test", headers=headers).status_code == 200
    assert client.delete(git_path, headers=headers).status_code == 204
    events = list(db.scalars(select(Event).where(Event.id.not_in(before))))
    assert {event.type for event in events} == {
        "provider.created",
        "provider.updated",
        "provider.binding_added",
        "provider.binding_primary",
        "provider.tested",
        "provider.failed",
        "git.connection_created",
        "git.connection_updated",
        "git.connection_tested",
        "git.connection_deleted",
    }
    assert all(event.company_id == account["company_id"] for event in events)
