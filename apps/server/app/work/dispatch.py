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
from app.work import handoff

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
#: M2.6：声明的上游**跑完了却没产出**可交付物 ⇒ 计划与事实不符（H5）
REASON_INPUT_ARTIFACTS_MISSING = C.REASON_INPUT_ARTIFACTS_MISSING
#: M2.8：这个人的执行环境还没配齐（工作区 / 运行时 / 供应商）—— W31 的门禁面
REASON_ASSIGNEE_NOT_READY = "assignee_not_ready"

#: 每个阻断原因对应的事件（事实 vs 需要管理决策，见契约 §10b）
REASON_EVENTS: dict[str, str] = {
    REASON_ASSIGNEE_MISSING: "task.assignment_required",
    REASON_ASSIGNEE_INACTIVE: "task.runtime_unavailable",
    REASON_RUNTIME_UNAVAILABLE: "task.runtime_unavailable",
    REASON_PROVIDER_MISSING: "task.runtime_unavailable",
    REASON_TASK_HELD: "task.blocked",
    REASON_TASK_FAILED: "task.failed",
    # 输入缺失 = 这张计划按现状跑不出预期结果 ⇒ 重规划（不新增事件，复用 M2.5 的封闭集）
    REASON_INPUT_ARTIFACTS_MISSING: "project.replan_required",
    # 没配齐环境 ⇒ 负责人/Runtime 不可用（复用 M2.5 的封闭事件集，不新增事件）
    REASON_ASSIGNEE_NOT_READY: "task.runtime_unavailable",
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


def graph_report(db: Session, project_id: int) -> C.TaskGraphReport:
    """这张图**结构上**能不能执行（自环 / 悬空依赖 / 重复边 / 环）。"""
    return C.validate_task_graph(graph_nodes(db, int(project_id)))


def structural_ready_task_ids(db: Session, project_id: int) -> tuple[int, ...]:
    """结构就绪集合（依赖全部完成且自身还没开始）。

    **fail-closed**：图非法时权威口径（`validate_task_graph` 内部的
    `resolve_ready_tasks`）直接给空集。非法图的任务**不会被调度**，
    也不会因为"依赖都完成了"被误判成就绪 —— 环上永远等不到终态。
    """
    return graph_report(db, project_id).ready


def ready_tasks(db: Session, project_id: int) -> list[Task]:
    ids = set(structural_ready_task_ids(db, project_id))
    return [task for task in project_repo.list_tasks(db, int(project_id)) if int(task.id) in ids]


# ---------------------------------------------------------------------------
# 可派发判定
# ---------------------------------------------------------------------------


def _readiness_block_reasons(db: Session, employee: Employee) -> list[str]:
    """M2.8（W31/RD3）：执行环境是否就绪。

    **只查"执行必须满足"的那几项**（见契约 §8c）：mock 运行时不需要工作区/实例，
    真实运行时缺任何一项都拦下并交给管理层 —— 系统不会"先跑跑看"。
    """
    from app.work import readiness

    return [REASON_ASSIGNEE_NOT_READY] if readiness.blocking_gaps(db, employee) else []


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

    # ① 结构就绪：**只看契约口径**（不在这里重写一遍"依赖满足了没有"）
    project_id = int(task.project_id)
    if int(task.id) not in structural_ready_task_ids(db, project_id):
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
            reasons.extend(_readiness_block_reasons(db, employee))
            reasons.extend(_runtime_block_reasons(db, employee))
        else:
            reasons.extend(_readiness_block_reasons(db, employee))
            reasons.extend(_runtime_block_reasons(db, employee))

    # ④b M2.6：声明要用的上游跑完了却没产出 ⇒ 不派发，交给管理层（H5/R10）。
    # 这是"计划与事实不符"，不是"缺人/缺资源"：换个人也拿不到不存在的产物。
    missing_inputs = handoff.missing_input_sources(db, task)
    if missing_inputs:
        reasons.append(REASON_INPUT_ARTIFACTS_MISSING)

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
    #: M2.7：**没有可跑的活了** —— 全部任务要么已完成、要么在等评审。
    #: 与 `all_tasks_done` 的区别很重要：`in_review` 是"干完了但还没结论"，
    #: 它**不算完成**（项目不能因此交付），但确实没有东西可以调度了。
    work_finished: bool = False
    #: 图结构非法（系统职责里"这张图能不能执行"，W16）⇒ 一律不调度，上报重规划
    invalid_graph: bool = False
    graph_problems: tuple[str, ...] = ()

    @property
    def has_work_in_flight(self) -> bool:
        return bool(self.dispatchable or self.queued)


def _all_work_finished(tasks: list[Task]) -> bool:
    """没有可跑的活了：全部任务要么已完成、要么在等评审（M2.7）。"""
    return all(
        str(task.status) in {TaskStatus.done.value, TaskStatus.in_review.value} for task in tasks
    )


def project_runtime_state(db: Session, project_id: int) -> ProjectRuntimeState:
    """一次性算清"这个项目现在处于什么状态"（调度器与观测共用同一份口径）。"""
    tasks = project_repo.list_tasks(db, int(project_id))
    report = C.validate_task_graph(
        C.TaskGraphNode(
            task_id=int(task.id),
            status=str(task.status),
            depends_on=tuple(
                int(dep.depends_on_id)
                for dep in project_repo.list_dependencies(db, int(project_id))
                if int(dep.task_id) == int(task.id)
            ),
        )
        for task in tasks
    )
    if not report.is_valid:
        # 非法图：不派发、不上报"要人决策"（没人能靠换个负责人修好一张有环的图），
        # 由调用方按 project.replan_required 上报 —— 这张计划得重做。
        return ProjectRuntimeState(
            project_id=int(project_id),
            all_tasks_done=bool(tasks)
            and all(str(task.status) == TaskStatus.done.value for task in tasks),
            work_finished=bool(tasks) and _all_work_finished(tasks),
            task_count=len(tasks),
            invalid_graph=True,
            graph_problems=_graph_problems(report),
        )
    evaluations = [evaluate_dispatch(db, task) for task in tasks]
    return ProjectRuntimeState(
        project_id=int(project_id),
        ready_task_ids=structural_ready_task_ids(db, int(project_id)),
        dispatchable=tuple(item for item in evaluations if item.dispatchable),
        queued=tuple(item for item in evaluations if item.queued),
        needs_management=tuple(item for item in evaluations if item.needs_management),
        all_tasks_done=bool(tasks)
        and all(str(task.status) == TaskStatus.done.value for task in tasks),
        work_finished=bool(tasks) and _all_work_finished(tasks),
        task_count=len(tasks),
    )


def tasks_awaiting_review(db: Session, project_id: int) -> list[Task]:
    """做完、但**还没有人接手评审**的任务（M2.7）。

    "没人接手" = `in_review` 且没有未决定的 `review_requests` 行。
    系统不替管理层指定评审人（W1/R2），所以这种状态是**要叫醒别人**的，
    而不是"系统自己通过"。
    """
    from app.repositories import review as review_repo

    pending: list[Task] = []
    for task in project_repo.list_tasks(db, int(project_id)):
        if str(task.status) != TaskStatus.in_review.value:
            continue
        open_rows = [
            row
            for row in review_repo.list_requests(db, task_id=int(task.id))
            if row.status == C.REVIEW_REQUEST_OPEN
        ]
        if not open_rows:
            pending.append(task)
    return pending


def _graph_problems(report: C.TaskGraphReport) -> tuple[str, ...]:
    """把校验报告翻成给管理层看的问题清单（只陈述结构问题）。"""
    problems: list[str] = []
    if report.self_loops:
        problems.append(f"self_loop:{sorted(report.self_loops)}")
    if report.dangling:
        problems.append(f"dangling:{sorted(report.dangling)}")
    if report.duplicates:
        problems.append(f"duplicate_edge:{sorted(report.duplicates)}")
    if report.cycles:
        problems.append(f"cycle:{sorted(report.cycles)}")
    return tuple(problems)


__all__ = [
    "DispatchEvaluation",
    "ProjectRuntimeState",
    "EXECUTABLE_PROJECT_STATUSES",
    "REASON_EVENTS",
    "graph_nodes",
    "graph_report",
    "tasks_awaiting_review",
    "structural_ready_task_ids",
    "ready_tasks",
    "evaluate_dispatch",
    "project_runtime_state",
]
