"""§3.4.1 (hard requirement): A's memory & private knowledge are invisible via B's endpoints."""

from app.repositories import knowledge as knowledge_repo


def test_employee_isolation(client, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    bob = employees_by_slug["bob"]

    knowledge_repo.create_memory_entry(
        db, alice["id"], kind="note", content="alice secret note", source_ref="test"
    )
    knowledge_repo.create_knowledge_item(
        db,
        scope="private",
        owner_employee_id=alice["id"],
        title="alice private lesson",
        content="only alice can see this",
        topic="isolation-test",
        status="active",
        confidence=0.9,
        sources=[],
    )
    db.commit()

    # owner sees own data
    memory = client.get(f"/api/v1/employees/{alice['id']}/memory").json()
    assert any("alice secret note" in m["content"] for m in memory)
    knowledge = client.get(f"/api/v1/employees/{alice['id']}/knowledge").json()
    assert any("alice private lesson" in k["title"] for k in knowledge)

    # B's endpoints never expose A's data
    bob_memory = client.get(f"/api/v1/employees/{bob['id']}/memory").json()
    assert all("alice secret note" not in m["content"] for m in bob_memory)
    bob_knowledge = client.get(f"/api/v1/employees/{bob['id']}/knowledge").json()
    assert all("alice private lesson" not in k["title"] for k in bob_knowledge)

    # private scope via /knowledge requires an owner id, and is scoped to that owner
    resp = client.get("/api/v1/knowledge", params={"scope": "private"})
    assert resp.status_code == 400
    resp = client.get("/api/v1/knowledge", params={"scope": "private", "employee_id": bob["id"]})
    assert resp.status_code == 200
    assert all("alice private lesson" not in k["title"] for k in resp.json())

    # non-private retrieval never leaks private items
    resp = client.get("/api/v1/knowledge", params={"scope": "company"})
    assert all("alice private lesson" not in k["title"] for k in resp.json())
