"""§5: full order → completed closed loop in mock mode."""

import time

TIMEOUT_SEC = 60


def test_project_workflow(client, employees_by_slug):
    resp = client.post(
        "/api/v1/projects", json={"name": "Demo App", "description": "Build a todo CLI"}
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert resp.json()["status"] == "requested"

    deadline = time.time() + TIMEOUT_SEC
    status = ""
    while time.time() < deadline:
        detail = client.get(f"/api/v1/projects/{project_id}").json()
        status = detail["status"]
        if status == "completed":
            break
        time.sleep(0.2)
    assert status == "completed", f"project did not complete within {TIMEOUT_SEC}s: {status}"

    # milestone/task graph exists
    kinds = {t["kind"] for t in detail["tasks"]}
    assert {
        "order_review",
        "planning",
        "research",
        "development",
        "testing",
        "final_review",
    } <= kinds
    assert {m["name"] for m in detail["milestones"]} == {
        "Discovery",
        "Build",
        "Verify",
        "Release",
    }

    # all expected artifact types were produced
    artifacts = client.get("/api/v1/artifacts", params={"project_id": project_id}).json()
    artifact_types = {a["type"] for a in artifacts}
    assert {"prd", "research_report", "source_code", "test_report", "release"} <= artifact_types

    # graph endpoint is React Flow-ready
    graph = client.get(f"/api/v1/projects/{project_id}/graph").json()
    assert len(graph["nodes"]) == len(detail["tasks"])
    assert len(graph["edges"]) == 3  # research→development→testing→final_review

    # every employee that worked has learning records and skills
    for slug in ("alice", "morgan", "bob", "charlie", "dana"):
        employee_id = employees_by_slug[slug]["id"]
        records = client.get(f"/api/v1/employees/{employee_id}/learning-records").json()
        assert records, f"no learning records for {slug}"
        skills = client.get(f"/api/v1/employees/{employee_id}/skills").json()
        assert skills and skills[0]["attempts"] >= 1, f"no skills for {slug}"
        # high-confidence reflection produced private knowledge
        knowledge = client.get(f"/api/v1/employees/{employee_id}/knowledge").json()
        assert knowledge, f"no private knowledge for {slug}"

    # events were persisted
    events = client.get("/api/v1/events", params={"limit": 500}).json()
    event_types = {e["type"] for e in events}
    assert {"project.created", "project.started", "project.completed"} <= event_types
    assert {"task.created", "task.started", "task.completed"} <= event_types
    assert {"artifact.created", "learning.completed", "knowledge.created"} <= event_types
