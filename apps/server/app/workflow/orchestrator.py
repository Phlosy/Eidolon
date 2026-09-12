"""Event-driven workflow orchestrator (not a general-purpose engine).

See docs/architecture.md §5. Consumes dispatch signals, drives WorkSessions via
the RuntimeGateway, and advances the fixed order → planning → build → verify →
release pipeline. One running session per employee; queued tasks wait.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.brain import DEFAULT_POLICY
from app.brain import policy_for as behavior_policy_for
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.learning import reflection, retrieval
from app.models.enums import (
    EmployeeRole,
    EmployeeStatus,
    MilestoneStatus,
    PlanningFixture,
    ProjectStatus,
    TaskKind,
    TaskStatus,
    WorkSessionStatus,
)
from app.models.project import Milestone
from app.models.provider import ModelBinding
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import RuntimeEventKind, TaskContext
from app.runtimes.gateway import gateway
from app.services import artifacts as artifact_service
from app.services import position_compat
from app.services import tasks as task_service
from app.work import work_defaults

logger = get_logger(__name__)

#: 确定性模板执行图 —— **测试/教程/CI/演示基础设施，不是生产规划逻辑**（M2.1，D3/W33）。
#:
#: 它替掉的是 **Manager Agent 的规划**：让
#: ``Project → Task Graph → 执行 → Artifact → Review → Completed``
#: 这条链在不依赖 LLM Manager Agent 的前提下可重复、可断言、零成本。
#:
#: 两条硬纪律：
#: 1. **生产项目永远不会落到它头上** —— 没有"Manager 没反应 → 用模板顶上"；
#: 2. 只有 `projects.planning_fixture == deterministic_template` 的项目才会应用它，
#:    而这个值只可能由**显式请求 + 部署门控**（`settings.allow_planning_fixtures`）写入。
#:
#: 名字刻意长而白：任何开发者看到 `DETERMINISTIC_TEMPLATE_PLAN` 都应该立刻明白
#: 这不是公司自己的决策逻辑。
DETERMINISTIC_TEMPLATE_PLAN = [
    ("Discovery", TaskKind.research.value, EmployeeRole.researcher.value, []),
    ("Build", TaskKind.development.value, EmployeeRole.engineer.value, [TaskKind.research.value]),
    (
        "Verify",
        TaskKind.testing.value,
        EmployeeRole.qa_engineer.value,
        [TaskKind.development.value],
    ),
    ("Release", TaskKind.final_review.value, EmployeeRole.ceo.value, [TaskKind.testing.value]),
]


class Orchestrator:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}  # employee_id -> session task

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._runner = asyncio.create_task(self._run(), name="eidolon-orchestrator")

    async def stop(self) -> None:
        for task in self._running.values():
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)

    def notify(self, signal: dict) -> None:
        """Thread-safe signal submission from sync request handlers."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, signal)

    async def _run(self) -> None:
        while True:
            try:
                signal = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                signal = {"type": "sweep"}
            try:
                await self._handle(signal)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("orchestrator failed handling signal: %s", signal)

    async def _handle(self, signal: dict) -> None:
        if signal["type"] in ("dispatch", "sweep"):
            await self._dispatch_pending()
        elif signal["type"] == "task_finished":
            # manual completion via PATCH /tasks/{id}
            self._finalize_external(signal["task_id"], signal["success"])

    # ---- dispatcher ----

    async def _dispatch_pending(self) -> None:
        if not settings.orchestrator_dispatch_enabled:
            # 门控：后台写者不与调用方抢同一份 SQLite（见 Settings 的注释）。
            return
        with SessionLocal() as db:
            candidates = []
            for task in project_repo.list_tasks_by_status(db, TaskStatus.todo.value):
                if task.assignee_id is None or task.assignee_id in self._running:
                    continue
                employee = org_repo.get_employee(db, task.assignee_id)
                if employee is None or employee.status != EmployeeStatus.idle.value:
                    continue
                if project_repo.get_running_session_for_employee(db, employee.id):
                    continue
                candidates.append((task.id, employee.id))
        for task_id, employee_id in candidates:
            # invariant §3.4.2: one running WorkSession per employee
            self._running[employee_id] = asyncio.create_task(
                self._run_task(task_id), name=f"work-session-task-{task_id}"
            )

    async def _run_task(self, task_id: int) -> None:
        employee_id: int | None = None
        started = datetime.now(UTC)
        try:
            with SessionLocal() as db:
                task = project_repo.get_task(db, task_id)
                if task is None or task.status != TaskStatus.todo.value:
                    return
                employee = org_repo.get_employee(db, task.assignee_id)
                if employee is None or employee.status != EmployeeStatus.idle.value:
                    return
                employee_id = employee.id
                company_id = task.project.company_id

                old_status = employee.status
                employee.status = (
                    EmployeeStatus.researching.value
                    if task.kind == TaskKind.research.value
                    else EmployeeStatus.working.value
                )
                employee.current_task_id = task.id
                task_service.transition_task(db, task, TaskStatus.in_progress.value)
                # v0.2: link the work session to the runtime instance + provider/model
                runtime_instance = runtime_repo.get_instance_for_employee(db, employee.id)
                # BehaviorPolicy：全任务只解析一次，后续所有接缝共享同一个对象（§7 接缝 1）。
                policy = behavior_policy_for(db, employee.id, getattr(employee, "company", None))
                binding = provider_repo.get_primary_binding(db, employee.id) or (
                    db.get(ModelBinding, runtime_instance.model_binding_id)
                    if runtime_instance and runtime_instance.model_binding_id
                    else None
                )
                project_repo.create_work_session(
                    db,
                    task_id=task.id,
                    employee_id=employee.id,
                    runtime_type=employee.runtime_type,
                    status=WorkSessionStatus.running.value,
                    started_at=started,
                    runtime_instance_id=runtime_instance.id if runtime_instance else None,
                    provider_id=binding.provider_id if binding else None,
                    model=binding.model if binding else None,
                    # 本次任务实际生效的策略版本（§8.4）
                    cost={
                        "policy_version": policy.runtime.policy_version,
                        "behavior_revision": policy.runtime.profile_revision,
                    },
                )
                if task.milestone_id:
                    milestone = db.get(Milestone, task.milestone_id)
                    if milestone and milestone.status == MilestoneStatus.pending.value:
                        milestone.status = MilestoneStatus.in_progress.value
                db.commit()
                runtime_config = dict(employee.runtime_config or {})
                # v0.2: inject the assignee's own relevant learning into the task context
                # v1: 额度由 BehaviorPolicy 决定；解析/检索异常时本任务回落 DEFAULT_POLICY
                try:
                    result = retrieval.retrieve_for_task(
                        db, employee.id, task.title, task.description, policy=policy
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.warning(
                        "retrieval failed, falling back to DEFAULT_POLICY", exc_info=True
                    )
                    policy = DEFAULT_POLICY
                    result = retrieval.RetrievalResult()
                prior_knowledge, validated_skills = result.knowledge, result.skill_names
                validated_skill_refs = result.skills
                task_ctx = TaskContext(
                    task_id=task.id,
                    project_id=task.project_id,
                    title=task.title,
                    kind=task.kind,
                    description=task.description,
                    acceptance_criteria=task.acceptance_criteria,
                    employee_name=employee.name,
                    # 提示词里的身份说来自职位定义（派生），不是镜像列
                    employee_role=position_compat.legacy_role_of(db, employee),
                    prior_knowledge=prior_knowledge,
                    validated_skills=validated_skills,
                )
                employee_snapshot = employee

            bus.publish(
                "employee.status_changed",
                {"id": employee_id, "from": old_status, "to": employee.status},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_ctx.project_id,
                task_id=task_id,
            )
            bus.publish(
                "task.started",
                {"id": task_id, "title": task_ctx.title, "assignee_id": employee_id},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_ctx.project_id,
                task_id=task_id,
            )

            instance = await gateway.get_or_create_instance(employee_snapshot)
            adapter = gateway.adapter_for(employee_snapshot.runtime_type)
            session = await adapter.create_session(instance, task_ctx)
            with SessionLocal() as db:
                ws = project_repo.get_running_session_for_task(db, task_id)
                if ws:
                    ws.runtime_session_ref = session.id
                    # 接缝 9 / §10.3：技能被交出去的那一刻记基准（含策略原因 + 版本）。
                    # 已验证技能也记：“这个任务到底跑在哪些技能上”本身是事实，不是判断。
                    for skill in validated_skill_refs:
                        knowledge_repo.create_skill_usage(
                            db,
                            employee_id=employee_id,
                            skill_id=skill.id,
                            task_id=task_id,
                            work_session_id=ws.id,
                            skill_validation_status=skill.validation_status,
                            selection_reason=skill.reason,
                            policy_version=policy.runtime.policy_version,
                            profile_revision=policy.runtime.profile_revision,
                        )
                    db.commit()
            prompt = (
                f"任务：{task_ctx.title}\n\n{task_ctx.description}\n\n"
                f"验收标准：{task_ctx.acceptance_criteria or '按任务描述完成'}"
            )
            await adapter.send_task(
                session,
                prompt,
                {
                    "task_context": task_ctx,
                    "runtime_config": runtime_config,
                    # 接缝 7：载荷是策略的**纯函数**（adapter 侧只渲染，不再做判断）
                    "behavior_policy": policy.as_dict(),
                },
            )

            success, error = True, None
            async for event in adapter.stream_events(session):
                if event.kind == RuntimeEventKind.error:
                    success, error = False, str(event.data.get("message", "runtime error"))
                elif event.kind == RuntimeEventKind.completed:
                    break
            produced = await adapter.get_artifacts(session) if success else []
            duration = (datetime.now(UTC) - started).total_seconds()
            self._finalize(task_id, success, produced, error, duration)
        except asyncio.CancelledError:
            logger.info("work session cancelled", extra={"task_id": task_id})
            self._finalize(task_id, False, [], "cancelled", 0.0)
            raise
        except Exception as exc:
            logger.exception("work session crashed", extra={"task_id": task_id})
            self._finalize(task_id, False, [], f"internal error: {exc}", 0.0)
        finally:
            if employee_id is not None:
                self._running.pop(employee_id, None)

    # ---- finalization & reflection ----

    def _finalize(
        self, task_id: int, success: bool, produced: list, error: str | None, duration: float
    ) -> None:
        with SessionLocal() as db:
            task = project_repo.get_task(db, task_id)
            if task is None:
                return
            project = project_repo.get_project(db, task.project_id)
            employee = org_repo.get_employee(db, task.assignee_id) if task.assignee_id else None
            company_id = project.company_id if project else None

            ws = project_repo.get_running_session_for_task(db, task_id)
            if ws is not None:
                ws.status = (
                    WorkSessionStatus.completed.value if success else WorkSessionStatus.failed.value
                )
                ws.ended_at = datetime.now(UTC)
                ws.summary = error or f"产出 {len(produced)} 个交付物"
                ws.error = error
                ws.cost = {**(ws.cost or {}), "duration_sec": round(duration, 3), "tokens": 0}
                # M1.5：算力成本（Sink）—— **计量总是发生**，扣款尽力而为（余额不足记 unpaid）。
                # 放在这里的原因：会话结束才有时长这个事实；计量失败绝不能影响任务终态，
                # 所以只记录日志（unpaid/异常都会在成本报表与日志里暴露）。
                try:
                    from app.services.economy.costs import ComputeCostService

                    if company_id is not None:
                        ComputeCostService(db).record_for_session(
                            ws, company_id=company_id, model=ws.model or ""
                        )
                except Exception:
                    logger.exception("compute cost recording failed for work_session=%s", ws.id)
                # §10.2：success 是客观事实，与"人是否评价过"无关，都必须落库
                for usage in knowledge_repo.list_skill_usages_for_task(db, task_id):
                    usage.success = success

            artifact_ids = []
            # v0.3: artifacts are drive documents in the project folder
            for item in produced if project is not None else []:
                node = artifact_service.record_project_artifact(
                    db,
                    project,
                    artifact_type=item.type,
                    title=item.title,
                    content=item.content,
                    author_id=employee.id if employee else None,
                    work_session_id=ws.id if ws else None,
                )
                artifact_ids.append((node.id, node.doc_type, node.name))

            if success:
                task_service.transition_task(db, task, TaskStatus.in_review.value)
                task_service.transition_task(db, task, TaskStatus.done.value)
            else:
                task_service.transition_task(db, task, TaskStatus.failed.value)
            if employee is not None:
                employee.status = EmployeeStatus.idle.value
                employee.current_task_id = None
            employee_id = employee.id if employee else None
            task_title, task_kind = task.title, task.kind
            db.commit()

        for artifact_id, artifact_type, artifact_title in artifact_ids:
            bus.publish(
                "artifact.created",
                {"id": artifact_id, "type": artifact_type, "title": artifact_title},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task.project_id,
                task_id=task_id,
            )
        bus.publish(
            "task.completed" if success else "task.failed",
            {"id": task_id, "title": task_title, "kind": task_kind, "error": error},
            company_id=company_id,
            actor_employee_id=employee_id,
            project_id=task.project_id,
            task_id=task_id,
        )
        if employee_id is not None:
            bus.publish(
                "employee.status_changed",
                {"id": employee_id, "to": EmployeeStatus.idle.value},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task.project_id,
                task_id=task_id,
            )
            reflection.reflect(task_id, employee_id, success, duration, error)
        self._advance(task_id, success)

    def _finalize_external(self, task_id: int, success: bool) -> None:
        """Manual PATCH-driven completion: reflection + workflow advance, no artifacts."""
        with SessionLocal() as db:
            task = project_repo.get_task(db, task_id)
            if task is None:
                return
            employee_id = task.assignee_id
        if employee_id is not None:
            reflection.reflect(task_id, employee_id, success, 0.0, None if success else "manual")
        self._advance(task_id, success)

    # ---- workflow advance (§5) ----

    def _advance(self, task_id: int, success: bool) -> None:
        with SessionLocal() as db:
            task = project_repo.get_task(db, task_id)
            if task is None:
                return
            project = project_repo.get_project(db, task.project_id)
            company_id = project.company_id
            events: list[tuple[str, dict]] = []

            if not success:
                if task.kind == TaskKind.testing.value:
                    # QA rejected → development 任务回到 todo（重做）
                    dev_task = next(
                        (
                            t
                            for t in project_repo.list_tasks(db, project.id)
                            if t.kind == TaskKind.development.value
                        ),
                        None,
                    )
                    if dev_task is not None:
                        task_service.transition_task(
                            db, dev_task, TaskStatus.todo.value, force=True
                        )
                        events.append(
                            (
                                "task.assigned",
                                {"id": dev_task.id, "title": dev_task.title, "rework": True},
                            )
                        )
                db.commit()
                self._publish_advance_events(events, company_id, project.id)
                self.notify({"type": "dispatch"})
                return

            if task.kind == TaskKind.order_review.value:
                project.status = ProjectStatus.planning.value
                if self._uses_deterministic_plan(project):
                    # 立项后接手的 PM：先问"谁占着 PM 编制"，没人任职才回退旧列镜像
                    pm = position_compat.employee_by_legacy_role(
                        db, company_id, EmployeeRole.product_manager.value
                    )
                    planning_start = project.planned_start_at or project.created_at
                    planning = task_service.create_task(
                        db,
                        project_id=project.id,
                        title=f"产品规划：{project.name}",
                        kind=TaskKind.planning.value,
                        assignee_id=pm.id if pm else None,
                        status=TaskStatus.todo.value,
                        description=project.source_order_text,
                        acceptance_criteria="产出完整 PRD",
                        priority=9,
                        sequence=1,
                        planned_start_at=planning_start + timedelta(days=1),
                        planned_end_at=planning_start + timedelta(days=2),
                    )
                    db.flush()
                    events.append(("task.created", _task_summary(planning)))
                    if planning.assignee_id:
                        events.append(("task.assigned", _task_summary(planning)))
                else:
                    # M2.1 / W34：**系统不接管规划**。
                    # Manager Agent 已完成接收，接下来的拆解/委派由它做（工具面在 M2.3）；
                    # 没有工具之前就**停在这里等**，而不是自己生成一张固定图。
                    events.append(
                        ("project.awaiting_management_action", _awaiting_payload(project))
                    )
            elif task.kind == TaskKind.planning.value:
                if self._uses_deterministic_plan(project):
                    events.extend(self._apply_deterministic_template_plan(db, project))
                    project.status = ProjectStatus.in_progress.value
                    events.append(("project.started", {"id": project.id, "name": project.name}))
                else:
                    events.append(
                        ("project.awaiting_management_action", _awaiting_payload(project))
                    )
            elif task.kind in (
                TaskKind.research.value,
                TaskKind.development.value,
                TaskKind.testing.value,
            ):
                events.extend(self._unblock_dependents(db, task))
            elif task.kind == TaskKind.final_review.value:
                project.status = ProjectStatus.completed.value
                if task.milestone_id:
                    milestone = db.get(Milestone, task.milestone_id)
                    if milestone:
                        milestone.status = MilestoneStatus.completed.value
                events.append(("project.completed", {"id": project.id, "name": project.name}))
                # D2/B7：首次真实项目走完 ⇒ 公司默认工作模式从 guided 推进到 managed。
                # 只改**默认值**，不改任何项目的 work_mode 快照（W35）。
                work_defaults.promote_after_project_completion(db, company_id)
            db.commit()
        self._publish_advance_events(events, company_id, project.id)
        self.notify({"type": "dispatch"})

    @staticmethod
    def _uses_deterministic_plan(project) -> bool:
        """该项目是否使用确定性模板替身规划（**基础设施**，见 W33）。"""
        return project.planning_fixture == PlanningFixture.deterministic_template.value

    def _apply_deterministic_template_plan(self, db, project) -> list[tuple[str, dict]]:
        """按**确定性模板**生成 Milestones+Tasks 图（仅 fixture 项目）。

        这是测试/教程/CI 的载具：research→development→testing→final_review。
        它**不是**公司的规划策略 —— 生产项目的 Task 图只能由 Manager Agent 或 Human
        经 `app/work/tools`（M2.3）创建。
        """
        events: list[tuple[str, dict]] = []
        by_kind: dict[str, int] = {}
        schedule_start = project.planned_start_at or project.created_at
        schedule_windows = ((2, 5), (5, 11), (11, 15), (15, 18))
        project.planned_start_at = schedule_start
        project.planned_end_at = schedule_start + timedelta(days=18)
        for order, (milestone_name, kind, role, deps) in enumerate(
            DETERMINISTIC_TEMPLATE_PLAN, start=1
        ):
            start_offset, end_offset = schedule_windows[order - 1]
            # 里程碑负责人同理：`role` 是 DETERMINISTIC_TEMPLATE_PLAN 里的旧口径，
            # 桥把它换算成"现在谁占着这个编制"，而不是"谁身上写着这个字符串"
            assignee = position_compat.employee_by_legacy_role(db, project.company_id, role)
            milestone = project_repo.create_milestone(
                db,
                project_id=project.id,
                name=milestone_name,
                description=f"{milestone_name} 阶段",
                order=order,
                owner_id=assignee.id if assignee else None,
                planned_start_at=schedule_start + timedelta(days=start_offset),
                planned_end_at=schedule_start + timedelta(days=end_offset),
            )
            task = task_service.create_task(
                db,
                project_id=project.id,
                milestone_id=milestone.id,
                title=f"{milestone_name}：{project.name}",
                kind=kind,
                assignee_id=assignee.id if assignee else None,
                status=TaskStatus.backlog.value,
                description=project.source_order_text,
                acceptance_criteria=f"产出符合要求的 {kind} 交付物",
                priority=10 - order,
                sequence=order + 1,
                depends_on=[by_kind[d] for d in deps],
                planned_start_at=schedule_start + timedelta(days=start_offset),
                planned_end_at=schedule_start + timedelta(days=end_offset),
            )
            by_kind[kind] = task.id
            events.append(("task.created", _task_summary(task)))
            if assignee:
                events.append(("task.assigned", _task_summary(task)))
        research_id = by_kind[TaskKind.research.value]
        research = project_repo.get_task(db, research_id)
        task_service.transition_task(db, research, TaskStatus.todo.value)
        milestone = db.get(Milestone, research.milestone_id)
        if milestone:
            milestone.status = MilestoneStatus.in_progress.value
        return events

    def _unblock_dependents(self, db, done_task) -> list[tuple[str, dict]]:
        """Open dependents (backlog, or failed awaiting rework) once all deps are done."""
        events: list[tuple[str, dict]] = []
        project_tasks = project_repo.list_tasks(db, done_task.project_id)
        for candidate in project_tasks:
            if candidate.status not in (TaskStatus.backlog.value, TaskStatus.failed.value):
                continue
            dep_ids = task_service.task_dependencies(candidate)
            if done_task.id not in dep_ids:
                continue
            if all(
                project_repo.get_task(db, dep_id).status == TaskStatus.done.value
                for dep_id in dep_ids
            ):
                task_service.transition_task(db, candidate, TaskStatus.todo.value, force=True)
                if candidate.milestone_id:
                    milestone = db.get(Milestone, candidate.milestone_id)
                    if milestone and milestone.status == MilestoneStatus.pending.value:
                        milestone.status = MilestoneStatus.in_progress.value
                events.append(("task.assigned", _task_summary(candidate)))
        # milestone completion sweep
        for milestone in project_repo.list_milestones(db, done_task.project_id):
            milestone_tasks = [t for t in project_tasks if t.milestone_id == milestone.id]
            if milestone_tasks and all(t.status == TaskStatus.done.value for t in milestone_tasks):
                milestone.status = MilestoneStatus.completed.value
        return events

    def _publish_advance_events(
        self, events: list[tuple[str, dict]], company_id: int, project_id: int
    ) -> None:
        for event_type, data in events:
            bus.publish(
                event_type,
                data,
                company_id=company_id,
                project_id=project_id,
                task_id=data.get("id") if event_type.startswith("task.") else None,
                actor_employee_id=data.get("assignee_id"),
            )


def _awaiting_payload(project) -> dict:
    """`project.awaiting_management_action` 的载荷。

    这个事件是 M2.1 的"诚实信号"：管理动作还没发生，项目就停在这里等 ——
    既不偷偷用模板，也不替 Manager 决定下一步。
    """
    return {
        "id": project.id,
        "name": project.name,
        "work_mode": project.work_mode,
        "management_employee_id": project.management_employee_id,
        "reason": "awaiting_management_action",
    }


def _task_summary(task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "kind": task.kind,
        "status": task.status,
        "assignee_id": task.assignee_id,
    }


orchestrator = Orchestrator()
