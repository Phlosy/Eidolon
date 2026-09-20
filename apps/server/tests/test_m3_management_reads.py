"""Project API and Agent reads share bounded, company-scoped source facts."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.models.event import Event
from app.models.management_dispatch import ManagementDispatch
from app.models.organization import Company, Employee
from app.models.project import Task, WorkSession
from app.repositories import project as project_repo
from app.schemas.project import ProjectManagementOut
from app.work import management_dispatch, tool_executor


@pytest.fixture()
def project_facts(db, default_company_id):
    project = project_repo.create_project(
        db, company_id=default_company_id, name="Waiting for a manager", status="planning"
    )
    actor = db.scalar(select(Employee).where(Employee.slug == "alice"))
    db.commit()
    return SimpleNamespace(
        project=project,
        context=tool_executor.context_for_employee(db, actor, origin="test"),
    )


def observe(db, project, count=1):
    sources = [
        Event(
            type="project.replan_required",
            company_id=project.company_id,
            project_id=project.id,
            payload={"private_body": "not part of the dispatch projection"},
        )
        for _ in range(count)
    ]
    db.add_all(sources)
    db.commit()
    return [management_dispatch.inspect_source(source.id) for source in sources]


def read_tool(db, facts, **args):
    return tool_executor.execute_tool(
        db,
        name="inspect_project",
        args={"project_id": facts.project.id, **args},
        context=facts.context,
    )


def test_api_and_agent_expose_identical_waiting_facts_without_rechecking(client, db, project_facts):
    view = observe(db, project_facts.project)[0]
    response = client.get(f"/api/v1/projects/{project_facts.project.id}/spec")
    assert response.status_code == 200
    result = read_tool(db, project_facts)
    assert result.ok, result.error
    http_management = response.json()["management"]
    tool_management = ProjectManagementOut.model_validate(result.data["management"])
    assert tool_management.model_dump(mode="json") == http_management
    dispatch = http_management["dispatches"][0]
    assert dispatch["source_event_id"] == view.source_event_id
    assert dispatch["waiting_reason"] == "recipient_missing"
    assert dispatch["recipient_employee_id"] is None
    assert dispatch["work_session"] is None and dispatch["resolution"] is None
    assert dispatch["checked_at"] and http_management["dispatches_next_before_id"] is None
    assert "private_body" not in str(http_management)
    db.expire_all()
    assert db.get(ManagementDispatch, view.id).checked_at == view.checked_at.replace(tzinfo=None)
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(WorkSession)) == 0
    assert project_facts.project.status == "planning"


def test_older_waits_remain_accessible_with_the_same_api_and_agent_cursor(
    client, db, project_facts
):
    views = observe(db, project_facts.project, count=3)
    path = f"/api/v1/projects/{project_facts.project.id}/spec"
    first = client.get(path, params={"dispatch_limit": 2}).json()["management"]
    assert [d["id"] for d in first["dispatches"]] == [views[2].id, views[1].id]
    cursor = first["dispatches_next_before_id"]
    assert cursor == views[1].id
    arguments = {"dispatch_limit": 2, "dispatch_before_id": cursor}
    second = client.get(path, params=arguments).json()["management"]
    result = read_tool(db, project_facts, **arguments)
    assert result.ok, result.error
    assert (
        ProjectManagementOut.model_validate(result.data["management"]).model_dump(mode="json")
        == second
    )
    assert [d["id"] for d in second["dispatches"]] == [views[0].id]
    assert second["dispatches"][0]["waiting_reason"] == "recipient_missing"
    assert second["dispatches_next_before_id"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"dispatch_limit": 0},
        {"dispatch_limit": 51},
        {"dispatch_before_id": 0},
        {"dispatch_before_id": -1},
    ],
)
def test_api_and_agent_reject_invalid_dispatch_page_bounds(client, db, project_facts, arguments):
    path = f"/api/v1/projects/{project_facts.project.id}/spec"
    assert client.get(path, params=arguments).status_code == 422
    result = read_tool(db, project_facts, **arguments)
    assert not result.ok
    assert result.reason in {"invalid_arguments", "domain_rejected"}


def test_foreign_project_dispatches_are_not_exposed(client, db, project_facts):
    observe(db, project_facts.project)
    company = Company(name="Private company", slug="private-management-reads")
    db.add(company)
    db.flush()
    foreign = project_repo.create_project(db, company_id=company.id, name="Private project")
    db.commit()
    foreign_dispatch = observe(db, foreign)[0]
    result = read_tool(db, project_facts)
    assert result.ok
    assert foreign_dispatch.id not in {d["id"] for d in result.data["management"]["dispatches"]}
    denied = read_tool(db, project_facts, project_id=foreign.id)
    assert not denied.ok and "management" not in (denied.data or {})
    token = set_request_identity(
        RequestIdentity(
            user_id=1,
            company_id=project_facts.project.company_id,
            membership_role="owner",
            session_id=1,
        )
    )
    try:
        assert client.get(f"/api/v1/projects/{foreign.id}/spec").status_code == 404
    finally:
        reset_request_identity(token)


def test_unobserved_project_stays_empty_when_read(client, db, project_facts):
    db.add(
        Event(
            type="project.replan_required",
            company_id=project_facts.project.company_id,
            project_id=project_facts.project.id,
        )
    )
    db.commit()
    response = client.get(f"/api/v1/projects/{project_facts.project.id}/spec")
    assert response.json()["management"]["dispatches"] == []
    assert read_tool(db, project_facts).data["management"]["dispatches"] == []
    assert db.scalar(select(func.count()).select_from(ManagementDispatch)) == 0
