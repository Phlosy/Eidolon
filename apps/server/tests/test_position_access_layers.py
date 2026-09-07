"""P4d 第 1 段：两层权限（人级 / 职位级）的原语。

这里守的是 docs/position-system.md §4 的四条：
  1. 复用同一套 union/diff（不写第二套）；
  2. `manual` 行永不隐式移除；
  3. 两层各管各的 source 行，谁都不能越界删对方；
  4. 人级优先 —— 同时被两层声称的包归人级，否则"离开职位"会把人级访问误撤。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session
from test_position_service import _department, _employee, _slot

from app.lifecycle import access
from app.models.enums import PackageSource
from app.models.lifecycle import AccessPackage, AccessPackageItem, EmployeePackage
from app.models.position import PositionAssignment, PositionDefinition
from app.repositories import lifecycle as lifecycle_repo
from app.schemas.position import AssignmentIn
from app.services import position_service

NOW = datetime.now(UTC)
_counter = itertools.count(1)


def _uniq(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}-{int(NOW.timestamp())}"


def _entitlement(db: Session, key: str):
    row = lifecycle_repo.get_entitlement_by_key(db, key)
    assert row is not None, f"种子里没有 {key}（测试依赖 lifecycle seed）"
    return row


def _package(db: Session, *, name: str, entitlement_keys: list[str]) -> AccessPackage:
    package = AccessPackage(slug=_uniq("pkgL"), name=name, built_in=False)
    db.add(package)
    db.flush()
    for key in entitlement_keys:
        entitlement = _entitlement(db, key)
        db.add(AccessPackageItem(package_id=package.id, entitlement_id=entitlement.id))
    db.flush()
    return package


@pytest.fixture()
def stage(db, default_company_id):
    """一个部门 + 一个声明了权限包的职位定义 + 一个空坑 + 两个人。"""
    department = _department(db, default_company_id)
    code = _uniq("SEL")
    definition = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code=code,
        name="软件工程师",
        description="",
        job_family="engineering",
        level=3,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role="engineer",
    )
    db.add(definition)
    db.flush()
    slot = _slot(db, default_company_id, department, definition)
    base = _package(db, name="base", entitlement_keys=["workspace:private"])
    shared = _package(
        db,
        name="职位共享包",
        entitlement_keys=["git:company-org-member", "docs:company-read"],
    )
    employees = {
        "alice": _employee(db, default_company_id, department),
        "bob": _employee(db, default_company_id, department),
    }
    # 人级：base + 手动授予的共享包（模拟"这个人本来就有的访问"）
    db.add(
        EmployeePackage(
            employee_id=employees["alice"].id,
            package_id=base.id,
            source=PackageSource.role.value,
        )
    )
    db.flush()
    return {
        "db": db,
        "company_id": default_company_id,
        "department": department,
        "definition": definition,
        "slot": slot,
        "base": base,
        "shared": shared,
        **employees,
    }


def _assignment(db, employee, *, slot_id, department_id, kind="primary", title="临时"):
    """直接写一条生效任职（用来造悬空坑 / 代理这类服务层不给造的形状）。"""
    row = PositionAssignment(
        employee_id=employee.id,
        department_id=department_id,
        position_id=None,
        position_slot_id=slot_id,
        assignment_type=kind,
        is_primary=kind == "primary",
        position_title_snapshot=title,
        employment_status="active",
        reason="test",
        joined_at=NOW,
        effective_from=NOW,
        effective_to=None,
        metadata_json={},
    )
    db.add(row)
    db.flush()
    return row


def _grant(db, employee, package, source: str):
    row = EmployeePackage(employee_id=employee.id, package_id=package.id, source=source)
    db.add(row)
    db.flush()
    return row


def _attach_package_to_definition(db, definition, package):
    from app.models.position import PositionDefinitionPackage

    db.add(PositionDefinitionPackage(position_definition_id=definition.id, package_id=package.id))
    db.flush()


# ------------------------------------------------------------------ 解析链


def test_position_packages_follow_the_resolution_chain(stage):
    """任职 → 坑 → 定义 → 包；任一环节断了就不给包，而不是猜一个。"""
    db, s = stage["db"], stage
    assert access.position_packages_for(db, s["alice"].id) == [], "没任职就没有职位包"

    _attach_package_to_definition(db, s["definition"], s["shared"])
    position_service.assign_position(db, s["alice"], AssignmentIn(slot_id=s["slot"].id))
    assert [p.id for p in access.position_packages_for(db, s["alice"].id)] == [s["shared"].id]

    # 关掉任职 → 职位包立刻不再属于他（权限收敛由 sync 负责）
    position_service.release_position(db, s["alice"], reason="项目结束")
    assert access.position_packages_for(db, s["alice"].id) == []


def test_position_packages_skip_dangling_slots_and_missing_packages(stage):
    """悬空坑（v0.4 遗留）与"定义声明了不存在的包"都不能编出权限。"""
    db, s = stage["db"], stage
    assignment = _assignment(
        db, s["bob"], slot_id=999_999, department_id=s["department"].id, title="历史遗留"
    )  # 不存在的坑
    assert access.position_packages_for(db, s["bob"].id) == []

    # 定义声明了一个不存在的包：跳过它，别的包照常给
    db.delete(assignment)
    _attach_package_to_definition(db, s["definition"], s["shared"])
    from app.models.position import PositionDefinitionPackage

    db.add(PositionDefinitionPackage(position_definition_id=s["definition"].id, package_id=999_998))
    db.flush()
    position_service.assign_position(db, s["bob"], AssignmentIn(slot_id=s["slot"].id))
    assert [p.id for p in access.position_packages_for(db, s["bob"].id)] == [s["shared"].id]


# -------------------------------------------------------------------- 两层


def test_layer_view_splits_person_and_position(stage):
    """`effective_access()` 要能回答"这条权限是谁给的"。"""
    db, s = stage["db"], stage
    _attach_package_to_definition(db, s["definition"], s["shared"])
    position_service.assign_position(db, s["alice"], AssignmentIn(slot_id=s["slot"].id))
    access.sync_position_packages(
        db, s["alice"].id, access.position_packages_for(db, s["alice"].id)
    )

    view = access.effective_access(db, s["alice"].id)
    person_keys = {e.entitlement.key for e in view["person"]}
    position_keys = {e.entitlement.key for e in view["position"]}
    assert person_keys == {"workspace:private"}, person_keys
    assert position_keys == {"git:company-org-member", "docs:company-read"}, position_keys
    assert {e.entitlement.key for e in view["effective"]} == person_keys | position_keys


def test_shared_entitlement_lists_both_layers(stage):
    """同一条 entitlement 由人级与职位级各给一次：并集只有一条，但 `sources` 有两条。

    docs/position-system.md §4 特别点名要覆盖这种"SE 与 Researcher 共享
    `git:company-org-member`"的情形 —— 只按 entitlement key 去重会丢掉来源。
    """
    db, s = stage["db"], stage
    _attach_package_to_definition(db, s["definition"], s["shared"])
    _grant(db, s["alice"], s["shared"], PackageSource.manual.value)  # 人级也有
    position_service.assign_position(db, s["alice"], AssignmentIn(slot_id=s["slot"].id))
    access.sync_position_packages(
        db, s["alice"].id, access.position_packages_for(db, s["alice"].id)
    )

    view = access.effective_access(db, s["alice"].id)
    entry = next(e for e in view["effective"] if e.entitlement.key == "git:company-org-member")
    assert {src["layer"] for src in entry.sources} == {"person"}, (
        "职位层已把包认领给人级（人级优先），所以这条只该有人级来源"
    )
    # 关键结论：离开职位后人级仍然有
    position_service.release_position(db, s["alice"], reason="卸任")
    access.sync_position_packages(
        db,
        s["alice"].id,
        access.position_packages_for(db, s["alice"].id),
        person_claimed_ids={s["shared"].id},
    )
    assert "git:company-org-member" in {
        e.entitlement.key for e in access.employee_entitlements(db, s["alice"].id)
    }, "卸任把人级本来就有的访问撤掉了"


def test_position_sync_never_touches_person_rows(stage):
    """职位层只删自己的行。"""
    db, s = stage["db"], stage
    _grant(db, s["bob"], s["base"], PackageSource.role.value)
    _grant(db, s["bob"], s["shared"], PackageSource.manual.value)
    added, removed = access.sync_position_packages(db, s["bob"].id, [s["shared"]])
    assert (added, removed) == ([], []), (
        "manual 行已经覆盖了这个包，职位层不该再造第二行（并集按 entitlement 去重）"
    )
    rows = lifecycle_repo.list_employee_packages(db, s["bob"].id)
    assert {row.source for row in rows} == {
        PackageSource.role.value,
        PackageSource.manual.value,
    }, "职位层不该新增行，也不该改别人的 source"

    added, removed = access.sync_position_packages(db, s["bob"].id, [])
    assert (added, removed) == ([], []), "两层都不该被清空 —— 职位层没有自己的行"


def test_position_sync_removes_only_unclaimed_position_rows(stage):
    """卸任后：没人声称的职位包被回收；`person_claimed_ids` 里的保得住。"""
    db, s = stage["db"], stage
    orphan = _package(db, name="只属于职位", entitlement_keys=["git:company-org-member"])
    _grant(db, s["alice"], orphan, PackageSource.position.value)
    _grant(db, s["alice"], s["shared"], PackageSource.position.value)

    added, removed = access.sync_position_packages(
        db, s["alice"].id, [], person_claimed_ids={s["shared"].id}
    )
    assert removed == [orphan.id], removed
    assert added == []
    left = {row.package_id for row in lifecycle_repo.list_employee_packages(db, s["alice"].id)}
    assert s["shared"].id in left and orphan.id not in left


def test_person_layer_adopts_position_owned_rows(db, default_company_id):
    """人级同步会把职位层的行**认领**成人级行（不认领 = 离坑时误撤）。

    用种子里真实的 `base-employee` 包来测：它就是"人级永远要有"的那个包，
    也正是最容易同时被职位包声明的 slug。
    """
    base = lifecycle_repo.get_package_by_slug(db, access.BASE_PACKAGE_SLUG)
    assert base is not None, "种子没有 base-employee 包（人级层的根基）"
    department = _department(db, default_company_id)
    person = _employee(db, default_company_id, department)
    row = _grant(db, person, base, PackageSource.position.value)

    desired = access.resolve_packages(db, role="engineer", department_slug=None)
    assert base.id in {p.id for p in desired}, "人级期望集里没有 base 包"
    access.sync_role_packages(db, person.id, desired)
    db.refresh(row)
    assert row.source == PackageSource.role.value, (
        "行还挂在 position 上 —— 下次职位层收敛就会把人级该有的权限删掉"
    )

    added, removed = access.sync_position_packages(db, person.id, [])
    assert (added, removed) == ([], []), "人级认领之后职位层不该再动它"


def test_layer_of_source_is_the_single_rule():
    """只有 `position` 是职位层，其余（含 project / 未知值）都算人级。"""
    assert access.layer_of_source(PackageSource.position.value) == "position"
    for source in (
        PackageSource.manual.value,
        PackageSource.role.value,
        PackageSource.project.value,
        "将来可能出现的新值",
    ):
        assert access.layer_of_source(source) == "person"


def test_position_sync_is_idempotent(stage):
    """事件可能重放；重复收敛不得产生第二行或第二次删除。"""
    db, s = stage["db"], stage
    _attach_package_to_definition(db, s["definition"], s["shared"])
    position_service.assign_position(db, s["alice"], AssignmentIn(slot_id=s["slot"].id))
    desired = access.position_packages_for(db, s["alice"].id)
    first = access.sync_position_packages(db, s["alice"].id, desired)
    second = access.sync_position_packages(db, s["alice"].id, desired)
    assert first[0] == [s["shared"].id]
    assert second == ([], []), f"重放产生了第二次变更：{second}"
    rows = lifecycle_repo.list_employee_packages(db, s["alice"].id)
    assert len([r for r in rows if r.package_id == s["shared"].id]) == 1


def test_acting_assignment_confers_position_access(stage):
    """职位权限跟着实际职责走：代理（ACTING）也算 —— 与"占不占编制"是两把尺。"""
    db, s = stage["db"], stage
    _attach_package_to_definition(db, s["definition"], s["shared"])
    _assignment(
        db,
        s["bob"],
        slot_id=s["slot"].id,
        department_id=s["department"].id,
        kind="acting",
        title="代理软件工程师",
    )
    assert [p.id for p in access.position_packages_for(db, s["bob"].id)] == [s["shared"].id]
    # 但代理不占编制（ADR-2 那把尺不变）
    assert position_service.slots_out(db, [s["slot"]])[0]["occupancy_status"] == "vacant"
