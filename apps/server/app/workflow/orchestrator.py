"""M2.5 **Canonical Task Graph Runtime**：纯调度器（不是通用引擎，也不做管理决策）。

用户拍板的执行语义（设计 §14d，R1–R12）：

```text
Manager chooses.   ← 建哪些 Task、依赖、谁负责、是否改派/取消/重规划（经 M2.3 工具 + M2.4 决策）
System schedules.  ← 依赖是否满足、是否就绪、能不能派、何时派
Worker executes.   ← WorkSession → Runtime
```

它**只做**四件事：

1. 按**契约的**就绪口径（`app/work/dispatch.py` → `resolve_ready_tasks`）找出可以执行的任务；
2. 对每个就绪任务做**可派发判定**，且只有两种结论：派给**它自己的**负责人（R1），
   或者上报管理决策（`task.assignment_required` / `task.runtime_unavailable` …，R4/R5）；
3. 驱动 WorkSession → Runtime → Artifact（执行本身）；
4. 记录事实（`task.ready` / `task.completed` / `project.delivery_ready` …）。

它**不再**做的事（M2.5 删除）：

```text
× 按 TaskKind 分支推进（order_review → planning → research → …）
× 生成 Task 图（那是 Manager Agent 或 `app/work/planning_fixture.py` 的事）
× 失败后自动把上游任务打回 todo（重做是管理决策，经 request_rework 工具，R10/R12）
× 替任何人选择负责人（R2：系统永不选人）
```

一员工同时一个 running session（不变式 §3.4.2）：其他就绪任务**排队**，不是错误。
"""

import asyncio
from datetime import UTC, datetime

from app.brain import DEFAULT_POLICY
from app.brain import policy_for as behavior_policy_for
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.learning import reflection, retrieval
from app.models.enums import (
    EmployeeStatus,
    MilestoneStatus,
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
from app.work import contracts as C
from app.work import dispatch as dispatch_runtime
from app.work import work_defaults

logger = get_logger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}  # employee_id -> session task
        #: (task_id, event_type) —— 同一任务同一原因只上报一次（重启后最多再报一次）
        self._escalated: set[tuple[int, str]] = set()

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
        """把**已就绪且可派发**的任务交给**它自己的**负责人（R1/R2）。

        三种结论，各自不同的处置：

        | 判定 | 处置 |
        | --- | --- |
        | `dispatchable` | 起一个 WorkSession（派给**已存在的** assignee）|
        | `queued`（负责人正忙）| 跳过 —— 下一轮再看，不是异常 |
        | `needs_management` | 发 Decision-needed 事件（去重），**绝不自动挑人**（R4/R5）|

        **去重**：同一 (task, event) 只在第一次遇到时上报；重启后最多再报一次
        （事件是事实，重述一次可以接受；反复刷屏不可以）。
        """
        if not settings.orchestrator_dispatch_enabled:
            # 门控：后台写者不与调用方抢同一份 SQLite（见 Settings 的注释）。
            return
        with SessionLocal() as db:
            candidates: list[tuple[int, int]] = []
            escalations: list[tuple[int, str, dict]] = []
            for project in project_repo.list_projects(db):
                if project.status not in dispatch_runtime.EXECUTABLE_PROJECT_STATUSES:
                    continue
                state = dispatch_runtime.project_runtime_state(db, int(project.id))
                for evaluation in state.dispatchable:
                    assignee_id = int(evaluation.assignee_id or 0)
                    if not assignee_id or assignee_id in self._running:
                        continue
                    if org_repo.get_employee(db, assignee_id) is None:
                        continue
                    candidates.append((int(evaluation.task_id), assignee_id))
                for evaluation in state.needs_management:
                    event = evaluation.event_type
                    if event is None:
                        continue
                    key = (int(evaluation.task_id), event)
                    if key in self._escalated:
                        continue
                    task = project_repo.get_task(db, int(evaluation.task_id))
                    escalations.append(
                        (
                            int(evaluation.task_id),
                            event,
                            {
                                "id": int(evaluation.task_id),
                                "title": task.title if task else "",
                                "kind": task.kind if task else "",
                                "project_id": int(project.id),
                                "company_id": int(project.company_id),
                                "reasons": list(evaluation.reasons),
                                "assignee_id": evaluation.assignee_id,
                                "management_employee_id": project.management_employee_id,
                                "needs_management": True,
                            },
                        )
                    )
                    self._escalated.add(key)
        for task_id, event, payload in escalations:
            logger.info("dispatch needs management: %s task=%s", event, task_id)
            bus.publish(
                event,
                payload,
                company_id=payload["company_id"],
                project_id=payload["project_id"],
                task_id=task_id,
                actor_employee_id=payload.get("assignee_id"),
            )
        for task_id, employee_id in candidates:
            # invariant §3.4.2: one running WorkSession per employee
            session_task = asyncio.create_task(
                self._run_task(task_id), name=f"work-session-task-{task_id}"
            )
            self._running[employee_id] = session_task
            # 释放键必须**挂在会话任务的生命周期上**，不能写在 `_run_task` 里：
            # 那里有若干早退分支（任务被别处改状态、员工不 idle 等），
            # 任何一次早退漏掉释放，这个员工就会被永久跳过（实测踩过）。
            session_task.add_done_callback(
                lambda _task, employee=employee_id: self._running.pop(employee, None)
            )

    async def _run_task(self, task_id: int) -> None:
        employee_id: int | None = None
        started = datetime.now(UTC)
        try:
            with SessionLocal() as db:
                task = project_repo.get_task(db, task_id)
                # 可派发的前提是"结构就绪 + 已指派"。`backlog` 表示"草稿"，
                # 就绪后由**系统**推进到 `todo`（就绪是系统职责，选人是管理职责，R3）。
                if task is None or task.status not in (
                    TaskStatus.backlog.value,
                    TaskStatus.todo.value,
                ):
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
                if task.status == TaskStatus.backlog.value:
                    # backlog → todo → in_progress：状态机不允许跳步，也不该跳
                    task_service.transition_task(db, task, TaskStatus.todo.value)
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

    # ---- 事实记录与推进（**没有** per-TaskKind 分支，R12） ----

    def _advance(self, task_id: int, success: bool) -> None:
        """任务结束后只做两件事：**记录事实**、**上报例外**。

        - 成功：任务已由 `_finalize` 标成 done ⇒ 发 `task.ready`（新就绪的后续任务），
          并检查项目是否全部完成（`project.delivery_ready` / `project.completed`）。
        - 失败：发 `project.replan_required` —— **不自动重做**。
          "把上游任务打回 todo" 是管理决策（`request_rework` 工具，R10/R12）。
        """
        with SessionLocal() as db:
            task = project_repo.get_task(db, task_id)
            if task is None:
                return
            project = project_repo.get_project(db, task.project_id)
            if project is None:  # pragma: no cover - 防御
                return
            company_id = int(project.company_id)
            project_id = int(project.id)
            events: list[tuple[str, dict]] = []

            if not success:
                events.append(("project.replan_required", _replan_payload(project, task)))
                db.commit()
                self._publish_advance_events(events, company_id, project_id)
                return

            # 完成一个任务可能让后续任务就绪 —— 用**契约口径**算，不猜
            for ready in dispatch_runtime.ready_tasks(db, project_id):
                events.append(
                    (
                        "task.ready",
                        {
                            "id": int(ready.id),
                            "title": ready.title,
                            "kind": ready.kind,
                            "assignee_id": ready.assignee_id,
                        },
                    )
                )

            # 项目级事实：全部任务完成 ⇒ 可交付（"计划跑完了"是事实，不是管理判断）
            state = dispatch_runtime.project_runtime_state(db, project_id)
            if state.all_tasks_done and project.status in (
                ProjectStatus.requested.value,
                ProjectStatus.planning.value,
            ):
                # 手里的活干完了、但项目还没进入执行态 ⇒ 事实是"等管理层下一步动作"。
                # 这不是替谁做决定：系统只说"我没活了"，要不要继续由 Manager Agent 决定（W34）。
                project.status = ProjectStatus.planning.value
                events.append(("project.awaiting_management_action", _awaiting_payload(project)))
            if state.all_tasks_done and project.status in (
                ProjectStatus.in_progress.value,
                ProjectStatus.in_review.value,
            ):
                project.status = ProjectStatus.completed.value
                for milestone in project_repo.list_milestones(db, project_id):
                    milestone.status = MilestoneStatus.completed.value
                events.append(("project.delivery_ready", {"id": project_id, "name": project.name}))
                events.append(("project.completed", {"id": project_id, "name": project.name}))
                # D2/B7：首次真实项目走完 ⇒ 公司默认工作模式从 guided 推进到 managed。
                # 只改**默认值**，不改任何项目的 work_mode 快照（W35）。
                work_defaults.promote_after_project_completion(db, company_id)
            db.commit()
        self._publish_advance_events(events, company_id, project_id)
        self.notify({"type": "dispatch"})

    def _publish_advance_events(
        self, events: list[tuple[str, dict]], company_id: int, project_id: int
    ) -> None:
        for event_type, data in events:
            # 事件集是**封闭**的（R11）：要么是事实通告，要么是要管理层介入。
            # 未登记的事件意味着"悄悄多了一条唤醒/通知路径"，这里直接炸。
            assert event_type in (C.FACT_EVENTS | C.DECISION_NEEDED_EVENTS), (
                f"未登记的事件类型：{event_type}（请先在 contracts 的事件表里登记）"
            )
            bus.publish(
                event_type,
                data,
                company_id=company_id,
                project_id=project_id,
                task_id=data.get("id") if event_type.startswith("task.") else None,
                actor_employee_id=data.get("assignee_id"),
            )


def _replan_payload(project, task) -> dict:
    """`project.replan_required` 的载荷：只陈述事实（谁失败了），不提议怎么重做。"""
    return {
        "id": int(project.id),
        "name": project.name,
        "work_mode": project.work_mode,
        "management_employee_id": project.management_employee_id,
        "failed_task_id": int(task.id),
        "failed_task_title": task.title,
        "failed_task_kind": task.kind,
        "assignee_id": task.assignee_id,
        "reason": "task_failed",
    }


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
