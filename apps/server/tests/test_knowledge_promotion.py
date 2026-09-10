"""§6.3: knowledge promotion — proposal → review → scope change.

K1: approve 通过后物化成 drive markdown 文档（company→handbook 区，
department→knowledge 区部门夹），并做 KnowledgeItem.sources ↔ DriveNode 双向回链。
"""

from pathlib import Path

from app.knowledge import promotion
from app.repositories import drive as drive_repo
from app.repositories import knowledge as knowledge_repo
from app.services import drive as drive_service


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


# ---- K1: 晋升物化到 drive ----


def _materialized_node(db, item):
    links = [s for s in item.sources if isinstance(s, str) and s.startswith("drive_node://")]
    assert len(links) == 1
    node = drive_repo.get_node(db, int(links[0].removeprefix("drive_node://")))
    assert node is not None
    return node


def test_promotion_approve_materializes_company_item_to_handbook(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["alice"]["id"], "company handbook entry")
    client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "company"})
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 200, resp.text

    db.refresh(item)
    node = _materialized_node(db, item)
    assert node.zone == "handbook"
    assert node.name == "company handbook entry"
    assert Path(node.path).parent.name == "handbook"  # 直接挂在 handbook 区根下
    content = Path(drive_service.abs_path(node)).read_text(encoding="utf-8")
    assert f"knowledge_item://{item.id}" in content  # DriveNode → KnowledgeItem 回链
    assert "promotion test content" in content
    # revision v1 落库
    revisions = drive_repo.list_revisions(db, node.id)
    assert [r.version for r in revisions] == [1]


def test_promotion_approve_materializes_department_item_to_dept_folder(
    client, db, employees_by_slug
):
    alice = employees_by_slug["alice"]
    item = _make_private_item(db, alice["id"], "dept knowledge entry")
    client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "department"})
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "department"

    db.refresh(item)
    node = _materialized_node(db, item)
    assert node.zone == "knowledge"
    # item 无 department_id → 回落到 owner 的部门夹 dept-<id>
    assert f"/dept-{alice['department_id']}/" in node.path
    parent = drive_repo.get_node(db, node.parent_id)
    assert parent.kind == "folder"
    assert f"knowledge_item://{item.id}" in Path(drive_service.abs_path(node)).read_text(
        encoding="utf-8"
    )


def test_promotion_reject_creates_no_document(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["bob"]["id"], "reject no doc")
    client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "company"})
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": False})
    assert resp.status_code == 200

    db.refresh(item)
    assert not [s for s in item.sources if str(s).startswith("drive_node://")]


def test_promotion_materialization_is_idempotent(client, db, employees_by_slug):
    item = _make_private_item(db, employees_by_slug["charlie"]["id"], "idempotent entry")
    client.post(f"/api/v1/knowledge/{item.id}/proposals", json={"target_scope": "company"})
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 200

    db.refresh(item)
    node = _materialized_node(db, item)

    # 再次物化：复用同一节点，不重复建文档
    again = promotion._materialize_to_drive(db, item)
    assert again.id == node.id
    handbook_docs = [
        n for n in drive_repo.list_nodes(db, zone="handbook") if n.name == "idempotent entry"
    ]
    assert len(handbook_docs) == 1

    # 重复评审被状态机挡住（proposed_scope 已清空 → 409）
    resp = client.post(f"/api/v1/knowledge/{item.id}/review", json={"approve": True})
    assert resp.status_code == 409
