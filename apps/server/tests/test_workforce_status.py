"""P4a：`WorkforceStatusResolver` 与 `position_compat` 的规则验收。

这里守的是两条最容易被"顺手改一下"破坏的东西：
  * 状态派生只有一处（`derive_workforce_status`），且**不入库**；
  * 旧 `employees.role` 只经兼容层，而且兼容层的真值来自任职时间轴，不是那个字段。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.enums import AssignmentType, EmployeeRole, LifecycleStatus, WorkforceStatus
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.repositories import position as position_repo
from app.services import position_compat
from app.workforce.status import (
    WorkforceStatusResolver,
    derive_workforce_status,
    has_active_primary,
)

NOW = datetime.now(UTC)


def _assignment(
    *,
    assignment_type: str = AssignmentType.primary.value,
    is_primary: bool = True,
    effective_to: datetime | None = None,
    slot_id: int | None = 1,
) -> PositionAssignment:
    """纯内存对象：规则是纯函数，测它不需要数据库。"""
    return PositionAssignment(
        employee_id=1,
        department_id=1,
        position_slot_id=slot_id,
        employment_status="active" if effective_to is None else "closed",
        effective_from=NOW - timedelta(days=1),
        effective_to=effective_to,
        metadata_json={},
        assignment_type=assignment_type,
        is_primary=is_primary,
        reason="",
        position_title_snapshot="",
    )


# ------------------------------------------------------------- 规则表（纯函数）


@pytest.mark.parametrize(
    ("lifecycle", "assignments", "expected"),
    [
        (LifecycleStatus.active.value, [_assignment()], WorkforceStatus.assigned),
        (LifecycleStatus.active.value, [], WorkforceStatus.available),
        (LifecycleStatus.active.value, [_assignment(effective_to=NOW)], WorkforceStatus.available),
        (LifecycleStatus.pending.value, [_assignment()], WorkforceStatus.onboarding),
        (LifecycleStatus.onboarding.value, [], WorkforceStatus.onboarding),
        (LifecycleStatus.suspended.value, [_assignment()], WorkforceStatus.suspended),
        (LifecycleStatus.offboarding.value, [_assignment()], WorkforceStatus.offboarding),
        (LifecycleStatus.offboarded.value, [_assignment()], WorkforceStatus.offboarded),
        # 转岗中由 lifecycle 轴决定；即便此刻只查得到一条主职，也不降级成 ASSIGNED
        (LifecycleStatus.transferring.value, [_assignment()], WorkforceStatus.transferring),
        (LifecycleStatus.transferring.value, [], WorkforceStatus.transferring),
        # 只有兼任/代理 ⇒ 仍然算"待分配"（有身份、没编制）
        (
            LifecycleStatus.active.value,
            [_assignment(assignment_type=AssignmentType.secondary.value, is_primary=False)],
            WorkforceStatus.available,
        ),
        # 未知值：退到最保守的在途态，不猜成 assigned/available
        ("made_up_status", [_assignment()], WorkforceStatus.onboarding),
        (None, [], WorkforceStatus.onboarding),
    ],
    ids=[
        "active+primary",
        "active+none",
        "active+ended",
        "pending+primary",
        "onboarding+none",
        "suspended",
        "offboarding",
        "offboarded",
        "transferring+primary",
        "transferring+none",
        "active+secondary-only",
        "unknown-lifecycle",
        "missing-lifecycle",
    ],
)
def test_rule_table(lifecycle, assignments, expected):
    assert derive_workforce_status(lifecycle, assignments) == expected


def test_transferring_is_never_inferred_from_overlapping_assignments():
    """拍板：不许用"两条 assignment 同时存在"反推转岗中。

    effective date 交叠、代理任职、兼任都会误判 —— 转岗是工作流事实，归 lifecycle 轴。
    """
    overlapping = [_assignment(), _assignment(slot_id=2, is_primary=True)]
    assert has_active_primary(overlapping) is True
    assert derive_workforce_status(LifecycleStatus.active.value, overlapping) in {
        WorkforceStatus.assigned
    }
    # 反过来：真的在转岗（lifecycle=transferring）也不要求第二条任职存在
    assert (
        derive_workforce_status(LifecycleStatus.transferring.value, [_assignment()])
        == WorkforceStatus.transferring
    )


def test_status_has_no_storage_column():
    """ADR-4 的结构断言：库里没有承载 workforce_status 的列。"""
    columns = {column.key for column in Employee.__table__.columns}
    assert "lifecycle_status" in columns  # 输入轴之一，这个是真的存
    assert "workforce_status" not in columns
    assert not any("position" in name for name in columns), columns


# -------------------------------------------------------- resolver（带库的一层）


@pytest.fixture()
def roster(db: Session, default_company_id: int) -> dict:
    """真实形状的小名册：2 人有主职、3 人 ACTIVE 但无主职（dev 库 11 人的缩影）。"""
    department = Department(company_id=default_company_id, name="P4a Roster", slug="p4a-roster")
    db.add(department)
    db.flush()
    definition = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code="p4a_roster_engineer",
        name="Roster Engineer",
        description="",
        job_family="engineering",
        level=2,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role="engineer",
    )
    custom = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code="p4a_dungeon_master",
        name="Dungeon Master",
        description="",
        job_family="creative",
        level=1,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role=None,  # 用户自定义职位：没有 legacy 对应
    )
    db.add_all([definition, custom])
    db.flush()
    slots = []
    for index, definition_row in ((1, definition), (2, definition), (3, custom)):
        slot = PositionSlot(
            company_id=default_company_id,
            department_id=department.id,
            position_definition_id=definition_row.id,
            slot_code=f"ROSTER-{index}",
            headcount_index=index,
            administrative_status="active",
            metadata_json={},
        )
        db.add(slot)
        slots.append(slot)
    db.flush()

    people = {}
    for slug in ("assigned-a", "assigned-b", "free-a", "free-b", "free-c"):
        person = Employee(
            company_id=default_company_id,
            department_id=department.id,
            name=slug,
            slug=f"p4a-{slug}",
            role="engineer",
            title=slug,
            avatar="",
            status="idle",
            lifecycle_status=LifecycleStatus.active.value,
            username=f"p4a-{slug}",
            runtime_type="mock",
            runtime_config={},
            workspace_path=f"data/employees/p4a-{slug}",
            memory_namespace=f"emp_p4a-{slug}",
        )
        db.add(person)
        people[slug] = person
    db.flush()
    for slug, slot in (("assigned-a", slots[0]), ("assigned-b", slots[1])):
        db.add(
            PositionAssignment(
                employee_id=people[slug].id,
                department_id=department.id,
                position_slot_id=slot.id,
                employment_status="active",
                joined_at=NOW,
                effective_from=NOW,
                effective_to=None,
                metadata_json={},
                assignment_type=AssignmentType.primary.value,
                is_primary=True,
                reason="roster",
                position_title_snapshot="Roster Engineer",
            )
        )
    db.flush()
    return {
        "department": department,
        "definition": definition,
        "custom": custom,
        "slots": slots,
        "people": people,
        "company_id": default_company_id,
    }


def test_resolver_matches_the_pure_rule_on_real_rows(db: Session, roster: dict):
    resolver = WorkforceStatusResolver(db)
    by_slug = {slug.split("-")[0]: slug for slug in roster["people"]}
    assert resolver.resolve(roster["people"][by_slug["assigned"]]) == WorkforceStatus.assigned
    assert resolver.resolve(roster["people"][by_slug["free"]]) == WorkforceStatus.available


def test_batch_views_never_disagree_with_single_resolve(db: Session, roster: dict):
    """两条读法（逐个 / 批量）必须同结论 —— 否则名册列表和详情页会显示不同状态。"""
    resolver = WorkforceStatusResolver(db)
    people = list(roster["people"].values())
    batch = resolver.views(people)
    for person in people:
        single = resolver.resolve(person)
        assert batch[int(person.id)].workforce_status == single, person.slug
    assert sum(1 for view in batch.values() if view.has_primary_assignment) == 2


def test_counts_report_available_as_a_subset_not_a_sibling(db: Session, roster: dict):
    counts = WorkforceStatusResolver(db).counts(list(roster["people"].values()))
    assert counts[WorkforceStatus.available.value] == 3
    assert counts[WorkforceStatus.assigned.value] == 2
    # "在岗"含 assigned + available + transferring：available 是 active 的子集，
    # 把 available 与 assigned 相加当"在岗"是错的，所以 counts() 直接给这个口径。
    assert counts["on_roster"] == 5


def test_resolve_does_not_write_anything(db: Session, roster: dict):
    resolver = WorkforceStatusResolver(db)
    for person in roster["people"].values():
        resolver.resolve(person)
    # SQLAlchemy 的 db.new/db.dirty 是 IdentitySet，只断言"为空"，不做相等比较
    assert not db.new and not db.dirty, "派生入口不许把结果写回实体"


def test_available_people_are_not_excluded_from_reads(db: Session, roster: dict):
    """文档 §6 #4：不许因为"没有职位"就把人从工作循环之外抹掉。"""
    free = [person for slug, person in roster["people"].items() if slug.startswith("free-")]
    free_ids = {int(person.id) for person in free}
    # 批量读法不会给他们伪造主职……
    assert position_repo.active_primaries_by_employee(db, sorted(free_ids)) == {}
    # ……但名册读法也一个都不能丢：没有职位的人仍然是人，仍在名册里。
    # （"仍能被派任务"这条要等 P5 的派单读路一起测，见 docs/talent-roster.md §6 #4）
    views = WorkforceStatusResolver(db).views(free)
    assert set(views) == free_ids
    assert {view.workforce_status for view in views.values()} == {WorkforceStatus.available}


# --------------------------------------------------------- 兼容层（唯一 role 读点）


def test_compat_position_comes_from_the_timeline_not_the_mirror(db: Session, roster: dict):
    """关键反证：`employees.role` 写着 ceo，真任职是 engineer ⇒ 报 engineer。

    这就是"镜像不再是真相"的可执行表达。
    """
    person = roster["people"]["assigned-a"]
    person.role = EmployeeRole.ceo.value
    db.flush()
    assert position_compat.legacy_role_of(db, person) == EmployeeRole.engineer.value
    current = position_compat.derived_current_position(db, person)
    assert current is not None
    assert current.code == "p4a_roster_engineer" and current.legacy_role == "engineer"
    assert current.position_is_custom is False


def test_custom_position_does_not_lie_about_role(
    db: Session, roster: dict, default_company_id: int
):
    slot = roster["slots"][2]
    person = roster["people"]["free-a"]
    db.add(
        PositionAssignment(
            employee_id=person.id,
            department_id=roster["department"].id,
            position_slot_id=slot.id,
            employment_status="active",
            joined_at=NOW,
            effective_from=NOW,
            effective_to=None,
            metadata_json={},
            assignment_type=AssignmentType.primary.value,
            is_primary=True,
            reason="custom",
            position_title_snapshot="Dungeon Master",
        )
    )
    db.flush()
    current = position_compat.derived_current_position(db, person)
    assert current is not None and current.code == "p4a_dungeon_master"
    assert current.legacy_role is None and current.position_is_custom is True
    # 兜底值 + 明示"这是兜底"，而不是把自定义职位谎报成一个存在的 role
    assert position_compat.legacy_role_of(db, person) == EmployeeRole.engineer.value


def test_available_people_have_no_position_but_still_a_readable_role(db: Session, roster: dict):
    """无主职 ⇒ current_position 为 null（不猜职位）；role 读镜像只为旧前端不破。"""
    person = roster["people"]["free-b"]
    assert position_compat.derived_current_position(db, person) is None
    person.role = EmployeeRole.qa_engineer.value
    db.flush()
    assert position_compat.legacy_role_of(db, person) == EmployeeRole.qa_engineer.value
    assert position_compat.workforce_status_of(db, person) == WorkforceStatus.available.value


def test_broken_mirror_falls_back_instead_of_echoing_garbage(db: Session, roster: dict):
    person = roster["people"]["free-c"]
    person.role = "chief_vibes_officer"
    db.flush()
    assert position_compat.legacy_role_of(db, person) == EmployeeRole.engineer.value


def test_enrich_employee_payload_carries_all_three_facts(db: Session, roster: dict):
    person = roster["people"]["assigned-b"]
    payload = position_compat.enrich_employee(db, person, {"id": person.id, "role": "ceo"})
    assert payload["role"] == EmployeeRole.engineer.value, "role 必须被派生值覆盖，不能原样透出"
    assert payload["workforce_status"] == WorkforceStatus.assigned.value
    assert payload["current_position"]["code"] == "p4a_roster_engineer"
    assert payload["current_position"]["slot_code"] == "ROSTER-2"
    assert payload["current_position"]["since"] is not None
    # Fit 不在这里：能力域（P9）之前返回任何数字都是编造
    assert "fit" not in payload["current_position"]
