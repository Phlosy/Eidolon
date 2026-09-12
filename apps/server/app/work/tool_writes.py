"""M2.3 **写工具**（Write Internal，T3 / T4 / T7）。

每个 handler 都是**既有领域服务**的适配器，且必须：

1. 先做**领域前置校验**（跨公司 / 状态机 / 人员可用性 / DAG 合法性），
   再调用既有 service —— 不绕过状态机、不绕过 Ledger、不自己写 SQL；
2. 依赖执行面**已经**做完 Authority 校验（`ToolSpec.required_authority` +
   `authority_target`，T4）—— 授权不是 handler 的职责，但也不是它的可选步骤；
3. 只返回"系统实际应用了什么"的事实（T7）。

**刻意不做**（用户拍板 §9）：`spend_credits` / `offboard_employee` / `purchase_talent`
等高影响动作 —— 它们的授权已存在（`HIGH_IMPACT_AUTHORITIES_RESERVED`），
但 M2.3 不写空业务。

**M2.3 的诚实边界**：`request_review` 目前只做状态推进 + 留痕 + 事件，
持久的 `ReviewRequest` 实体是 M2.7 的交付物；返回值里用
`review_entity: deferred_to_M2.7` 明说这件事，不假装评审流程已经存在。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import (
    DecisionSemantics,
    LifecycleStatus,
    TaskKind,
    TaskStatus,
)
from app.models.project import Task
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import tools

#: 允许由管理 Agent 创建的任务类型（`general` 刻意放开：不是每个工作项都能塞进固定分类）
CREATABLE_TASK_KINDS = tuple(kind.value for kind in TaskKind)

#: `update_task` 允许改的字段（状态另有专用工具；改 id/来源列不在管理动作范围内）
UPDATABLE_TASK_FIELDS = ("title", "description", "acceptance_criteria", "priority")


def _task_in_company(db: Session, ctx: tools.ToolCallContext, task_id: int) -> tuple[Task, int]:
    """取任务并强制公司作用域；返回 (task, company_id)。"""
    task = project_repo.get_task(db, int(task_id))
    if task is None:
        raise tools.ToolError("task not found")
    project = project_repo.get_project(db, int(task.project_id))
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("task not found in this company")
    return task, int(project.company_id)


def _active_employee(db: Session, ctx: tools.ToolCallContext, employee_id: int):
    employee = org_repo.get_employee(db, int(employee_id))
    if (
        employee is None
        or employee.company_id is None
        or int(employee.company_id) != ctx.company_id
    ):
        raise tools.ToolError("employee not found in this company")
    if employee.lifecycle_status != LifecycleStatus.active.value:
        # 生命周期冲突：停用/离职中的人不该被派活（硬约束，不是管理判断）
        raise tools.ToolError(
            f"employee is not active (lifecycle_status={employee.lifecycle_status})"
        )
    return employee


def _task_snapshot(task: Task) -> dict:
    return {
        "task_id": int(task.id),
        "project_id": int(task.project_id),
        "title": task.title,
        "kind": task.kind,
        "status": task.status,
        "assignee_id": task.assignee_id,
        "priority": int(task.priority),
        "sequence": int(task.sequence),
    }


# ---------------------------------------------------------------------------
# 工作图：建 / 改 / 连依赖 / 阻塞 / 取消
# ---------------------------------------------------------------------------


def _create_task(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    project_id = int(args["project_id"])
    project = project_repo.get_project(db, project_id)
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("project not found in this company")
    kind = str(args.get("kind") or TaskKind.general.value)
    if kind not in CREATABLE_TASK_KINDS:
        raise tools.ToolError(f"unknown task kind: {kind}")

    depends_on = [int(item) for item in (args.get("depends_on") or [])]
    for dependency_id in depends_on:
        _task_in_company(db, ctx, dependency_id)

    assignee_id = args.get("assignee_id")
    if assignee_id is not None:
        _active_employee(db, ctx, int(assignee_id))

    task = task_service.create_task(
        db,
        project_id=project_id,
        title=str(args["title"]).strip(),
        kind=kind,
        assignee_id=int(assignee_id) if assignee_id is not None else None,
        status=TaskStatus.backlog.value,
        description=str(args.get("description") or ""),
        acceptance_criteria=str(args.get("acceptance_criteria") or ""),
        priority=int(args.get("priority", 0)),
        sequence=int(args.get("sequence", 0)),
        depends_on=depends_on,
    )
    db.commit()
    db.refresh(task)
    task_service.publish_task_created(task, ctx.company_id)
    bus.publish(
        "work.task_created",
        {"task_id": int(task.id), "project_id": project_id, "via": "agent_tool"},
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=project_id,
        task_id=int(task.id),
    )
    return {"task": _task_snapshot(task), "applied": "created"}


def _update_task(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    before = _task_snapshot(task)
    changed: list[str] = []
    for field_name in UPDATABLE_TASK_FIELDS:
        if field_name in args and args[field_name] is not None:
            setattr(task, field_name, args[field_name])
            changed.append(field_name)
    new_status = args.get("status")
    if new_status is not None:
        if new_status not in {status.value for status in TaskStatus}:
            raise tools.ToolError(f"unknown task status: {new_status}")
        try:
            task_service.transition_task(db, task, str(new_status))
        except task_service.InvalidTransitionError as exc:
            raise tools.ToolError(str(exc)) from exc
        changed.append("status")
    if not changed:
        raise tools.ToolError("nothing to update (no mutable field provided)")
    db.commit()
    db.refresh(task)
    # refresh 会开一个读事务；SQLite 单写者下它挡住 bus.publish 的写事务 —— 先释放
    db.commit()
    bus.publish(
        "work.task_updated",
        {"task_id": int(task.id), "fields": changed, "via": "agent_tool"},
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {"task": _task_snapshot(task), "before": before, "changed": changed}


def _create_dependency(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    depends_on_id = int(args["depends_on_id"])
    _task_in_company(db, ctx, depends_on_id)
    if int(task.id) == depends_on_id:
        raise tools.ToolError("a task cannot depend on itself")

    # 依赖图从**表**读，不从 ORM 关系读：`SessionLocal` 是 `expire_on_commit=False`，
    # 关系属性会在同一会话里保持旧值（实测：同一会话里第二次建边时，前一次刚提交的边
    # 在 `task.dependencies` 里看不见 → 环检测会漏）。查询是新鲜的，关系缓存不是。
    deps_by_task: dict[int, list[int]] = {}
    for row in project_repo.list_dependencies(db, int(task.project_id)):
        deps_by_task.setdefault(int(row.task_id), []).append(int(row.depends_on_id))
    existing = deps_by_task.get(int(task.id), [])
    if depends_on_id in existing:
        raise tools.ToolError("dependency already exists")

    # DAG 正确性是**系统**的职责（W16）：把整张图（含本次拟新增的边）交给唯一校验器，
    # 环 / 自环 / 悬空依赖在这里被挡住。
    project_tasks = project_repo.list_tasks(db, int(task.project_id))
    nodes = []
    for row in project_tasks:
        deps = tuple(deps_by_task.get(int(row.id), ()))
        if int(row.id) == int(task.id):
            deps = deps + (depends_on_id,)
        nodes.append(C.TaskGraphNode(task_id=int(row.id), status=row.status, depends_on=deps))
    report = C.validate_task_graph(nodes)
    if not report.is_valid:
        raise tools.ToolError(f"task graph would become invalid: {report.error}")

    project_repo.add_dependency(db, task_id=int(task.id), depends_on_id=depends_on_id)
    # **提交后不要再查库**：SQLite 是单写者，读事务会挡住 `bus.publish` 的写事务
    # （实测：`database is locked`）。依赖清单用提交前就算好的 `deps_by_task`。
    dependencies = sorted({*deps_by_task.get(int(task.id), ()), depends_on_id})
    db.commit()
    bus.publish(
        "work.dependency_created",
        {"task_id": int(task.id), "depends_on_id": depends_on_id, "via": "agent_tool"},
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {
        "task_id": int(task.id),
        "depends_on_id": depends_on_id,
        "dependencies": dependencies,
        "graph_ready": list(report.ready),
    }


def _mark_task_blocked(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    try:
        task_service.transition_task(db, task, TaskStatus.blocked.value)
    except task_service.InvalidTransitionError as exc:
        raise tools.ToolError(str(exc)) from exc
    db.commit()
    db.refresh(task)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    bus.publish(
        "work.task_blocked",
        {
            "task_id": int(task.id),
            "reason": str(args.get("reason") or ""),
            "via": "agent_tool",
        },
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {"task": _task_snapshot(task), "applied": "blocked"}


def _cancel_task(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    try:
        task_service.transition_task(db, task, TaskStatus.cancelled.value)
    except task_service.InvalidTransitionError as exc:
        raise tools.ToolError(str(exc)) from exc
    db.commit()
    db.refresh(task)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    bus.publish(
        "work.task_cancelled",
        {"task_id": int(task.id), "reason": str(args.get("reason") or ""), "via": "agent_tool"},
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {"task": _task_snapshot(task), "applied": "cancelled"}


# ---------------------------------------------------------------------------
# 指派 / 委派
# ---------------------------------------------------------------------------


def _assign_task(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """把任务派给某人（**Agent 已经做完选择**，系统只验证并应用，T7）。

    系统在这里**不**回答"谁最合适"：它只校验
      * 目标人属于本公司且处于 active（生命周期冲突）；
      * 任务在可指派状态（终态任务不可改派）；
      * actor 有 `assign_task` 授权（执行面已完成，见 T4）。
    """
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    employee = _active_employee(db, ctx, int(args["employee_id"]))
    if task.status in (TaskStatus.done.value, TaskStatus.cancelled.value):
        raise tools.ToolError(f"cannot assign a task in terminal state: {task.status}")
    if TaskStatus(task.status) is TaskStatus.backlog:
        # 指派即开工：backlog → todo（状态机唯一合法路径）
        task_service.transition_task(db, task, TaskStatus.todo.value)
    previous = task.assignee_id
    task.assignee_id = int(employee.id)
    db.commit()
    db.refresh(task)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    bus.publish(
        "task.assigned",
        {
            "id": int(task.id),
            "title": task.title,
            "assignee_id": int(employee.id),
            "previous_assignee_id": previous,
            "via": "agent_tool",
        },
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {
        "task": _task_snapshot(task),
        "assignee": {
            "employee_id": int(employee.id),
            "person_id": int(employee.person_id) if employee.person_id else None,
            "lifecycle_status": employee.lifecycle_status,
            "employee_status": employee.status,
        },
        "previous_assignee_id": previous,
        "applied": "assigned",
    }


def _delegate_project(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """把一个项目交给另一位管理 Agent 负责（D1 的"CEO 可以委派"）。

    只改 `management_*` **当前指针**（M2-ADR-14）；权威仍是责任路由，
    历史归 `DecisionRecord`（M2.4）。若目标人尚未就绪（非 active）则拒绝。
    """
    project_id = int(args["project_id"])
    project = project_repo.get_project(db, project_id)
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("project not found in this company")
    employee = _active_employee(db, ctx, int(args["to_employee_id"]))
    if project.status in ("completed", "cancelled", "rejected"):
        raise tools.ToolError(f"cannot delegate a project in terminal state: {project.status}")

    before = {
        "management_employee_id": project.management_employee_id,
        "management_person_id": project.management_person_id,
    }
    project.management_employee_id = int(employee.id)
    project.management_person_id = int(employee.person_id) if employee.person_id else None
    project.management_assigned_at = utcnow()
    db.commit()
    db.refresh(project)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    projected = {
        "project_id": int(project.id),
        "management_employee_id": project.management_employee_id,
        "management_person_id": project.management_person_id,
    }
    bus.publish(
        "project.management_delegated",
        {**projected, "via": "agent_tool", "note": str(args.get("note") or "")},
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(project.id),
    )
    return {"project": projected, "before": before, "applied": "delegated"}


# ---------------------------------------------------------------------------
# 评审 / 返工
# ---------------------------------------------------------------------------


def _request_review(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """请人评审一件已完成的工作：状态推进到 `in_review` + 留痕 + 事件。

    M2.3 的诚实边界：**没有**持久的 `ReviewRequest` 实体（那是 M2.7）；
    被指定的评审人记在审计与事件里，返回值明确说明这一点。
    """
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    reviewer_id = args.get("reviewer_employee_id")
    reviewer = _active_employee(db, ctx, int(reviewer_id)) if reviewer_id is not None else None
    try:
        task_service.transition_task(db, task, TaskStatus.in_review.value)
    except task_service.InvalidTransitionError as exc:
        raise tools.ToolError(str(exc)) from exc
    db.commit()
    db.refresh(task)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    bus.publish(
        "work.review_requested",
        {
            "task_id": int(task.id),
            "reviewer_employee_id": int(reviewer.id) if reviewer else None,
            "via": "agent_tool",
        },
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {
        "task": _task_snapshot(task),
        "reviewer_employee_id": int(reviewer.id) if reviewer else None,
        "review_entity": "deferred_to_M2.7",
        "applied": "in_review",
    }


def _request_rework(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """要求返工：`in_review → rejected → todo`（状态机两步，理由记入审计）。

    可以选择同时改派（`assignee_id`）；不传则保持原负责人。
    """
    task, _ = _task_in_company(db, ctx, int(args["task_id"]))
    reason = str(args.get("reason") or "").strip()
    if not reason:
        raise tools.ToolError("a rework request must state a reason")
    assignee_id = args.get("assignee_id")
    new_assignee = _active_employee(db, ctx, int(assignee_id)) if assignee_id is not None else None
    try:
        task_service.transition_task(db, task, TaskStatus.rejected.value)
        task_service.transition_task(db, task, TaskStatus.todo.value)
    except task_service.InvalidTransitionError as exc:
        raise tools.ToolError(str(exc)) from exc
    previous_assignee = task.assignee_id
    if new_assignee is not None:
        task.assignee_id = int(new_assignee.id)
    db.commit()
    db.refresh(task)
    db.commit()  # 释放 refresh 的读事务（SQLite 单写者）
    bus.publish(
        "task.rework_requested",
        {
            "id": int(task.id),
            "task_id": int(task.id),
            "reason": reason,
            "assignee_id": task.assignee_id,
            "via": "agent_tool",
        },
        company_id=ctx.company_id,
        actor_employee_id=ctx.employee_id,
        project_id=int(task.project_id),
        task_id=int(task.id),
    )
    return {
        "task": _task_snapshot(task),
        "reason": reason,
        "previous_assignee_id": previous_assignee,
        "applied": "rework_requested",
    }


# ---------------------------------------------------------------------------
# Authority target（执行面用；**与 handler 分离**，让授权校验在 handler 之前发生）
# ---------------------------------------------------------------------------


def _target_company(db: Session, ctx: tools.ToolCallContext, args: dict) -> C.AuthorityTarget:
    return C.AuthorityTarget(company_id=ctx.company_id)


def _target_task_assignee(db: Session, ctx: tools.ToolCallContext, args: dict) -> C.AuthorityTarget:
    """派活类动作的目标 = "被派的那个人"（这样 `direct_reports` 作用域才有意义）。"""
    employee_id = args.get("employee_id") or args.get("assignee_id") or args.get("to_employee_id")
    return C.AuthorityTarget(
        company_id=ctx.company_id,
        employee_id=int(employee_id) if employee_id is not None else None,
    )


def _target_task_owner(db: Session, ctx: tools.ToolCallContext, args: dict) -> C.AuthorityTarget:
    """对任务本身的动作（改/连/阻/取消/请评审）：目标是任务当前负责人（可能是自己）。"""
    task_id = args.get("task_id")
    task = project_repo.get_task(db, int(task_id)) if task_id else None
    assignee = task.assignee_id if task is not None else None
    return C.AuthorityTarget(company_id=ctx.company_id, employee_id=assignee)


def _target_rework(db: Session, ctx: tools.ToolCallContext, args: dict) -> C.AuthorityTarget:
    target = args.get("assignee_id")
    if target is not None:
        return C.AuthorityTarget(company_id=ctx.company_id, employee_id=int(target))
    task = project_repo.get_task(db, int(args["task_id"]))
    assignee = task.assignee_id if task is not None else None
    return C.AuthorityTarget(company_id=ctx.company_id, employee_id=assignee)


# ---------------------------------------------------------------------------
# 注册表条目
# ---------------------------------------------------------------------------

_STR = {"type": "string"}
_INT = {"type": "integer"}
_TASK_ID = {"task_id": _INT}
_PROJECT_ID = {"project_id": _INT}


def build_write_tools() -> tuple[tools.ToolSpec, ...]:
    """写工具清单（**只有内部执行面**；人类管理动作走各领域自己的正式 API）。"""
    return (
        tools.ToolSpec(
            name="create_task",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="在项目里新建一个任务（可选依赖与初始指派；不自动执行）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_company,
            input_schema=tools.object_schema(
                {
                    **_PROJECT_ID,
                    "title": _STR,
                    "kind": {"type": "string", "enum": list(CREATABLE_TASK_KINDS)},
                    "description": _STR,
                    "acceptance_criteria": _STR,
                    "priority": _INT,
                    "sequence": _INT,
                    "assignee_id": _INT,
                    "depends_on": {"type": "array", "items": _INT},
                },
                ("project_id", "title"),
            ),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_create_task,
        ),
        tools.ToolSpec(
            name="update_task",
            # 决策语义 OPTIONAL：状态推进/维护类（用户拍板 §6）
            decision_semantics=DecisionSemantics.optional,
            description="改任务的字段或做一次合法状态迁移（状态机强制）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_task_owner,
            input_schema=tools.object_schema(
                {
                    **_TASK_ID,
                    "title": _STR,
                    "description": _STR,
                    "acceptance_criteria": _STR,
                    "priority": _INT,
                    "status": {"type": "string", "enum": [s.value for s in TaskStatus]},
                },
                ("task_id",),
            ),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_update_task,
        ),
        tools.ToolSpec(
            name="create_dependency",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="声明任务依赖（系统校验 DAG 正确性：环/自环/悬空一律拒绝）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_task_owner,
            input_schema=tools.object_schema(
                {**_TASK_ID, "depends_on_id": _INT}, ("task_id", "depends_on_id")
            ),
            output_schema=tools.object_schema({"dependencies": {"type": "array"}}),
            handler=_create_dependency,
        ),
        tools.ToolSpec(
            name="assign_task",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="把任务派给某位 active 员工（是否最合适由 Agent 自己判断）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.assign_task,
            authority_target=_target_task_assignee,
            input_schema=tools.object_schema(
                {**_TASK_ID, "employee_id": _INT}, ("task_id", "employee_id")
            ),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_assign_task,
        ),
        tools.ToolSpec(
            name="delegate_project",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="把项目交给另一位管理 Agent 负责（只改当前管理指针）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.delegate_management,
            authority_target=_target_task_assignee,
            input_schema=tools.object_schema(
                {**_PROJECT_ID, "to_employee_id": _INT, "note": _STR},
                ("project_id", "to_employee_id"),
            ),
            output_schema=tools.object_schema({"project": {"type": "object"}}),
            handler=_delegate_project,
        ),
        tools.ToolSpec(
            name="request_review",
            # 决策语义 OPTIONAL：状态推进/维护类（用户拍板 §6）
            decision_semantics=DecisionSemantics.optional,
            description="请人评审一件已完成的工作（状态推进到 in_review + 留痕）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_rework,
            input_schema=tools.object_schema(
                {**_TASK_ID, "reviewer_employee_id": _INT}, ("task_id",)
            ),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_request_review,
        ),
        tools.ToolSpec(
            name="request_rework",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="要求返工（必须给理由；可同时改派）",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.request_rework,
            authority_target=_target_rework,
            input_schema=tools.object_schema(
                {**_TASK_ID, "reason": _STR, "assignee_id": _INT}, ("task_id", "reason")
            ),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_request_rework,
        ),
        tools.ToolSpec(
            name="mark_task_blocked",
            # 决策语义 OPTIONAL：状态推进/维护类（用户拍板 §6）
            decision_semantics=DecisionSemantics.optional,
            description="把任务标记为阻塞（等外部输入 / 等依赖 / 等人），并记下原因",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_task_owner,
            input_schema=tools.object_schema({**_TASK_ID, "reason": _STR}, ("task_id",)),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_mark_task_blocked,
        ),
        tools.ToolSpec(
            name="cancel_task",
            # 决策语义 REQUIRED：这是真正的管理动作，必须隶属于一条决策（DR7）
            decision_semantics=DecisionSemantics.required,
            description="取消任务（计划变了）。终态任务不可取消 —— 历史不改写",
            side_effect=C.ToolSideEffect.write,
            required_authority=C.AuthorityKind.plan_project_work,
            authority_target=_target_task_owner,
            input_schema=tools.object_schema({**_TASK_ID, "reason": _STR}, ("task_id",)),
            output_schema=tools.object_schema({"task": {"type": "object"}}),
            handler=_cancel_task,
        ),
    )


__all__ = ["build_write_tools", "CREATABLE_TASK_KINDS", "UPDATABLE_TASK_FIELDS"]
