"""Position repositories —— 职位域的**唯一读取入口**（docs/position-system.md §5）。

为什么存在一个"看起来只是包了一层 select"的模块，而不是让 service 直接查：

1. **占用态与当前职位是派生事实**，必须由这一层用同一套条件算出来。允许各处自己
   写 `where effective_to is null` 的后果，v0.4 已经演示过了 —— 19 条任职里 14 条
   没有 `position_id`，因为"当前职位"这件事从来没有一个权威读法，人人都读
   `employees.role` 文本。
2. 本层刻意**不依赖 `relationship()`**（见 `app/models/position.py` 模块注释）：
   任职是时间轴，`assignment.employee` 这类属性会诱导出 N+1 与"随手取第一个"的
   错误语义。批量入口（`occupancy_map` / `active_primaries_by_employee`）在这里
   用聚合查询一次取完，而不是让调用方在循环里逐条查。
3. `position_slot_id` **没有数据库外键**（SQLite 侧 `PRAGMA foreign_keys` 从不开启，
   加了也不生效，而 batch 重建历史表的风险是真实的）。所以完整性由
   `require_slot()` 这一类显式校验 + 测试守卫承担 —— 这是 ADR，不是遗漏。

写入不在本层：分配/调岗/关闭走 `app/services/position_service.py`（P4b+），
本模块只读。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.enums import AssignmentType, OccupancyStatus, SlotAdministrativeStatus
from app.models.lifecycle import AccessPackage
from app.models.organization import Department
from app.models.position import (
    PositionAssignment,
    PositionDefinition,
    PositionDefinitionPackage,
    PositionSlot,
    occupancy_from_count,
)


class SlotNotFound(LookupError):
    """引用了不存在的坑 —— 没有 DB 外键，所以这里必须是显式错误。"""


@dataclass(frozen=True)
class CurrentPosition:
    """某人当前的职位视图（派生，不落库）。"""

    employee_id: int
    definition_id: int
    code: str
    name: str
    level: int
    job_family: str
    legacy_role: str | None
    department_id: int | None
    department_name: str | None
    slot_id: int
    slot_code: str
    since: datetime
    assignment_type: str

    @property
    def is_custom(self) -> bool:
        """没有 legacy 对应 ⇒ 兼容层要如实报"自定义职位"，不谎报成某个 role。"""
        return self.legacy_role is None


def _scoped_company_id(db: Session, company_id: int | None) -> int | None:
    identity = get_request_identity()
    if company_id is None and identity is not None:
        return identity.company_id
    return company_id


# ---------------------------------------------------------------- definitions


def list_definitions(db: Session, company_id: int | None = None) -> list[PositionDefinition]:
    target = _scoped_company_id(db, company_id)
    stmt = select(PositionDefinition).order_by(PositionDefinition.code)
    if target is not None:
        # 公司的 + 内置模板（scope=system 且 company_id 为空）都可见；
        # 内置模板被公司采用时是**复制**成 company 行，不共享可变行。
        stmt = stmt.where(
            (PositionDefinition.company_id == target)
            | (PositionDefinition.template_scope == "system")
        )
    return list(db.scalars(stmt))


def get_definition(db: Session, definition_id: int) -> PositionDefinition | None:
    return db.get(PositionDefinition, definition_id)


def get_definition_by_code(
    db: Session, code: str, company_id: int | None = None
) -> PositionDefinition | None:
    target = _scoped_company_id(db, company_id)
    stmt = select(PositionDefinition).where(PositionDefinition.code == code)
    if target is not None:
        stmt = stmt.where(PositionDefinition.company_id == target)
    return db.scalars(stmt.order_by(PositionDefinition.id).limit(1)).first()


def definition_packages(db: Session, definition_id: int) -> list[AccessPackage]:
    """职位层权限包（P4d 两层的第二层在这里取数）。"""
    return list(
        db.scalars(
            select(AccessPackage)
            .join(
                PositionDefinitionPackage,
                PositionDefinitionPackage.package_id == AccessPackage.id,
            )
            .where(PositionDefinitionPackage.position_definition_id == definition_id)
            .order_by(AccessPackage.slug)
        )
    )


def definition_packages_map(
    db: Session, definition_ids: Sequence[int] | None = None
) -> dict[int, list[str]]:
    """批量版：定义 id → 权限包 slug 列表（Definitions 列表页一次取完）。

    P4d 的第二层权限（职位带来的包）读的就是这份映射，所以它必须是**唯一**的
    "定义→包"解法；再写一个 join 就会在两处给出不同的默认权限。
    """
    rows = db.execute(
        select(PositionDefinitionPackage.position_definition_id, AccessPackage.slug)
        .join(AccessPackage, AccessPackage.id == PositionDefinitionPackage.package_id)
        .order_by(PositionDefinitionPackage.position_definition_id, AccessPackage.slug)
    ).all()
    grouped: dict[int, list[str]] = {}
    for definition_id, slug in rows:
        if definition_id is None or (definition_ids and definition_id not in set(definition_ids)):
            continue
        grouped.setdefault(int(definition_id), []).append(slug)
    return grouped
    return [package.slug for package in definition_packages(db, definition_id)]


def definition_package_slugs(db: Session, definition_id: int) -> list[str]:
    """单定义版：`definition_packages_map()` 的包装。

    之前它是自己 join 一遍，现在只保留一份 join 实现 —— 职位权限包（P4d）会同时用到
    单点与批量两种读法，两份实现迟早给出不同答案。
    """
    return definition_packages_map(db, [definition_id]).get(definition_id, [])


# --------------------------------------------------------------------- slots


def list_slots(
    db: Session,
    company_id: int | None = None,
    *,
    department_id: int | None = None,
    definition_id: int | None = None,
) -> list[PositionSlot]:
    target = _scoped_company_id(db, company_id)
    stmt = select(PositionSlot).order_by(PositionSlot.department_id, PositionSlot.id)
    if target is not None:
        stmt = stmt.where(PositionSlot.company_id == target)
    if department_id is not None:
        stmt = stmt.where(PositionSlot.department_id == department_id)
    if definition_id is not None:
        stmt = stmt.where(PositionSlot.position_definition_id == definition_id)
    return list(db.scalars(stmt))


def get_slot(db: Session, slot_id: int) -> PositionSlot | None:
    return db.get(PositionSlot, slot_id)


def require_slot(db: Session, slot_id: int) -> PositionSlot:
    """写路径的完整性闸：坑不存在就报错，绝不静默接受悬空引用（无 DB FK 的代价补偿）。"""
    slot = get_slot(db, slot_id)
    if slot is None:
        raise SlotNotFound(f"position slot #{slot_id} 不存在")
    return slot


def active_primary_by_slot(db: Session, slot_ids: Sequence[int]) -> dict[int, int]:
    """批量算"每个坑有几个生效主职"，避免在循环里逐条查（N+1）。"""
    if not slot_ids:
        return {}
    rows = db.execute(
        select(
            PositionAssignment.position_slot_id,
            func.count(PositionAssignment.id),
        )
        .where(
            PositionAssignment.position_slot_id.in_(set(slot_ids)),
            PositionAssignment.effective_to.is_(None),
            PositionAssignment.assignment_type == AssignmentType.primary.value,
        )
        .group_by(PositionAssignment.position_slot_id)
    ).all()
    return {int(slot_id): int(count) for slot_id, count in rows if slot_id is not None}


def occupancy_map(db: Session, slots: Iterable[PositionSlot]) -> dict[int, OccupancyStatus]:
    """坑 → 占用态。算法只有一份：`app.models.position.occupancy_from_count`（ADR-2）。"""
    materialized = list(slots)
    if not materialized:
        return {}
    counts = active_primary_by_slot(db, [slot.id for slot in materialized])
    # 走 occupancy_from_count（模型层唯一口径）而不是在这里复制条件，
    # 也不是构造临时实体去喂 derive_occupancy —— 那样只是把假对象传进真算法。
    return {slot.id: occupancy_from_count(slot, counts.get(slot.id, 0)) for slot in materialized}


def slot_occupancy(db: Session, slot: PositionSlot) -> OccupancyStatus:
    return occupancy_map(db, [slot])[slot.id]


def slot_incumbents(db: Session, slot_id: int) -> list[PositionAssignment]:
    """在任者（生效主职）。兼任/代理不算在任，见 `derive_occupancy`。"""
    return list(
        db.scalars(
            select(PositionAssignment)
            .where(
                PositionAssignment.position_slot_id == slot_id,
                PositionAssignment.effective_to.is_(None),
                PositionAssignment.assignment_type == AssignmentType.primary.value,
            )
            .order_by(PositionAssignment.effective_from)
        )
    )


def incumbents_by_slot(db: Session, slot_ids: Sequence[int]) -> dict[int, list[int]]:
    """批量版在任者：坑 → 员工 id 列表（组织页一次取完，不逐坑查）。"""
    if not slot_ids:
        return {}
    # 用 execute 而不是 scalars：scalars() 会把多列行**降成第一列**，
    # 于是 `for slot_id, employee_id in rows` 拿到的是裸 int 而炸在这里。
    rows = db.execute(
        select(PositionAssignment.position_slot_id, PositionAssignment.employee_id)
        .where(
            PositionAssignment.position_slot_id.in_(set(slot_ids)),
            PositionAssignment.effective_to.is_(None),
            PositionAssignment.assignment_type == AssignmentType.primary.value,
        )
        .order_by(PositionAssignment.position_slot_id, PositionAssignment.effective_from)
    ).all()
    grouped: dict[int, list[int]] = {}
    for slot_id, employee_id in rows:
        if slot_id is None:
            continue
        grouped.setdefault(int(slot_id), []).append(int(employee_id))
    return grouped


def vacant_slots(db: Session, company_id: int | None = None) -> list[PositionSlot]:
    slots = list_slots(db, company_id)
    occupancy = occupancy_map(db, slots)
    return [
        slot
        for slot in slots
        if occupancy[slot.id] == OccupancyStatus.vacant
        and slot.administrative_status != SlotAdministrativeStatus.closed.value
    ]


# --------------------------------------------------------------- assignments


def active_assignments(db: Session, employee_id: int) -> list[PositionAssignment]:
    return list(
        db.scalars(
            select(PositionAssignment)
            .where(
                PositionAssignment.employee_id == employee_id,
                PositionAssignment.effective_to.is_(None),
            )
            .order_by(PositionAssignment.effective_from, PositionAssignment.id)
        )
    )


def active_primary_assignment(db: Session, employee_id: int) -> PositionAssignment | None:
    """当前主职。

    注意与旧的 `lifecycle.get_current_employment()` 的区别：老实现是
    `order_by(id.desc()).limit(1)`，一旦允许兼任/代理就会随机挑到一条非主职。
    这里按 `assignment_type=primary AND is_primary` 取，且部分唯一索引保证最多一条。
    """
    return db.scalars(
        select(PositionAssignment)
        .where(
            PositionAssignment.employee_id == employee_id,
            PositionAssignment.effective_to.is_(None),
            PositionAssignment.assignment_type == AssignmentType.primary.value,
            PositionAssignment.is_primary.is_(True),
        )
        .order_by(PositionAssignment.effective_from.desc(), PositionAssignment.id.desc())
        .limit(1)
    ).first()


def active_primaries_by_employee(
    db: Session, employee_ids: Sequence[int]
) -> dict[int, PositionAssignment]:
    """名册批量取"每人当前主职"（P5 名册一页 50 人 ⇒ 1 次查询，不是 50 次）。"""
    if not employee_ids:
        return {}
    rows = db.scalars(
        select(PositionAssignment)
        .where(
            PositionAssignment.employee_id.in_(set(employee_ids)),
            PositionAssignment.effective_to.is_(None),
            PositionAssignment.assignment_type == AssignmentType.primary.value,
            PositionAssignment.is_primary.is_(True),
        )
        .order_by(PositionAssignment.employee_id, PositionAssignment.effective_from.desc())
    ).all()
    picked: dict[int, PositionAssignment] = {}
    for row in rows:
        # 唯一索引保证每人最多一条；仍只留第一条，避免任何"后写覆盖"的隐式语义。
        picked.setdefault(int(row.employee_id), row)
    return picked


def career_history(db: Session, employee_id: int) -> list[PositionAssignment]:
    """职业履历：全时间轴，含已关闭的行（本表永不删行）。"""
    return list(
        db.scalars(
            select(PositionAssignment)
            .where(PositionAssignment.employee_id == employee_id)
            .order_by(PositionAssignment.effective_from, PositionAssignment.id)
        )
    )


def current_positions_by_employee(
    db: Session, employee_ids: Sequence[int]
) -> dict[int, CurrentPosition]:
    """批量当前职位：坑与定义各自**去重后一次取**，所以一页名册不会 N+1。

    没有主职、或主职的坑/定义解析不到的人，直接不出现在结果里（= `AVAILABLE`）。
    """
    primaries = active_primaries_by_employee(db, employee_ids)
    slots: dict[int, PositionSlot | None] = {}
    definitions: dict[int, PositionDefinition | None] = {}
    departments: dict[int, str | None] = {}
    for assignment in primaries.values():
        if assignment.position_slot_id is None:
            continue
        slot_id = int(assignment.position_slot_id)
        if slot_id not in slots:
            slots[slot_id] = get_slot(db, slot_id)
        slot = slots[slot_id]
        if slot is None:
            continue
        if slot.position_definition_id not in definitions:
            definitions[slot.position_definition_id] = get_definition(
                db, slot.position_definition_id
            )
        if slot.department_id not in departments:
            department = db.get(Department, slot.department_id)
            departments[slot.department_id] = department.name if department else None
    result: dict[int, CurrentPosition] = {}
    for employee_id, assignment in primaries.items():
        if assignment.position_slot_id is None:
            continue
        slot = slots.get(int(assignment.position_slot_id))
        if slot is None:
            # 悬空引用（无 DB FK 的代价）：宁缺不错 —— 报无职位，不编一个职位。
            continue
        definition = definitions.get(slot.position_definition_id)
        if definition is None:
            continue
        result[int(employee_id)] = CurrentPosition(
            employee_id=int(employee_id),
            definition_id=definition.id,
            code=definition.code,
            name=definition.name,
            level=definition.level,
            job_family=definition.job_family,
            legacy_role=definition.legacy_role,
            department_id=slot.department_id,
            department_name=departments.get(slot.department_id),
            slot_id=slot.id,
            slot_code=slot.slot_code,
            since=assignment.effective_from,
            assignment_type=assignment.assignment_type,
        )
    return result


def employee_current_position(db: Session, employee_id: int) -> CurrentPosition | None:
    """当前职位视图：生效 PRIMARY 任职 → 坑 → 定义；解析不到就是 None。

    单条走法只是批量走法的一个元素 —— 两处实现迟早给出不一致的职位。
    """
    return current_positions_by_employee(db, [employee_id]).get(int(employee_id))


def legacy_position_id_of_slot(slot: PositionSlot) -> int | None:
    """读 v13 存在坑上的回填补丁 —— **映射只存一份，不重跑 slug 规则**。

    新开的坑没有这个标记（它是纯历史对应关系），所以回 None 是正常的：
    旧 `employments.position_id` 从此只是给 v0.4 UI 读的兼容镜像，不再是真相。
    """
    marker = slot.metadata_json.get("__position_id") if slot.metadata_json else None
    return int(marker) if isinstance(marker, int) else None


def slot_for_legacy_position(db: Session, position_id: int | None) -> PositionSlot | None:
    """v0.4 的 `position_id` → 坑。靠 `__position_id` 标记找，不靠名字推断。

    在 Python 里过滤而不是 `json_extract`：SQLite 有 `json_extract`、PG 没有等价函数，
    而坑的量级很细（一个编制一行），不值得为此把服务锁到单一方言。
    """
    if position_id is None:
        return None
    for slot in db.scalars(select(PositionSlot).order_by(PositionSlot.id)):
        if legacy_position_id_of_slot(slot) == position_id:
            return slot
    return None


def next_headcount_index(db: Session, department_id: int, position_definition_id: int) -> int:
    """下一个编制序号：取已有最大值 +1，而不是 count +1 —— 坑可以被关，count 会重复。"""
    highest = db.scalar(
        select(func.max(PositionSlot.headcount_index)).where(
            PositionSlot.department_id == department_id,
            PositionSlot.position_definition_id == position_definition_id,
        )
    )
    return int(highest or 0) + 1


def definition_slot_counts(db: Session, definition_ids: Sequence[int]) -> dict[int, int]:
    """每个职位定义开了多少坑（批量，组织页用）。"""
    if not definition_ids:
        return {}
    rows = db.execute(
        select(PositionSlot.position_definition_id, func.count(PositionSlot.id))
        .where(PositionSlot.position_definition_id.in_(set(definition_ids)))
        .group_by(PositionSlot.position_definition_id)
    ).all()
    return {int(definition_id): int(count) for definition_id, count in rows}


def employees_in_position(
    db: Session, definition_code: str, company_id: int | None = None
) -> list[int]:
    """按职位 code 找人（取代 `get_employee_by_role`，orchestrator 里程碑负责人用它）。"""
    target = _scoped_company_id(db, company_id)
    stmt = (
        select(PositionAssignment.employee_id)
        .join(PositionSlot, PositionAssignment.position_slot_id == PositionSlot.id)
        .join(
            PositionDefinition,
            PositionSlot.position_definition_id == PositionDefinition.id,
        )
        .where(
            PositionDefinition.code == definition_code,
            PositionAssignment.effective_to.is_(None),
            PositionAssignment.assignment_type == AssignmentType.primary.value,
            PositionAssignment.is_primary.is_(True),
        )
        .order_by(PositionAssignment.effective_from)
    )
    if target is not None:
        stmt = stmt.where(PositionDefinition.company_id == target)
    return [int(row) for row in db.execute(stmt).scalars()]
