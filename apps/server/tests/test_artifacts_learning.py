"""Artifacts as real files + learning retrieval injection (v0.2)."""

import hashlib
import time
from pathlib import Path

from app.core.config import settings
from app.learning import retrieval
from app.models.enums import KnowledgeScope
from app.repositories import knowledge as knowledge_repo


def _wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_artifact_written_to_disk_with_sha256(client, no_work_intake):
    # M2.1：只要一个项目容器（不跑仪式、不跑 Agent）⇒ 显式 managed。
    # 测试公司的 Work Intake 职位无人任职，因此项目停在 waiting_for_management，
    # 不产生任何副作用 —— 这正是我们要的"干净容器"。
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "artifact-file-check",
            "description": "验证 artifact 落盘",
            "work_mode": "managed",
        },
    ).json()
    content = "# Hello\n\nartifact body for sha256 check\n"
    response = client.post(
        "/api/v1/artifacts",
        json={
            "project_id": project["id"],
            "type": "research_report",
            "title": "File Check",
            "content": content,
        },
    )
    assert response.status_code == 201
    artifact = response.json()
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert artifact["sha256"] == expected
    path = Path(artifact["path"])
    assert path.exists()
    assert path.read_text(encoding="utf-8") == content
    # v0.3: research_report lands under the project's drive docs/ folder
    assert path.parent == (
        Path(settings.data_root) / "drive" / "projects" / "artifact-file-check" / "docs"
    )


def test_workflow_artifacts_materialized_with_session_link(client):
    # M2.1（D3/W33）：全链路回归 = 基础设施项目 ⇒ 显式请求确定性规划 fixture
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "workflow-artifacts",
            "description": "全链路 artifact 落盘验证",
            "planning_fixture": "deterministic_template",
        },
    ).json()

    def done():
        detail = client.get(f"/api/v1/projects/{project['id']}").json()
        return detail["status"] == "completed"

    assert _wait_for(done), "project did not complete"
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    artifacts = detail["artifacts"]
    assert len(artifacts) >= 5  # order review, PRD, research, code, test report, release
    for artifact in artifacts:
        assert artifact["sha256"]
        assert artifact["path"] and Path(artifact["path"]).exists()
        assert artifact["work_session_id"] is not None
        on_disk = Path(artifact["path"]).read_text(encoding="utf-8")
        assert hashlib.sha256(on_disk.encode("utf-8")).hexdigest() == artifact["sha256"]


def test_retrieval_matches_private_knowledge(db, employees_by_slug):
    alice = employees_by_slug["alice"]
    bob = employees_by_slug["bob"]
    knowledge_repo.create_knowledge_item(
        db,
        scope=KnowledgeScope.private.value,
        owner_employee_id=alice["id"],
        title="onboarding portal 经验",
        content="上次做过 onboarding portal，注意权限模型",
        topic="onboarding portal",
        confidence=0.9,
        sources=[],
    )
    db.commit()

    # v1: retrieve_for_task 返回 RetrievalResult（带 skill id，§9），旧调用点只取名字
    result = retrieval.retrieve_for_task(
        db, alice["id"], "订单评审：onboarding portal", "构建 onboarding portal"
    )
    assert "onboarding portal" in result.knowledge
    # 默认策略不放开候选技能（等价性专项断言见 tests/test_brain_contract.py）
    assert not [skill for skill in result.skills if skill.is_candidate]

    # private knowledge does not leak across employees
    knowledge_bob = retrieval.retrieve_for_task(
        db, bob["id"], "订单评审：onboarding portal", "构建 onboarding portal"
    ).knowledge
    assert "onboarding portal" not in knowledge_bob

    # no overlap → no injection
    none_hit = retrieval.retrieve_for_task(db, alice["id"], " unrelated xyzzy", "").knowledge
    assert "onboarding portal" not in none_hit


def test_mock_runtime_weaves_prior_knowledge(client, db, employees_by_slug):
    """End-to-end: private knowledge for the CEO shows up in the order-review artifact."""
    alice = employees_by_slug["alice"]
    marker_topic = f"zztopic-{int(time.time() * 1000)}"
    knowledge_repo.create_knowledge_item(
        db,
        scope=KnowledgeScope.private.value,
        owner_employee_id=alice["id"],
        title=f"{marker_topic} 经验",
        content="经验内容",
        topic=marker_topic,
        confidence=0.9,
        sources=[],
    )
    db.commit()

    # M2.1（D3/W33）：需要真实派发才能观察检索注入 ⇒ 显式请求确定性 fixture
    project = client.post(
        "/api/v1/projects",
        json={
            "name": f"{marker_topic} project",
            "description": "测试检索注入",
            "planning_fixture": "deterministic_template",
        },
    ).json()

    def order_review_done():
        detail = client.get(f"/api/v1/projects/{project['id']}").json()
        return any(a["type"] == "plan" for a in detail["artifacts"])

    assert _wait_for(order_review_done), "order review artifact not produced"
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    plan = next(a for a in detail["artifacts"] if a["type"] == "plan")
    assert "using prior knowledge" in plan["content"]
    assert marker_topic in plan["content"]
