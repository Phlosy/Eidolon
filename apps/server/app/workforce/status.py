"""Workforce status —— "这个人现在在组织里处于什么状态"的**唯一派生入口**（ADR-4）。

两个正交轴：

```text
Lifecycle Axis   employees.lifecycle_status      （人级：入册 / 就绪 / 停用 / 离职）
Assignment Axis  employments（PositionAssignment） （职位级：现在有没有主职）
                              │
                              ▼
                 derive_workforce_status()  →  WorkforceStatus（**不入库**）
```

三条纪律，都是这次重构的立足点：

1. **不存列。** 存了就有第二个真相，而且必然与时间轴漂移。名册要按状态筛选时，
   先用这个 resolver 出数；真到了需要物化读模型的量级，写入口也仍然只能在这里。
2. **不许各处再推一遍。** API / service / 前端拿到的是本模块的输出。
   前端自己写 `status === 'active' && !position ? 'AVAILABLE' : ...` 就是漂移起点。
3. **`AVAILABLE` 是常态，不是缺陷。** 已入册、人级资源就绪、暂无主职 —— 这次迁移
   之后 dev 库就有 11 个这样的人。任何代码都不许因为"没有职位"把这种人排除在
   工作循环之外（`tests/test_workforce_status.py` 有这条断言）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.enums import AssignmentType, LifecycleStatus, WorkforceStatus
from app.models.organization import Employee
from app.models.position import PositionAssignment
from app.repositories import position as position_repo

_ONBOARDING_LIFECYCLE = (LifecycleStatus.pending.value, LifecycleStatus.onboarding.value)
#: 直接由 lifecycle 轴决定的状态（先判这些，因为它们与有没有职位无关）
_LIFECYCLE_SHORTCUTS = {
    LifecycleStatus.offboarded.value: WorkforceStatus.offboarded,
    LifecycleStatus.offboarding.value: WorkforceStatus.offboarding,
    LifecycleStatus.suspended.value: WorkforceStatus.suspended,
    # TRANSFERRING 只从 lifecycle 轴来。拍板理由：用"两条 assignment 同时存在"反推
    # 转岗中会被 effective date 交叠、代理任职（acting）等形态误判 —— 那类信息属于
    # 转岗工作流的上下文，不是任职时间轴能独立表达的事实。
    LifecycleStatus.transferring.value: WorkforceStatus.transferring,
}


def has_active_primary(active_assignments: Sequence[PositionAssignment]) -> bool:
    """任职轴的低层判据：存在生效中的 PRIMARY（不看它有没有坑）。"""
    return any(
        assignment.assignment_type == AssignmentType.primary.value
        and assignment.is_primary
        and assignment.effective_to is None
        for assignment in active_assignments
    )


def occupies_establishment(active_assignments: Sequence[PositionAssignment]) -> bool:
    """任职轴的业务判据：存在一个**占住编制**的生效主职。

    为什么要多一步 `position_slot_id is not None`：迁移后有 11 条生效主职没有坑
    （v0.4 写任职时从来不填 `position_id`）。它们代表"有任职记录、没有编制"，
    而这正是 `AVAILABLE` 的定义。把这种人报成 `ASSIGNED` 等于凭空造一个不存在的职位，
    也直接违背本次迁移"宁缺不错"的拍板。
    """
    return has_active_primary(active_assignments) and any(
        assignment.position_slot_id is not None for assignment in active_assignments
    )


def derive_workforce_status(
    lifecycle_status: str | None,
    active_assignments: Sequence[PositionAssignment],
) -> WorkforceStatus:
    """纯函数规则表（docs/talent-roster.md §2）。顺序即优先级，不要重排。

    关于"人级资源是否就绪"：这里**只信 lifecycle_status**。入册与 provisioning
    完成与否已经由它表达（`pending` / `onboarding` → `active` 正是 job 收尾时翻转的），
    再读一次 runtime 会让同一件事有两个判据 —— 两个判据不一致时没人能解释状态。
    """
    status = lifecycle_status or LifecycleStatus.pending.value
    if status in _LIFECYCLE_SHORTCUTS:
        return _LIFECYCLE_SHORTCUTS[status]
    if status == LifecycleStatus.active.value:
        # 只有"人已就绪"这一个事实成立之后，任职轴才有资格决定 assigned / available。
        return (
            WorkforceStatus.assigned
            if occupies_establishment(active_assignments)
            else WorkforceStatus.available
        )
    if status in _ONBOARDING_LIFECYCLE:
        # 入职未完成时不报 AVAILABLE：那个人还没进来。
        return WorkforceStatus.onboarding
    # 未知 lifecycle 值（枚举漂移、脏数据、别的系统写进来的）：退到最保守的在途态。
    # 刻意不"照着任职轴给个看起来合理的值" —— 状态解释器猜一次，数据漂移就永久隐身。
    return WorkforceStatus.onboarding


@dataclass(frozen=True)
class WorkforceView:
    """名册一行所需的状态事实（都是派生值，库里没有它们的列）。"""

    employee_id: int
    workforce_status: WorkforceStatus
    #: 存在一张生效 PRIMARY 任职行 —— 包括 v0.4 遗留的"没有坑"的那些
    has_primary_assignment: bool
    #: 该主职确实解析得到坑与定义 ⇒ 真正占住编制。迁移后 dev 库有 11 人
    #: has_primary_assignment=True 而 occupies_establishment=False。
    occupies_establishment: bool
    position_slot_id: int | None
    definition_code: str | None
    definition_name: str | None

    def as_dict(self) -> dict:
        return {
            "workforce_status": self.workforce_status.value,
            "has_primary_assignment": self.has_primary_assignment,
            "occupies_establishment": self.occupies_establishment,
            "position_slot_id": self.position_slot_id,
            "position_code": self.definition_code,
            "position_name": self.definition_name,
        }


class WorkforceStatusResolver:
    """唯一派生入口。构造时给一个 `Session`；批量方法保证常数次查询。

    坑→定义的解析结果在本实例内缓存（`_position_of_slot`），这样
    `resolve()`（详情页，逐个）与 `views()`（名册，批量）走的是**同一份解析**，
    不会一个把悬空坑当成有职位、另一个不当 —— 两处结论不一致是最难查的状态 bug。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._slot_positions: dict[int, tuple[int, str, str] | None] = {}

    # ---- 内部：把任职轴压成"是否占住编制"这一个事实 ----

    def _position_of_slot(self, slot_id: int | None) -> tuple[int, str, str] | None:
        """slot_id → (definition_id, code, name)；解析不到就是 None（不猜）。"""
        if slot_id is None:
            return None
        if slot_id not in self._slot_positions:
            slot = position_repo.get_slot(self.db, int(slot_id))
            definition = (
                position_repo.get_definition(self.db, slot.position_definition_id)
                if slot is not None
                else None
            )
            self._slot_positions[slot_id] = (
                None if definition is None else (definition.id, definition.code, definition.name)
            )
        return self._slot_positions[slot_id]

    def _assignable(self, assignments: Sequence[PositionAssignment]) -> list[PositionAssignment]:
        """只保留"坑与定义都能解析"的生效主职。

        悬空 `position_slot_id`（没有 DB 外键的代价）在这里被剔除：宁报 AVAILABLE，
        也不给一个人配一个不存在的职位。
        """
        return [
            assignment
            for assignment in assignments
            if self._position_of_slot(assignment.position_slot_id) is not None
        ]

    def _status(self, employee: Employee, assignments: Sequence[PositionAssignment]):
        return derive_workforce_status(employee.lifecycle_status, self._assignable(assignments))

    # ---- 对外 ----

    def resolve(self, employee: Employee) -> WorkforceStatus:
        return self._status(employee, position_repo.active_assignments(self.db, int(employee.id)))

    def view(self, employee: Employee) -> WorkforceView:
        return self._build_view(
            employee, position_repo.active_assignments(self.db, int(employee.id))
        )

    def views(self, employees: Sequence[Employee]) -> dict[int, WorkforceView]:
        """名册批量：1 次取全量生效主职 + 每个坑至多 1 次定义解析（实例内缓存）。"""
        ids = [int(employee.id) for employee in employees]
        primaries = position_repo.active_primaries_by_employee(self.db, ids)
        result: dict[int, WorkforceView] = {}
        for employee in employees:
            primary = primaries.get(int(employee.id))
            # 传原始行，过滤留在 _build_view 里 —— 与 resolve() 走同一条路径，
            # 否则"详情页显示已分配、名册显示待分配"这类不一致极难定位。
            result[int(employee.id)] = self._build_view(
                employee, [] if primary is None else [primary]
            )
        return result

    def counts(self, employees: Sequence[Employee]) -> dict[str, int]:
        """`{"available": 11, "assigned": 5, ..., "on_roster": 16}`。

        口径提示：`available` 是"已就绪但没有编制"，它是 `on_roster` 的子集而不是兄弟。
        把 `available + assigned` 当在岗人数会漏掉 transferring，所以这里直接给
        `on_roster`，名册顶部读它，避免每个前端自己合成一遍。
        """
        views = self.views(employees)
        buckets: dict[str, int] = {}
        for view in views.values():
            buckets[view.workforce_status.value] = buckets.get(view.workforce_status.value, 0) + 1
        buckets["on_roster"] = sum(
            1
            for view in views.values()
            if view.workforce_status
            in {
                WorkforceStatus.assigned,
                WorkforceStatus.available,
                WorkforceStatus.transferring,
            }
        )
        return buckets

    # ---- 构造视图 ----

    def _build_view(
        self, employee: Employee, assignments: Sequence[PositionAssignment]
    ) -> WorkforceView:
        raw_primary = next(
            (
                assignment
                for assignment in assignments
                if assignment.assignment_type == AssignmentType.primary.value
                and assignment.is_primary
                and assignment.effective_to is None
            ),
            None,
        )
        positioned = self._assignable(assignments)
        primary = next(
            (assignment for assignment in positioned if assignment.position_slot_id is not None),
            None,
        )
        info = self._position_of_slot(primary.position_slot_id) if primary else None
        return WorkforceView(
            employee_id=int(employee.id),
            workforce_status=derive_workforce_status(employee.lifecycle_status, positioned),
            has_primary_assignment=raw_primary is not None,
            occupies_establishment=primary is not None,
            position_slot_id=primary.position_slot_id if primary else None,
            definition_code=info[1] if info else None,
            definition_name=info[2] if info else None,
        )
