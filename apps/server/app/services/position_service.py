"""Position service —— 编制开设与**显式**职位分配工作流（P4b）。

三条来自拍板的硬规则，本模块是唯一的执行点：

1. **只有这里能创建 PRIMARY 任职**（`app/services/lifecycle.py` 的招聘/调岗都调进来）。
   招聘本身只造人与资源；点不到坑就如实留空，人派生成 `AVAILABLE`。
2. **`position_slot_id` 必须过 `require_slot()`**。本项目 SQLite 不开
   `PRAGMA foreign_keys`，所以这里不校验就等于没有约束（docs/position-system.md §6.1）。
3. **不猜坑、不补坑**。`position_id` → 坑只走 v13 留下的 `__position_id` 标记；
   新开的坑没这个标记就是没有旧职位对应，绝不按名字/title 反推。

权限 Provisioning 刻意**不在本事务里**：任职是"谁承担哪个编制"，权限是"因此需要哪些
资源"，后者由 P4d 通过 `employee.position_assigned` 事件与 Desired State 收敛。
把两件事写进一个事务的后果是：权限供应失败会回滚掉一个真实发生过的任命。
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.lifecycle import audit
from app.models.base import utcnow
from app.models.enums import (
    AssignmentType,
    OccupancyStatus,
    SlotAdministrativeStatus,
    TemplateScope,
)
from app.models.lifecycle import Position
from app.models.organization import Department, Employee
from app.models.position import (
    PositionAssignment,
    PositionDefinition,
    PositionSlot,
)
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.schemas.position import (
    AssignmentIn,
    IntegrityItemOut,
    PositionDefinitionIn,
    SlotAdminIn,
    SlotIn,
)
from app.workforce.status import WorkforceStatusResolver, row_occupies_establishment

#: 能被填的坑。FROZEN/CLOSED 不在此列 —— 派生态回声行政态的那条规则在这里落地。
_FILLABLE = {
    SlotAdministrativeStatus.planned.value,
    SlotAdministrativeStatus.active.value,
}
_ADMIN_STATUSES = {member.value for member in SlotAdministrativeStatus}
#: CLOSED 是终态：关掉一个编制是组织决定，重开应当显式新建，而不是把历史行翻活。
_ALLOWED_ADMIN_TRANSITIONS: dict[str, set[str]] = {
    SlotAdministrativeStatus.planned.value: {
        SlotAdministrativeStatus.active.value,
        SlotAdministrativeStatus.frozen.value,
        SlotAdministrativeStatus.closed.value,
    },
    SlotAdministrativeStatus.active.value: {
        SlotAdministrativeStatus.frozen.value,
        SlotAdministrativeStatus.closed.value,
    },
    SlotAdministrativeStatus.frozen.value: {
        SlotAdministrativeStatus.active.value,
        SlotAdministrativeStatus.closed.value,
    },
    SlotAdministrativeStatus.closed.value: set(),
}


def _require_slot(db: Session, slot_id: int) -> PositionSlot:
    """仓库层的 `SlotNotFound` 在这里翻译成 404。

    领域错误（LookupError）与 HTTP 语义要分开：仓库不该知道 HTTP；
    但服务层把它原样抛给 API 会变成 500 —— 而"引用了不存在的坑"是客户端的错。
    """
    try:
        return position_repo.require_slot(db, slot_id)
    except position_repo.SlotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _code_for(title: str) -> str:
    """职位 code 生成规则。

    注意：这与 `migrations/versions/*v12*` 里的同名实现是**故意的两份**。
    迁移必须自包含（应用层的规则会变，旧迁移不能因此改变行为），
    所以这里不 import 迁移，也不反过来。两边规则一致由
    `tests/test_position_service.py::test_code_rule_matches_the_v12_migration` 钉住。
    """
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", (title or "").strip().lower()).strip("_")
    return slug or "position"


# --------------------------------------------------------------- 定义与编制


def slots_out(db: Session, slots: list[PositionSlot]) -> list[dict]:
    """坑 → API 形状（含派生占用态、定义摘要、在任者）。**整批一次查询**。

    这是坑的唯一序列化出口：`GET /organizations/tree`、`GET /slots/{id}`、开编制、
    空缺列表全部走它。写在这里的理由不是"方便"，而是派生字段必须有唯一算法 ——
    如果组织页自己算一遍 occupancy、详情端点再算一遍，两处就会在 ACTING 这种
    边界上给出不同答案，而那正是这次重构要消灭的"第二个真相"。
    """
    if not slots:
        return []
    slot_ids = [slot.id for slot in slots]
    incumbents = position_repo.incumbents_by_slot(db, slot_ids)
    occupancy = position_repo.occupancy_map(db, slots)
    definition_ids = {slot.position_definition_id for slot in slots}
    definitions = {
        definition.id: definition
        for definition in db.scalars(
            select(PositionDefinition).where(PositionDefinition.id.in_(definition_ids))
        )
    }
    department_ids = {slot.department_id for slot in slots}
    departments = {
        int(department.id): department.name
        for department in db.scalars(select(Department).where(Department.id.in_(department_ids)))
    }
    employee_ids = {eid for ids in incumbents.values() for eid in ids}
    people: dict[int, Employee] = {}
    if employee_ids:
        people = {
            int(employee.id): employee
            for employee in db.scalars(select(Employee).where(Employee.id.in_(employee_ids)))
        }
    return [
        {
            "id": slot.id,
            "company_id": slot.company_id,
            "department_id": slot.department_id,
            "department_name": departments.get(slot.department_id),
            "position_definition_id": slot.position_definition_id,
            # 解析不到定义时宁可给 null：名册/组织页据此显示"未解析"，
            # 而不是编一个名字（`宁缺不错`）。悬空引用由 integrity 诊断单独说明。
            "position_code": (definitions.get(slot.position_definition_id) or None)
            and definitions[slot.position_definition_id].code,
            "position_name": (definitions.get(slot.position_definition_id) or None)
            and definitions[slot.position_definition_id].name,
            "slot_code": slot.slot_code,
            "headcount_index": slot.headcount_index,
            "administrative_status": slot.administrative_status,
            "occupancy_status": occupancy[slot.id].value,
            "manager_slot_id": slot.manager_slot_id,
            "closed_at": slot.closed_at,
            "incumbents": [
                {"id": eid, "name": people[eid].name, "slug": people[eid].slug}
                for eid in incumbents.get(slot.id, [])
                if eid in people
            ],
        }
        for slot in slots
    ]


def slot_out(db: Session, slot_id: int) -> dict:
    slot = _require_slot(db, slot_id)
    return slots_out(db, [slot])[0]


def definitions_out(db: Session, company_id: int | None) -> list[dict]:
    """职位模板列表（Definitions 标签页数据源）。

    三个派生字段（`slot_count` / `vacant_count` / `package_slugs`）必须在这里算：
    schema 上的默认值是 0/空 —— 端点直接返回 ORM 对象时，前端会看到"每个模板
    都没有编制"，而那不是"没有数据"，是**假数据**。所以这个函数是定义列表的
    唯一出口，`list_definitions()`（仓库的原始 ORM）不对外暴露。
    """
    definitions = position_repo.list_definitions(db, company_id)
    if not definitions:
        return []
    definition_ids = [definition.id for definition in definitions]
    counts = position_repo.definition_slot_counts(db, definition_ids)
    slots = position_repo.list_slots(db, company_id)
    occupancy = position_repo.occupancy_map(db, slots)
    vacant_by_definition: dict[int, int] = {}
    for slot in slots:
        if occupancy[slot.id] == OccupancyStatus.vacant:
            vacant_by_definition[slot.position_definition_id] = (
                vacant_by_definition.get(slot.position_definition_id, 0) + 1
            )
    packages = position_repo.definition_packages_map(db, definition_ids)
    result = []
    for definition in definitions:
        payload = {
            column: getattr(definition, column)
            for column in (
                "id",
                "company_id",
                "template_scope",
                "code",
                "name",
                "job_family",
                "level",
                "description",
                "legacy_role",
                "built_in",
            )
        }
        payload["slot_count"] = counts.get(definition.id, 0)
        payload["vacant_count"] = vacant_by_definition.get(definition.id, 0)
        payload["package_slugs"] = packages.get(definition.id, [])
        result.append(payload)
    return result


def definition_out(db: Session, definition_id: int, company_id: int | None) -> dict:
    """单条定义出口：复用 `definitions_out` 的批量算法（派生值必须同源，不允许第二段实现）。

    `POST /definitions` 曾直接返回 ORM 对象 —— ADR-12 去掉 schema 假默认值后当场报错
    （ResponseValidationError: slot_count required）。创建后必须经这里回读再返回。
    """
    for payload in definitions_out(db, company_id):
        if payload["id"] == definition_id:
            return payload
    raise HTTPException(status_code=404, detail="position definition not found")


def assignment_out(db: Session, assignment: PositionAssignment) -> dict:
    """单条任职 → API 形状，并填入派生的 `occupied_slot`。

    判据来自 `workforce.status.row_occupies_establishment()`（全系统唯一一份），
    所以"这条任职占没占编制"与"这个人算不算 ASSIGNED"不会分家。
    """
    payload = {
        column: getattr(assignment, column)
        for column in (
            "id",
            "employee_id",
            "department_id",
            "position_slot_id",
            "position_id",
            "assignment_type",
            "is_primary",
            "employment_status",
            "joined_at",
            "effective_from",
            "effective_to",
            "position_title_snapshot",
            "reason",
        )
    }
    payload["occupied_slot"] = row_occupies_establishment(assignment)
    return payload


def get_slot(db: Session, slot_id: int) -> dict:
    return slot_out(db, slot_id)


def vacant_slots(db: Session, company_id: int | None) -> list[dict]:
    """真实空缺。招聘建议的数据源（`talent-roster.md §2`：AVAILABLE 的人配 VACANT 的坑）。"""
    return slots_out(db, position_repo.vacant_slots(db, company_id))


def freeze_slot(db: Session, slot_id: int, reason: str = "") -> PositionSlot:
    """冻结：不接收新人，也不算空缺（两个轴里各退一步）。"""
    return set_slot_administrative_status(
        db, slot_id, SlotAdminIn(administrative_status="frozen", reason=reason)
    )


def close_slot(db: Session, slot_id: int, reason: str = "") -> PositionSlot:
    """撤销编制（终态）。在任者不被动 —— 关坑与卸任是两个动作，见 §4。"""
    return set_slot_administrative_status(
        db, slot_id, SlotAdminIn(administrative_status="closed", reason=reason)
    )


def activate_slot(db: Session, slot_id: int, reason: str = "") -> PositionSlot:
    return set_slot_administrative_status(
        db, slot_id, SlotAdminIn(administrative_status="active", reason=reason)
    )


def create_definition(
    db: Session, payload: PositionDefinitionIn, company_id: int
) -> PositionDefinition:
    code = payload.code.strip()
    existing = position_repo.get_definition_by_code(db, code, company_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"position code already exists in this company: {code}",
        )
    definition = PositionDefinition(
        company_id=company_id,
        template_scope=TemplateScope.company.value,
        code=code,
        name=payload.name.strip(),
        description=payload.description,
        job_family=payload.job_family,
        level=payload.level,
        responsibilities=list(payload.responsibilities),
        career_path_metadata={},
        built_in=False,
        legacy_role=payload.legacy_role,
    )
    db.add(definition)
    db.flush()
    audit.record(
        db,
        action="position.definition_created",
        employee_id=None,
        before=None,
        after={"id": definition.id, "code": definition.code, "name": definition.name},
        reason="position_service",
    )
    db.commit()
    return definition


def ensure_definition_and_slot_for_position(
    db: Session, position: Position
) -> tuple[PositionDefinition, PositionSlot]:
    """给 v0.4 的 `(department, title)` 头衔补出**定义 + 一个编制**。

    只在两处被调用：启动引导（`seed_lifecycle`）与测试。它不是"自动补人"，
    而是"公司模板与编制的存在性保证" —— 没有它，全新库里根本不存在坑，
    招聘就永远分配不了职位。
    """
    department = db.get(Department, position.department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="position has no department")
    # 刻意在 Python 侧按签名匹配，而不是 SQL 里的 JSON 路径比较：
    # `json_extract(...)=?` 在 SQLite 可用、PG 要换写法，而定义行数很少 —— 为省一次
    # 全表扫描把查询锁死在单一方言上不划算。
    candidates = list(
        db.scalars(
            select(PositionDefinition).where(
                PositionDefinition.company_id == department.company_id,
                PositionDefinition.name == position.title,
            )
        )
    )
    definition = next(
        (
            item
            for item in candidates
            if (item.career_path_metadata or {}).get("__dept_id") == department.id
        ),
        None,
    )
    if definition is None:
        definition = db.scalars(
            select(PositionDefinition).where(
                PositionDefinition.company_id == department.company_id,
                PositionDefinition.code == _code_for(position.title),
            )
        ).first()
    if definition is None:
        definition = PositionDefinition(
            company_id=department.company_id,
            template_scope=TemplateScope.company.value,
            code=_code_for(position.title),
            name=position.title,
            description="",
            job_family=department.slug,
            level=1,
            responsibilities=[{"title": position.title}] if position.title else [],
            career_path_metadata={"__backfill": "runtime", "__dept_id": department.id},
            built_in=True,
            legacy_role=None,
        )
        db.add(definition)
        db.flush()
    slot = position_repo.slot_for_legacy_position(db, position.id)
    if slot is None:
        index = position_repo.next_headcount_index(db, department.id, definition.id)
        slot = PositionSlot(
            company_id=department.company_id,
            department_id=department.id,
            position_definition_id=definition.id,
            slot_code=f"{department.slug.upper()}-{definition.code.upper()}-{index}"[:100],
            headcount_index=index,
            administrative_status=SlotAdministrativeStatus.active.value,
            metadata_json={"__backfill": "runtime", "__position_id": position.id},
        )
        db.add(slot)
        db.flush()
    return definition, slot


def open_slots(
    db: Session, definition_id: int, payload: SlotIn, company_id: int
) -> list[PositionSlot]:
    definition = position_repo.get_definition(db, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="position definition not found")
    if definition.company_id not in (None, company_id):
        raise HTTPException(status_code=404, detail="position definition not in this company")
    department = db.get(Department, payload.department_id)
    if department is None or department.company_id != company_id:
        raise HTTPException(status_code=404, detail="department not found in this company")
    if payload.manager_slot_id is not None:
        _require_slot(db, payload.manager_slot_id)  # 上级坑必须存在
    created: list[PositionSlot] = []
    for _ in range(payload.count):
        index = position_repo.next_headcount_index(db, department.id, definition.id)
        slot = PositionSlot(
            company_id=company_id,
            department_id=department.id,
            position_definition_id=definition.id,
            slot_code=f"{department.slug.upper()}-{definition.code.upper()}-{index}"[:100],
            headcount_index=index,
            administrative_status=SlotAdministrativeStatus.planned.value,
            manager_slot_id=payload.manager_slot_id,
            metadata_json={"note": payload.note} if payload.note else {},
        )
        db.add(slot)
        # 必须逐轮 flush：`next_headcount_index()` 读的是库里的 MAX()，
        # 未落盘的上一轮对它不可见 —— 一次开 3 个就会全部拿到同一个序号，
        # 然后撞 uq_position_slot_index（这个 bug 是由批量开坑的用例抓出来的）。
        db.flush()
        created.append(slot)
    db.flush()
    for slot in created:
        audit.record(
            db,
            action="position.slot_opened",
            employee_id=None,
            before=None,
            after={"id": slot.id, "code": slot.slot_code, "status": slot.administrative_status},
            reason=payload.note,
        )
    db.commit()
    return created


def set_slot_administrative_status(db: Session, slot_id: int, payload: SlotAdminIn) -> PositionSlot:
    """**行政轴的唯一点写入口**。占用态不在这里、也不能在任何地方被写（ADR-2）。"""
    target = payload.administrative_status
    if target not in _ADMIN_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"administrative_status must be one of {sorted(_ADMIN_STATUSES)}; "
                "VACANT/OCCUPIED 是派生态，不能作为入参"
            ),
        )
    slot = _require_slot(db, slot_id)
    if target == slot.administrative_status:
        return slot
    if target not in _ALLOWED_ADMIN_TRANSITIONS.get(slot.administrative_status, set()):
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot move a slot from {slot.administrative_status} to {target}"
                "（CLOSED 是终态，重开请显式新建编制）"
            ),
        )
    before = {"id": slot.id, "administrative_status": slot.administrative_status}
    slot.administrative_status = target
    if target == SlotAdministrativeStatus.closed.value:
        slot.closed_at = utcnow()
    db.flush()
    audit.record(
        db,
        action="position.slot_status_changed",
        employee_id=None,
        before=before,
        after={"id": slot.id, "administrative_status": target},
        reason=payload.reason,
    )
    db.commit()
    return slot


# ------------------------------------------------------------------ 任职工作流


def _target_slot(db: Session, payload: AssignmentIn) -> PositionSlot:
    if payload.slot_id is not None:
        return _require_slot(db, payload.slot_id)
    if payload.position_id is not None:
        # v0.4 兼容：只按 v13 的标记找坑，找不到就是没有编制（不新建、不反推 title）
        slot = position_repo.slot_for_legacy_position(db, payload.position_id)
        if slot is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"legacy position #{payload.position_id} 没有对应编制（PositionSlot）；"
                    "请先在组织页开设编制"
                ),
            )
        return slot
    raise HTTPException(status_code=422, detail="slot_id 或 position_id 必须给一个")


def assign_position(
    db: Session,
    employee: Employee,
    payload: AssignmentIn,
    *,
    commit: bool = True,
) -> PositionAssignment:
    """显式分配：关旧主职 + 开新主职，全程一个事务。

    `commit=False`（T2.6 招募事务用）：不 commit、也不发 `employee.position_assigned` ——
    调用方持有事务边界，并在自己的 commit 之后补发该事件（`assess_person_competencies`
    的同一约定）。默认 True 时行为与本函数历史完全一致。
    """
    slot = _target_slot(db, payload)
    definition = position_repo.get_definition(db, slot.position_definition_id)
    if definition is None:
        # 无外键的代价：坑在、定义不在 —— 这不是"没有职位"，这是数据坏了，必须报出来
        raise HTTPException(
            status_code=409,
            detail=f"slot #{slot.id} 的职位定义缺失，需要人工修复（见 /roster/integrity）",
        )
    if slot.administrative_status not in _FILLABLE:
        raise HTTPException(
            status_code=409,
            detail=f"坑 {slot.slot_code} 处于 {slot.administrative_status}，不能被填",
        )

    incumbents = position_repo.slot_incumbents(db, slot.id)
    holding = [item for item in incumbents if item.employee_id != employee.id]
    if holding:
        raise HTTPException(
            status_code=409,
            detail=f"坑 {slot.slot_code} 已有在任者（{len(holding)} 人），请先解任或另开编制",
        )
    already = next((item for item in incumbents if item.employee_id == employee.id), None)
    if already is not None:
        return already  # 幂等：重复点"分配"不该制造第二条主职

    kind = payload.kind if payload.kind in {"assign", "transfer"} else "assign"
    effective_from = payload.effective_from or utcnow()
    previous = [
        assignment
        for assignment in position_repo.active_assignments(db, int(employee.id))
        if assignment.assignment_type == AssignmentType.primary.value and assignment.is_primary
    ]
    # joined_at 跨任职延续（v0.4 语义：转岗不改入职日）。取"最早创建的那一行"的值
    # 用 id 单调序，而不是对 datetime 取 min：DateTime 列没有时区，从库里读回来是
    # naive，而 utcnow() 是 aware —— 两者一比就 TypeError（seed 在启动时就会炸）。
    earliest = min((item.id for item in previous), default=None)
    joined_at = effective_from
    for item in previous:
        if item.id == earliest and item.joined_at is not None:
            joined_at = item.joined_at
            break
    else:
        if employee.created_at is not None:
            joined_at = employee.created_at
    for old in previous:
        old.effective_to = effective_from
        # v0.4 的 transfer 语义保留 "transferred" 这个词（历史 API 契约在读它）；
        # 纯分配路径没有"转岗"含义，用 superseded 更准确。
        old.employment_status = "transferred" if kind == "transfer" else "superseded"
        old.metadata_json = {
            **dict(old.metadata_json or {}),
            "superseded_by_reason": payload.reason,
            "superseded_on": kind,
        }

    # 显式请求的上级优先（v0.4 向导会带 manager_employee_id）；没给才从组织线推导。
    manager_employee_id = payload.manager_employee_id or _manager_of_slot(db, slot)
    assignment = PositionAssignment(
        employee_id=int(employee.id),
        department_id=slot.department_id,
        position_id=position_repo.legacy_position_id_of_slot(slot),  # 兼容镜像，可为 None
        manager_employee_id=manager_employee_id,
        employment_status="active",
        joined_at=joined_at,
        effective_from=effective_from,
        effective_to=None,
        position_slot_id=slot.id,
        assignment_type=AssignmentType.primary.value,
        is_primary=True,
        assigned_by=payload.assigned_by,
        reason=payload.reason,
        position_title_snapshot=definition.name,
        metadata_json={"kind": kind, "via": "position_service"},
    )
    db.add(assignment)
    # 人随编制走：坑在哪个部门，人就属于哪个部门。否则会出现"人在 QA、
    # 坐在 Engineering 的坑上"这种没法解释的组合。
    employee.department_id = slot.department_id
    db.flush()

    audit.record(
        db,
        action="employee.position_assigned",
        employee_id=employee.id,
        before={"assignments": [item.id for item in previous]},
        after={
            "assignment_id": assignment.id,
            "slot_id": slot.id,
            "slot_code": slot.slot_code,
            "position_code": definition.code,
            "department_id": slot.department_id,
        },
        reason=payload.reason,
    )
    if commit:
        db.commit()
        bus.publish(
            "employee.position_assigned",
            {
                "id": employee.id,
                "slot_id": slot.id,
                "position_code": definition.code,
                "department_id": slot.department_id,
                "kind": kind,
            },
            company_id=employee.company_id,
            actor_employee_id=employee.id,
        )
    # P4d：Desired Access 由这个事件驱动重算，不在本事务里同步；
    # commit=False 时由调用方在自己的 commit 之后补发。
    return assignment


def release_position(
    db: Session, employee: Employee, *, reason: str = ""
) -> list[PositionAssignment]:
    """解任：关掉生效主职，人不删、资源不删，状态派生成 `AVAILABLE`。

    返回被关掉的行（正常恰好一条；历史重叠时可能多条）。只回计数的话，调用方无从
    区分"关了"与"根本没有主职可关"，而卸任端点的响应体正需要这个区别。
    """
    closed: list[PositionAssignment] = []
    for assignment in position_repo.active_assignments(db, int(employee.id)):
        if assignment.assignment_type != AssignmentType.primary.value or not assignment.is_primary:
            continue
        assignment.effective_to = utcnow()
        assignment.employment_status = "released"
        assignment.metadata_json = {
            **dict(assignment.metadata_json or {}),
            "released_reason": reason,
        }
        closed.append(assignment)
    if not closed:
        return []
    db.flush()
    audit.record(
        db,
        action="employee.position_released",
        employee_id=employee.id,
        before=None,
        after={"closed": [item.id for item in closed]},
        reason=reason,
    )
    db.commit()
    bus.publish(
        "employee.position_released",
        {"id": employee.id, "closed": [item.id for item in closed]},
        company_id=employee.company_id,
        actor_employee_id=employee.id,
    )
    return closed


def _manager_of_slot(db: Session, slot: PositionSlot) -> int | None:
    """组织线的旧镜像：从上级坑的在任者取 `manager_employee_id`。

    这是**镜像**不是真相 —— 真相是 `slot.manager_slot_id`；写它是为了让 v0.4 的
    汇报关系视图在 P4d 之前不破。
    """
    if slot.manager_slot_id is None:
        return None
    incumbents = position_repo.slot_incumbents(db, int(slot.manager_slot_id))
    return int(incumbents[0].employee_id) if incumbents else None


# ------------------------------------------------------------- 只读组合层


def roster(db: Session, company_id: int, status: str | None = None) -> list[dict]:
    """人才名册：人 + 派生状态 + 当前职位 + 完整性标记（一次批量，不 N+1）。"""
    people = org_repo.list_employees(db, company_id)
    resolver = WorkforceStatusResolver(db)
    views = resolver.views(people)
    # 批量：一页 50 人只走"主职一次 + 坑一次 + 定义一次"，不是 50×3 次
    positions = position_repo.current_positions_by_employee(db, [int(p.id) for p in people])
    issues = integrity_by_employee(db)
    department_names = {
        department.id: department.name
        for department in db.scalars(select(Department).where(Department.company_id == company_id))
    }
    entries: list[dict] = []
    for person in people:
        view = views[int(person.id)]
        if status is not None and view.workforce_status.value != status:
            continue
        current = positions.get(int(person.id))
        entries.append(
            {
                "employee_id": int(person.id),
                "name": person.name,
                "slug": person.slug,
                "avatar": person.avatar or "",
                "department_id": person.department_id,
                "department_name": department_names.get(person.department_id or 0),
                "lifecycle_status": person.lifecycle_status,
                "workforce_status": view.workforce_status.value,
                "has_primary_assignment": view.has_primary_assignment,
                "occupies_establishment": view.occupies_establishment,
                "current_position": _position_payload(current),
                "integrity": issues.get(int(person.id), []),
            }
        )
    return entries


def roster_stats(db: Session, company_id: int) -> dict:
    people = org_repo.list_employees(db, company_id)
    counts = WorkforceStatusResolver(db).counts(people)
    slots = position_repo.list_slots(db, company_id)
    occupancy = position_repo.occupancy_map(db, slots)
    by_status = {key: value for key, value in counts.items() if key != "on_roster"}
    return {
        "total": len(people),
        "on_roster": counts.get("on_roster", 0),
        "by_status": by_status,
        "slots_total": len(slots),
        "slots_vacant": sum(1 for value in occupancy.values() if value.value == "vacant"),
    }


def organization(db: Session, company_id: int) -> dict:
    """组织树：部门 → 编制 → 在任者。两个状态字段并列返回（ADR-2 的读面）。"""
    departments = list(
        db.scalars(
            select(Department).where(Department.company_id == company_id).order_by(Department.id)
        )
    )
    slots = position_repo.list_slots(db, company_id)
    serialized = slots_out(db, slots)
    by_department: dict[int, list[dict]] = {}
    for slot, payload in zip(slots, serialized, strict=True):
        by_department.setdefault(slot.department_id, []).append(payload)
    return {
        "company_id": company_id,
        "departments": [
            {
                "id": department.id,
                "name": department.name,
                "slug": department.slug,
                "slots": by_department.get(department.id, []),
            }
            for department in departments
        ],
    }


def integrity_by_employee(db: Session) -> dict[int, list[str]]:
    """只读诊断（员工 → 问题种类）。用于名册区分"正常待分配"与"历史悬空引用"。"""
    report = integrity(db)
    grouped: dict[int, list[str]] = {}
    for item in report["items"]:
        grouped.setdefault(item.employee_id, []).append(item.kind)
    return grouped


def integrity(db: Session, company_id: int | None = None) -> dict:
    """`AssignmentIntegrity` 诊断：报告问题，**不修问题**，也不新增状态列。

    种类含义：
      - `NO_SLOT_HISTORICAL`：v0.4 遗留的无坑主职（P4b 之前写入的）。名册上这些人
        仍是 `AVAILABLE`，因为任职轴要求"占住编制"。
      - `DANGLING_SLOT` / `MISSING_DEFINITION`：引用悬空（无 DB 外键的代价）⇒ 需要人工修。
      - `SLOT_DEPARTMENT_MISMATCH`：任职行的部门与坑的部门不一致。
      - `MULTIPLE_ACTIVE_PRIMARY`：同一员工多条生效主职（唯一索引之后不该出现，留着兜底）。
    """
    assignments = list(
        db.scalars(
            select(PositionAssignment)
            .where(
                PositionAssignment.effective_to.is_(None),
                PositionAssignment.assignment_type == AssignmentType.primary.value,
            )
            .order_by(PositionAssignment.employee_id, PositionAssignment.id)
        )
    )
    slots: dict[int, PositionSlot | None] = {}
    definitions: dict[int, PositionDefinition | None] = {}
    items: list[IntegrityItemOut] = []
    per_employee: dict[int, int] = {}

    for assignment in assignments:
        employee = db.get(Employee, assignment.employee_id)
        if company_id is not None and (employee is None or employee.company_id != company_id):
            continue
        slug = employee.slug if employee is not None else None
        per_employee[assignment.employee_id] = per_employee.get(assignment.employee_id, 0) + 1
        if assignment.position_slot_id is None:
            items.append(
                IntegrityItemOut(
                    assignment_id=assignment.id,
                    employee_id=assignment.employee_id,
                    employee_slug=slug,
                    kind="NO_SLOT_HISTORICAL",
                    detail="v0.4 遗留：写任职时没有关联职位（P4b 起不再产生）",
                )
            )
            continue
        slot_id = int(assignment.position_slot_id)
        if slot_id not in slots:
            slots[slot_id] = position_repo.get_slot(db, slot_id)
        slot = slots[slot_id]
        if slot is None:
            items.append(
                IntegrityItemOut(
                    assignment_id=assignment.id,
                    employee_id=assignment.employee_id,
                    employee_slug=slug,
                    kind="DANGLING_SLOT",
                    detail=f"position_slot_id={slot_id} 不存在",
                )
            )
            continue
        definition_id = int(slot.position_definition_id)
        if definition_id not in definitions:
            definitions[definition_id] = position_repo.get_definition(db, definition_id)
        if definitions[definition_id] is None:
            items.append(
                IntegrityItemOut(
                    assignment_id=assignment.id,
                    employee_id=assignment.employee_id,
                    employee_slug=slug,
                    kind="MISSING_DEFINITION",
                    detail=f"坑 {slot.slot_code} 的职位定义 #{definition_id} 缺失",
                )
            )
            continue
        if assignment.department_id is not None and assignment.department_id != slot.department_id:
            items.append(
                IntegrityItemOut(
                    assignment_id=assignment.id,
                    employee_id=assignment.employee_id,
                    employee_slug=slug,
                    kind="SLOT_DEPARTMENT_MISMATCH",
                    detail=f"任职部门 {assignment.department_id} ≠ 坑部门 {slot.department_id}",
                )
            )
    for employee_id, count in per_employee.items():
        if count > 1:
            items.append(
                IntegrityItemOut(
                    assignment_id=0,
                    employee_id=employee_id,
                    employee_slug=None,
                    kind="MULTIPLE_ACTIVE_PRIMARY",
                    detail=f"{count} 条生效主职（唯一索引之后不应出现，需要人工核对）",
                )
            )
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return {"read_only": True, "counts": counts, "items": items}


def _position_payload(current) -> dict | None:
    if current is None:
        return None
    return {
        "definition_id": current.definition_id,
        "code": current.code,
        "name": current.name,
        "level": current.level,
        "job_family": current.job_family,
        "legacy_role": current.legacy_role,
        "department_id": current.department_id,
        "department_name": current.department_name,
        "slot_id": current.slot_id,
        "slot_code": current.slot_code,
        "since": current.since,
        "assignment_type": current.assignment_type,
        "position_is_custom": current.is_custom,
    }


def current_employment_rows(db: Session, employee_id: int) -> list[PositionAssignment]:
    """兼容 v0.4 的 `/employees/{id}/employment`：返回全履历（含已关闭行）。"""
    return position_repo.career_history(db, employee_id)


def current_employment(db: Session, employee_id: int) -> PositionAssignment | None:
    return position_repo.active_primary_assignment(db, employee_id)


__all__ = [
    "assign_position",
    "current_employment",
    "current_employment_rows",
    "ensure_definition_and_slot_for_position",
    "integrity",
    "open_slots",
    "organization",
    "release_position",
    "assignment_out",
    "definitions_out",
    "slot_out",
    "slots_out",
    "vacant_slots",
    "get_slot",
    "freeze_slot",
    "close_slot",
    "activate_slot",
    "integrity_by_employee",
    "roster",
    "roster_stats",
    "set_slot_administrative_status",
    "create_definition",
]
