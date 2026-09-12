"""§5: full order → completed closed loop in mock mode."""

import time

from app.core.config import settings

TIMEOUT_SEC = 60


def test_project_workflow(client, employees_by_slug, monkeypatch):
    # 这条链需要真的执行：显式打开编排器派发（conftest 默认关，见那里的注释）
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    # M2.1（D3/W33）：这是一条**基础设施回归**（orchestrator + mock runtime），
    # 因此显式请求确定性规划 fixture —— 生产项目不会隐式落到固定模板上。
    resp = client.post(
        "/api/v1/projects",
        json={
            "name": "Demo App",
            "description": "Build a todo CLI",
            "planning_fixture": "deterministic_template",
        },
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    # M2.5：fixture 项目在立项时就建好**整张**确定性图（含 Intake 阶段），
    # 所以它一开始就是 in_progress —— 旧实现是"分步生成"，那要求编排器按
    # TaskKind 分支推进，正是本阶段拆掉的东西。
    assert resp.json()["status"] == "in_progress"

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
    # M2.5：确定性图现在包含 Intake / Planning 两个前台阶段（整图在立项时建好）
    assert {m["name"] for m in detail["milestones"]} == {
        "Intake",
        "Planning",
        "Discovery",
        "Build",
        "Verify",
        "Release",
    }
    assert all(m["status"] == "completed" for m in detail["milestones"])

    # all expected artifact types were produced
    artifacts = client.get("/api/v1/artifacts", params={"project_id": project_id}).json()
    artifact_types = {a["type"] for a in artifacts}
    assert {"prd", "research_report", "source_code", "test_report", "release"} <= artifact_types

    # graph endpoint is React Flow-ready
    graph = client.get(f"/api/v1/projects/{project_id}/graph").json()
    assert len(graph["nodes"]) == len(detail["tasks"])
    # 链式依赖：Intake→Planning→Discovery→Build→Verify→Release（5 条边）
    assert len(graph["edges"]) == 5

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
