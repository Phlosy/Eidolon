"""M2.5 **Canonical Task Graph Runtime**：就绪判定 + 可派发判定 + 例外上报。

用户拍板的执行语义（设计 §14d，R1–R12）：

```text
Manager chooses.   ← 建哪些 Task、依赖、**谁负责**、是否改派/取消/重规划
System schedules.  ← 依赖是否满足、是否就绪、能不能派、何时派
Worker executes.   ← WorkSession → Runtime
```

这个模块是"System schedules"的**唯一实现**，两条边界写死在代码里：

1. **结构就绪**用的是契约里的纯函数 `resolve_ready_tasks`（依赖全部完成）。
   它**不**承担人员选择职责（R3）。
2. **可派发**只回答两件事之一：**派给已存在的负责人**，或**交给管理决策**
   （`assignee_missing` / `assignee_inactive` / `runtime_unavailable` …）。
   **没有第三条分支** —— 系统永远不会"自己挑一个负责人"（R1/R2/R5/R12）。

因此"负责人暂时不可用"与"任务没设负责人"都**不是**自动补救的触发条件，
而是**上报**的触发条件：发 Decision-needed 事件，等被授权的管理 Agent 决定
等 / 改派 / 修运行时 / 重规划。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.enums import (
    EmployeeStatus,
    LifecycleStatus,
    ProjectStatus,
    RuntimeType,
    TaskStatus,
)
from app.models.organization import Employee
from app.models.project import Task
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.work import contracts as C

#: 项目处于这些状态时允许执行（终态与"等管理层"不在此列）
EXECUTABLE_PROJECT_STATUSES: frozenset[str] = frozenset(
    {
        ProjectStatus.requested.value,
        ProjectStatus.planning.value,
        ProjectStatus.in_progress.value,
        ProjectStatus.in_review.value,
    }
)

#: 派发阻断原因（与契约里的两类对齐；契约测试钉住完备性）
REASON_NOT_READY = "not_ready"
REASON_PROJECT_NOT_EXECUTABLE = "project_not_executable"
REASON_TASK_HELD = "task_held"
REASON_ASSIGNEE_MISSING = "assignee_missing"
REASON_ASSIGNEE_INACTIVE = "assignee_inactive"
REASON_RUNTIME_UNAVAILABLE = "runtime_unavailable"
REASON_PROVIDER_MISSING = "provider_missing"
#: 执行失败：**结构上**是就绪候选（契约如此），但**运行上**需要管理决策（R10/R12）
REASON_TASK_FAILED = "task_failed"

#: 每个阻断原因对应的事件（事实 vs 需要管理决策，见契约 §10b）
REASON_EVENTS: dict[str, str] = {
    REASON_ASSIGNEE_MISSING: "task.assignment_required",
    REASON_ASSIGNEE_INACTIVE: "task.runtime_unavailable",
    REASON_RUNTIME_UNAVAILABLE: "task.runtime_unavailable",
    REASON_PROVIDER_MISSING: "task.runtime_unavailable",
    REASON_TASK_HELD: "task.blocked",
    REASON_TASK_FAILED: "task.failed",
}

assert set(REASON_EVENTS) == set(C.REQUIRES_MANAGEMENT_DECISION | {REASON_TASK_HELD}), (
    "每个阻断原因都必须有事件归属（含 blocked → task.blocked）"
)


@dataclass(frozen=True)
class DispatchEvaluation:
    """一个 Task 的派发结论（**只有两种正当结果**：能派 / 需要管理决策）。"""

    task_id: int
    dispatchable: bool
    reasons: tuple[str, ...] = ()
    #: 暂时排不上（负责人正忙）：不是异常，也不需要管理决策，等就行
    queued: bool = False
    assignee_id: int | None = None

    @property
    def needs_management(self) -> bool:
        return any(reason in C.REQUIRES_MANAGEMENT_DECISION for reason in self.reasons)

    @property
    def event_type(self) -> str | None:
        """第一个需要上报的原因对应的事件（None = 不需要上报）。"""
        for reason in self.reasons:
            event = REASON_EVENTS.get(reason)
            if event is not None:
                return event
        return None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "dispatchable": self.dispatchable,
            "queued": self.queued,
            "assignee_id": self.assignee_id,
            "reasons": list(self.reasons),
            "needs_management": self.needs_management,
        }


# ---------------------------------------------------------------------------
# 结构就绪（唯一口径 = 契约的纯函数）
# ---------------------------------------------------------------------------


def graph_nodes(db: Session, project_id: int) -> tuple[C.TaskGraphNode, ...]:
    """把项目的任务图读成契约节点（依赖从**表**读，不用 ORM 关系缓存）。"""
    deps: dict[int, list[int]] = {}
    for row in project_repo.list_dependencies(db, int(project_id)):
        deps.setdefault(int(row.task_id), []).append(int(row.depends_on_id))
    return tuple(
        C.TaskGraphNode(
            task_id=int(task.id),
            status=task.status,
            depends_on=tuple(sorted(deps.get(int(task.id), ()))),
        )
        for task in project_repo.list_tasks(db, int(project_id))
    )


def structural_ready_task_ids(db: Session, project_id: int) -> tuple[int, ...]:
    """结构就绪集合（依赖全部完成且自身还没开始）。"""
    return C.resolve_ready_tasks(graph_nodes(db, project_id))


def ready_tasks(db: Session, project_id: int) -> list[Task]:
    ids = set(structural_ready_task_ids(db, project_id))
    return [task for task in project_repo.list_tasks(db, int(project_id)) if int(task.id) in ids]


# ---------------------------------------------------------------------------
# 可派发判定
# ---------------------------------------------------------------------------


def _runtime_block_reasons(db: Session, employee: Employee) -> list[str]:
    """运行时/模型绑定是否可用。

    **mock 模式**：不需要 provider 绑定（MockAdapter 自身就是可执行的替身）。
    **真实 runtime**：必须有相应的 runtime instance **且**绑定了模型 ——
    两者缺一都归到 `runtime_unavailable` / `provider_missing`，由管理层决定怎么修。
    """
    runtime_type = str(employee.runtime_type or RuntimeType.mock.value)
    if runtime_type == RuntimeType.mock.value:
        return []
    instance = runtime_repo.get_instance_for_employee(db, int(employee.id))
    if instance is None:
        return [REASON_RUNTIME_UNAVAILABLE]
    if str(instance.status) in {"crashed", "error", "unhealthy", "stopped", "deleted"}:
        return [REASON_RUNTIME_UNAVAILABLE]
    if instance.model_binding_id is None:
        return [REASON_PROVIDER_MISSING]
    return []


def evaluate_dispatch(db: Session, task: Task) -> DispatchEvaluation:
    """一个 Task 现在能不能派给**它自己的**负责人（R1/R3）。

    判定顺序（先结构、再项目、再人、最后运行时）：
    依赖 → 状态 → 项目 → 负责人存在 → 负责人可用 → 运行时/绑定 → 负责人忙不忙。

    最后一个（忙）**不是**阻断：它表示"排着"，编排器下一轮再看。
    """
    reasons: list[str] = []
    assignee_id = task.assignee_id
    status = str(task.status)

    # ① 结构就绪：依赖必须全部完成
    project_id = int(task.project_id)
    deps: dict[int, list[int]] = {}
    for row in project_repo.list_dependencies(db, project_id):
        deps.setdefault(int(row.task_id), []).append(int(row.depends_on_id))
    done = {
        int(row.id)
        for row in project_repo.list_tasks(db, project_id)
        if str(row.status) == TaskStatus.done.value
    }
    if not all(dep in done for dep in deps.get(int(task.id), ())):
        reasons.append(REASON_NOT_READY)

    # ② 任务自身状态：只有 backlog / todo 可以进入执行
    if status == TaskStatus.blocked.value:
        reasons.append(REASON_TASK_HELD)
    if status == TaskStatus.failed.value:
        # 一次失败就是一次**需要判断**的事实：重做 / 改派 / 改方案由管理层选（R10）。
        # 系统既不自作主张重跑（会无限重试），也不把别人的活挪过来（R5）。
        reasons.append(REASON_TASK_FAILED)
    if status not in C.READY_CANDIDATE_STATUSES:
        if REASON_NOT_READY not in reasons and REASON_TASK_HELD not in reasons:
            reasons.append(REASON_NOT_READY)

    # ③ 项目是否处于可执行状态
    project = project_repo.get_project(db, project_id)
    if project is None or str(project.status) not in EXECUTABLE_PROJECT_STATUSES:
        reasons.append(REASON_PROJECT_NOT_EXECUTABLE)

    # ④ 负责人：**只认已存在的指派**，绝不代替管理层选择（R2）
    if assignee_id is None:
        reasons.append(REASON_ASSIGNEE_MISSING)
        return DispatchEvaluation(
            task_id=int(task.id), dispatchable=False, reasons=tuple(reasons), assignee_id=None
        )

    employee = db.get(Employee, int(assignee_id))
    if employee is None:
        reasons.append(REASON_ASSIGNEE_INACTIVE)
    else:
        if employee.lifecycle_status != LifecycleStatus.active.value:
            reasons.append(REASON_ASSIGNEE_INACTIVE)
        elif employee.status in {
            EmployeeStatus.offline.value,
            EmployeeStatus.error.value,
        }:
            # 离线的 Agent 仍然可以"被派"（runtime 会把它拉起来）；只有真错误的才拦。
            reasons.extend(_runtime_block_reasons(db, employee))
        else:
            reasons.extend(_runtime_block_reasons(db, employee))

    if reasons:
        return DispatchEvaluation(
            task_id=int(task.id),
            dispatchable=False,
            reasons=tuple(reasons),
            assignee_id=int(assignee_id),
        )

    # ⑤ 负责人是否已经有别的 running 会话（一员工同时一个 session 的不变式）
    if project_repo.get_running_session_for_employee(db, int(assignee_id)) is not None:
        return DispatchEvaluation(
            task_id=int(task.id),
            dispatchable=False,
            queued=True,
            reasons=("assignee_busy",),
            assignee_id=int(assignee_id),
        )
    return DispatchEvaluation(task_id=int(task.id), dispatchable=True, assignee_id=int(assignee_id))


@dataclass(frozen=True)
class ProjectRuntimeState:
    """一个项目的运行态快照（**事实**：就绪 / 可派发 / 需要管理决策 / 是否全部完成）。"""

    project_id: int
    ready_task_ids: tuple[int, ...] = ()
    dispatchable: tuple[DispatchEvaluation, ...] = field(default_factory=tuple)
    queued: tuple[DispatchEvaluation, ...] = field(default_factory=tuple)
    needs_management: tuple[DispatchEvaluation, ...] = field(default_factory=tuple)
    all_tasks_done: bool = False
    task_count: int = 0

    @property
    def has_work_in_flight(self) -> bool:
        return bool(self.dispatchable or self.queued)


def project_runtime_state(db: Session, project_id: int) -> ProjectRuntimeState:
    """一次性算清"这个项目现在处于什么状态"（调度器与观测共用同一份口径）。"""
    tasks = project_repo.list_tasks(db, int(project_id))
    evaluations = [evaluate_dispatch(db, task) for task in tasks]
    return ProjectRuntimeState(
        project_id=int(project_id),
        ready_task_ids=structural_ready_task_ids(db, int(project_id)),
        dispatchable=tuple(item for item in evaluations if item.dispatchable),
        queued=tuple(item for item in evaluations if item.queued),
        needs_management=tuple(item for item in evaluations if item.needs_management),
        all_tasks_done=bool(tasks)
        and all(str(task.status) == TaskStatus.done.value for task in tasks),
        task_count=len(tasks),
    )


__all__ = [
    "DispatchEvaluation",
    "ProjectRuntimeState",
    "EXECUTABLE_PROJECT_STATUSES",
    "REASON_EVENTS",
    "graph_nodes",
    "structural_ready_task_ids",
    "ready_tasks",
    "evaluate_dispatch",
    "project_runtime_state",
]
