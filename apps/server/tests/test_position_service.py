"""P4b：`PositionService` 与分配工作流的规则验收（docs/position-system.md §4–§5）。

这里守的是四条拍板纪律，每条都可能被"顺手方便一下"破坏：

1. 招聘不写无坑 PRIMARY；只有显式分配工作流能创建主职。
2. `position_slot_id` 必须过 `require_slot()`，缺坑不猜、不建。
3. 占用态/行政态是两个轴：只有行政态可写。
4. 启动引导不许替用户做人事决定（seed 只落 5 位创始人，UI 招来的人一律 AVAILABLE）。
"""

from __future__ import annotations

import importlib.util
import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import AssignmentType, OccupancyStatus
from app.models.lifecycle import Position
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.repositories import position as position_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotAdminIn, SlotIn
from app.services import lifecycle as lifecycle_service
from app.services import position_service

NOW = datetime.now(UTC)
_counter = itertools.count(1)


def _uniq(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}-{int(NOW.timestamp())}"


def _department(db: Session, company_id: int) -> Department:
    slug = _uniq("dept")
    department = Department(company_id=company_id, name=slug.title(), slug=slug)
    db.add(department)
    db.flush()
    return department


def _definition(db: Session, company_id: int, legacy_role: str | None = None) -> PositionDefinition:
    code = _uniq("eng")
    definition = PositionDefinition(
        company_id=company_id,
        template_scope="company",
        code=code,
        name=code.replace("-", " ").title(),
        description="",
        job_family="engineering",
        level=2,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role=legacy_role,
    )
    db.add(definition)
    db.flush()
    return definition


def _slot(
    db: Session,
    company_id: int,
    department: Department,
    definition: PositionDefinition,
    *,
    administrative_status: str = "active",
    legacy_position_id: int | None = None,
    manager_slot_id: int | None = None,
    index: int = 1,
) -> PositionSlot:
    metadata = (
        {"__backfill": "v13", "__position_id": legacy_position_id}
        if legacy_position_id is not None
        else {}
    )
    slot = PositionSlot(
        company_id=company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code=f"{department.slug.upper()}-{definition.code.upper()}-{index}"[:100],
        headcount_index=index,
        administrative_status=administrative_status,
        manager_slot_id=manager_slot_id,
        metadata_json=metadata,
    )
    db.add(slot)
    db.flush()
    return slot


def _employee(db: Session, company_id: int, department: Department, lifecycle="active") -> Employee:
    slug = _uniq("person")
    employee = Employee(
        company_id=company_id,
        department_id=department.id,
        name=slug,
        slug=slug,
        role="engineer",
        title=slug,
        avatar="",
        status="idle",
        lifecycle_status=lifecycle,
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
def stage(db: Session, default_company_id: int) -> dict:
    department = _department(db, default_company_id)
    definition = _definition(db, default_company_id)
    slot = _slot(db, default_company_id, department, definition)
    employee = _employee(db, default_company_id, department)
    return {
        "db": db,
        "company_id": default_company_id,
        "department": department,
        "definition": definition,
        "slot": slot,
        "employee": employee,
    }


def _assign(db: Session, employee, slot_id, **kwargs) -> PositionAssignment:
    return position_service.assign_position(db, employee, AssignmentIn(slot_id=slot_id, **kwargs))


# ----------------------------------------------------------- 1. 唯一点：不猜不建


def test_assign_refuses_to_guess_and_leaves_the_person_unassigned(stage: dict):
    db = stage["db"]
    employee = stage["employee"]
    with pytest.raises(HTTPException) as neither:
        position_service.assign_position(db, employee, AssignmentIn())
    assert neither.value.status_code == 422

    with pytest.raises(HTTPException) as missing:
        position_service.assign_position(db, employee, AssignmentIn(slot_id=999_999))
    assert missing.value.status_code == 404, "引用不存在的坑不该冒成 500"

    assert position_repo.active_assignments(db, employee.id) == []
    assert position_repo.employee_current_position(db, employee.id) is None


def test_legacy_position_id_maps_only_through_the_v13_marker(stage: dict):
    """旧 `position_id` 只按标记映射；映射不到就 409，绝不新建坑、绝不按 title 反推。"""
    db = stage["db"]
    legacy = Position(
        department_id=stage["department"].id, title="Nobody Ever Mapped Me", level="1"
    )
    db.add(legacy)
    db.flush()
    slots_before = db.scalar(sa.text("SELECT COUNT(*) FROM position_slots"))
    with pytest.raises(HTTPException) as exc:
        position_service.assign_position(db, stage["employee"], AssignmentIn(position_id=legacy.id))
    assert exc.value.status_code == 409
    # 失败请求不许留下任何"顺手补出来的坑"
    slots_after = db.scalar(sa.text("SELECT COUNT(*) FROM position_slots"))
    assert slots_after == slots_before


def test_v04_position_id_finds_its_slot_through_the_marker(stage: dict):
    db = stage["db"]
    legacy = Position(department_id=stage["department"].id, title="Mapped Legacy Title", level="1")
    db.add(legacy)
    db.flush()
    slot = _slot(
        db,
        stage["company_id"],
        stage["department"],
        stage["definition"],
        legacy_position_id=legacy.id,
        index=2,
    )
    assignment = position_service.assign_position(
        db, stage["employee"], AssignmentIn(position_id=legacy.id)
    )
    assert assignment.position_slot_id == slot.id
    assert assignment.position_id == legacy.id, "兼容镜像要写回，否则 v0.4 任职页看不到职位"


# ------------------------------------------------------ 2. 行政态决定"能不能填"


@pytest.mark.parametrize(
    ("administrative_status", "expected_status"),
    [
        ("planned", 200),
        ("active", 200),
        ("frozen", 409),
        ("closed", 409),
    ],
)
def test_only_planned_and_active_slots_can_be_filled(
    stage: dict, administrative_status, expected_status
):
    db = stage["db"]
    slot = _slot(
        db,
        stage["company_id"],
        stage["department"],
        stage["definition"],
        administrative_status=administrative_status,
        index=7,
    )
    if expected_status == 200:
        assignment = _assign(db, stage["employee"], slot.id)
        assert assignment.position_slot_id == slot.id
    else:
        with pytest.raises(HTTPException) as exc:
            _assign(db, stage["employee"], slot.id)
        assert exc.value.status_code == expected_status
        assert administrative_status in str(exc.value.detail)


def test_occupied_slot_rejects_a_second_person_but_same_person_is_idempotent(stage: dict):
    db = stage["db"]
    slot = stage["slot"]
    first = _assign(db, stage["employee"], slot.id)
    other = _employee(db, stage["company_id"], stage["department"])
    with pytest.raises(HTTPException) as exc:
        _assign(db, other, slot.id)
    assert exc.value.status_code == 409
    assert "已有在任者" in exc.value.detail
    # 重复点"分配"不制造第二条主职（幂等，且不是"关掉前一个人"）
    again = _assign(db, stage["employee"], slot.id)
    assert again.id == first.id
    assert len(position_repo.slot_incumbents(db, slot.id)) == 1


# --------------------------------------------- 3. 时间轴：关旧开新，人不被改写


def test_reassignment_closes_the_old_primary_and_keeps_joined_at(stage: dict):
    db = stage["db"]
    first = _assign(db, stage["employee"], stage["slot"].id, reason="hire in")
    second = _slot(db, stage["company_id"], stage["department"], stage["definition"], index=3)
    moved = position_service.assign_position(
        db, stage["employee"], AssignmentIn(slot_id=second.id, kind="transfer", reason="reorg")
    )
    assert first.effective_to is not None
    assert first.employment_status == "transferred", "v0.4 历史契约在读这个词"
    assert first.metadata_json["superseded_by_reason"] == "reorg"
    assert moved.effective_to is None
    assert moved.joined_at == first.joined_at, "转岗不改入职日"
    assert moved.reason == "reorg"
    # 纯分配（kind=assign）语义上是"取代"，不是"转岗"
    third = _slot(db, stage["company_id"], stage["department"], stage["definition"], index=4)
    plain = _assign(db, stage["employee"], third.id, reason="appoint")
    assert moved.employment_status == "superseded"
    assert plain.employment_status == "active"
    assert len(position_repo.career_history(db, stage["employee"].id)) == 3


def test_person_follows_the_establishment_and_manager_mirror_is_derived(stage: dict):
    db = stage["db"]
    manager_person = _employee(db, stage["company_id"], stage["department"])
    manager_slot = _slot(
        db, stage["company_id"], stage["department"], stage["definition"], index=11
    )
    _assign(db, manager_person, manager_slot.id)
    child_department = _department(db, stage["company_id"])
    child_slot = _slot(
        db,
        stage["company_id"],
        child_department,
        stage["definition"],
        index=1,
        manager_slot_id=manager_slot.id,
    )
    assert stage["employee"].department_id != child_department.id
    assignment = _assign(db, stage["employee"], child_slot.id)
    assert assignment.department_id == child_department.id
    assert stage["employee"].department_id == child_department.id, "人随编制走"
    assert assignment.manager_employee_id == manager_person.id, "上级从组织线推导，不是靠调用方记"


def test_explicit_manager_overrides_the_slot_line_but_marker_is_the_default(stage: dict):
    db = stage["db"]
    boss = _employee(db, stage["company_id"], stage["department"])
    assignment = position_service.assign_position(
        db,
        stage["employee"],
        AssignmentIn(slot_id=stage["slot"].id, manager_employee_id=boss.id),
    )
    assert assignment.manager_employee_id == boss.id


def test_release_returns_the_establishment_without_touching_the_person(stage: dict):
    db = stage["db"]
    employee = stage["employee"]
    assignment = _assign(db, employee, stage["slot"].id)
    before = (employee.id, employee.slug, employee.workspace_path, employee.memory_namespace)
    assert position_repo.slot_occupancy(db, stage["slot"]) == OccupancyStatus.occupied

    closed = position_service.release_position(db, employee, reason="bench")
    assert len(closed) == 1
    assert position_repo.slot_occupancy(db, stage["slot"]) == OccupancyStatus.vacant
    assert assignment.effective_to is not None
    assert assignment.employment_status == "released"
    assert position_repo.active_primary_assignment(db, employee.id) is None
    assert position_repo.employee_current_position(db, employee.id) is None
    db.refresh(employee)
    # 人级数据一项都不能少（§8.1）
    assert (
        employee.id,
        employee.slug,
        employee.workspace_path,
        employee.memory_namespace,
    ) == before
    # 履历仍在
    assert len(position_repo.career_history(db, employee.id)) == 1
    # 再解任一次不该关掉什么，也不该造假行
    assert position_service.release_position(db, employee, reason="again") == []


# ----------------------------------------- 4. 启动引导不许替用户做人事决定


def test_seed_is_idempotent_and_never_displaces_an_incumbent(db: Session):
    """seed 每次启动都跑，所以它必须既不重复落子、也不挤掉任何人。

    刻意不断言"5 位创始人一定都在坑上"：创始人可能已经被用户调走（人随编制走），
    此时她部门的坑被别人占着，seed 就该让她保持 AVAILABLE —— 那是 §52 #2 要的行为。
    空库开局的事实另测（见 `test_bootstrap_from_an_empty_db`）。
    """
    lifecycle_service.seed_lifecycle(db)
    db.commit()
    slots_before = db.scalar(sa.text("SELECT COUNT(*) FROM position_slots"))
    rows_before = db.scalar(sa.text("SELECT COUNT(*) FROM employments"))
    pairs_before = {
        (row[0], row[1])
        for row in db.execute(
            sa.text(
                "SELECT employee_id, position_slot_id FROM employments"
                " WHERE effective_to IS NULL AND assignment_type = 'primary'"
            )
        )
    }
    lifecycle_service.seed_lifecycle(db)
    db.commit()
    assert db.scalar(sa.text("SELECT COUNT(*) FROM position_slots")) == slots_before
    assert db.scalar(sa.text("SELECT COUNT(*) FROM employments")) == rows_before
    pairs_after = {
        (row[0], row[1])
        for row in db.execute(
            sa.text(
                "SELECT employee_id, position_slot_id FROM employments"
                " WHERE effective_to IS NULL AND assignment_type = 'primary'"
            )
        )
    }
    assert pairs_after == pairs_before, "第二次 seed 改动了既成任职 —— 那等于重启覆盖用户决定"


# ------------------------------------------------------- 只读诊断（不修数据）


def test_integrity_report_classifies_without_repairing(stage: dict):
    db = stage["db"]
    employee = stage["employee"]
    # (a) v0.4 遗留：生效主职但没有坑
    leftover = PositionAssignment(
        employee_id=_employee(db, stage["company_id"], stage["department"]).id,
        department_id=stage["department"].id,
        employment_status="active",
        joined_at=NOW - timedelta(days=9),
        effective_from=NOW - timedelta(days=9),
        effective_to=None,
        metadata_json={"kind": "legacy_backfill"},
        assignment_type=AssignmentType.primary.value,
        is_primary=True,
        reason="",
        position_title_snapshot="",
    )
    # (b) 悬空引用（无外键的代价）
    dangling_owner = _employee(db, stage["company_id"], stage["department"])
    dangling = PositionAssignment(
        employee_id=dangling_owner.id,
        department_id=stage["department"].id,
        position_slot_id=999_997,
        employment_status="active",
        joined_at=NOW,
        effective_from=NOW,
        effective_to=None,
        metadata_json={},
        assignment_type=AssignmentType.primary.value,
        is_primary=True,
        reason="",
        position_title_snapshot="",
    )
    db.add_all([leftover, dangling])
    db.flush()
    rows_before = db.scalar(sa.text("SELECT COUNT(*) FROM employments"))

    report = position_service.integrity(db, stage["company_id"])
    kinds = {item.kind for item in report["items"]}
    assert {"NO_SLOT_HISTORICAL", "DANGLING_SLOT"} <= kinds, kinds
    assert report["read_only"] is True
    assert report["counts"]["NO_SLOT_HISTORICAL"] >= 1
    assert db.scalar(sa.text("SELECT COUNT(*) FROM employments")) == rows_before, "诊断改写了数据"
    assert not db.dirty and not db.new
    # 诊断不是状态：模型里没有承载它的列
    assert not any("integrity" in column.key for column in PositionAssignment.__table__.columns)
    # 悬空引用的人仍然是"没有职位"，不是"有个坏职位"
    assert position_repo.employee_current_position(db, dangling_owner.id) is None
    # 干净的人不许被牵连进诊断：没有任职记录 ≠ 数据有问题
    assert employee.id not in {item.employee_id for item in report["items"]}


def test_mismatched_department_is_reported(stage: dict):
    db = stage["db"]
    other_department = _department(db, stage["company_id"])
    assignment = _assign(db, stage["employee"], stage["slot"].id)
    assignment.department_id = other_department.id
    db.flush()
    kinds = {item.kind for item in position_service.integrity(db, stage["company_id"])["items"]}
    assert "SLOT_DEPARTMENT_MISMATCH" in kinds


# ------------------------------------------------------ 编制开设与行政态迁移


def test_open_slots_increments_headcount_within_department(stage: dict):
    db = stage["db"]
    created = position_service.open_slots(
        db,
        stage["definition"].id,
        SlotIn(department_id=stage["department"].id, count=3, note="team growth"),
        stage["company_id"],
    )
    indexes = [slot.headcount_index for slot in created]
    assert len(set(indexes)) == 3
    assert all(index > 1 for index in indexes), "夹具已占 1 号，新开的必须往后走"
    assert all(slot.administrative_status == "planned" for slot in created), (
        "新开的坑默认是 PLANNED（已编制未定人），ACTIVE 才是可用"
    )
    assert all(position_repo.slot_occupancy(db, slot) == OccupancyStatus.vacant for slot in created)


def test_headcount_index_survives_a_closed_slot_without_reuse(stage: dict):
    """序号只增不复用：坑可以被关，但 `count + 1` 会把新坑编成已存在的那一号。"""
    db = stage["db"]
    assert (
        position_repo.next_headcount_index(db, stage["department"].id, stage["definition"].id) == 2
    )
    stage["slot"].administrative_status = "closed"
    db.flush()
    assert (
        position_repo.next_headcount_index(db, stage["department"].id, stage["definition"].id) == 2
    )


@pytest.mark.parametrize(
    ("from_status", "to_status", "allowed"),
    [
        ("planned", "active", True),
        ("active", "frozen", True),
        ("frozen", "active", True),
        ("active", "closed", True),
        ("closed", "active", False),
        ("planned", "closed", True),
    ],
)
def test_administrative_transitions(stage: dict, from_status, to_status, allowed):
    db = stage["db"]
    slot = _slot(
        db,
        stage["company_id"],
        stage["department"],
        stage["definition"],
        administrative_status=from_status,
        index=31,
    )
    if allowed:
        updated = position_service.set_slot_administrative_status(
            db, slot.id, SlotAdminIn(administrative_status=to_status, reason="org change")
        )
        assert updated.administrative_status == to_status
        if to_status == "closed":
            assert updated.closed_at is not None
    else:
        with pytest.raises(HTTPException) as exc:
            position_service.set_slot_administrative_status(
                db, slot.id, SlotAdminIn(administrative_status=to_status)
            )
        assert exc.value.status_code == 409


def test_occupancy_can_never_be_written_through_the_api_surface(stage: dict):
    """ADR-2 在写入面上的表达：把派生态当入参会被 422 拒绝。"""
    db = stage["db"]
    for forbidden in ("vacant", "occupied", "assigned"):
        with pytest.raises(HTTPException) as exc:
            position_service.set_slot_administrative_status(
                db, stage["slot"].id, SlotAdminIn(administrative_status=forbidden)
            )
        assert exc.value.status_code == 422, forbidden


def test_definition_codes_cannot_collide_within_a_company(stage: dict):
    db = stage["db"]
    payload = PositionDefinitionIn(code=_uniq("lead"), name="Team Lead", job_family="engineering")
    created = position_service.create_definition(db, payload, stage["company_id"])
    assert created.template_scope == "company" and created.built_in is False
    with pytest.raises(HTTPException) as exc:
        position_service.create_definition(
            db,
            PositionDefinitionIn(code=payload.code, name="Another Lead"),
            stage["company_id"],
        )
    assert exc.value.status_code == 409


def test_code_rule_matches_the_v12_migration() -> None:
    """应用层与迁移里的 slug 规则必须一致（两份实现要有钉子，不然迟早分家）。

    迁移不 import 应用代码是刻意的（旧迁移不能因为应用演化而改变行为），
    所以这里反向把迁移模块加载起来对表。
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "k6f9b2c5d841_v12_position_definitions.py"
    )
    spec = importlib.util.spec_from_file_location("v12_for_rule_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for title in (
        "Software Engineer",
        "QA Engineer",
        "Chief Vibes Officer",
        "  Weird---Title  /  !!  ",
        "Product Manager",
        "L5//平台组",
    ):
        assert position_service._code_for(title) == module._slugify(title), title


def test_available_people_are_still_dispatchable_material(stage: dict):
    """§6 #4 的服务侧表达：没有职位不影响这个人被读进工作循环。

    名册读得到、按部门读得到、状态是 available —— 三件事必须同时成立。
    """
    from app.workforce.status import WorkforceStatusResolver

    db = stage["db"]
    employee = stage["employee"]
    assert WorkforceStatusResolver(db).resolve(employee) is not None
    entries = position_service.roster(db, stage["company_id"])
    assert employee.id in {entry["employee_id"] for entry in entries}
    entry = next(item for item in entries if item["employee_id"] == employee.id)
    assert entry["workforce_status"] == "available"
    assert entry["occupies_establishment"] is False
    assert entry["current_position"] is None
