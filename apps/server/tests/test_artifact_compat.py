"""Artifact compat layer (v0.3): legacy /artifacts shape over drive documents."""

import json

ARTIFACT_FIELDS = {
    "id",
    "company_id",
    "project_id",
    "task_id",
    "type",
    "title",
    "content",
    "path",
    "sha256",
    "version",
    "status",
    "author_id",
    "work_session_id",
    "created_at",
    "updated_at",
}


def test_artifact_compat_response_shape(client):
    project = client.post(
        "/api/v1/projects", json={"name": "compat-shape", "description": "兼容层形状"}
    ).json()
    content = "# PRD\n\ncompat body"
    resp = client.post(
        "/api/v1/artifacts",
        json={
            "project_id": project["id"],
            "type": "prd",
            "title": "Compat PRD",
            "content": content,
        },
    )
    assert resp.status_code == 201, resp.text
    artifact = resp.json()
    assert set(artifact.keys()) == ARTIFACT_FIELDS
    assert artifact["project_id"] == project["id"]
    assert artifact["type"] == "prd"
    assert artifact["title"] == "Compat PRD"
    assert artifact["content"] == content
    assert artifact["version"] == 1
    assert artifact["status"] == "draft"
    assert artifact["task_id"] is None
    assert "/drive/projects/compat-shape/docs/" in artifact["path"]

    # GET /artifacts/{id} returns the same shape with content from disk
    fetched = client.get(f"/api/v1/artifacts/{artifact['id']}").json()
    assert set(fetched.keys()) == ARTIFACT_FIELDS
    assert fetched["content"] == content

    # unknown id → 404
    assert client.get("/api/v1/artifacts/999999").status_code == 404


def _bare_project(client, name: str) -> dict:
    """只建一个项目容器（M2.1：显式 managed + 不路由管理动作）。

    引导形态（guided）会在立项时生成 Project Charter 等仪式文档；managed 在有在任者时
    会起一个接收任务并异步派发 mock 运行时。本文件测的是 **artifact 兼容层的过滤语义**，
    需要干净的容器 ⇒ 显式 managed + `no_work_intake`（停在 waiting_for_management）。
    """
    return client.post(
        "/api/v1/projects",
        json={"name": name, "description": name, "work_mode": "managed"},
    ).json()


def test_project_artifacts_filter(client, no_work_intake):
    project_a = _bare_project(client, "compat-filter-a")
    project_b = _bare_project(client, "compat-filter-b")
    client.post(
        "/api/v1/artifacts",
        json={"project_id": project_a["id"], "type": "prd", "title": "A PRD", "content": "a"},
    )
    client.post(
        "/api/v1/artifacts",
        json={
            "project_id": project_b["id"],
            "type": "research_report",
            "title": "B Research",
            "content": "b",
        },
    )

    a_docs = client.get(f"/api/v1/projects/{project_a['id']}/artifacts").json()
    assert {a["title"] for a in a_docs} == {"A PRD"}
    assert all(a["project_id"] == project_a["id"] for a in a_docs)

    # /artifacts?project_id=&type= filters compose
    filtered = client.get(
        "/api/v1/artifacts", params={"project_id": project_b["id"], "type": "prd"}
    ).json()
    assert filtered == []
    filtered = client.get(
        "/api/v1/artifacts", params={"project_id": project_b["id"], "type": "research_report"}
    ).json()
    assert [a["title"] for a in filtered] == ["B Research"]


def test_compat_payload_never_leaks_drive_internals(client):
    project = client.post(
        "/api/v1/projects", json={"name": "compat-leak", "description": "x"}
    ).json()
    resp = client.post(
        "/api/v1/artifacts",
        json={"project_id": project["id"], "type": "other", "title": "Leak", "content": "x"},
    )
    body = resp.json()
    assert "doc_type" not in json.dumps(body)
    assert "parent_id" not in body
