"""Runtime tools use the backend actor's company, without a human HTTP identity."""

import pytest

from app.core.request_context import (
    RequestIdentity,
    get_request_identity,
    reset_request_identity,
    set_request_identity,
)
from app.models.decision import ToolAudit
from app.models.organization import Company
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.services import projects as project_service
from app.work import tool_executor as executor


@pytest.mark.parametrize("foreign_http_context", [False, True])
def test_runtime_project_list_uses_actor_company(db, default_company_id, foreign_http_context):
    employee = org_repo.get_employee_by_role(db, default_company_id, "ceo")
    assert employee is not None
    other = Company(name="Other company", slug="runtime-read-other-company")
    db.add(other)
    db.flush()
    own = project_repo.create_project(
        db, company_id=default_company_id, name="Visible project", description="Own facts"
    )
    foreign = project_repo.create_project(
        db, company_id=other.id, name="Private project", description="Foreign facts"
    )
    db.commit()
    context = executor.context_for_employee(db, employee, origin="internal")
    assert get_request_identity() is None
    token = None
    if foreign_http_context:
        token = set_request_identity(
            RequestIdentity(user_id=1, company_id=other.id, membership_role="owner", session_id=1)
        )
    try:
        if foreign_http_context:
            # The shared human query keeps its existing request-local scope.
            assert [row.id for row in project_service.get_project_portfolio(db)] == [foreign.id]
        result = executor.execute_tool(db, name="list_company_projects", context=context)
        assert result.ok, result.error
        ids = {row["project_id"] for row in result.data["projects"]}
        assert own.id in ids
        assert foreign.id not in ids
        audit = db.get(ToolAudit, result.audit_id)
        assert audit.actor_company_id == default_company_id
        assert audit.result_json["projects"] == result.data["projects"]
    finally:
        if token is not None:
            reset_request_identity(token)
