"""Structured project intake, human review gates, baselines and delivery."""

from io import BytesIO
from zipfile import ZipFile

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Base
from app.models.organization import Employee
from app.services.seed import seed_default_company


def _structured_project(owner_id: int) -> dict:
    return {
        "name": "Classic Snake",
        "code": "SNAKE",
        "priority": "high",
        "customer": "Eidolon Tutorial",
        "owner_id": owner_id,
        "background": "验证完整的软件项目交付生命周期。",
        "objectives": ["提供可在浏览器运行的经典贪吃蛇游戏"],
        "requirements": [
            {
                "code": "REQ-001",
                "title": "游戏区域",
                "description": "显示清晰的游戏区域。",
                "priority": "must",
                "acceptance_criteria": "打开页面后可见游戏网格。",
            },
            {
                "code": "REQ-002",
                "title": "键盘控制",
                "description": "支持方向键控制蛇移动。",
                "priority": "must",
                "acceptance_criteria": "四个方向键均能改变移动方向。",
            },
        ],
        "technical_requirements": ["React", "Vite", "TypeScript", "启动时间 < 3 seconds"],
        "constraints": ["无需 Backend", "可离线部署"],
        "deliverables": [
            "Source Code",
            "Production Build",
            "Requirements Report",
            "Design Document",
            "Test Report",
            "Acceptance Report",
        ],
        "review_configuration": {
            "requirements_review": True,
            "design_review": True,
            "acceptance_review": True,
            "additional_reviews": [],
        },
        "participants": {
            "project_owner_employee_id": owner_id,
            "presenter_employee_id": owner_id,
            "reviewer_names": ["Customer"],
            "approver_names": ["Customer"],
        },
        "tutorial_accelerated": True,
    }


def _phase(lifecycle: dict, phase_type: str) -> dict:
    return next(phase for phase in lifecycle["phases"] if phase["phase_type"] == phase_type)


def _complete_phase(client, project_id: int, lifecycle: dict, phase_type: str) -> dict:
    phase = _phase(lifecycle, phase_type)
    response = client.post(f"/api/v1/projects/{project_id}/phases/{phase['id']}/complete")
    assert response.status_code == 200, response.text
    return response.json()


def _decide(client, review_id: int, decision: str, comments: str = "确认") -> dict:
    response = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        json={"decision": decision, "comments": comments, "action_items": []},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_structured_project_runs_real_review_gates_and_delivery(client, employees_by_slug):
    owner_id = employees_by_slug["alice"]["id"]
    response = client.post("/api/v1/projects", json=_structured_project(owner_id))
    assert response.status_code == 201, response.text
    project = response.json()
    project_id = project["id"]
    assert project["code"] == "SNAKE"
    assert project["status"] == "in_progress"

    lifecycle = client.get(f"/api/v1/projects/{project_id}/lifecycle").json()
    assert [phase["phase_type"] for phase in lifecycle["phases"]] == [
        "initiation",
        "requirements_analysis",
        "requirements_review",
        "system_design",
        "system_design_review",
        "development",
        "internal_testing",
        "user_acceptance_testing",
        "acceptance_review",
        "delivery",
        "project_archive",
    ]
    assert _phase(lifecycle, "initiation")["status"] == "completed"
    assert _phase(lifecycle, "requirements_analysis")["status"] == "in_progress"
    assert any(
        document["document_type"] == "project_charter" for document in lifecycle["documents"]
    )

    lifecycle = _complete_phase(client, project_id, lifecycle, "requirements_analysis")
    requirements_gate = _phase(lifecycle, "requirements_review")
    assert requirements_gate["status"] == "waiting_review"
    assert lifecycle["pending_user_action"]["review_id"] == requirements_gate["review_id"]
    review = client.get(f"/api/v1/reviews/{requirements_gate['review_id']}").json()
    assert {document["format"] for document in review["package"]["documents"]} >= {
        "docx",
        "pptx",
        "markdown",
    }
    assert review["presenter_employee_id"] == owner_id

    lifecycle = _decide(client, review["id"], "approved")
    assert _phase(lifecycle, "requirements_review")["status"] == "completed"
    assert _phase(lifecycle, "system_design")["status"] == "in_progress"
    assert any(baseline["baseline_type"] == "requirements" for baseline in lifecycle["baselines"])

    lifecycle = _complete_phase(client, project_id, lifecycle, "system_design")
    first_design_review = _phase(lifecycle, "system_design_review")["review_id"]
    lifecycle = _decide(
        client,
        first_design_review,
        "changes_requested",
        "补充离线部署和异常处理设计。",
    )
    assert _phase(lifecycle, "system_design")["status"] == "changes_requested"

    lifecycle = _complete_phase(client, project_id, lifecycle, "system_design")
    second_design_review = _phase(lifecycle, "system_design_review")["review_id"]
    assert second_design_review != first_design_review
    second_review = client.get(f"/api/v1/reviews/{second_design_review}").json()
    assert second_review["package"]["version"] == 2
    revised_document = next(
        document
        for document in second_review["package"]["documents"]
        if document["document_type"] == "system_design_specification"
    )
    assert revised_document["version_label"] == "v0.2"
    revised_drive_node = client.get(
        f"/api/v1/drive/nodes/{revised_document['drive_node_id']}"
    ).json()
    assert "v0.2" in revised_drive_node["name"]
    lifecycle = _decide(client, second_design_review, "conditionally_approved", "按行动项完善。")
    assert _phase(lifecycle, "development")["status"] == "in_progress"
    assert any(baseline["baseline_type"] == "design" for baseline in lifecycle["baselines"])
    development_phase_id = _phase(lifecycle, "development")["id"]
    project_detail = client.get(f"/api/v1/projects/{project_id}").json()
    development_tasks = [
        task for task in project_detail["tasks"] if task["title"].startswith("DEV-")
    ]
    assert len(development_tasks) == 5
    assert all(task["phase_id"] == development_phase_id for task in development_tasks)

    lifecycle = _complete_phase(client, project_id, lifecycle, "development")
    completed_tasks = client.get(f"/api/v1/projects/{project_id}").json()["tasks"]
    assert all(
        task["status"] == "done"
        for task in completed_tasks
        if task["phase_id"] == development_phase_id
    )
    lifecycle = _complete_phase(client, project_id, lifecycle, "internal_testing")
    lifecycle = _complete_phase(client, project_id, lifecycle, "user_acceptance_testing")
    acceptance_review_id = _phase(lifecycle, "acceptance_review")["review_id"]
    lifecycle = _decide(client, acceptance_review_id, "approved")
    assert _phase(lifecycle, "delivery")["status"] == "in_progress"

    lifecycle = _complete_phase(client, project_id, lifecycle, "delivery")
    assert lifecycle["project"]["status"] == "completed"
    assert lifecycle["delivery_packages"]
    manifest = lifecycle["delivery_packages"][0]["manifest"]
    assert {"source", "build", "documents", "review_materials", "project_history"} <= set(manifest)
    assert any(item["document_type"] == "source_code" for item in lifecycle["documents"])
    assert any(item["document_type"] == "production_build" for item in lifecycle["documents"])
    assert {
        "user_manual",
        "deployment_manual",
        "release_notes",
        "delivery_checklist",
    } <= {item["document_type"] for item in lifecycle["documents"]}
    package_response = client.get(
        f"/api/v1/drive/nodes/{lifecycle['delivery_packages'][0]['drive_node_id']}/content"
    )
    assert package_response.status_code == 200
    with ZipFile(BytesIO(package_response.content)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert any(name.startswith("source/") and name.endswith(".zip") for name in names)
        assert any(name.startswith("build/") and name.endswith(".zip") for name in names)
        assert any(name.startswith("documents/") and "User Manual" in name for name in names)

    traceability = client.get(f"/api/v1/projects/{project_id}/traceability").json()
    assert traceability["requirements"]
    assert all(item["design_refs"] for item in traceability["requirements"])
    assert all(item["implementation_refs"] for item in traceability["requirements"])
    assert all(item["test_refs"] for item in traceability["requirements"])
    assert all(item["acceptance_refs"] for item in traceability["requirements"])


def test_change_request_preserves_existing_baseline(client, employees_by_slug):
    owner_id = employees_by_slug["alice"]["id"]
    payload = _structured_project(owner_id)
    payload.update({"name": "Classic Snake Change", "code": "SNAKE-CR"})
    project = client.post("/api/v1/projects", json=payload).json()
    lifecycle = client.get(f"/api/v1/projects/{project['id']}/lifecycle").json()
    lifecycle = _complete_phase(client, project["id"], lifecycle, "requirements_analysis")
    review_id = _phase(lifecycle, "requirements_review")["review_id"]
    lifecycle = _decide(client, review_id, "approved")
    original = next(b for b in lifecycle["baselines"] if b["baseline_type"] == "requirements")
    baseline_document = next(
        document
        for document in lifecycle["documents"]
        if document["id"] in original["document_artifact_ids"] and document["format"] == "docx"
    )
    direct_edit = client.patch(
        f"/api/v1/drive/nodes/{baseline_document['drive_node_id']}",
        json={"content": "overwrite approved requirements"},
    )
    assert direct_edit.status_code == 409

    created = client.post(
        f"/api/v1/projects/{project['id']}/change-requests",
        json={
            "title": "增加难度选择",
            "reason": "客户希望增加游戏可玩性",
            "requested_by": "Customer",
            "priority": "high",
            "affected_requirements": ["REQ-001"],
        },
    )
    assert created.status_code == 201, created.text
    change = created.json()
    assert change["code"] == "CR-001"
    assert change["status"] == "impact_analysis"
    analyzed = client.post(f"/api/v1/change-requests/{change['id']}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["status"] == "waiting_approval"
    approved = client.post(
        f"/api/v1/change-requests/{change['id']}/decision",
        json={"decision": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "implementing"
    closed = client.post(f"/api/v1/change-requests/{change['id']}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert (
        client.get(f"/api/v1/projects/{project['id']}/lifecycle").json()["baselines"][0]["id"]
        == original["id"]
    )


def test_tutorial_skip_is_persistent_and_creates_no_business_data(client):
    """跳过只写状态，不产生业务数据。

    能整体跳过的只有 First Project Practice —— 核心教程靠真实动作通关，
    一键跳过它等于承认"没做完也算做完"，所以那里明确返回 409。
    """
    before_employees = client.get("/api/v1/employees").json()
    before_projects = client.get("/api/v1/projects").json()

    started = client.post("/api/v1/practice/start").json()
    assert started["status"] == "active"
    skipped = client.post("/api/v1/practice/skip").json()
    assert skipped["status"] == "skipped"
    assert client.get("/api/v1/practice").json()["progress"]["status"] == "skipped"

    assert client.post("/api/v1/tutorial/skip").status_code == 409

    assert client.get("/api/v1/employees").json() == before_employees
    assert client.get("/api/v1/projects").json() == before_projects


def test_new_company_has_no_employees_without_demo_seed(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-company.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "seed_demo_workforce", False, raising=False)
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "data"))
    with Session(engine) as db:
        company = seed_default_company(db)
        employees = list(db.scalars(select(Employee).where(Employee.company_id == company.id)))
        assert employees == []
        assert len(company.departments) >= 5
