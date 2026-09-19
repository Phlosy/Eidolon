"""P9 人才名册 v2 —— 派生/富化/筛选/批量（docs/talent-roster.md §5/§58 子集）。

锁：
- workforce_status 派生（AVAILABLE ≠ 员工 idle work 状态是两个维度）；
- 默认排除 offboarded/pending；
- unrated 员工可见，top 能力不因低置信而出现（置信≥0.4 才算 Top）；
- 能力筛选强制同时给 confidence（防止 Score90/Conf5% 误导）；
- trait 筛选独立于能力筛选（Behavioral Preference ≠ Competency）；
- N+1 守卫：一页 N 人的富化查询数固定，不随 N 或不同编制数线性增长；
- 无跨公司统计（company-scoped）。
"""

from __future__ import annotations

import threading

import pytest
import sqlalchemy as sa
from factories import make_employee, person_id_of
from sqlalchemy.orm import Session

from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.models.competency import EmployeeCompetency
from app.models.organization import Company, Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.services import talent_roster as roster_service
from app.workforce.status import WorkforceStatusResolver

_seq = 0


def _hire(db, company_id: int, *, name: str = "") -> int:
    global _seq
    _seq += 1
    employee = make_employee(
        db,
        company_id=company_id,
        slug=f"roster-{_seq}",
        name=name or f"Roster {_seq}",
        workspace_path=f"/tmp/r-{_seq}-ws",
        memory_namespace=f"mem-r-{_seq}",
        lifecycle_status="active",
    )
    db.commit()
    return int(employee.id)


def _set_comp(db, employee_id: int, code: str, score: int | None, confidence: float | None):
    from app.models.competency import CompetencyDefinition, CompetencyDomain

    def_id = db.scalar(
        sa.select(CompetencyDefinition.id)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == code,
            CompetencyDomain.company_id.is_(None),
        )
    )
    row = db.scalar(
        sa.select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == def_id,
        )
    )
    data = {
        "employee_id": employee_id,
        "person_id": person_id_of(db, employee_id),
        "competency_definition_id": def_id,
        "score": score,
        "confidence": confidence,
        "status": "assessed" if score is not None else "unrated",
        "evidence_count": 0,
    }
    if row is None:
        db.add(EmployeeCompetency(**data))
    else:
        row.score = score
        row.confidence = confidence
    db.flush()


def test_roster_status_is_derived(client, default_company_id):
    body = client.get("/api/v1/talent-roster").json()
    assert all("workforce_status" in entry for entry in body)
    assert all("lifecycle_status" in entry for entry in body)
    assert all("runtime" in entry for entry in body)
    assert all("traits_summary" in entry for entry in body)


def test_unrated_employee_visible_with_no_fake_tops(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    entry = next(
        item
        for item in client.get("/api/v1/talent-roster").json()
        if item["employee_id"] == employee_id
    )
    assert entry["top_general_competencies"] == []
    assert entry["top_professional_competencies"] == []
    assert entry["assessment_summary"]["evidence_coverage"] == "none"
    assert entry["assessment_summary"]["assessed_general_count"] == 0


def test_top_competency_requires_sufficient_confidence(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _set_comp(
        db, employee_id, "planning_organization", score=95, confidence=0.08
    )  # general 高置信不足
    _set_comp(db, employee_id, "execution", score=84, confidence=0.8)  # general 达标
    _set_comp(db, employee_id, "testing", score=84, confidence=0.8)  # professional 达标
    db.commit()
    entry = next(
        item
        for item in client.get("/api/v1/talent-roster").json()
        if item["employee_id"] == employee_id
    )
    general = {item["code"] for item in entry["top_general_competencies"]}
    assert "execution" in general
    assert "planning_organization" not in general  # 分高但置信不足 ⇒ 不作 Top（待验证）
    professional = {item["code"] for item in entry["top_professional_competencies"]}
    assert "testing" in professional  # professional 能力置信达标 ⇒ 可入 Top


def test_competency_filter_requires_both_score_and_confidence(client, db, default_company_id):
    employee_ok = _hire(db, default_company_id)
    employee_low_conf = _hire(db, default_company_id)
    _set_comp(db, employee_ok, "execution", score=75, confidence=0.8)
    _set_comp(db, employee_low_conf, "execution", score=95, confidence=0.05)
    db.commit()
    filtered = client.get(
        "/api/v1/talent-roster",
        params={
            "competency_code": "execution",
            "min_competency_score": 70,
            "min_competency_confidence": 0.5,
        },
    ).json()
    ids = {entry["employee_id"] for entry in filtered}
    assert employee_ok in ids
    assert employee_low_conf not in ids  # Score 高但置信不达标 ⇒ 筛选排除（防误导）


def test_trait_filter_is_independent_of_competency(client, db, default_company_id):
    from app.brain.traits import BrainTraits, write_traits_to_brain
    from app.repositories import runtimes as runtime_repo

    employee_id = _hire(db, default_company_id)
    # brain 的唯一写入口（R1.1 双写：person_id 权威 + employee_id 镜像）
    brain = runtime_repo.ensure_brain(db, employee_id)
    write_traits_to_brain(brain, BrainTraits.build({"curiosity": 0.9, "warmth": 0.3}))
    db.commit()
    hits = client.get(
        "/api/v1/talent-roster",
        params={"trait_code": "curiosity", "min_trait_value": 0.7},
    ).json()
    assert any(entry["employee_id"] == employee_id for entry in hits)
    assert hits  # 人格筛选是独立的 Behavioral Preference 维度


def test_status_filter_and_available_scope(client, db, default_company_id):
    available = client.get("/api/v1/talent-roster", params={"status": "available"}).json()
    assigned = client.get("/api/v1/talent-roster", params={"status": "assigned"}).json()
    assert {entry["workforce_status"] for entry in available} <= {"available"}
    assert {entry["workforce_status"] for entry in assigned} <= {"assigned"}


@pytest.mark.parametrize("query_target", ["views", "roster"])
def test_roster_enrichment_does_not_fan_out_per_employee(db, query_target, record_property):
    """冷 Session + 独立编制/定义：已分配人数增长不能增加名册富化查询数。"""
    from app.models.event import Event

    company = Company(name="Batch Roster", slug=f"roster-batch-{query_target}-{_seq}")
    db.add(company)
    db.flush()
    department = Department(company_id=company.id, name="Batch", slug="batch")
    db.add(department)
    db.commit()
    company_id = int(company.id)
    department_id = int(department.id)
    assigned_slots: dict[int, int] = {}

    def _grow(count: int) -> None:
        for _ in range(count):
            employee_id = _hire(db, company_id)
            definition = PositionDefinition(
                company_id=company_id,
                code=f"roster-{employee_id}",
                name=f"Position {employee_id}",
            )
            db.add(definition)
            db.flush()
            slot = PositionSlot(
                company_id=company_id,
                department_id=department_id,
                position_definition_id=definition.id,
                slot_code=f"ROSTER-{employee_id}",
                administrative_status="active",
            )
            db.add(slot)
            db.flush()
            db.add(
                PositionAssignment(
                    employee_id=employee_id,
                    department_id=department_id,
                    position_slot_id=slot.id,
                    assignment_type="primary",
                    is_primary=True,
                )
            )
            db.add(
                Event(
                    type="task.completed",
                    company_id=company_id,
                    actor_employee_id=employee_id,
                    actor_person_id=person_id_of(db, employee_id),
                    payload={},
                )
            )
            assigned_slots[employee_id] = int(slot.id)
        db.commit()

    def _run() -> int:
        owner = threading.get_ident()
        statements: list[str] = []
        engine = db.get_bind()
        from sqlalchemy import event as sa_event

        def _count(conn, cursor, statement, parameters, context, executemany):
            if threading.get_ident() == owner:
                statements.append(statement)

        sa_event.listen(engine, "before_cursor_execute", _count)
        try:
            # 每次新建 Session，防止 fixture/上一次读取的 identity map 掩盖逐行查询。
            with Session(engine) as cold_db:
                if query_target == "views":
                    people = list(
                        cold_db.scalars(
                            sa.select(Employee).where(Employee.company_id == company_id)
                        )
                    )
                    result = WorkforceStatusResolver(cold_db).views(people)
                    assert set(result) == set(assigned_slots)
                    for employee_id, view in result.items():
                        assert view.workforce_status.value == "assigned"
                        assert view.position_slot_id == assigned_slots[employee_id]
                else:
                    result = roster_service.roster_query(cold_db, company_id, limit=100)
                    assert result["total"] == len(assigned_slots)
                    assert len(result["items"]) == len(assigned_slots)
                    for entry in result["items"]:
                        assert entry["workforce_status"] == "assigned"
                        assert (
                            entry["current_position"]["slot_id"]
                            == assigned_slots[entry["employee_id"]]
                        )
        finally:
            sa_event.remove(engine, "before_cursor_execute", _count)
        return len(statements)

    _grow(4)
    base = _run()
    _grow(12)
    grown = _run()
    record_property("small_assigned_count", 4)
    record_property("large_assigned_count", 16)
    record_property("small_query_count", base)
    record_property("large_query_count", grown)
    assert grown == base, f"{query_target} 查询数随已分配人数增长：{base} → {grown}"


def test_company_isolation_and_no_global_totals(client, db, default_company_id):
    other = Company(name="OtherR", slug=f"roster-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign = _hire(db, int(other.id))
    default_ids = {entry["employee_id"] for entry in client.get("/api/v1/talent-roster").json()}
    assert foreign not in default_ids, "Company A 名册绝不能包含 Company B 员工"


@pytest.mark.parametrize("scope", ["explicit", "request_identity"])
def test_assigned_roster_references_and_integrity_stay_with_selected_people(
    db, default_company_id, scope
):
    other = Company(name="Scoped roster", slug=f"roster-scope-{scope}-{_seq}")
    db.add(other)
    db.commit()
    other_id = int(other.id)
    employees = [_hire(db, company_id) for company_id in [default_company_id, other_id]]
    broken_employee = _hire(db, default_company_id)
    selected_slot_id = None
    for company_id, employee_id in zip([default_company_id, other_id], employees, strict=True):
        department = Department(company_id=company_id, name="Scoped", slug=f"scope-{_seq}")
        definition = PositionDefinition(
            company_id=company_id,
            code=f"shared-code-{_seq}",
            name=f"Company {company_id} position",
        )
        db.add_all([department, definition])
        db.flush()
        slot = PositionSlot(
            company_id=company_id,
            department_id=department.id,
            position_definition_id=definition.id,
            slot_code=f"SCOPE-{employee_id}",
        )
        db.add(slot)
        db.flush()
        db.add(
            PositionAssignment(
                employee_id=employee_id,
                department_id=department.id,
                position_slot_id=slot.id,
                assignment_type="primary",
                is_primary=True,
            )
        )
        if company_id == other_id:
            selected_slot_id = int(slot.id)
    # 别家的人有历史无坑任职；诊断不能混进这次名册或导致富化 KeyError。
    db.add(
        PositionAssignment(
            employee_id=broken_employee,
            position_slot_id=None,
            assignment_type="primary",
            is_primary=True,
        )
    )
    db.flush()
    token = set_request_identity(
        RequestIdentity(
            user_id=1,
            company_id=default_company_id if scope == "explicit" else other_id,
            membership_role="OWNER",
            session_id=1,
        )
    )
    try:
        result = roster_service.roster_query(db, other_id if scope == "explicit" else None)
        assert result["total"] == 1
        entry = result["items"][0]
        assert entry["employee_id"] == employees[1]
        assert entry["workforce_status"] == "assigned"
        assert entry["current_position"]["slot_id"] == selected_slot_id
        assert entry["current_position"]["code"] == f"shared-code-{_seq}"
        assert entry["current_position"]["name"] == f"Company {other_id} position"
        assert entry["integrity"] == []
    finally:
        reset_request_identity(token)
