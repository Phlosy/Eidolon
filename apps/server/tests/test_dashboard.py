"""Dashboard counts stay company-scoped and bounded as the company grows."""

import hashlib
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.models.auth import CompanyMembership, User, UserSession
from app.models.base import utcnow
from app.models.drive import DriveNode
from app.models.knowledge import Skill
from app.models.organization import Company, Employee
from app.models.person import Person
from app.models.project import Project, Task
from app.repositories import organization as org_repo
from app.services import employees as employee_service


@pytest.fixture
def records(db):
    rows = []

    def add(row):
        db.add(row)
        db.flush()
        rows.append((type(row), row.id))
        return row

    def account():
        key = uuid4().hex
        company = add(Company(name="Dashboard company", slug=f"dashboard-{key}"))
        user = add(
            User(
                email=f"dashboard-{key}@example.com",
                username=f"dashboard-{key}",
                email_verified=True,
                password_hash="unused",
            )
        )
        add(CompanyMembership(company_id=company.id, user_id=user.id, role="OWNER"))
        token = uuid4().hex
        session = add(
            UserSession(
                user_id=user.id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                csrf_token=uuid4().hex,
                expires_at=utcnow() + timedelta(hours=1),
                last_seen_at=utcnow(),
            )
        )
        return SimpleNamespace(
            company_id=company.id,
            user_id=user.id,
            session_id=session.id,
            headers={"Cookie": f"{settings.session_cookie_name}={token}"},
        )

    def employee(company_id, *, status="idle", with_person=True):
        key = uuid4().hex
        person = add(Person(name="Dashboard person", slug=key)) if with_person else None
        return add(
            Employee(
                company_id=company_id,
                person_id=person.id if person else None,
                name="Dashboard employee",
                slug=key,
                workspace_path=f"dashboard/{key}",
                memory_namespace=key,
                status=status,
            )
        )

    def project(company_id, status="requested", task_statuses=()):
        row = add(Project(company_id=company_id, name="Dashboard project", status=status))
        for task_status in task_statuses:
            add(Task(project_id=row.id, title="Dashboard task", status=task_status))
        return row

    def document(company_id, kind="document"):
        return add(
            DriveNode(
                company_id=company_id,
                name="Dashboard document",
                kind=kind,
                path=f"dashboard/{uuid4().hex}",
            )
        )

    try:
        yield SimpleNamespace(
            add=add, account=account, employee=employee, project=project, document=document
        )
    finally:
        db.rollback()
        for model, row_id in reversed(rows):
            db.execute(delete(model).where(model.id == row_id))
        db.commit()


@contextmanager
def identity(account):
    token = set_request_identity(
        RequestIdentity(
            user_id=account.user_id,
            company_id=account.company_id,
            session_id=account.session_id,
            membership_role="OWNER",
        )
    )
    try:
        yield
    finally:
        reset_request_identity(token)


def summary(client, account=None, **params):
    response = client.get(
        "/api/v1/dashboard/summary", headers=account.headers if account else None, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_company_returns_zero_metrics(client, db, records):
    account = records.account()
    db.commit()
    assert summary(client, account) == {
        "company_id": account.company_id,
        "employees_online": 0,
        "employees_total": 0,
        "active_projects": 0,
        "completed_projects": 0,
        "tasks_in_progress": 0,
        "tasks_total": 0,
        "tasks_completed": 0,
        "documents_count": 0,
        "total_attempts": 0,
        "project_summaries": [],
    }


def test_mixed_states_preserve_counts_person_attempts_and_project_preview(client, db, records):
    own, foreign = records.account(), records.account()
    employees = [
        records.employee(own.company_id, status=status, with_person=index != 3)
        for index, status in enumerate(("offline", "idle", "working", "error"))
    ]
    foreign_employee = records.employee(foreign.company_id)
    skills = [
        (employees[0].id, employees[0].person_id, 2),
        (None, employees[0].person_id, 3),
        (employees[1].id, employees[1].person_id, 7),
        (employees[1].id, None, 100),  # mapped employees use person truth, not old mirrors
        (employees[3].id, None, 11),  # an unmapped legacy employee retains the old fallback
        (foreign_employee.id, employees[0].person_id, 13),  # person wins over a stale mirror
        (foreign_employee.id, foreign_employee.person_id, 999),
        (None, None, 9999),
    ]
    for employee_id, person_id, attempts in skills:
        records.add(
            Skill(
                employee_id=employee_id,
                person_id=person_id,
                name="dashboard skill",
                attempts=attempts,
            )
        )
    statuses = (
        "requested",
        "planning",
        "in_progress",
        "in_review",
        "completed",
        "cancelled",
        "rejected",
        "waiting_for_management",
    )
    task_statuses = (
        "backlog",
        "todo",
        "in_progress",
        "in_review",
        "done",
        "failed",
        "rejected",
        "blocked",
        "cancelled",
    )
    projects = [records.project(own.company_id, status, task_statuses) for status in statuses]
    records.project(foreign.company_id, "completed", ["done"] * 11)
    records.document(own.company_id)
    records.document(own.company_id)
    records.document(own.company_id, "folder")
    records.document(foreign.company_id)
    records.document(None)  # unscoped rows never join a company's counts
    db.commit()

    result = summary(client, own, company_id=foreign.company_id)
    assert result == {
        "company_id": own.company_id,
        "employees_online": 3,
        "employees_total": 4,
        "active_projects": 4,
        "completed_projects": 1,
        "tasks_in_progress": 16,
        "tasks_total": 72,
        "tasks_completed": 8,
        "documents_count": 2,
        "total_attempts": 36,
        "project_summaries": [
            {
                "project_id": projects[index].id,
                "tasks_total": 9,
                "tasks_completed": 1,
                "blockers": 2,
            }
            for index in (7, 3, 2, 1)
        ],
    }
    with identity(own):
        assert result["total_attempts"] == sum(
            employee_service.get_performance(db, employee.id).attempts for employee in employees
        )
    other = summary(client, foreign, company_id=own.company_id)
    assert other == {
        "company_id": foreign.company_id,
        "employees_online": 1,
        "employees_total": 1,
        "active_projects": 0,
        "completed_projects": 1,
        "tasks_in_progress": 0,
        "tasks_total": 11,
        "tasks_completed": 11,
        "documents_count": 1,
        "total_attempts": 999,
        "project_summaries": [],
    }


def test_project_preview_includes_empty_waiting_project(client, db, records):
    account = records.account()
    project = records.project(account.company_id, "waiting_for_management")
    db.commit()
    result = summary(client, account)
    assert result["active_projects"] == 0  # preserve the KPI's four-status definition
    assert result["project_summaries"] == [
        {"project_id": project.id, "tasks_total": 0, "tasks_completed": 0, "blockers": 0}
    ]


def test_auth_required_rejects_anonymous_dashboard(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_legacy_anonymous_dashboard_uses_only_default_company(client, db, records, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    company = org_repo.get_default_company(db)
    before = summary(client)
    own_project = records.project(company.id, "requested", ["done", "in_review"])
    records.employee(company.id)
    records.document(company.id)
    foreign = records.account()
    records.project(foreign.company_id, "requested", ["done"] * 20)
    records.employee(foreign.company_id)
    records.document(foreign.company_id)
    db.commit()
    after = summary(client, company_id=foreign.company_id)
    assert after["company_id"] == company.id
    assert after["employees_total"] == before["employees_total"] + 1
    assert after["active_projects"] == before["active_projects"] + 1
    assert after["tasks_total"] == before["tasks_total"] + 2
    assert after["tasks_completed"] == before["tasks_completed"] + 1
    assert after["tasks_in_progress"] == before["tasks_in_progress"] + 1
    assert after["documents_count"] == before["documents_count"] + 1
    assert after["project_summaries"][0]["project_id"] == own_project.id


def test_sql_query_count_stays_constant_with_growing_cold_company(db, records):
    from app.services.dashboard import get_summary

    account = records.account()
    counts = []
    for additional in (1, 8, 30):  # cold company sizes: 1, 9, then 39
        for _ in range(additional):
            employee = records.employee(account.company_id)
            records.add(Skill(name="dashboard skill", person_id=employee.person_id, attempts=3))
            records.project(account.company_id, "in_progress", ["done", "in_progress"])
        db.commit()
        statements = []
        with db.get_bind().connect() as connection:

            def capture(
                _conn,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
                captured=statements,
            ):
                if statement.lstrip().upper().startswith("SELECT"):
                    captured.append(statement)

            event.listen(connection, "before_cursor_execute", capture)
            with Session(bind=connection) as cold_db, identity(account):
                result = get_summary(cold_db)
                assert len(result.project_summaries) == min(result.active_projects, 4)
            counts.append(len(statements))
    assert counts == [7, 7, 7]


def test_missing_company_returns_not_found(db):
    from fastapi import HTTPException

    from app.services.dashboard import get_summary

    missing_id = max(db.scalars(select(Company.id))) + 1000
    with identity(SimpleNamespace(company_id=missing_id, user_id=0, session_id=0)):
        with pytest.raises(HTTPException) as error:
            get_summary(db)
    assert error.value.status_code == 404
