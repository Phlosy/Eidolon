"""Drive API (v0.3): folder tree, workflow documents, revisions, permissions."""

import hashlib
import time
from pathlib import Path

from app.core.config import settings
from app.repositories import drive as drive_repo
from app.repositories import project as project_repo
from app.services import drive as drive_service
from app.services.drive_migration import migrate_artifacts_to_drive


def _wait_for(predicate, timeout=60.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _create_project(client, name: str) -> dict:
    resp = client.post("/api/v1/projects", json={"name": name, "description": "drive 测试项目"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_zone_roots_seeded(client):
    tree = client.get("/api/v1/drive/tree").json()
    paths = {n["path"] for n in tree}
    assert {"drive/projects", "drive/knowledge", "drive/skills", "drive/handbook"} <= paths
    roots = [n for n in tree if n["path"] == "drive/knowledge"]
    assert roots[0]["kind"] == "folder"
    assert roots[0]["zone"] == "knowledge"
    assert roots[0]["parent_id"] is None


def test_project_creation_creates_drive_tree(client):
    project = _create_project(client, "drive-tree-check")
    tree = client.get("/api/v1/drive/tree", params={"zone": "projects"}).json()
    paths = {n["path"] for n in tree}
    assert "drive/projects/drive-tree-check" in paths
    for sub in ("docs", "source", "tests", "release"):
        assert f"drive/projects/drive-tree-check/{sub}" in paths
    folder = next(n for n in tree if n["path"] == "drive/projects/drive-tree-check")
    assert folder["kind"] == "folder"
    assert folder["project_id"] == project["id"]
    assert folder["doc_type"] is None
    # folders exist on disk too
    for sub in ("docs", "source", "tests", "release"):
        assert (Path(settings.data_root) / "drive" / "projects" / "drive-tree-check" / sub).is_dir()


def test_workflow_artifacts_land_in_drive(client):
    project = _create_project(client, "drive-workflow")

    def done():
        return client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "completed"

    assert _wait_for(done), "project did not complete"

    tree = client.get("/api/v1/drive/tree", params={"zone": "projects"}).json()
    docs = [n for n in tree if n["kind"] == "document" and n["project_id"] == project["id"]]
    doc_types = {d["doc_type"] for d in docs}
    assert {
        "plan",
        "prd",
        "research_report",
        "source_code",
        "test_report",
        "release",
    } <= doc_types

    for doc in docs:
        assert doc["current_version"] == 1
        assert doc["owner_employee_id"] is not None
        revisions = client.get(f"/api/v1/drive/nodes/{doc['id']}/revisions").json()
        assert len(revisions) == 1
        assert revisions[0]["version"] == 1
        detail = client.get(f"/api/v1/drive/nodes/{doc['id']}").json()
        assert detail["content"]
        assert detail["collaborators"] == []
        assert (
            hashlib.sha256(detail["content"].encode("utf-8")).hexdigest() == revisions[0]["sha256"]
        )

    # doc_type → subfolder mapping
    by_type = {d["doc_type"]: d for d in docs}
    assert "/source/" in by_type["source_code"]["path"]
    assert "/tests/" in by_type["test_report"]["path"]
    assert "/release/" in by_type["release"]["path"]
    assert "/docs/" in by_type["prd"]["path"]
    assert "/docs/" in by_type["plan"]["path"]


def test_patch_document_creates_revision(client, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    root = drive_repo.get_node_by_path(db, "drive/knowledge")
    node = drive_service.create_document(
        db,
        parent=root,
        name="Patch Me",
        content="v1 content",
        doc_type="knowledge",
        owner_employee_id=alice["id"],
    )
    db.commit()

    # folders reject PATCH
    assert client.patch(f"/api/v1/drive/nodes/{root.id}", json={"content": "x"}).status_code == 400

    resp = client.patch(
        f"/api/v1/drive/nodes/{node.id}?employee_id={alice['id']}",
        json={"content": "v2 content", "message": "second pass"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_version"] == 2

    # file on disk updated
    disk = Path(settings.data_root) / node.path
    assert disk.read_text(encoding="utf-8") == "v2 content"

    # old revision retained, newest first
    revisions = client.get(f"/api/v1/drive/nodes/{node.id}/revisions").json()
    assert [r["version"] for r in revisions] == [2, 1]
    assert revisions[0]["message"] == "second pass"
    assert revisions[0]["sha256"] == hashlib.sha256(b"v2 content").hexdigest()
    assert revisions[1]["sha256"] == hashlib.sha256(b"v1 content").hexdigest()


def test_knowledge_doc_author_only(client, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    bob = employees_by_slug["bob"]
    root = drive_repo.get_node_by_path(db, "drive/knowledge")
    node = drive_service.create_document(
        db,
        parent=root,
        name="Alice Knowledge",
        content="alice only",
        doc_type="knowledge",
        owner_employee_id=alice["id"],
    )
    db.commit()

    # non-author write → 403
    resp = client.patch(
        f"/api/v1/drive/nodes/{node.id}?employee_id={bob['id']}", json={"content": "hijack"}
    )
    assert resp.status_code == 403
    # reads are open
    assert client.get(f"/api/v1/drive/nodes/{node.id}").status_code == 200
    # author can write
    assert (
        client.patch(
            f"/api/v1/drive/nodes/{node.id}?employee_id={alice['id']}", json={"content": "ok"}
        ).status_code
        == 200
    )


def test_create_folder_endpoint(client, db, employees_by_slug):
    resp = client.post(
        "/api/v1/drive/folders?employee_id=" + str(employees_by_slug["alice"]["id"]),
        json={"zone": "knowledge", "name": "Guides"},
    )
    assert resp.status_code == 201, resp.text
    folder = resp.json()
    assert folder["kind"] == "folder"
    assert folder["path"] == "drive/knowledge/guides"
    assert (Path(settings.data_root) / folder["path"]).is_dir()

    # parent mismatch zone → 400
    projects_root = drive_repo.get_node_by_path(db, "drive/projects")
    assert (
        client.post(
            "/api/v1/drive/folders",
            json={"zone": "knowledge", "parent_id": projects_root.id, "name": "bad"},
        ).status_code
        == 400
    )


def test_upload_and_read_binary_documents(client):
    pdf_bytes = b"%PDF-1.4\n% Eidolon preview fixture\n"
    response = client.post(
        "/api/v1/drive/files",
        params={"zone": "knowledge", "name": "Quarterly Report.pdf"},
        content=pdf_bytes,
        headers={"content-type": "application/pdf"},
    )

    assert response.status_code == 201, response.text
    node = response.json()
    assert node["name"] == "Quarterly Report.pdf"
    assert node["doc_type"] == "pdf"
    assert node["path"].endswith("quarterly-report.pdf")

    detail = client.get(f"/api/v1/drive/nodes/{node['id']}")
    assert detail.status_code == 200
    assert detail.json()["content"] is None

    raw = client.get(f"/api/v1/drive/nodes/{node['id']}/content")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("application/pdf")
    assert raw.content == pdf_bytes

    revisions = client.get(f"/api/v1/drive/nodes/{node['id']}/revisions").json()
    assert revisions[0]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()

    assert (
        client.patch(f"/api/v1/drive/nodes/{node['id']}", json={"content": "overwrite"}).status_code
        == 400
    )


def test_upload_markdown_keeps_text_preview_and_sanitizes_name(client):
    markdown = "# Safe upload\n\nRendered by default."
    response = client.post(
        "/api/v1/drive/files",
        params={"zone": "handbook", "name": "../../Launch Notes.md"},
        content=markdown.encode(),
        headers={"content-type": "text/markdown"},
    )

    assert response.status_code == 201, response.text
    node = response.json()
    assert node["name"] == "Launch Notes.md"
    assert node["path"].startswith("drive/handbook/")
    assert ".." not in node["path"]
    assert client.get(f"/api/v1/drive/nodes/{node['id']}").json()["content"] == markdown

    unsupported = client.post(
        "/api/v1/drive/files",
        params={"zone": "knowledge", "name": "payload.exe"},
        content=b"not allowed",
    )
    assert unsupported.status_code == 400


def test_legacy_artifacts_migrate_to_drive(client, db, default_company_id):
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="legacy-migration",
        description="v0.2 artifact",
        goal="",
        status="completed",
        owner_id=None,
        source_order_text="",
    )
    old_dir = Path(settings.data_root) / "projects" / str(project.id) / "docs"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "prd-legacy-prd.md"
    old_file.write_text("legacy content", encoding="utf-8")
    project_repo.create_artifact(
        db,
        company_id=default_company_id,
        project_id=project.id,
        task_id=None,
        type="prd",
        title="Legacy PRD",
        content="legacy content",
        path=str(old_file),
        status="draft",
        author_id=None,
    )
    db.commit()

    migrate_artifacts_to_drive(db)

    node = drive_repo.get_node_by_path(db, "drive/projects/legacy-migration/docs/prd-legacy-prd.md")
    assert node is not None
    assert node.doc_type == "prd"
    assert node.project_id == project.id
    assert node.current_version == 1
    # file moved, content preserved
    assert not old_file.exists()
    assert (Path(settings.data_root) / node.path).read_text(encoding="utf-8") == "legacy content"
    # backup taken before moving
    assert (Path(settings.data_root) / "projects.bak" / str(project.id) / "docs").is_dir()
    revisions = drive_repo.list_revisions(db, node.id)
    assert len(revisions) == 1
    assert revisions[0].version == 1
    assert revisions[0].sha256 == hashlib.sha256(b"legacy content").hexdigest()

    # idempotent: a second run changes nothing
    before = len(drive_repo.list_nodes(db))
    migrate_artifacts_to_drive(db)
    assert len(drive_repo.list_nodes(db)) == before

    # migrated artifact is visible through the compat API
    artifacts = client.get(f"/api/v1/projects/{project.id}/artifacts").json()
    assert [a["title"] for a in artifacts] == ["Legacy PRD"]
    assert artifacts[0]["content"] == "legacy content"
