"""P4a：`app/repositories/position.py` 的读取语义验收。

隔离方式：用共享 `db` fixture 但**只 flush 不 commit**，测试结束 session 关闭即回滚，
不会污染其它用例看到的种子数据。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.enums import AssignmentType, OccupancyStatus
from app.models.lifecycle import AccessPackage, Employment
from app.models.organization import Department, Employee
from app.models.position import (
    PositionDefinition,
    PositionDefinitionPackage,
    PositionSlot,
)
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import position as position_repo

NOW = datetime.now(UTC)


def _employee(db: Session, company_id: int, department_id: int, slug: str) -> Employee:
    employee = Employee(
        company_id=company_id,
        department_id=department_id,
        name=slug.title(),
        slug=slug,
        role="engineer",
        title=slug,
        avatar="",
        status="idle",
        lifecycle_status="active",
        username=slug,
        runtime_type="mock",
        runtime_config={},
        workspace_path=f"data/employees/{slug}",
        memory_namespace=f"emp_{slug}",
    )
    db.add(employee)
    db.flush()
    return employee


@pytest.fixture()
def graph(db: Session, default_company_id: int) -> dict:
    """一家公司 + 一个部门 + 一个职位定义 + 一个坑 + 一个员工。"""
    department = Department(company_id=default_company_id, name="P4a Engineering", slug="p4a-eng")
    db.add(department)
    db.flush()
    definition = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code="p4a_engineer",
        name="P4a Engineer",
        description="",
        job_family="engineering",
        level=3,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role="engineer",
    )
    db.add(definition)
    package = AccessPackage(
        slug="p4a-engineer", name="P4a Engineer", description="", role="engineer", built_in=False
    )
    db.add(package)
    db.flush()
    db.add(PositionDefinitionPackage(position_definition_id=definition.id, package_id=package.id))
    slot = PositionSlot(
        company_id=default_company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code="P4A-ENGINEER-1",
        headcount_index=1,
        administrative_status="active",
        metadata_json={},
    )
    db.add(slot)
    db.flush()
    employee = _employee(db, default_company_id, department.id, "p4a-person")
    return {
        "company_id": default_company_id,
        "department": department,
        "definition": definition,
        "package": package,
        "slot": slot,
        "employee": employee,
    }


def _extra_slot(db: Session, graph: dict, index: int) -> PositionSlot:
    """再加一个编制。同坑第二条生效主职会被 `uq_employment_slot_primary` 拒绝 ——
    那是设计意图（见 `test_slot_can_hold_only_one_active_primary`），不是测试借口。"""
    slot = PositionSlot(
        company_id=graph["company_id"],
        department_id=graph["department"].id,
        position_definition_id=graph["definition"].id,
        slot_code=f"P4A-ENGINEER-{index}",
        headcount_index=index,
        administrative_status="active",
        metadata_json={},
    )
    db.add(slot)
    db.flush()
    return slot


def _assign(
    db: Session,
    graph: dict,
    employee: Employee,
    *,
    slot_id: int | None = None,
    assignment_type: str = AssignmentType.primary.value,
    is_primary: bool = True,
    effective_to: datetime | None = None,
    effective_from: datetime = NOW,
) -> Employment:
    assignment = Employment(
        employee_id=employee.id,
        department_id=graph["department"].id,
        position_slot_id=graph["slot"].id if slot_id is None else slot_id,
        employment_status="active" if effective_to is None else "closed",
        joined_at=effective_from,
        effective_from=effective_from,
        effective_to=effective_to,
        metadata_json={},
        assignment_type=assignment_type,
        is_primary=is_primary,
        reason="p4a",
        position_title_snapshot="P4a Engineer",
    )
    db.add(assignment)
    db.flush()
    return assignment


# ---------------------------------------------------------------- definitions


def test_definitions_are_company_scoped_and_packages_resolve(db: Session, graph: dict):
    definitions = position_repo.list_definitions(db, graph["company_id"])
    assert any(item.code == "p4a_engineer" for item in definitions)
    assert position_repo.get_definition_by_code(db, "p4a_engineer", graph["company_id"])
    assert position_repo.get_definition_by_code(db, "no_such_code", graph["company_id"]) is None
    assert position_repo.definition_package_slugs(db, graph["definition"].id) == ["p4a-engineer"]


def test_other_company_definitions_are_not_visible(db: Session, graph: dict):
    other = Department(company_id=graph["company_id"], name="x", slug="p4a-other-dep")
    db.add(other)
    db.flush()
    foreign = PositionDefinition(
        company_id=graph["company_id"] + 999,
        template_scope="company",
        code="p4a_foreign",
        name="Foreign",
        description="",
        job_family="",
        level=1,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role=None,
    )
    db.add(foreign)
    db.flush()
    codes = {item.code for item in position_repo.list_definitions(db, graph["company_id"])}
    assert "p4a_foreign" not in codes


# --------------------------------------------------------------------- slots


def test_slot_integrity_is_checked_in_code_not_by_foreign_key(db: Session, graph: dict):
    """没有 DB 外键 ⇒ 悬空引用必须在这里显式炸掉（ADR：完整性由 repository/service 承担）。"""
    assert position_repo.require_slot(db, graph["slot"].id).id == graph["slot"].id
    with pytest.raises(position_repo.SlotNotFound):
        position_repo.require_slot(db, 999_999)


def test_occupancy_is_derived_per_administrative_status(db: Session, graph: dict):
    slot = graph["slot"]
    assert position_repo.slot_occupancy(db, slot) == OccupancyStatus.vacant
    _assign(db, graph, graph["employee"], slot_id=slot.id)
    assert position_repo.slot_occupancy(db, slot) == OccupancyStatus.occupied

    for status, expected in (
        ("frozen", OccupancyStatus.frozen),
        ("closed", OccupancyStatus.closed),
        ("planned", OccupancyStatus.occupied),  # 有人就报有人，行政态只在没人时区分冻结/关闭
    ):
        slot.administrative_status = status
        db.flush()
        assert position_repo.slot_occupancy(db, slot) == expected
    slot.administrative_status = "active"
    db.flush()


def test_secondary_and_acting_assignments_do_not_block_vacancy(db: Session, graph: dict):
    """兼任/代理不占编制，所以不能把坑从 vacancy 列表里抹掉。"""
    _assign(
        db,
        graph,
        graph["employee"],
        assignment_type=AssignmentType.secondary.value,
        is_primary=False,
    )
    _assign(
        db,
        graph,
        _employee(db, graph["company_id"], graph["department"].id, "p4a-acting"),
        assignment_type=AssignmentType.acting.value,
        is_primary=False,
    )
    vacant = [item.id for item in position_repo.vacant_slots(db, graph["company_id"])]
    assert graph["slot"].id in vacant
    assert position_repo.slot_incumbents(db, graph["slot"].id) == []


# --------------------------------------------------------------- assignments


def test_current_assignment_ignores_newer_non_primary_rows(db: Session, graph: dict):
    """回归防护：旧读法是 `order_by(id desc).limit(1)`，会挑到后创建的兼任行。"""
    primary = _assign(db, graph, graph["employee"])
    later_secondary = _assign(
        db,
        graph,
        graph["employee"],
        assignment_type=AssignmentType.secondary.value,
        is_primary=False,
        effective_from=NOW + timedelta(days=1),
    )
    assert later_secondary.id > primary.id
    assert position_repo.active_primary_assignment(db, graph["employee"].id) is primary
    # 旧的对外函数名保持可用，但读法已收口到同一处
    assert lifecycle_repo.get_current_employment(db, graph["employee"].id) is primary
    assert len(position_repo.active_assignments(db, graph["employee"].id)) == 2


def test_ended_assignments_leave_the_timeline_but_not_the_present(db: Session, graph: dict):
    employee = graph["employee"]
    old = _assign(db, graph, employee, effective_from=NOW - timedelta(days=20))
    old.effective_to = NOW - timedelta(days=5)
    old.employment_status = "closed"
    db.flush()
    assert position_repo.active_primary_assignment(db, employee.id) is None
    history = position_repo.career_history(db, employee.id)
    assert [item.id for item in history] == [old.id], "履历是全时间轴，含已关闭的行"
    assert position_repo.employee_current_position(db, employee.id) is None


def test_current_position_walks_assignment_to_slot_to_definition(db: Session, graph: dict):
    employee = graph["employee"]
    _assign(db, graph, employee)
    current = position_repo.employee_current_position(db, employee.id)
    assert current is not None
    assert (current.code, current.name, current.level) == ("p4a_engineer", "P4a Engineer", 3)
    assert current.legacy_role == "engineer" and current.is_custom is False
    assert current.department_name == "P4a Engineering"
    assert current.slot_code == "P4A-ENGINEER-1"
    assert current.since is not None


def test_dangling_slot_reference_yields_no_position_instead_of_a_guess(db: Session, graph: dict):
    """无外键的代价在这里补偿：悬空引用报"没有职位"，绝不编一个职位出来。"""
    employee = graph["employee"]
    assignment = _assign(db, graph, employee)
    assignment.position_slot_id = 999_998
    db.flush()
    assert position_repo.employee_current_position(db, employee.id) is None


def test_slot_can_hold_only_one_active_primary(db: Session, graph: dict):
    """ADR-2 的另一半：一个坑同时只允许一人（数据库层拒绝，不是应用层约定）。"""
    _assign(db, graph, graph["employee"])
    other = _employee(db, graph["company_id"], graph["department"].id, "p4a-clasher")
    with pytest.raises(sa.exc.IntegrityError):
        _assign(db, graph, other)
    db.rollback()


def test_employees_in_position_replaces_get_employee_by_role(db: Session, graph: dict):
    first = graph["employee"]
    second = _employee(db, graph["company_id"], graph["department"].id, "p4a-second")
    _assign(
        db,
        graph,
        second,
        slot_id=_extra_slot(db, graph, 2).id,
        effective_from=NOW + timedelta(days=2),
    )
    _assign(db, graph, first, effective_from=NOW + timedelta(days=1))
    ids = position_repo.employees_in_position(db, "p4a_engineer", graph["company_id"])
    assert ids == [first.id, second.id], "按 effective_from 排序，便于「最早任命者优先」读法"


# ------------------------------------------------------------------ 批量读法


def test_batch_readers_do_not_fan_out_per_employee(db: Session, graph: dict):
    """名册一页 50 人必须是常数次查询 —— 这条锁住"批量入口"的承诺，不是微优化。"""
    people = [
        _employee(db, graph["company_id"], graph["department"].id, f"p4a-bulk-{index}")
        for index in range(12)
    ]
    for offset, person in enumerate(people):
        # 每人一个坑：不是偷懒，而是"一坑一人"本来就被唯一索引锁死
        slot_id = graph["slot"].id if offset == 0 else _extra_slot(db, graph, offset + 1).id
        _assign(db, graph, person, slot_id=slot_id)
    db.flush()

    # 统计只认**本线程**发出的语句：全量 suite 里先跑的用例可能启动过 runtime 更新/
    # 健康检查等后台线程，它们与测试共用 engine，会在计数窗口里掺进 SELECT ——
    # 那是本次 3 条断言偶发 flaky 的根因（单跑必绿、全量偶红）。按线程过滤后，
    # “本测试自己只发了 3 条”的语义不变，且不再被后台噪声污染。
    import threading

    owner_thread = threading.get_ident()
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if threading.get_ident() == owner_thread:
            statements.append(statement)

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _count)
    try:
        primaries = position_repo.active_primaries_by_employee(db, [p.id for p in people])
        slots = position_repo.list_slots(db, graph["company_id"])
        occupancy = position_repo.occupancy_map(db, slots)
    finally:
        sa.event.remove(engine, "before_cursor_execute", _count)

    assert len(primaries) == 12
    assert len(occupancy) == len(slots)
    # 12 个人 + N 个坑，实际只跑了 3 条 SELECT：
    # 主职一次、坑列表一次、坑计数一次。退化成 N+1 时这个数会随人数增长。
    assert len(statements) == 3, f"批量读法退化成 N+1：{len(statements)} 条语句"
