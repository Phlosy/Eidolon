"""Employee-owned providers (v0.3): /employees/{id}/providers endpoints."""

import json

from app.providers.secrets.store import get_secret_store
from app.repositories import providers as provider_repo

API_KEY = "sk-employee-abcdef1234567890SECRET"


def test_employee_provider_create_and_list(client, db, employees_by_slug):
    bob = employees_by_slug["bob"]
    alice = employees_by_slug["alice"]

    resp = client.post(
        f"/api/v1/employees/{bob['id']}/providers",
        json={
            "name": "bob-openai",
            "provider_type": "openai",
            "api_key": API_KEY,
            "model": "gpt-5",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "employee"
    assert body["owner_employee_id"] == bob["id"]
    assert body["has_credential"] is True
    assert body["credential_mask"].endswith("CRET")
    # the plaintext key never leaves the backend
    assert API_KEY not in json.dumps(body)
    assert "credential_ref" not in body

    # secret is retrievable only via the secret store
    provider = provider_repo.get_provider(db, body["id"])
    assert get_secret_store().retrieve(db, provider.credential_ref) == API_KEY

    # the given model became the primary binding
    binding = provider_repo.get_primary_binding(db, bob["id"])
    assert binding is not None
    assert binding.provider_id == body["id"]
    assert binding.model == "gpt-5"

    # GET lists the employee's own + company-scope shared accounts
    shared = client.post(
        "/api/v1/providers",
        json={"name": "company-shared", "provider_type": "openrouter", "scope": "company"},
    ).json()
    listing = client.get(f"/api/v1/employees/{bob['id']}/providers").json()
    ids = [p["id"] for p in listing]
    assert body["id"] in ids
    assert shared["id"] in ids
    assert API_KEY not in json.dumps(listing)

    # another employee does not see bob's account
    alice_ids = [p["id"] for p in client.get(f"/api/v1/employees/{alice['id']}/providers").json()]
    assert body["id"] not in alice_ids
    assert shared["id"] in alice_ids


def test_employee_provider_unknown_employee(client):
    resp = client.post(
        "/api/v1/employees/999999/providers",
        json={"name": "ghost", "provider_type": "openai"},
    )
    assert resp.status_code == 404
    assert client.get("/api/v1/employees/999999/providers").status_code == 404


def test_global_create_defaults_to_employee_scope_with_owner(client, employees_by_slug):
    bob = employees_by_slug["bob"]
    resp = client.post(
        "/api/v1/providers",
        json={"name": "implicit-scope", "provider_type": "openai", "owner_employee_id": bob["id"]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scope"] == "employee"
    assert resp.json()["owner_employee_id"] == bob["id"]

    # no owner → still company scope by default
    resp = client.post(
        "/api/v1/providers", json={"name": "implicit-company", "provider_type": "openai"}
    )
    assert resp.status_code == 201
    assert resp.json()["scope"] == "company"
