"""M2.3 **读工具**（Read Shared，T2 / T6）。

这些 handler 是**既有读面的适配器**，不是第二套查询实现：

| 工具 | 复用的既有读面 |
| --- | --- |
| `list_company_projects` / `inspect_project` | `services.projects` 项目读面 |
| `list_company_people` | `services.talent_roster.roster_query` |
| `inspect_person` | `talent.person.read_model.person_profile` |
| `inspect_position` | `services.position_service.{definitions_out,slots_out}` |
| `inspect_assignments` | `repositories.position.career_history`（任职时间轴） |
| `get_competencies` | `talent.person.read_model.competencies_out` |
| `get_evidence` | `services.competency.{person_evidence_rows,evidence_payload}` |
| `calculate_task_fit` | `evidence.policy.{TASK_KIND_HINTS,ROLE_STRENGTH}` + 既有能力行 |
| `get_current_load` | `repositories.project`（在办任务/会话） |
| `get_runtime_status` | `repositories.runtimes` |
| `search_company_knowledge` | `repositories.knowledge.list_knowledge_items` + FTS |
| `inspect_artifact` | `repositories.drive` + `services.drive.read_content` |

**公司作用域**在每一处都由 `ctx.company_id` 强制；跨公司的 id 一律按"不存在"处理
（404 语义，不泄露存在性）。

**T6**：读工具只给事实。`calculate_task_fit` 的输出**按 employee_id 排序**、
**没有 rank/recommended/best 字段** —— 有排序就会变成推荐，有推荐就等于系统替
Manager 选人（W1/W11）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.competency import CompetencyDefinition, EmployeeCompetency
from app.models.enums import KnowledgeScope, KnowledgeStatus, TaskStatus, WorkSessionStatus
from app.models.organization import Employee
from app.models.project import Project, Task, WorkSession
from app.repositories import drive as drive_repo
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.services import competency as competency_service
from app.services import drive as drive_service
from app.services import projects as project_service
from app.services import talent_roster as roster_service
from app.work import contracts as C
from app.work import role_context as role_context_service
from app.work import tools, work_intake

#: 在办任务状态（"这个人手上还有多少活"的口径，与派生进度的口径同源）
OPEN_TASK_STATUSES = (
    TaskStatus.todo.value,
    TaskStatus.in_progress.value,
    TaskStatus.in_review.value,
    TaskStatus.blocked.value,
)


def _project_or_none(db: Session, ctx: tools.ToolCallContext, project_id: int) -> Project:
    project = project_repo.get_project(db, int(project_id))
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("project not found in this company")
    return project


def _employee_or_none(db: Session, ctx: tools.ToolCallContext, employee_id: int) -> Employee:
    employee = org_repo.get_employee(db, int(employee_id))
    if (
        employee is None
        or employee.company_id is None
        or int(employee.company_id) != ctx.company_id
    ):
        raise tools.ToolError("employee not found in this company")
    return employee


def _company_employees(db: Session, ctx: tools.ToolCallContext) -> list[Employee]:
    return org_repo.list_employees(db, ctx.company_id)


# ---------------------------------------------------------------------------
# 项目 / 团队事实
# ---------------------------------------------------------------------------


def _list_company_projects(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    portfolio = project_service.get_project_portfolio(db)
    items = [
        {
            "project_id": int(entry.id),
            "name": entry.name,
            "status": entry.status,
            "work_mode": entry.work_mode,
            "planning_fixture": entry.planning_fixture,
            "management_employee_id": entry.management_employee_id,
            "task_count": len(entry.tasks),
            "milestone_count": len(entry.milestones),
        }
        for entry in portfolio
    ]
    return {"projects": items, "total": len(items)}


def _inspect_project(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    project = _project_or_none(db, ctx, int(args["project_id"]))
    detail = project_service.get_project_detail(db, project)
    spec = project_service.get_project_spec(db, project)
    return {
        "project": {
            "project_id": int(project.id),
            "name": project.name,
            "status": project.status,
            "work_mode": project.work_mode,
            "planning_fixture": project.planning_fixture,
            "owner_id": project.owner_id,
            "management_employee_id": project.management_employee_id,
            "management_person_id": project.management_person_id,
            "priority": project.priority,
            "deadline": (project.planned_end_at.isoformat() if project.planned_end_at else None),
        },
        "spec": spec["spec"],
        "completeness": spec["completeness"],
        "execution": spec["execution"],
        "work_intake": spec["work_intake"],
        "tasks": [
            {
                "task_id": int(task.id),
                "title": task.title,
                "kind": task.kind,
                "status": task.status,
                "assignee_id": task.assignee_id,
                "dependencies": [int(dep) for dep in (task.dependencies or [])],
                "acceptance_criteria": task.acceptance_criteria,
            }
            for task in detail.tasks
        ],
        "artifacts": [
            {"artifact_id": int(item.id), "type": item.type, "title": item.title}
            for item in detail.artifacts
        ],
    }


def _list_company_people(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    payload = roster_service.roster_query(
        db,
        ctx.company_id,
        statuses=args.get("statuses") or None,
        department_id=args.get("department_id"),
        position_code=args.get("position_code"),
        search=args.get("search"),
        limit=int(args.get("limit", 50)),
        offset=int(args.get("offset", 0)),
    )
    return payload


def _inspect_person(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    from app.repositories import persons as person_repo
    from app.talent.person import read_model

    person_id = int(args["person_id"])
    person = person_repo.get_person(db, person_id)
    if person is None:
        raise tools.ToolError("person not found")
    # 公司作用域：这个人必须在公司里（在册或曾在册）—— 别家公司的档案不可见
    employed = db.scalar(
        select(func.count())
        .select_from(Employee)
        .where(Employee.person_id == person_id, Employee.company_id == ctx.company_id)
    )
    if not employed:
        raise tools.ToolError("person not found in this company")
    return read_model.person_profile(db, person)


def _inspect_position(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    from app.services import position_service

    definitions = position_service.definitions_out(db, ctx.company_id)
    code = args.get("position_code")
    definition_id = args.get("position_definition_id")
    if code is not None:
        definitions = [row for row in definitions if row["code"] == code]
    if definition_id is not None:
        definitions = [row for row in definitions if int(row["id"]) == int(definition_id)]
    if not definitions:
        raise tools.ToolError("position not found in this company")
    slots = [
        position_service.slot_out(db, int(slot.id))
        for slot in position_repo.list_slots(
            db, company_id=ctx.company_id, definition_id=int(definitions[0]["id"])
        )
    ]
    return {"positions": definitions, "slots": slots}


def _inspect_assignments(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    employee_id = args.get("employee_id")
    if employee_id is None:
        employee_id = ctx.employee_id
    employee = _employee_or_none(db, ctx, int(employee_id))
    history = position_repo.career_history(db, int(employee.id))
    return {
        "employee_id": int(employee.id),
        "person_id": int(employee.person_id) if employee.person_id else None,
        "lifecycle_status": employee.lifecycle_status,
        "assignments": [
            {
                "assignment_id": int(row.id),
                "position_slot_id": row.position_slot_id,
                "assignment_type": row.assignment_type,
                "is_primary": bool(row.is_primary),
                "effective_from": row.effective_from.isoformat() if row.effective_from else None,
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "position_title_snapshot": row.position_title_snapshot,
                "reason": row.reason,
            }
            for row in history
        ],
    }


# ---------------------------------------------------------------------------
# 能力 / 证据 / 负载
# ---------------------------------------------------------------------------


def _person_of(db: Session, ctx: tools.ToolCallContext, args: dict) -> int:
    """解析 person_id（允许传 employee_id，换算只走 persons 仓库）。"""
    if args.get("person_id") is not None:
        person_id = int(args["person_id"])
        employed = db.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.person_id == person_id, Employee.company_id == ctx.company_id)
        )
        if not employed:
            raise tools.ToolError("person not found in this company")
        return person_id
    employee = _employee_or_none(db, ctx, int(args.get("employee_id") or ctx.employee_id))
    if employee.person_id is None:
        raise tools.ToolError("employee has no person_id (legacy row)")
    return int(employee.person_id)


def _get_competencies(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    from app.talent.person import read_model

    person_id = _person_of(db, ctx, args)
    return {
        "person_id": person_id,
        "competencies": read_model.competencies_out(db, person_id),
    }


def _get_evidence(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    person_id = _person_of(db, ctx, args)
    rows = competency_service.person_evidence_rows(
        db,
        person_id,
        limit=int(args.get("limit", 20)),
        offset=int(args.get("offset", 0)),
        source_type=args.get("source_type"),
        competency=args.get("competency"),
    )
    payload = competency_service.evidence_payload(db, rows)
    for item in payload:
        item["person_id"] = person_id
    return {"person_id": person_id, "evidence": payload, "count": len(payload)}


def _task_requirement_expectations(db: Session, task: Task) -> list[dict]:
    """任务隐含的能力需求 —— **复用证据流水线用的同一张映射表**。

    `evidence.policy.TASK_KIND_HINTS` 是"这种任务通常考验哪些能力"的**唯一**声明处；
    这里直接用它，而不是再发明一套任务能力需求表（那会立刻产生第二套真相）。
    它给的是 `(competency_code, role)`，**没有** minimum/target ——
    所以本工具输出的是"已知/未知 + 已知加权均值"，**不是**合格判定。
    """
    from app.evidence.policy import ROLE_STRENGTH, TASK_KIND_HINTS

    hints = TASK_KIND_HINTS.get(task.kind or "", [])
    requirements: list[dict] = []
    for code, role in hints:
        definition = db.scalar(
            select(CompetencyDefinition).where(CompetencyDefinition.code == code)
        )
        if definition is None:
            continue
        requirements.append(
            {
                "competency_code": code,
                "competency_definition_id": int(definition.id),
                "role": role,
                "weight": ROLE_STRENGTH.get(role, 0.5),
            }
        )
    return requirements


def _calculate_task_fit(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """**事实**版任务适配查询（T6 / W11）：逐人给已知/未知与加权已知均值，**不排名**。

    刻意不做的事：
      * 不返回 rank / recommended / best / 建议 —— 排序就是推荐，推荐就是替 Manager 选人；
      * 不把"未知"当 0 分（Unknown != Bad，与 Fit 引擎同一条规则）；
      * 不合并成一个"适配百分比"当结论 —— 它只是**已知**能力覆盖的加权均值，
        缺哪几项在同一行里列得清清楚楚。
    """
    from app.evidence.policy import ROLE_STRENGTH

    task = project_repo.get_task(db, int(args["task_id"]))
    if task is None:
        raise tools.ToolError("task not found")
    project = project_repo.get_project(db, int(task.project_id))
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("task not found in this company")

    requirements = _task_requirement_expectations(db, task)
    candidates = args.get("candidate_employee_ids")
    if candidates:
        employees = [_employee_or_none(db, ctx, int(eid)) for eid in candidates]
    else:
        employees = _company_employees(db, ctx)

    employee_ids = [int(employee.id) for employee in employees]
    rows = (
        list(
            db.scalars(
                select(EmployeeCompetency).where(
                    EmployeeCompetency.employee_id.in_(employee_ids),
                    EmployeeCompetency.competency_definition_id.in_(
                        [r["competency_definition_id"] for r in requirements]
                    )
                    if requirements
                    else [],
                )
            )
        )
        if requirements
        else []
    )
    by_employee: dict[int, dict[int, EmployeeCompetency]] = {}
    for row in rows:
        by_employee.setdefault(int(row.employee_id or 0), {})[int(row.competency_definition_id)] = (
            row
        )

    results: list[dict] = []
    for employee in sorted(employees, key=lambda item: int(item.id)):
        known = 0
        weight_sum = 0.0
        weighted_score = 0.0
        per_competency: list[dict] = []
        for requirement in sorted(requirements, key=lambda item: item["competency_code"]):
            row = by_employee.get(int(employee.id), {}).get(
                int(requirement["competency_definition_id"])
            )
            score = int(row.score) if row is not None and row.score is not None else None
            confidence = (
                float(row.confidence) if row is not None and row.confidence is not None else None
            )
            weight = float(ROLE_STRENGTH.get(requirement["role"], 0.5))
            is_known = score is not None
            if is_known:
                known += 1
                weight_sum += weight
                weighted_score += weight * float(score)
            per_competency.append(
                {
                    "competency_code": requirement["competency_code"],
                    "role": requirement["role"],
                    "known": is_known,
                    "score": score,
                    "confidence": confidence,
                }
            )
        results.append(
            {
                "employee_id": int(employee.id),
                "person_id": int(employee.person_id) if employee.person_id else None,
                "lifecycle_status": employee.lifecycle_status,
                "requirements_total": len(requirements),
                "requirements_known": known,
                "known_weighted_score": (
                    round(weighted_score / weight_sum, 2) if weight_sum > 0 else None
                ),
                "competencies": per_competency,
            }
        )

    return {
        "task": {
            "task_id": int(task.id),
            "title": task.title,
            "kind": task.kind,
            "status": task.status,
            "current_assignee_id": task.assignee_id,
        },
        "requirement_source": "evidence.policy.TASK_KIND_HINTS",
        "requirements": [
            {
                "competency_code": r["competency_code"],
                "role": r["role"],
                "weight": r["weight"],
            }
            for r in requirements
        ],
        "candidates": results,
        #: 明确声明：这是事实，不是建议（T6 / W11）
        "result_kind": "facts_only",
        "note": (
            "No ranking and no recommendation: unknown competencies are reported as unknown, "
            "never as zero. Choosing an assignee is the manager agent's decision."
        ),
    }


def _get_current_load(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    employee_ids = args.get("employee_ids")
    if employee_ids:
        employees = [_employee_or_none(db, ctx, int(eid)) for eid in employee_ids]
    else:
        employees = _company_employees(db, ctx)
    ids = [int(employee.id) for employee in employees]

    task_rows = db.execute(
        select(Task.assignee_id, func.count(Task.id))
        .where(Task.assignee_id.in_(ids), Task.status.in_(OPEN_TASK_STATUSES))
        .group_by(Task.assignee_id)
    ).all()
    open_tasks = {int(assignee): int(count) for assignee, count in task_rows if assignee}

    session_rows = db.execute(
        select(WorkSession.employee_id, func.count(WorkSession.id))
        .where(
            WorkSession.employee_id.in_(ids),
            WorkSession.status == WorkSessionStatus.running.value,
        )
        .group_by(WorkSession.employee_id)
    ).all()
    running = {int(employee_id): int(count) for employee_id, count in session_rows if employee_id}

    return {
        "load": [
            {
                "employee_id": int(employee.id),
                "employee_status": employee.status,
                "lifecycle_status": employee.lifecycle_status,
                "open_task_count": open_tasks.get(int(employee.id), 0),
                "running_sessions": running.get(int(employee.id), 0),
                "current_task_id": employee.current_task_id,
            }
            for employee in sorted(employees, key=lambda item: int(item.id))
        ]
    }


def _get_runtime_status(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    instances = runtime_repo.list_instances(db)
    allowed = {int(employee.id) for employee in _company_employees(db, ctx)}
    rows = [
        {
            "instance_id": int(row.id),
            "employee_id": row.employee_id,
            "runtime_type": row.runtime_type,
            "deployment_mode": row.deployment_mode,
            "status": row.status,
            "health_status": row.health_status,
            "image": row.image,
            "runtime_version": row.runtime_version,
        }
        for row in instances
        if row.employee_id is not None and int(row.employee_id) in allowed
    ]
    return {"runtimes": rows, "count": len(rows)}


def _search_company_knowledge(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    query = str(args.get("query") or "")
    limit = int(args.get("limit", 10))
    tokens = {term for term in query.lower().split() if term}
    base = []
    for scope in (KnowledgeScope.department.value, KnowledgeScope.company.value):
        base += knowledge_repo.list_knowledge_items(db, scope=scope, company_id=ctx.company_id)
    active = [item for item in base if item.status == KnowledgeStatus.active.value]

    hit_ids = knowledge_repo.fts_match_ids(db, tokens) if tokens else None
    if tokens and hit_ids is not None:
        matches = [item for item in active if int(item.id) in hit_ids]
    elif tokens:
        matches = [
            item
            for item in active
            if any(token in f"{item.title} {item.topic} {item.content}".lower() for token in tokens)
        ]
    else:
        matches = active
    return {
        "query": query,
        "matches": [
            {
                "knowledge_item_id": int(item.id),
                "title": item.title,
                "topic": item.topic,
                "scope": item.scope,
                "confidence": item.confidence,
                "freshness_status": item.freshness_status,
                "content_excerpt": (item.content or "")[:400],
            }
            for item in matches[:limit]
        ],
        "total_candidates": len(matches),
        "searched_scopes": [KnowledgeScope.department.value, KnowledgeScope.company.value],
    }


def _inspect_artifact(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    node_id = int(args["artifact_id"])
    node = drive_repo.get_node(db, node_id)
    if node is None or (node.company_id is not None and int(node.company_id) != ctx.company_id):
        raise tools.ToolError("artifact not found in this company")
    revision = drive_repo.get_revision(db, node.id, node.current_version)
    return {
        "artifact_id": int(node.id),
        "name": node.name,
        "kind": node.kind,
        "zone": node.zone,
        "doc_type": node.doc_type,
        "project_id": node.project_id,
        "current_version": int(node.current_version),
        "sha256": revision.sha256 if revision else None,
        "content": (drive_service.read_content(node) or "")[:4000],
    }


def _inspect_role_context(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """把自己（或指定同事）的履职上下文摊开 —— 复用 M2.2 的唯一投影。"""
    employee = _employee_or_none(db, ctx, int(args.get("employee_id") or ctx.employee_id))
    context = role_context_service.build_role_context(db, employee)
    resources = role_context_service.resolve_role_resources(db, employee)
    return {
        "employee_id": context.employee_id,
        "person_id": context.person_id,
        "position_code": context.position_code,
        "responsibilities": list(context.responsibilities),
        "authority": [
            {
                "kind": grant.kind.value,
                "scope_kind": grant.scope_kind.value,
                "scope_ref": grant.scope_ref,
                "max_amount": grant.max_amount,
                "grant_id": grant.grant_id,
            }
            for grant in context.authority
        ],
        "expectations": [
            {
                "competency_code": item.competency_code,
                "requirement_type": item.requirement_type,
                "critical": item.critical,
            }
            for item in context.expectations
        ],
        "advisory_scope": list(context.advisory_scope),
        "direct_reports": list(context.direct_reports),
        "resources": [
            {
                "kind": item.kind.value,
                "ref": item.ref,
                "resolution": item.resolution.value,
                "pointer": item.pointer,
            }
            for item in resources
        ],
    }


def _inspect_work_intake(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """Work Intake 责任当前解析到谁（复用 M2.1 的唯一路由解析）。"""
    company = org_repo.get_company(db, ctx.company_id)
    if company is None:  # pragma: no cover - 防御
        raise tools.ToolError("company not found")
    resolution = work_intake.resolve_work_intake(db, company)
    return {
        "responsibility": resolution.responsibility.value,
        "status": resolution.status.value,
        "position_code": resolution.configured_position_code,
        "is_configured": bool(resolution.is_configured),
        "employee_id": resolution.employee_id,
        "person_id": resolution.person_id,
        "candidate_employee_ids": list(resolution.candidate_employee_ids),
        "owner_user_id": resolution.owner_user_id,
        "reason": resolution.reason,
    }


# ---------------------------------------------------------------------------
# 注册表条目
# ---------------------------------------------------------------------------

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}


def _list_task_artifacts(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """与 HTTP 读面**同一个**查询层（T2/T11）：`handoff.task_artifact_report()`。"""
    from app.work import handoff

    task_id = int(args["task_id"])
    task = project_repo.get_task(db, task_id)
    if task is None:
        raise tools.ToolError("task not found")
    project = project_repo.get_project(db, int(task.project_id))
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("task not found in this company")
    return handoff.task_artifact_report(db, task_id).as_dict()


def _inspect_task_review(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """与 HTTP 读面**同一个**查询层（T2/T11）：`reviews.review_view()`。"""
    from app.work import reviews

    task_id = int(args["task_id"])
    task = project_repo.get_task(db, task_id)
    if task is None:
        raise tools.ToolError("task not found")
    project = project_repo.get_project(db, int(task.project_id))
    if project is None or int(project.company_id) != ctx.company_id:
        raise tools.ToolError("task not found in this company")
    request = reviews.latest_request_for_task(db, task_id)
    return {
        "task_id": task_id,
        "task_status": str(task.status),
        "rework_count": int(task.rework_count or 0),
        "verdict_targets": dict(C.REVIEW_VERDICT_TARGETS),
        "review": reviews.review_view(db, request).as_dict() if request else None,
    }


def _inspect_readiness(db: Session, ctx: tools.ToolCallContext, args: dict) -> dict:
    """与 HTTP 读面**同一个**查询层（T2/T11）：`readiness.readiness_report()`。"""
    from app.work import readiness

    employee = org_repo.get_employee(db, int(args["employee_id"]))
    if employee is None or int(employee.company_id or 0) != ctx.company_id:
        raise tools.ToolError("employee not found in this company")
    return readiness.readiness_report(db, employee).as_dict()


def build_read_tools() -> tuple[tools.ToolSpec, ...]:
    """读工具清单（**共享能力**：同一批事实也由既有领域读面服务 UI/CLI）。"""
    return (
        tools.ToolSpec(
            name="list_company_projects",
            description="列出本公司在办项目（状态、工作模式、管理归属、任务数）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({}),
            output_schema=tools.object_schema({"projects": {"type": "array"}, "total": _INT}),
            handler=_list_company_projects,
        ),
        tools.ToolSpec(
            name="inspect_project",
            description="读一个项目的完整事实：Canonical Spec、任务图、Artifact、执行状态",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"project_id": _INT}, ("project_id",)),
            output_schema=tools.object_schema({"project": {"type": "object"}}),
            handler=_inspect_project,
        ),
        tools.ToolSpec(
            name="list_company_people",
            description="按状态/部门/职位/关键词列出本公司人员（名册读面）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema(
                {
                    "statuses": {"type": "array", "items": _STR},
                    "department_id": _INT,
                    "position_code": _STR,
                    "search": _STR,
                    "limit": _INT,
                    "offset": _INT,
                }
            ),
            output_schema=tools.object_schema({"items": {"type": "array"}}),
            handler=_list_company_people,
        ),
        tools.ToolSpec(
            name="inspect_person",
            description="读一个人（person 口径）的档案：身份、人格、能力、知识摘要",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"person_id": _INT}, ("person_id",)),
            output_schema=tools.object_schema({"identity": {"type": "object"}}),
            handler=_inspect_person,
        ),
        tools.ToolSpec(
            name="inspect_position",
            description="读职位定义与编制（含 advisory_scope 与空缺）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema(
                {"position_code": _STR, "position_definition_id": _INT}
            ),
            output_schema=tools.object_schema({"positions": {"type": "array"}}),
            handler=_inspect_position,
        ),
        tools.ToolSpec(
            name="inspect_assignments",
            description="读某人的任职时间轴（含历史，权威为 employments）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"employee_id": _INT}),
            output_schema=tools.object_schema({"assignments": {"type": "array"}}),
            handler=_inspect_assignments,
        ),
        tools.ToolSpec(
            name="inspect_role_context",
            description="读履职上下文：职责 / 生效授权 / 期望引用 / 资源指针 / 汇报下线",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"employee_id": _INT}),
            output_schema=tools.object_schema({"authority": {"type": "array"}}),
            handler=_inspect_role_context,
        ),
        tools.ToolSpec(
            name="inspect_work_intake",
            description="Work Intake 责任当前解析到哪个职位/哪个人（没解析到就如实说）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({}),
            output_schema=tools.object_schema({"status": _STR}),
            handler=_inspect_work_intake,
        ),
        tools.ToolSpec(
            name="get_competencies",
            description="读某人的能力行（score 与 confidence 并列；未评估 = null）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"person_id": _INT, "employee_id": _INT}),
            output_schema=tools.object_schema({"competencies": {"type": "object"}}),
            handler=_get_competencies,
        ),
        tools.ToolSpec(
            name="get_evidence",
            description="读某人的能力证据（可下钻到来源：任务/评审/技能使用/学习）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema(
                {
                    "person_id": _INT,
                    "employee_id": _INT,
                    "limit": _INT,
                    "offset": _INT,
                    "source_type": _STR,
                    "competency": _STR,
                }
            ),
            output_schema=tools.object_schema({"evidence": {"type": "array"}}),
            handler=_get_evidence,
        ),
        tools.ToolSpec(
            name="calculate_task_fit",
            description=(
                "任务 × 候选人的**事实**对照：每人逐项已知/未知 + 已知加权均值。"
                "不含排名与推荐（选人是管理 Agent 的决定）"
            ),
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema(
                {"task_id": _INT, "candidate_employee_ids": {"type": "array", "items": _INT}},
                ("task_id",),
            ),
            output_schema=tools.object_schema(
                {"candidates": {"type": "array"}, "result_kind": _STR}
            ),
            handler=_calculate_task_fit,
        ),
        tools.ToolSpec(
            name="get_current_load",
            description="读每人当前负载：在办任务数、运行中的会话、当前任务",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"employee_ids": {"type": "array", "items": _INT}}),
            output_schema=tools.object_schema({"load": {"type": "array"}}),
            handler=_get_current_load,
        ),
        tools.ToolSpec(
            name="inspect_readiness",
            description=("读一个人的就绪事实（职位 / 工作区 / 运行时 / 供应商）+ 缺口（W31）"),
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"employee_id": _INT}, ("employee_id",)),
            output_schema=tools.object_schema({"employee_id": _INT}),
            handler=_inspect_readiness,
        ),
        tools.ToolSpec(
            name="get_runtime_status",
            description="读本公司 Agent 的运行时实例状态（不健康/崩溃要看得见）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({}),
            output_schema=tools.object_schema({"runtimes": {"type": "array"}}),
            handler=_get_runtime_status,
        ),
        tools.ToolSpec(
            name="search_company_knowledge",
            description="检索公司/部门知识（K1/K2 同一套 scope 与 FTS 口径）",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"query": _STR, "limit": _INT}),
            output_schema=tools.object_schema({"matches": {"type": "array"}}),
            handler=_search_company_knowledge,
        ),
        tools.ToolSpec(
            name="inspect_task_review",
            description="读一个任务的评审：请求 / 事实（系统收集）/ 结论 / 返工次数",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"task_id": _INT}, ("task_id",)),
            output_schema=tools.object_schema({"task_id": _INT}),
            handler=_inspect_task_review,
        ),
        tools.ToolSpec(
            name="list_task_artifacts",
            description=("读一个任务的交付物全景：产出了什么 / 用了谁的产品 / 上游链（lineage）"),
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"task_id": _INT}, ("task_id",)),
            output_schema=tools.object_schema({"task_id": _INT}),
            handler=_list_task_artifacts,
        ),
        tools.ToolSpec(
            name="inspect_artifact",
            description="读一个交付物（Drive 文档）：版本、sha256、内容摘录",
            side_effect=C.ToolSideEffect.read,
            input_schema=tools.object_schema({"artifact_id": _INT}, ("artifact_id",)),
            output_schema=tools.object_schema({"artifact_id": _INT}),
            handler=_inspect_artifact,
        ),
    )


__all__ = ["build_read_tools", "OPEN_TASK_STATUSES"]
