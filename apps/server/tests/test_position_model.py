"""ADR-2 / §8.2 的机器守卫：编制占用态**只能被算出来，不能被写进去**。

为什么值得单独一个文件：占用态是这次重构里最容易被"顺手加个字段"破坏的东西。
一旦 `occupancy_status` 落库，它和 `PositionAssignment` 时间轴就会各说各话 ——
那正是本次重构要消灭的"两个真相"。所以这里既测真值表，也测**结构上没有写入口**。
"""

from __future__ import annotations

from app.models.enums import AssignmentType, OccupancyStatus
from app.models.position import (
    PositionAssignment,
    PositionDefinition,
    PositionSlot,
    derive_occupancy,
)


def _slot(administrative_status: str = "active", slot_id: int | None = 1) -> PositionSlot:
    slot = PositionSlot(
        company_id=1,
        department_id=1,
        position_definition_id=1,
        slot_code="ENG-SOFTWARE_ENGINEER-1",
        headcount_index=1,
        administrative_status=administrative_status,
    )
    slot.id = slot_id
    return slot


def _assignment(
    slot_id: int | None = 1,
    *,
    assignment_type: str = AssignmentType.primary.value,
    is_primary: bool = True,
    effective_to=None,
) -> PositionAssignment:
    return PositionAssignment(
        employee_id=1,
        department_id=1,
        position_slot_id=slot_id,
        employment_status="active",
        assignment_type=assignment_type,
        is_primary=is_primary,
        reason="",
        position_title_snapshot="",
        metadata_json={},
        effective_to=effective_to,
    )


def test_occupancy_truth_table():
    slot = _slot()
    assert derive_occupancy(slot, []) == OccupancyStatus.vacant
    assert derive_occupancy(slot, [_assignment()]) == OccupancyStatus.occupied
    # 已结束的任职不占坑
    from datetime import UTC, datetime

    assert (
        derive_occupancy(slot, [_assignment(effective_to=datetime.now(UTC))])
        == OccupancyStatus.vacant
    )
    # 别的坑上的任职不影响本坑
    assert derive_occupancy(slot, [_assignment(slot_id=99)]) == OccupancyStatus.vacant


def test_frozen_and_closed_slots_are_never_reported_vacant():
    """冻结坑算成 VACANT 会诱导用户去填一个不该填的坑；已关闭坑不能复活。"""
    assert derive_occupancy(_slot("frozen"), []) == OccupancyStatus.frozen
    assert derive_occupancy(_slot("frozen"), [_assignment()]) == OccupancyStatus.frozen
    assert derive_occupancy(_slot("closed"), [_assignment()]) == OccupancyStatus.closed
    assert derive_occupancy(_slot("planned"), []) == OccupancyStatus.vacant


def test_secondary_assignments_do_not_occupy_a_slot():
    """兼任/代理不占编制 —— 否则一人两岗会把坑算成"已占满"，招聘建议就错了。"""
    slot = _slot()
    assert (
        derive_occupancy(
            slot,
            [
                _assignment(
                    assignment_type=AssignmentType.secondary.value,
                    is_primary=False,
                )
            ],
        )
        == OccupancyStatus.vacant
    )


def test_occupancy_has_no_storage_or_write_path():
    """结构性断言：库里没有承载占用态的列，实体上也没有可写的占用态字段。"""
    slot_columns = {column.key for column in PositionSlot.__table__.columns}
    assert "administrative_status" in slot_columns
    assert not any("occupancy" in name or name == "status" for name in slot_columns), slot_columns

    assignment_columns = {column.key for column in PositionAssignment.__table__.columns}
    assert not any("occupancy" in name for name in assignment_columns), assignment_columns

    # 实体上不存在可写的占用态属性（Declarative 实例允许随手挂属性，所以只断言类定义）
    assert not hasattr(PositionSlot, "occupancy_status")
    assert not hasattr(PositionSlot, "vacant")
    # 并且 derive_occupancy 是纯函数：算一次不会把结论写回坑上
    slot = _slot()
    before = dict(slot.__dict__)
    derive_occupancy(slot, [_assignment()])
    assert slot.__dict__ == before, "派生函数有副作用 —— 那等于偷偷把派生态变成存储态"


def test_slot_status_enum_only_carries_administrative_values():
    """DB 侧枚举里不许混进 VACANT/OCCUPIED —— 混进来就等于允许落库。"""
    from app.models.enums import SlotAdministrativeStatus

    stored = {member.value for member in SlotAdministrativeStatus}
    assert stored == {"planned", "active", "frozen", "closed"}, stored
    derived = {member.value for member in OccupancyStatus}
    assert {"vacant", "occupied"} <= derived
    # 不可妥协的是：库侧枚举里绝不能出现"占用/空缺" —— 出现就有落库入口，就有第二个真相。
    # frozen/closed 在两个轴上同名是**故意的**：派生轴要能回声行政态，
    # 否则 UI 得自己合成"冻结的坑不算空缺"这条规则，合成点一多就会漂。
    assert not ({"vacant", "occupied"} & stored), stored


def test_employment_model_still_maps_the_legacy_table():
    """别名不是第二个实体：读代码的老名字和新名字必须指向同一张表同一个映射。"""
    from app.models.lifecycle import Employment

    assert Employment is PositionAssignment
    assert PositionAssignment.__tablename__ == "employments"
    assert "position_slot_id" in {c.key for c in PositionAssignment.__table__.columns}
    # 旧 v0.4 的 positions 表在 v18 之前仍然存在，但它只有一个映射，
    # 不会与 position_definitions 混成两套职位真相。
    from app.models.lifecycle import Position

    assert Position.__tablename__ == "positions"
    assert Position is not PositionDefinition
