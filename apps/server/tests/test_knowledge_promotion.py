"""§6.3: knowledge promotion — proposal → review → scope change."""

from app.repositories import knowledge as knowledge_repo


def _make_private_item(db, employee_id, title):
    item = knowledge_repo.create_knowledge_item(
        db,
        scope="private",
        owner_employee_id=employee_id,
        title=title,
        content="promotion test content",
        topic="promotion-test",
        status="active",
        confidence=0.9,
        sources=[],
    )
    db.commit()
    return item


def test_knowledge_promotion_approve(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["alice"]["id"], "promote me")

    resp = client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "company"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "proposed"
    assert resp.json()["scope"] == "private"  # scope changes only on approval

    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "company"
    assert resp.json()["status"] == "active"

    # now retrievable as company knowledge
    items = client.get("/api/v1/knowledge", params={"scope": "company"}).json()
    assert any(i["title"] == "promote me" for i in items)


def test_knowledge_promotion_reject(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["bob"]["id"], "reject me")

    resp = client.post(
        f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "department"}
    )
    assert resp.status_code == 200
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": False})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["scope"] == "private"  # stays private


def test_knowledge_promotion_guards(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["charlie"]["id"], "guard me")

    # review without proposal → 409
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 409
    # proposing back to private → 400
    resp = client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "private"})
    assert resp.status_code == 400
