"""Project services: 立项（Canonical Executable Project，M2.1）与读模型。

**唯一立项入口**是 `create_project()`（B2）：路由由两个**显式**维度决定 ——
`work_mode`（产品：guided | managed）与 `planning_fixture`（基础设施：none |
确定模板）。`is_structured` 已退役为兼容谓词，不再影响任何服务端分支。

三条不能破的纪律（设计 §1 / W32–W36）：

1. **系统不替公司规划**：managed 路径只把 Project Context 交给 Work Intake 责任人；
   找不到责任人 ⇒ `waiting_for_management` + 提示 Owner，绝不用模板顶上；
2. **fixture 只能显式请求且受部署门控**，生产项目不会隐式落到确定性模板；
3. **`work_mode` 在创建时快照**，公司默认值之后变化不改写既有项目。
"""

from collections import defaultdict
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import (
    EmployeeRole,
    MilestoneStatus,
    PlanningFixture,
    ProjectStatus,
    ProjectWorkMode,
    ResponsibilityKind,
    TaskKind,
    TaskStatus,
)
from app.models.project import Milestone, Project, Task
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project import (
    GraphEdge,
    GraphNode,
    MilestoneOut,
    ProjectCreate,
    ProjectDetail,
    ProjectGraph,
    ProjectOut,
    ProjectTimeline,
)
from app.services import artifacts as artifact_service
from app.services import drive as drive_service
from app.services import position_compat
from app.services import tasks as task_service
from app.work import contracts, planning_fixture, work_defaults, work_intake

#: 未指定 deadline 时的默认计划窗口（天）。M2.1 起 guided / managed 共用它，
#: 保证"只传 name/description"的老客户端在 schedule 字段上逐字段兼容（B4）。
DEFAULT_PLANNING_WINDOW_DAYS = 18


def create_project(db: Session, payload: ProjectCreate) -> Project:
    """`POST /projects` —— **唯一**立项入口（M2.1，B2 / M2-ADR-11/12）。

    路由由两个**显式**维度决定，不再由 `is_structured` 隐式分叉：

    ```text
    planning_fixture = deterministic_template   → 基础设施项目（确定性执行图）
    work_mode       = guided                    → 引导/协助形态（11 阶段 + 人工评审门）
    work_mode       = managed                   → 路由给 Work Intake 责任持有者（见下）
    ```

    三条纪律：
    1. **系统不替公司规划**（W34）：managed 路径只把 Project Context 交给负责人；
       找不到负责人就停在 `waiting_for_management`，绝不用模板顶上；
    2. **fixture 只能显式请求且受部署门控**（W33）；
    3. **work_mode 在此刻快照**（W35）：公司默认值之后怎么变都不改写本行。
    """
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    try:
        choice = work_defaults.resolve_execution_plan(
            db, company, payload.work_mode, payload.planning_fixture
        )
    except work_defaults.WorkPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_owner(db, company, payload.owner_id)

    if choice.planning_fixture is PlanningFixture.deterministic_template:
        return _create_fixture_project(db, payload, company, choice)
    if choice.work_mode is ProjectWorkMode.guided:
        return _create_guided_project(db, payload, company, choice)
    return _create_managed_project(db, payload, company, choice)


def create_order(db: Session, payload: ProjectCreate) -> Project:
    """**退役别名**（M2.1，B2）：历史调用点的语义就是"立项"，直接转调 `create_project`。

    保留它是为了不在同一阶段同时改 API 路由、测试与外部调用点；它**不再**包含
    任何 `is_structured` 分叉逻辑。
    """
    return create_project(db, payload)


# ---------------------------------------------------------------------------
# M2.1 内部：Canonical Spec 构造与三条创建路径
# ---------------------------------------------------------------------------


def _validate_owner(db: Session, company, owner_id: int | None) -> None:
    if owner_id is None:
        return
    owner = org_repo.get_employee(db, owner_id)
    if owner is None or int(owner.company_id) != int(company.id):
        raise HTTPException(status_code=422, detail="project owner does not belong to company")


def _resolve_code(db: Session, company, payload: ProjectCreate, *, required: bool) -> str | None:
    raw = (payload.code or "").strip()
    if not raw and not required:
        return None
    code = (raw or _derive_code(payload.name)).upper()
    existing = next(
        (p for p in project_repo.list_projects(db, company.id) if (p.code or "").upper() == code),
        None,
    )
    if existing:
        raise HTTPException(status_code=409, detail="project code already exists")
    return code


def _canonical_fields(
    payload: ProjectCreate,
    *,
    company_id: int,
    choice: work_defaults.ExecutionPlanChoice,
    code: str | None,
    status: str,
    owner_id: int | None,
    schedule_start,
    schedule_end,
) -> dict:
    """Canonical Spec 的**唯一**写入处（设计 §11.4）。

    两种产品模式与 fixture 项目共用同一份字段构造 —— 这是 B3
    （"guided 与 managed 共用同一 projects 行与同一 tasks 表"）的实际体现：
    差异只在**后续步骤**（仪式 / 路由 / 确定性模板），不在**事实的写法**。
    """
    return {
        "company_id": int(company_id),
        "name": payload.name.strip(),
        "description": payload.description,
        "goal": "\n".join(payload.objectives) or payload.goal,
        "status": status,
        "owner_id": owner_id,
        # 旧客户端只给 description 时，它同时充当背景与订单原文（逐字段兼容，B4）
        "source_order_text": payload.background or payload.description,
        "planned_start_at": schedule_start,
        "planned_end_at": schedule_end,
        "code": code,
        "priority": payload.priority,
        "customer": payload.customer,
        "background": payload.background,
        "objectives": list(payload.objectives),
        "technical_requirements": list(payload.technical_requirements),
        "constraints": list(payload.constraints),
        "deliverables": list(payload.deliverables),
        "review_configuration": payload.review_configuration.model_dump(),
        "participants": payload.participants.model_dump(),
        "tutorial_accelerated": payload.tutorial_accelerated,
        "work_mode": choice.work_mode.value,
        "planning_fixture": choice.planning_fixture.value,
        "spec_version": contracts.PROJECT_SPEC_VERSION,
    }


def _derive_code(name: str) -> str:
    """从项目名派生一个稳定的机器码（M2.1：guided 路径仍需 code 作为对外标识）。

    与 `project_delivery._derive_code` 同义，但保留在本模块以免新建依赖环：
    guarded 路径先于交付域 import，而交付域反过来依赖本项目服务。
    """
    cleaned = "".join(ch.upper() if ch.isalnum() else "-" for ch in name.strip())
    compact = "-".join(part for part in cleaned.split("-") if part)
    return (compact or "PROJECT")[:50]


def _store_project(db: Session, company, fields: dict) -> Project:
    """建行 + 项目 Drive 目录树（v0.3 起每个项目都有 drive/projects/{slug}/...）。"""
    project = project_repo.create_project(db, **fields)
    db.flush()
    drive_service.ensure_project_folders(db, project)
    return project


def _create_fixture_project(
    db: Session, payload: ProjectCreate, company, choice: work_defaults.ExecutionPlanChoice
) -> Project:
    """**基础设施项目**（D3/W33）：用确定性模板替掉 Manager Agent 的规划。

    它是测试/教程/CI/演示的载具，因此刻意**不走** guided 仪式（11 阶段与文档
    本身就是规划流程的产物，而这里规划被替身取代了）。

    M2.5 起：**整张图在立项时就建好**（`app/work/planning_fixture.py`），
    然后与生产项目走**完全相同的**调度路径 —— 就绪判定 / 可派发 / WorkSession / Runtime
    都由 `Orchestrator` + `app/work/dispatch.py` 负责。fixture 与生产的唯一区别是
    **谁创建了这张图**（R7）。
    """
    pm = position_compat.employee_by_legacy_role(db, company.id, EmployeeRole.product_manager.value)
    schedule_start = utcnow()
    project = _store_project(
        db,
        company,
        _canonical_fields(
            payload,
            company_id=int(company.id),
            choice=choice,
            code=_resolve_code(db, company, payload, required=False),
            status=ProjectStatus.in_progress.value,
            owner_id=pm.id if pm else None,
            schedule_start=schedule_start,
            schedule_end=schedule_start + timedelta(days=DEFAULT_PLANNING_WINDOW_DAYS),
        ),
    )
    tasks = planning_fixture.build_deterministic_plan(db, project)
    db.commit()
    db.refresh(project)
    bus.publish(
        "project.created",
        {"id": project.id, "name": project.name, "status": project.status},
        company_id=int(company.id),
        project_id=project.id,
    )
    bus.publish(
        "project.started",
        {"id": project.id, "name": project.name, "fixture": planning_fixture.__name__},
        company_id=int(company.id),
        project_id=project.id,
    )
    for task in tasks:
        task_service.publish_task_created(task, int(company.id))
    from app.workflow.orchestrator import orchestrator

    orchestrator.notify({"type": "dispatch"})
    return project


def _project_timeline(
    project: Project, milestones: list[Milestone], tasks: list[Task]
) -> ProjectTimeline:
    milestone_outputs = []
    for milestone in milestones:
        milestone_tasks = [task for task in tasks if task.milestone_id == milestone.id]
        output = MilestoneOut.model_validate(milestone)
        if milestone_tasks and all(
            task.status == TaskStatus.done.value for task in milestone_tasks
        ):
            output.status = MilestoneStatus.completed.value
        elif any(
            task.status
            in {
                TaskStatus.todo.value,
                TaskStatus.in_progress.value,
                TaskStatus.in_review.value,
            }
            for task in milestone_tasks
        ):
            output.status = MilestoneStatus.in_progress.value
        milestone_outputs.append(output)
    return ProjectTimeline(
        **ProjectOut.model_validate(project).model_dump(),
        milestones=milestone_outputs,
        tasks=[task_service.task_out(t) for t in tasks],
    )


def get_project_portfolio(db: Session) -> list[ProjectTimeline]:
    projects = project_repo.list_projects(db)
    project_ids = [project.id for project in projects]
    milestones = project_repo.list_milestones_for_projects(db, project_ids)
    tasks = project_repo.list_tasks_for_projects(db, project_ids)
    milestones_by_project: dict[int, list[Milestone]] = defaultdict(list)
    tasks_by_project: dict[int, list[Task]] = defaultdict(list)
    for milestone in milestones:
        milestones_by_project[milestone.project_id].append(milestone)
    for task in tasks:
        tasks_by_project[task.project_id].append(task)
    return [
        _project_timeline(
            project,
            milestones_by_project[project.id],
            tasks_by_project[project.id],
        )
        for project in projects
    ]


def get_project_detail(db: Session, project: Project) -> ProjectDetail:
    tasks = project_repo.list_tasks(db, project.id)
    milestones = project_repo.list_milestones(db, project.id)
    timeline = _project_timeline(project, milestones, tasks)
    # v0.3: artifacts are a typed view over the project's drive documents
    artifacts = artifact_service.list_artifact_nodes(db, project_id=project.id)
    return ProjectDetail(
        **timeline.model_dump(),
        artifacts=[artifact_service.artifact_out(db, a) for a in artifacts],
    )


def get_project_graph(db: Session, project: Project) -> ProjectGraph:
    tasks = project_repo.list_tasks(db, project.id)
    dependencies = project_repo.list_dependencies(db, project.id)
    return ProjectGraph(
        nodes=[
            GraphNode(
                id=str(t.id),
                type=t.kind,
                label=f"#EID-{t.id} {t.title}",
                status=t.status,
            )
            for t in tasks
        ],
        edges=[
            GraphEdge(source=str(dep.depends_on_id), target=str(dep.task_id))
            for dep in dependencies
        ],
    )


def _create_guided_project(
    db: Session, payload: ProjectCreate, company, choice: work_defaults.ExecutionPlanChoice
) -> Project:
    """**guided（教学/协助）形态**（D2，W36）。

    差别只有 human involvement：Manager Agent 仍然自主决策，但关键动作要人类确认与讲解。
    它**不是**"系统替 CEO 规划" —— 11 阶段与评审门是**交付仪式**（人类确认点），
    不是系统生成的执行计划。
    """
    schedule_start = utcnow()
    project = _store_project(
        db,
        company,
        _canonical_fields(
            payload,
            company_id=int(company.id),
            choice=choice,
            code=_resolve_code(db, company, payload, required=True),
            status=ProjectStatus.in_progress.value,
            owner_id=payload.owner_id or payload.participants.project_owner_employee_id,
            schedule_start=schedule_start,
            # B4：不传 deadline 时沿用历史默认计划窗口（18 天），
            # 这样"只传 name/description"的老客户端拿到的 schedule 字段与 M2.1 之前一致。
            schedule_end=payload.deadline
            or schedule_start + timedelta(days=DEFAULT_PLANNING_WINDOW_DAYS),
        ),
    )
    from app.services import project_delivery as delivery_service

    return delivery_service.create_guided_project_body(db, payload, project)


def _create_managed_project(
    db: Session, payload: ProjectCreate, company, choice: work_defaults.ExecutionPlanChoice
) -> Project:
    """**managed 形态**（D1/B11，W32 / W34）。

    系统在这里做**两件事**，都只是"把工作交给该负责的人"：

    1. 解析 Work Intake 责任（公司可配职位 → 在任者）；
    2. 把 Project Context 交给那位 Manager Agent，请它决定是否接受、如何组织。

    系统**不做**：拆解、选人、判断优先级。
    找不到负责人时进 `waiting_for_management` 并**停止** —— 不用模板顶上，也不随便挑人。
    """
    schedule_start = utcnow()
    project = _store_project(
        db,
        company,
        _canonical_fields(
            payload,
            company_id=int(company.id),
            choice=choice,
            code=_resolve_code(db, company, payload, required=False),
            status=ProjectStatus.requested.value,
            owner_id=payload.owner_id,
            schedule_start=schedule_start,
            schedule_end=payload.deadline
            or schedule_start + timedelta(days=DEFAULT_PLANNING_WINDOW_DAYS),
        ),
    )
    project.work_intake_position_code = work_intake.configured_position_code(
        company, ResponsibilityKind.work_intake
    )
    resolution = work_intake.resolve_work_intake(db, company)

    if not resolution.is_routed:
        # W34：没有任何人被授权/在位来接收它 —— 如实停在等待，交给 Owner 处理。
        project.status = ProjectStatus.waiting_for_management.value
        db.commit()
        db.refresh(project)
        bus.publish(
            "project.created",
            {"id": project.id, "name": project.name, "status": project.status},
            company_id=int(company.id),
            project_id=project.id,
        )
        bus.publish(
            "project.waiting_for_management",
            {
                "id": project.id,
                "name": project.name,
                "work_intake_status": resolution.status.value,
                "position_code": resolution.configured_position_code,
                "owner_user_id": resolution.owner_user_id,
                "reason": resolution.reason,
            },
            company_id=int(company.id),
            project_id=project.id,
        )
        return project

    project.management_employee_id = resolution.employee_id
    project.management_person_id = resolution.person_id
    project.management_assigned_at = utcnow()
    task = task_service.create_task(
        db,
        project_id=project.id,
        title=f"工作接收：{project.name}",
        kind=TaskKind.order_review.value,
        assignee_id=resolution.employee_id,
        status=TaskStatus.todo.value,
        description=_project_brief(db, project),
        acceptance_criteria=(
            "决定是否接受本项目，以及如何组织（自行规划 / 委派 / 暂缓），"
            "并说明依据。系统不代为决定。"
        ),
        priority=10,
        sequence=0,
        planned_start_at=schedule_start,
        planned_end_at=schedule_start + timedelta(days=1),
    )
    db.commit()
    db.refresh(project)
    bus.publish(
        "project.created",
        {"id": project.id, "name": project.name, "status": project.status},
        company_id=int(company.id),
        project_id=project.id,
    )
    bus.publish(
        "project.management_assigned",
        {
            "id": project.id,
            "employee_id": resolution.employee_id,
            "person_id": resolution.person_id,
            "position_code": resolution.configured_position_code,
            "work_intake_status": resolution.status.value,
        },
        company_id=int(company.id),
        project_id=project.id,
        actor_employee_id=resolution.employee_id,
    )
    task_service.publish_task_created(task, int(company.id))
    from app.workflow.orchestrator import orchestrator

    orchestrator.notify({"type": "dispatch"})
    return project


def _project_brief(db: Session, project: Project) -> str:
    """把 Canonical Spec 渲染成给 Manager Agent 的**事实简报**（设计 §11.5）。

    刻意只列事实：背景 / 目标 / 需求 / 约束 / 交付物 / 验收 / 优先级 / 截止。
    **不含**任何"建议怎么拆""建议选谁" —— 那是 Agent 的判断（W1/W2）。
    """
    requirements = delivery_repo.list_requirements(db, project.id)
    lines = [f"# 项目简报：{project.name}", ""]
    if project.background:
        lines += ["## 背景", project.background, ""]
    if project.goal:
        lines += ["## 目标", project.goal, ""]
    if requirements:
        lines.append("## 需求")
        for row in requirements:
            code = row.code or "-"
            lines.append(f"- [{code}] {row.title}（{row.priority}）")
            if row.acceptance_criteria:
                lines.append(f"  验收：{row.acceptance_criteria}")
        lines.append("")
    elif project.source_order_text:
        lines += ["## 原始需求", project.source_order_text, ""]
    if project.constraints:
        lines += ["## 约束"] + [f"- {item}" for item in project.constraints] + [""]
    if project.deliverables:
        lines += ["## 交付物"] + [f"- {item}" for item in project.deliverables] + [""]
    lines += [
        "## 事实",
        f"- 优先级：{project.priority}",
        f"- 计划截止：{project.planned_end_at.isoformat() if project.planned_end_at else '未指定'}",
        f"- 工作模式：{project.work_mode or '未分类（历史项目）'}",
        "",
        "请决定：是否接受、如何组织（自行规划 / 委派给其他管理职位 / 暂缓）。",
    ]
    return "\n".join(lines)


def get_project_spec(db: Session, project: Project) -> dict:
    """`GET /projects/{id}/spec` —— Canonical Spec 与那 8 个问题的**唯一**答复处。

    设计 §11.5：Project 必须能回答
    ``canonical spec / work_mode / Work Intake 责任 / 承担它的任职 / 管理 actor /
    requirements+deliverables+acceptance / spec 版本 / 是否进入执行``。

    这是一份**纯读模型**：不落库、不写入、不改项目状态。它把事实摊开，不做建议 ——
    "这份 spec 该不该接""该不该拆"仍然由 Manager Agent 回答（W1/W2）。
    """
    company = org_repo.get_company(db, int(project.company_id))
    requirements = delivery_repo.list_requirements(db, project.id)
    tasks = project_repo.list_tasks(db, project.id)
    phases = delivery_repo.list_phases(db, project.id)
    artifacts = artifact_service.list_artifact_nodes(db, project_id=project.id)

    spec = {
        "background": project.background,
        "goal": project.goal,
        "requirements": [
            {
                "code": row.code or f"REQ-{index:03d}",
                "title": row.title,
                "priority": row.priority,
                "acceptance_criteria": row.acceptance_criteria or "",
            }
            for index, row in enumerate(requirements, start=1)
        ],
        # 没有逐条需求时，验收标准只能来自项目级配置 —— 如实报空，不编造
        "acceptance_criteria": [
            row.acceptance_criteria for row in requirements if row.acceptance_criteria
        ],
        "constraints": list(project.constraints or []),
        "deliverables": list(project.deliverables or []),
        "priority": project.priority,
        "deadline": project.planned_end_at,
        "context": project.description,
    }
    missing = contracts.canonical_spec_gaps(spec)

    resolution = (
        work_intake.resolve_work_intake(db, company)
        if company is not None
        else None  # pragma: no cover - 项目必有公司
    )
    status_counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        status_counts[task.status] += 1

    responsible_employee_id = resolution.employee_id if resolution is not None else None
    management_stale = (
        project.management_employee_id is not None
        and responsible_employee_id is not None
        and int(project.management_employee_id) != int(responsible_employee_id)
    )
    management_holder = (
        org_repo.get_employee(db, resolution.employee_id)
        if resolution is not None and resolution.employee_id is not None
        else None
    )
    management_position = (
        position_compat.derived_current_position(db, management_holder)
        if management_holder is not None
        else None
    )

    return {
        "project_id": int(project.id),
        "spec_version": int(project.spec_version or contracts.PROJECT_SPEC_VERSION),
        "spec": spec,
        "completeness": {
            "is_complete": not missing,
            "missing": list(missing),
            "optional_fields": sorted(contracts.SPEC_OPTIONAL_FIELDS),
        },
        "work_mode": project.work_mode,
        "planning_fixture": project.planning_fixture,
        "work_intake": {
            "responsibility": ResponsibilityKind.work_intake.value,
            "status": resolution.status.value if resolution else "no_position",
            "position_code": resolution.configured_position_code if resolution else "",
            "default_position_code": contracts.RESPONSIBILITY_DEFAULTS[
                ResponsibilityKind.work_intake
            ],
            "is_configured": bool(resolution.is_configured) if resolution else False,
            "position_definition_id": resolution.position_definition_id if resolution else None,
            "assignment": (
                {
                    "employee_id": resolution.employee_id,
                    "person_id": resolution.person_id,
                    "slot_id": resolution.slot_id,
                    "since": None,
                }
                if resolution is not None and resolution.employee_id is not None
                else None
            ),
            "candidate_employee_ids": list(resolution.candidate_employee_ids) if resolution else [],
            "owner_user_id": resolution.owner_user_id if resolution else None,
            "reason": resolution.reason if resolution else "",
        },
        "management": {
            "employee_id": project.management_employee_id,
            "person_id": project.management_person_id,
            "assigned_at": project.management_assigned_at,
            "position_code": (
                management_position.code if management_position is not None else None
            ),
            "position_definition_id": (
                management_position.definition_id if management_position is not None else None
            ),
            "current_responsible_employee_id": responsible_employee_id,
            # 快照与当前责任持有者不一致 = 只是"项目行还没被重新路由"，不是错误。
            # 历史归属由 DecisionRecord 承担（M2.4），这里只如实报告漂移。
            "stale": bool(management_stale),
        },
        "execution": {
            "entered": bool(tasks or phases),
            "task_count": len(tasks),
            "task_status_counts": dict(status_counts),
            "phase_count": len(phases),
            "artifact_count": len(artifacts),
            "planning_fixture": project.planning_fixture,
        },
        "questions": dict(contracts.PROJECT_SPEC_QUESTIONS),
    }
