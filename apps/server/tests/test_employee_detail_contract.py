"""/employees/{id} 派生三区契约（EmployeeDetailOut）—— 由 /talent-roster/{id} 临时宿主承载。

拍板（本轮 P4 收尾）：`/employees/{id}` = 人物详情 + 当前派生任职视图；
`/talent-roster` = 名册查询/筛选/分页。employees.py 是并行 WIP，所以现在**不接线**，
但契约先锁死：`/talent-roster/{id}` 的输出必须能通过 `EmployeeDetailOut` 校验，
并保证三个派生区只读、统一来源、不出现自相矛盾（ASSIGNED 却 occupies=False 之类）。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.schemas.organization import EmployeeDetailOut
from app.workforce.status import WorkforceStatusResolver


@pytest.fixture()
def departments(client):
    """部门 id 从 /company 拿（v0.4 就把部门挂在公司信息里，没有独立列表端点）。"""
    company = client.get("/api/v1/company").json()
    return {row["slug"]: row["id"] for row in company["departments"]}


def _hire(client, departments: dict, tag: str) -> int:
    response = client.post(
        "/api/v1/employees/onboard",
        json={
            "name": tag.replace("-", " ").title(),
            "slug": tag,
            "title": "Contract Tester",
            "role": "engineer",
            "department_id": departments["engineering"],
            "runtime_type": "mock",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["employee"]["id"]


def _roster_detail(client, employee_id: int) -> dict:
    response = client.get(f"/api/v1/talent-roster/{employee_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_detail_contract_validates_for_available_employee(client, departments, fake_gitea):
    """AVAILABLE 的人：current_position=None、integrity valid —— 缺职位不是数据问题。"""
    employee_id = _hire(client, departments, f"detail-avail-{departments['engineering']}")
    body = _roster_detail(client, employee_id)
    parsed = EmployeeDetailOut.model_validate(body)  # 契约锁死：缺派生字段会在这里炸
    assert parsed.workforce_status == "available"
    assert parsed.current_position is None
    assert parsed.has_primary_assignment is False
    assert parsed.occupies_establishment is False
    integrity = parsed.assignment_integrity
    assert integrity.status == "valid"
    assert integrity.issues == []
    assert integrity.read_only is True


def test_detail_derived_zones_come_from_the_single_resolver(client, db, departments, fake_gitea):
    """三区与唯一 resolver 同源（端点不是另算的一份）。"""
    employee_id = _hire(client, departments, f"detail-src-{departments['engineering']}")
    body = _roster_detail(client, employee_id)

    from app.core.database import SessionLocal
    from app.models.organization import Employee
    from app.services import position_service

    with SessionLocal() as session:
        person = session.get(Employee, employee_id)
        view = WorkforceStatusResolver(session).view(person)
        assert body["workforce_status"] == view.workforce_status.value
        issues = position_service.integrity_by_employee(session).get(employee_id, [])
        assert body["assignment_integrity"]["issues"] == issues
        assert body["assignment_integrity"]["status"] == ("invalid" if issues else "valid")


def test_detail_contract_for_assigned_employee(client, db, departments, fake_gitea):
    """分配后：current_position 带 definition_id/slot_id 等派生视图，integrity 仍 valid。"""
    definition_id = db.scalar(
        sa.text("SELECT id FROM position_definitions WHERE code = 'engineer' LIMIT 1")
    )
    assert definition_id is not None, "engineer 定义必须存在"
    opened = client.post(
        f"/api/v1/organizations/definitions/{definition_id}/slots",
        json={"department_id": departments["engineering"], "note": "detail contract"},
    )
    assert opened.status_code == 201, opened.text
    slot = opened.json()[0]

    employee_id = _hire(client, departments, f"detail-assigned-{departments['engineering']}")
    assigned = client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments",
        json={"slot_id": slot["id"], "reason": "contract test"},
    )
    assert assigned.status_code == 201, assigned.text

    body = _roster_detail(client, employee_id)
    parsed = EmployeeDetailOut.model_validate(body)
    assert parsed.workforce_status == "assigned"
    assert parsed.occupies_establishment is True
    position = parsed.current_position
    assert position is not None
    assert position.definition_id == slot["position_definition_id"]
    assert position.slot_id == slot["id"]
    assert position.department_id == slot["department_id"]
    assert parsed.assignment_integrity.status == "valid"


def test_derived_zones_have_no_patch_entry_and_are_read_only():
    """派生三区没有 PATCH 入口：写面只存在于 assignment 工作流，不在员工 PATCH。"""
    from app.schemas.organization import EmployeePatch

    allowed = set(EmployeePatch.model_fields)
    for derived_key in (
        "workforce_status",
        "current_position",
        "assignment_integrity",
        "has_primary_assignment",
        "occupies_establishment",
    ):
        assert derived_key not in allowed, f"派生键 {derived_key} 不该有 PATCH 入口"


def test_assignment_integrity_read_only_flag_is_public_and_true(client, departments, fake_gitea):
    """read_only 是这条边界的公开承诺：完整性只读，永远不回落成可写状态。"""
    employee_id = _hire(client, departments, f"detail-ro-{departments['engineering']}")
    assert _roster_detail(client, employee_id)["assignment_integrity"]["read_only"] is True
