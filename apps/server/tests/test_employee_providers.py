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


def test_multi_model_create_and_primary_switching(client, employees_by_slug):
    bob = employees_by_slug["bob"]

    # 一次创建带多个模型，primary_model 指定默认启动模型
    resp = client.post(
        f"/api/v1/employees/{bob['id']}/providers",
        json={
            "name": "bob-deepseek",
            "provider_type": "deepseek",
            "api_key": API_KEY,
            "models": [
                {"model": "deepseek-chat", "alias": "聊天"},
                {"model": "deepseek-reasoner"},
            ],
            "primary_model": "deepseek-reasoner",
        },
    )
    assert resp.status_code == 201, resp.text
    provider_id = resp.json()["id"]

    bindings = client.get(f"/api/v1/employees/{bob['id']}/bindings").json()
    mine = [b for b in bindings if b["provider_id"] == provider_id]
    assert [b["model"] for b in mine] == ["deepseek-chat", "deepseek-reasoner"]
    assert mine[0]["alias"] == "聊天"
    assert mine[1]["alias"] == ""
    primary = [b for b in mine if b["is_primary"]]
    assert len(primary) == 1 and primary[0]["model"] == "deepseek-reasoner"

    # 切换默认模型
    chat = next(b for b in mine if b["model"] == "deepseek-chat")
    switched = client.post(f"/api/v1/employees/{bob['id']}/bindings/{chat['id']}/primary")
    assert switched.status_code == 200
    after = switched.json()
    assert [b for b in after if b["is_primary"]][0]["model"] == "deepseek-chat"
    assert sum(1 for b in after if b["is_primary"]) == 1

    # 再加一条绑定；重复的 provider+model 被拒
    added = client.post(
        f"/api/v1/employees/{bob['id']}/bindings",
        json={"provider_id": provider_id, "model": "deepseek-coder"},
    )
    assert added.status_code == 201
    dup = client.post(
        f"/api/v1/employees/{bob['id']}/bindings",
        json={"provider_id": provider_id, "model": "deepseek-coder"},
    )
    assert dup.status_code == 409

    # 删掉当前默认模型 → 自动顶上剩下按 position 最靠前的一条
    current_primary = [b for b in client.get(f"/api/v1/employees/{bob['id']}/bindings").json()
                       if b["is_primary"] and b["provider_id"] == provider_id][0]
    assert client.delete(
        f"/api/v1/employees/{bob['id']}/bindings/{current_primary['id']}"
    ).status_code == 204
    # 测试库是 session 级共享的（bob 可能有别的绑定），断言只盯本次建的 provider
    rest = [b for b in client.get(f"/api/v1/employees/{bob['id']}/bindings").json()
            if b["provider_id"] == provider_id]
    assert len(rest) == 2
    all_bindings = client.get(f"/api/v1/employees/{bob['id']}/bindings").json()
    assert sum(1 for b in all_bindings if b["is_primary"]) == 1

    # 别人的绑定动不了
    assert client.post(
        f"/api/v1/employees/{employees_by_slug['alice']['id']}/bindings/{rest[0]['id']}/primary"
    ).status_code == 404


def test_probe_unsaved_provider_config(client):
    # 没填 key 的远程厂商：不发请求，直接报缺凭据
    missing = client.post("/api/v1/providers/probe", json={"provider_type": "openai"})
    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert "credential" in missing.json()["error"]

    # ollama 免 key：本机没起服务 → ok=False，但走的是真实探测路径
    ollama = client.post("/api/v1/providers/probe", json={"provider_type": "ollama"})
    assert ollama.status_code == 200
    assert ollama.json()["ok"] is False


def test_model_names_are_validated_before_creating_anything(client, employees_by_slug):
    bob = employees_by_slug["bob"]
    before = len(client.get(f"/api/v1/employees/{bob['id']}/providers").json())

    bad = client.post(
        f"/api/v1/employees/{bob['id']}/providers",
        json={
            "name": "bad-model",
            "provider_type": "deepseek",
            "models": [{"model": "not a model!"}],
        },
    )
    assert bad.status_code == 422
    # 校验失败不能留下孤儿 provider
    after = client.get(f"/api/v1/employees/{bob['id']}/providers").json()
    assert len(after) == before
    assert "bad-model" not in [p["name"] for p in after]

    # 合法的自定义命名（org/model、tag 形态）可以过
    ok = client.post(
        f"/api/v1/employees/{bob['id']}/providers",
        json={
            "name": "custom-names",
            "provider_type": "ollama",
            "models": [{"model": "qwen3:8b"}, {"model": "my-org/my-model.v2"}],
        },
    )
    assert ok.status_code == 201, ok.text
