"""Provider API: scope isolation, credential masking, delete-while-in-use (v0.2)."""

import json

from app.models.enums import ProviderScope, ProviderType
from app.repositories import providers as provider_repo

API_KEY = "sk-test-abcdef1234567890SECRET"


def _create_provider(client, **overrides):
    payload = {
        "name": "OpenAI main",
        "provider_type": ProviderType.openai.value,
        "scope": ProviderScope.company.value,
        "api_key": API_KEY,
    }
    payload.update(overrides)
    return client.post("/api/v1/providers", json=payload)


def test_create_provider_masks_credential(client):
    response = _create_provider(client)
    assert response.status_code == 201
    body = response.json()
    assert body["has_credential"] is True
    assert body["credential_mask"] is not None
    assert body["credential_mask"].endswith("CRET")
    # the plaintext key must never appear in the API response
    assert API_KEY not in json.dumps(body)
    assert "credential_ref" not in body
    assert "api_key" not in body


def test_company_scope_visible_to_all(client, employees_by_slug):
    created = _create_provider(client, name="shared-openai").json()
    alice_id = employees_by_slug["alice"]["id"]
    bob_id = employees_by_slug["bob"]["id"]

    without_ctx = client.get("/api/v1/providers").json()
    assert created["id"] in [p["id"] for p in without_ctx]
    with_alice = client.get(f"/api/v1/providers?employee_id={alice_id}").json()
    assert created["id"] in [p["id"] for p in with_alice]
    with_bob = client.get(f"/api/v1/providers?employee_id={bob_id}").json()
    assert created["id"] in [p["id"] for p in with_bob]


def test_employee_scope_isolation(client, employees_by_slug):
    alice_id = employees_by_slug["alice"]["id"]
    bob_id = employees_by_slug["bob"]["id"]
    created = _create_provider(
        client,
        name="alice-private",
        scope=ProviderScope.employee.value,
        owner_employee_id=alice_id,
    ).json()

    # not visible without owner context
    assert created["id"] not in [p["id"] for p in client.get("/api/v1/providers").json()]
    # visible to the owner
    assert created["id"] in [
        p["id"] for p in client.get(f"/api/v1/providers?employee_id={alice_id}").json()
    ]
    # invisible to another employee: list AND direct access
    assert created["id"] not in [
        p["id"] for p in client.get(f"/api/v1/providers?employee_id={bob_id}").json()
    ]
    assert client.get(f"/api/v1/providers/{created['id']}?employee_id={bob_id}").status_code == 404
    assert (
        client.patch(
            f"/api/v1/providers/{created['id']}?employee_id={bob_id}", json={"name": "hijack"}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/providers/{created['id']}?employee_id={bob_id}").status_code == 404
    )
    # owner can see and manage it
    assert (
        client.get(f"/api/v1/providers/{created['id']}?employee_id={alice_id}").status_code == 200
    )
    assert (
        client.delete(f"/api/v1/providers/{created['id']}?employee_id={alice_id}").status_code
        == 204
    )


def test_employee_scope_requires_owner(client):
    response = client.post(
        "/api/v1/providers",
        json={"name": "broken", "provider_type": "openai", "scope": "employee"},
    )
    assert response.status_code == 422


def test_provider_test_endpoint_no_key(client):
    created = client.post(
        "/api/v1/providers",
        json={"name": "no-key", "provider_type": "openai", "scope": "company"},
    ).json()
    result = client.post(f"/api/v1/providers/{created['id']}/test").json()
    assert result["ok"] is False
    assert result["error"]


def test_provider_test_unreachable_is_graceful(client):
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "dead-end",
            "provider_type": "openai",
            "base_url": "http://127.0.0.1:9/nowhere",
            "scope": "company",
            "api_key": API_KEY,
        },
    ).json()
    result = client.post(f"/api/v1/providers/{created['id']}/test").json()
    assert result["ok"] is False
    assert result["latency_ms"] is not None
    assert API_KEY not in json.dumps(result)


def test_provider_models_endpoint_shape(client):
    created = _create_provider(client, name="models-src").json()
    result = client.get(f"/api/v1/providers/{created['id']}/models").json()
    assert result["source"] == "api"
    assert isinstance(result["models"], list)


def test_delete_provider_in_use_returns_409(client, db, employees_by_slug):
    created = _create_provider(client, name="in-use").json()
    employee_id = employees_by_slug["alice"]["id"]
    provider_repo.create_binding(
        db, employee_id=employee_id, provider_id=created["id"], model="gpt-5", is_primary=True
    )
    db.commit()

    in_use = client.get(f"/api/v1/providers/{created['id']}").json()
    assert in_use["in_use_by"] == 1
    assert client.delete(f"/api/v1/providers/{created['id']}").status_code == 409

    # after removing the binding, delete succeeds
    from app.models.provider import ModelBinding

    binding = provider_repo.get_primary_binding(db, employee_id)
    db.delete(db.get(ModelBinding, binding.id))
    db.commit()
    assert client.delete(f"/api/v1/providers/{created['id']}").status_code == 204


def test_provider_events_are_redacted(client):
    created = _create_provider(client, name="event-check").json()
    # even if a buggy caller puts a key into an event payload, the bus redacts it
    from app.events.bus import bus

    bus.publish("provider.tested", {"id": created["id"], "debug": f"key={API_KEY}"})
    events = client.get("/api/v1/events?limit=200").json()
    provider_events = [
        e
        for e in events
        if e["type"].startswith("provider.") and e["payload"].get("id") == created["id"]
    ]
    assert provider_events, "expected provider.* events"
    assert API_KEY not in json.dumps(provider_events)
    debug_events = [e for e in provider_events if "debug" in e["payload"]]
    assert debug_events and "••••" in debug_events[0]["payload"]["debug"]
