"""Project timeline contract: nested portfolio, schedule editing, and actual dates."""

from datetime import UTC, datetime, timedelta

from app.repositories import organization as org_repo
from app.repositories import project as project_repo


def test_portfolio_returns_projects_with_milestones_and_tasks(client, db, default_company_id):
    owner = org_repo.get_employee_by_role(db, default_company_id, "product_manager")
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Portfolio timeline fixture",
        description="Nested execution plan",
        owner_id=owner.id if owner else None,
    )
    milestone = project_repo.create_milestone(
        db,
        project_id=project.id,
        name="Delivery",
        description="Delivery stage",
        owner_id=owner.id if owner else None,
        order=1,
    )
    task = project_repo.create_task(
        db,
        project_id=project.id,
        milestone_id=milestone.id,
        title="Ship the release",
        kind="general",
        status="in_progress",
        assignee_id=owner.id if owner else None,
        sequence=1,
    )
    db.commit()

    response = client.get("/api/v1/projects/portfolio")
    assert response.status_code == 200, response.text
    nested = next(item for item in response.json() if item["id"] == project.id)
    assert nested["milestones"][0]["id"] == milestone.id
    assert nested["milestones"][0]["owner_id"] == (owner.id if owner else None)
    assert nested["tasks"][0]["id"] == task.id
    assert "planned_start_at" in nested
    assert "planned_start_at" in nested["milestones"][0]
    assert "planned_start_at" in nested["tasks"][0]


def test_schedule_patch_validates_ranges_and_records_actual_dates(client, db, default_company_id):
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Schedule patch fixture",
        description="Schedule validation",
    )
    task = project_repo.create_task(
        db,
        project_id=project.id,
        title="Scheduled work",
        kind="general",
        status="backlog",
        sequence=1,
    )
    db.commit()

    start = datetime(2026, 9, 4, tzinfo=UTC)
    end = start + timedelta(days=3)
    response = client.patch(
        f"/api/v1/tasks/{task.id}",
        json={
            "planned_start_at": start.isoformat(),
            "planned_end_at": end.isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["planned_start_at"].startswith("2026-09-04")

    invalid = client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"planned_end_at": (start - timedelta(days=1)).isoformat()},
    )
    assert invalid.status_code == 400

    assert client.patch(f"/api/v1/tasks/{task.id}", json={"status": "todo"}).status_code == 200
    started = client.patch(f"/api/v1/tasks/{task.id}", json={"status": "in_progress"})
    assert started.status_code == 200
    assert started.json()["actual_start_at"] is not None

    assert client.patch(f"/api/v1/tasks/{task.id}", json={"status": "in_review"}).status_code == 200
    finished = client.patch(f"/api/v1/tasks/{task.id}", json={"status": "done"})
    assert finished.status_code == 200
    assert finished.json()["actual_end_at"] is not None


def test_milestone_schedule_and_owner_are_editable(client, db, default_company_id):
    owner = org_repo.get_employee_by_role(db, default_company_id, "researcher")
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="Milestone patch fixture",
        description="Milestone ownership",
    )
    milestone = project_repo.create_milestone(
        db,
        project_id=project.id,
        name="Research",
        description="Research stage",
        order=1,
    )
    db.commit()

    response = client.patch(
        f"/api/v1/projects/{project.id}/milestones/{milestone.id}",
        json={
            "owner_id": owner.id if owner else None,
            "planned_start_at": "2026-09-03T00:00:00Z",
            "planned_end_at": "2026-09-08T00:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["owner_id"] == (owner.id if owner else None)
    assert response.json()["planned_end_at"].startswith("2026-09-08")
