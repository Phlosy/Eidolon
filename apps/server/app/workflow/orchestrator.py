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

from sqlalchemy import select

from app.brain import DEFAULT_POLICY
from app.brain import policy_for as behavior_policy_for
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.transaction_effects import ensure_sqlite_transaction
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
from app.models.project import Milestone, Task, WorkSession
from app.models.provider import ModelBinding
from app.models.runtime_execution import STOP_REASONS, RuntimeAttempt
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import RuntimeEventKind, RuntimeSession, TaskContext
from app.runtimes.gateway import gateway
from app.services import artifacts as artifact_service
from app.services import position_compat
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import dispatch as dispatch_runtime
from app.work import (
    dispatch_notices,
    handoff,
    review_fixture,
    runtime_artifacts,
    telemetry,
    work_defaults,
)

logger = get_logger(__name__)


async def _stop_worker(adapter, session):
    """Drain owned local work even when remote stop transport fails."""
    try:
        await adapter.cancel_task(session)
    except (Exception, asyncio.CancelledError):
        logger.warning("worker stop unconfirmed", extra={"task_id": session.task_id})
    finally:
        background = session.background
        if background is not None:
            if not background.done() and not background.cancelling():
                background.cancel()
            await asyncio.gather(background, return_exceptions=True)


class Orchestrator:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}  # employee_id -> session task
        self._workers: dict[int, asyncio.Task] = {}  # original WorkSession id -> owned coroutine

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._runner = asyncio.create_task(self._run(), name="eidolon-orchestrator")

    async def stop(self) -> None:
        self._loop = None
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        owned = set(self._running.values()) | set(self._workers.values())
        for task in owned:
            if not task.cancelling():
                task.cancel("shutdown")
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)

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
            self._stop_requested_workers()
            await self._dispatch_pending()
        elif signal["type"] == "stop_work":
            identifier = signal.get("work_session_id")
            if type(identifier) is int:
                self._stop_requested_workers({identifier})
        elif signal["type"] == "task_finished":
            # manual completion via PATCH /tasks/{id}
            self._finalize_external(signal["task_id"], signal["success"])

    def _stop_requested_workers(self, identifiers=None):
        if not self._workers:
            return
        with SessionLocal() as db:
            for work_id, owned in tuple(self._workers.items()):
                if identifiers is not None and work_id not in identifiers:
                    continue
                if owned.done() or owned.cancelling():
                    continue
                work = db.get(WorkSession, work_id)
                task = db.get(Task, work.task_id) if work is not None else None
                reason = db.scalar(
                    select(RuntimeAttempt.stop_reason)
                    .where(
                        RuntimeAttempt.work_session_id == work_id,
                        RuntimeAttempt.stop_requested_at.is_not(None),
                    )
                    .order_by(RuntimeAttempt.id)
                    .limit(1)
                )
                if reason is None and (
                    work is None
                    or work.status != "running"
                    or task is None
                    or task.status != "in_progress"
                ):
                    reason = (
                        "blocked" if task is not None and task.status == "blocked" else "cancelled"
                    )
                if reason is not None:
                    owned.cancel(reason)

    def _release_running(self, employee_id, completed):
        if self._running.get(employee_id) is completed:
            self._running.pop(employee_id)

    # ---- dispatcher ----

    async def _dispatch_pending(self) -> None:
        """把**已就绪且可派发**的任务交给**它自己的**负责人（R1/R2）。

        三种结论，各自不同的处置：

        | 判定 | 处置 |
        | --- | --- |
        | `dispatchable` | 起一个 WorkSession（派给**已存在的** assignee）|
        | `queued`（负责人正忙）| 跳过 —— 下一轮再看，不是异常 |
        | `needs_management` | 发 Decision-needed 事件（去重），**绝不自动挑人**（R4/R5）|

        通知按持久周期去重，条件消失或改派后结束，再次发生可重新通知。
        """
        if not settings.orchestrator_dispatch_enabled:
            # 门控：后台写者不与调用方抢同一份 SQLite（见 Settings 的注释）。
            return
        with SessionLocal() as db:
            # SQLite's legacy read transactions otherwise allow another sweep or
            # recovery write between observing a fault and recording its cycle.
            # This short phase only reads local facts and writes Events; external
            # calls and broadcasts occur after commit.
            dispatch_notices.begin_observation(db)
            candidates: list[tuple[int, int]] = []
            escalations: list[tuple[int | None, str, dict]] = []
            for project in project_repo.list_projects(db):
                if project.status not in dispatch_runtime.EXECUTABLE_PROJECT_STATUSES:
                    continue
                state = dispatch_runtime.project_runtime_state(db, int(project.id))
                if state.invalid_graph:
                    # 图本身有环/悬空 ⇒ 这张计划无法执行：上报重规划（等管理层修图），
                    # 既不调度、也不假装它是"缺人/缺资源"（那是另一类判断）。
                    escalations.append(
                        (
                            None,
                            "project.replan_required",
                            {
                                "id": int(project.id),
                                "name": project.name,
                                "company_id": int(project.company_id),
                                "project_id": int(project.id),
                                "work_mode": project.work_mode,
                                "management_employee_id": project.management_employee_id,
                                "reasons": list(state.graph_problems),
                                "needs_management": True,
                            },
                        )
                    )
                    continue
                for evaluation in state.dispatchable:
                    assignee_id = int(evaluation.assignee_id or 0)
                    if not assignee_id or assignee_id in self._running:
                        continue
                    if org_repo.get_employee(db, assignee_id) is None:
                        continue
                    candidates.append((int(evaluation.task_id), assignee_id))
                # M2.7：做完了却没指定评审人 ⇒ 叫管理层（系统不替它选人，W1/R2/RV1）
                for pending in dispatch_runtime.tasks_awaiting_review(db, int(project.id)):
                    escalations.append(
                        (
                            int(pending.id),
                            "task.review_required",
                            {
                                "id": int(pending.id),
                                "task_id": int(pending.id),
                                "title": pending.title,
                                "kind": pending.kind,
                                "company_id": int(project.company_id),
                                "project_id": int(project.id),
                                "management_employee_id": project.management_employee_id,
                                "needs_reviewer": True,
                                "needs_management": True,
                            },
                        )
                    )
                for evaluation in state.needs_management:
                    event = evaluation.event_type
                    if event is None:
                        continue
                    task = project_repo.get_task(db, int(evaluation.task_id))
                    if task is None or task.status not in {
                        TaskStatus.backlog.value,
                        TaskStatus.todo.value,
                        TaskStatus.blocked.value,
                        TaskStatus.failed.value,
                    }:
                        continue
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
                                "waiting_for_environment": evaluation.waiting_for_environment,
                                "readiness": evaluation.readiness,
                                "management_employee_id": project.management_employee_id,
                                "needs_management": True,
                            },
                        )
                    )
            dispatch_notices.reconcile(db, escalations)
            db.commit()
        dispatch_notices.broadcast_pending()
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
                lambda completed, employee=employee_id: self._release_running(employee, completed)
            )

    async def _run_task(self, task_id: int) -> None:
        employee_id: int | None = None
        work_session_id: int | None = None
        attempt = None
        adapter = session = cleanup = None
        started = datetime.now(UTC)

        async def stop_worker(reason="runtime_error"):
            nonlocal cleanup
            if adapter is None or session is None:
                return False
            if cleanup is None:
                session.stop_reason = reason
                cleanup = asyncio.create_task(
                    _stop_worker(adapter, session), name=f"worker-cleanup-{task_id}"
                )
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Remember cancellation without releasing the employee while
                    # its producer/stream is still being drained.
                    interrupted = True
            cleanup.result()
            return interrupted

        try:
            with SessionLocal() as db:
                # Worker and non-worker claims share the same admission lock.
                # Fresh occupancy/authority reads and creation must be atomic;
                # the transaction ends before any external runtime call.
                dispatch_notices.begin_observation(db)
                task = project_repo.get_task(db, task_id)
                # 可派发的前提是"结构就绪 + 已指派"。`backlog` 表示"草稿"，
                # 就绪后由**系统**推进到 `todo`（就绪是系统职责，选人是管理职责，R3）。
                if task is None or task.status not in (
                    TaskStatus.backlog.value,
                    TaskStatus.todo.value,
                ):
                    return
                # A manager may requeue/reassign a task before its executor exits.
                # The new assignee starts only after that session has finished.
                if project_repo.get_running_session_for_task(db, task_id) is not None:
                    return
                # Authorization and observed readiness may have changed since the
                # sweep queued this coroutine; reuse the same dispatch evaluator.
                if not dispatch_runtime.evaluate_dispatch(db, task).dispatchable:
                    return
                employee = org_repo.get_employee(db, task.assignee_id)
                if employee is None or employee.status != EmployeeStatus.idle.value:
                    return
                employee_id = employee.id
                company_id = task.project.company_id
                adapter = gateway.adapter_for(employee.runtime_type)

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
                # The global mock override selects an actual MockAdapter even if
                # this employee has a configured real environment and model.
                if adapter.type.value == "mock":
                    runtime_instance = binding = None
                ws = project_repo.create_work_session(
                    db,
                    task_id=task.id,
                    employee_id=employee.id,
                    runtime_type=adapter.type.value,
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
                work_session_id = int(ws.id)
                self._workers[work_session_id] = asyncio.current_task()
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
                # M2.6（H4/H6）：输入在**运行期**解析 —— 上游产物这时才一定存在。
                # 声明的上游由 DAG 保证先跑完；在跑的产物不会被取到（H8）。
                input_artifacts = handoff.resolve_input_artifacts(db, task)
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
                    input_artifacts=[item.as_dict() for item in input_artifacts],
                    required_artifact_types=list(handoff.declared_artifact_types(task)),
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
            session = await adapter.create_session(instance, task_ctx)
            with SessionLocal() as db:
                ws = db.get(WorkSession, work_session_id)
                if ws:
                    ws.runtime_session_ref = session.id
                    # M2.6（H3/G5）：**使用事实**在会话建立的那一刻落库 ——
                    # 「这个产物被谁、在哪次会话用掉了」必须可追溯，且与产出归属分开记。
                    if input_artifacts:
                        handoff.record_consumed(
                            db,
                            task_id=task_id,
                            artifact_ids=[item.artifact_id for item in input_artifacts],
                            artifact_refs=input_artifacts,
                            work_session_id=int(ws.id),
                            actor_employee_id=employee_id,
                            reason="auto: declared input",
                            commit=False,
                        )
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
            if task_ctx.input_artifacts:
                # M2.6（H6）：上游产物作为**输入**进入 prompt（有界摘要）。
                blocks = "\n\n".join(
                    f"### {item['title']}（来自任务「{item['source_task_title']}」）\n\n"
                    f"{item['excerpt']}"
                    for item in task_ctx.input_artifacts
                )
                prompt += f"\n\n## 上游交付物（本次任务的输入）\n\n{blocks}"
            attempt = telemetry.prepare_attempt(
                work_session_id,
                session,
                prompt,
                request_key="worker",
                kind="worker",
                request_context={
                    "runtime_config": runtime_config,
                    "behavior_policy": policy.as_dict(),
                    "input_artifacts": task_ctx.input_artifacts,
                    "artifact_protocol": {
                        "version": "eidolon.artifacts.v1",
                        "required_types": task_ctx.required_artifact_types,
                    },
                },
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

            success, error = False, "stream_ended_without_terminal"
            async for event in adapter.stream_events(session):
                attempt.observe(event)
                if event.kind == RuntimeEventKind.error:
                    success, error = False, "runtime_error"
                    break
                elif event.kind == RuntimeEventKind.completed:
                    if adapter.type.value == "mock":
                        attempt.terminal("completed", "mock.stream")
                    success, error = True, None
                    break
            if success and session.background is not None:
                await asyncio.shield(session.background)
            elif not success and await stop_worker():
                raise asyncio.CancelledError
            produced = await adapter.get_artifacts(session) if success else []
            duration = (datetime.now(UTC) - started).total_seconds()
            self._finalize(
                task_id,
                success,
                produced,
                error,
                duration,
                work_session_id=work_session_id,
                runtime_session=session,
            )
            attempt.finish_host_safely(
                "completed" if success else "failed",
                "stream_completed" if success else "stream_ended",
            )
        except asyncio.CancelledError as exc:
            reason = exc.args[0] if exc.args and exc.args[0] in STOP_REASONS else "cancelled"
            await stop_worker(reason)
            if attempt is not None:
                attempt.finish_host_safely("cancelled", "cancelled")
            logger.info("work session cancelled", extra={"task_id": task_id})
            if work_session_id is not None:
                self._finalize(
                    task_id, False, [], "cancelled", 0.0, work_session_id=work_session_id
                )
            raise
        except Exception:
            cancelled = await stop_worker("host_error")
            if attempt is not None:
                attempt.finish_host_safely(
                    "cancelled" if cancelled else "failed",
                    "cancelled" if cancelled else "host_error",
                )
            logger.warning("work session crashed", extra={"task_id": task_id})
            if work_session_id is not None:
                self._finalize(
                    task_id,
                    False,
                    [],
                    "cancelled" if cancelled else "runtime_error",
                    0.0,
                    work_session_id=work_session_id,
                )
            if cancelled:
                raise asyncio.CancelledError from None
        finally:
            if self._workers.get(work_session_id) is asyncio.current_task():
                self._workers.pop(work_session_id)

    # ---- finalization & reflection ----

    def _finalize(
        self,
        task_id: int,
        success: bool,
        produced: list,
        error: str | None,
        duration: float,
        *,
        work_session_id: int | None = None,
        runtime_session: RuntimeSession | None = None,
    ) -> None:
        with SessionLocal() as db:
            ensure_sqlite_transaction(db)
            task = project_repo.get_task(db, task_id)
            if task is None:
                return
            ws = (
                db.get(WorkSession, work_session_id)
                if work_session_id is not None
                else project_repo.get_running_session_for_task(db, task_id)
            )
            # A post-commit failure or repeated callback must not record effects again,
            # nor finalize a later session belonging to the task's new assignee.
            if ws is None or ws.task_id != task_id or ws.status != WorkSessionStatus.running.value:
                return
            project = project_repo.get_project(db, task.project_id)
            employee = org_repo.get_employee(db, ws.employee_id)
            company_id = project.company_id if project else None

            artifact_ids = []
            if success and ws.runtime_type != "mock":
                nodes = runtime_artifacts.accept_collection(
                    db,
                    work_session_id=ws.id,
                    attempt_id=getattr(
                        getattr(runtime_session, "attempt", None), "attempt_id", None
                    ),
                    session=runtime_session,
                    produced=produced,
                )
                artifact_ids = [(node.id, node.doc_type, node.name) for node in nodes]
            ws.status = (
                WorkSessionStatus.completed.value if success else WorkSessionStatus.failed.value
            )
            ws.ended_at = datetime.now(UTC)
            ws.summary = error or f"产出 {len(produced)} 个交付物"
            ws.error = error
            ws.cost = {**(ws.cost or {}), "duration_sec": round(duration, 3)}
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

            # v0.3: artifacts are drive documents in the project folder
            for item in produced if project is not None and ws.runtime_type == "mock" else []:
                node = artifact_service.record_project_artifact(
                    db,
                    project,
                    artifact_type=item.type,
                    title=item.title,
                    content=item.content,
                    author_id=employee.id if employee else None,
                    work_session_id=ws.id,
                    # M2.6（H2）：产出归属 = 这个 Task。写在这里、此后不变。
                    task_id=task_id,
                )
                artifact_ids.append((node.id, node.doc_type, node.name))

            fixture_review_enabled = False
            # Administrative changes own task state; runtime outcome owns the session.
            # Reassignment changes the next executor, never this session's attribution.
            advance_task = task.status == TaskStatus.in_progress.value
            if advance_task and success:
                # M2.7（RV2 / 设计 §12.3）：**删除 in_review → done 的自动连跳**。
                # 工作做完了就停在 `in_review`：通过与否是 Reviewer Agent 的判断，
                # 系统只负责把事实摆出来（`review_facts`）。
                task_service.transition_task(db, task, TaskStatus.in_review.value)
                # 测试/教程/CI 的替身评审（与 planning_fixture 同款门控）在**提交之后**
                # 用一段**独立短事务**完成：它要走真实的评审服务（读事实 + 写请求 +
                # 写结论），塞进这个事务里会让 SQLite 的单写者窗口变长（实测：
                # 并发的 HTTP 读会吃 `database is locked`）。
                fixture_review_enabled = review_fixture.enabled_for(project)
            elif advance_task:
                task_service.transition_task(db, task, TaskStatus.failed.value)
            released_employee = employee is not None and employee.current_task_id == task_id
            if released_employee:
                employee.status = EmployeeStatus.idle.value
                employee.current_task_id = None
            employee_id = employee.id if employee else None
            task_title, task_kind = task.title, task.kind
            task_status_after = str(task.status)
            task_project_id = int(task.project_id)
            db.commit()

        reviewed_inline = False
        if success and fixture_review_enabled:
            # 替身评审自带短事务（见 `review_fixture` 的说明）。它返回 None 表示
            # "没评成" —— 任务留在 in_review 等管理层，**不**因此自动通过。
            request = review_fixture.review_now(task_id=task_id, project_id=task_project_id)
            reviewed_inline = request is not None
            if reviewed_inline:
                # 替身结论已落地：任务状态按**同一个映射**推导，不另写一套语义
                task_status_after = (
                    C.REVIEW_VERDICT_TARGETS.get(request.verdict) or task_status_after
                )

        for artifact_id, artifact_type, artifact_title in artifact_ids:
            bus.publish(
                "artifact.created",
                {"id": artifact_id, "type": artifact_type, "title": artifact_title},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_project_id,
                task_id=task_id,
            )
        if advance_task and success:
            # M2.7：工作干完了，进入"等评审"（事实）
            bus.publish(
                "task.in_review",
                {
                    "id": task_id,
                    "title": task_title,
                    "kind": task_kind,
                    "status": task_status_after,
                    "fixture_review": reviewed_inline,
                },
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_project_id,
                task_id=task_id,
            )
            if not reviewed_inline:
                # 还没有结论、也还没有指定评审人 ⇒ 需要管理层（把 reviewer 指定出来）。
                # 系统**不**替它选人（W1/R2），所以这条必须叫醒别人。
                bus.publish(
                    "task.review_required",
                    {
                        "id": task_id,
                        "task_id": task_id,
                        "title": task_title,
                        "kind": task_kind,
                        "needs_reviewer": True,
                    },
                    company_id=company_id,
                    actor_employee_id=employee_id,
                    project_id=task_project_id,
                    task_id=task_id,
                )
        if advance_task:
            bus.publish(
                "task.completed" if success else "task.failed",
                {"id": task_id, "title": task_title, "kind": task_kind, "error": error},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_project_id,
                task_id=task_id,
            )
        if released_employee:
            bus.publish(
                "employee.status_changed",
                {"id": employee_id, "to": EmployeeStatus.idle.value},
                company_id=company_id,
                actor_employee_id=employee_id,
                project_id=task_project_id,
                task_id=task_id,
            )
        if employee_id is not None:
            reflection.reflect(task_id, employee_id, success, duration, error)
        if advance_task:
            self._advance(task_id, success)
        else:
            self.notify({"type": "dispatch"})

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
            if state.work_finished and project.status in (
                ProjectStatus.requested.value,
                ProjectStatus.planning.value,
            ):
                # 手里的活干完了（含"干完但还在等评审"）、但项目还没进入执行态 ⇒
                # 事实是"等管理层下一步动作"。这不是替谁做决定：系统只说"我没活了"，
                # 要不要继续由 Manager Agent 决定（W34）。
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
