"""Event-driven workflow orchestrator (not a general-purpose engine).

See docs/architecture.md §5. Consumes dispatch signals, drives WorkSessions via
the RuntimeGateway, and advances the fixed order → planning → build → verify →
release pipeline. One running session per employee; queued tasks wait.
"""

import asyncio
from datetime import UTC, datetime

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.learning import reflection, retrieval
from app.models.enums import (
    ArtifactStatus,
    EmployeeRole,
    EmployeeStatus,
    MilestoneStatus,
    ProjectStatus,
    TaskKind,
    TaskStatus,
    WorkSessionStatus,
)
from app.models.project import Milestone
from app.models.provider import ModelBinding
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import RuntimeEventKind, TaskContext
from app.runtimes.gateway import gateway
from app.services import artifacts as artifact_service
from app.services import tasks as task_service

logger = get_logger(__name__)

# Milestone/task graph template generated after planning (§5).
GRAPH_TEMPLATE = [
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
                )
                if task.milestone_id:
                    milestone = db.get(Milestone, task.milestone_id)
                    if milestone and milestone.status == MilestoneStatus.pending.value:
                        milestone.status = MilestoneStatus.in_progress.value
                db.commit()
                runtime_config = dict(employee.runtime_config or {})
                # v0.2: inject the assignee's own relevant learning into the task context
                prior_knowledge, validated_skills = retrieval.retrieve_for_task(
                    db, employee.id, task.title, task.description
                )
                task_ctx = TaskContext(
                    task_id=task.id,
                    project_id=task.project_id,
                    title=task.title,
                    kind=task.kind,
                    description=task.description,
                    acceptance_criteria=task.acceptance_criteria,
                    employee_name=employee.name,
                    employee_role=employee.role,
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
                    db.commit()
            prompt = (
                f"任务：{task_ctx.title}\n\n{task_ctx.description}\n\n"
                f"验收标准：{task_ctx.acceptance_criteria or '按任务描述完成'}"
            )
            await adapter.send_task(
                session, prompt, {"task_context": task_ctx, "runtime_config": runtime_config}
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
                ws.cost = {"duration_sec": round(duration, 3), "tokens": 0}

            artifact_ids = []
            for item in produced:
                artifact = project_repo.create_artifact(
                    db,
                    company_id=company_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    type=item.type,
                    title=item.title,
                    content=item.content,
                    status=ArtifactStatus.draft.value,
                    author_id=employee.id if employee else None,
                )
                # v0.2: artifacts are also real files on disk (path + sha256)
                artifact_service.materialize_artifact(db, artifact, ws.id if ws else None)
                artifact_ids.append((artifact.id, artifact.type, artifact.title))

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
                pm = org_repo.get_employee_by_role(
                    db, company_id, EmployeeRole.product_manager.value
                )
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
                )
                db.flush()
                events.append(("task.created", _task_summary(planning)))
                if planning.assignee_id:
                    events.append(("task.assigned", _task_summary(planning)))
            elif task.kind == TaskKind.planning.value:
                events.extend(self._generate_graph(db, project))
                project.status = ProjectStatus.in_progress.value
                events.append(("project.started", {"id": project.id, "name": project.name}))
            elif task.kind in (
                TaskKind.research.value,
                TaskKind.development.value,
                TaskKind.testing.value,
            ):
                events.extend(self._unblock_dependents(db, task))
            elif task.kind == TaskKind.final_review.value:
                project.status = ProjectStatus.completed.value
                events.append(("project.completed", {"id": project.id, "name": project.name}))
            db.commit()
        self._publish_advance_events(events, company_id, project.id)
        self.notify({"type": "dispatch"})

    def _generate_graph(self, db, project) -> list[tuple[str, dict]]:
        """按模板生成 Milestones+Tasks 图：research→development→testing→final_review."""
        events: list[tuple[str, dict]] = []
        by_kind: dict[str, int] = {}
        for order, (milestone_name, kind, role, deps) in enumerate(GRAPH_TEMPLATE, start=1):
            milestone = project_repo.create_milestone(
                db,
                project_id=project.id,
                name=milestone_name,
                description=f"{milestone_name} 阶段",
                order=order,
            )
            assignee = org_repo.get_employee_by_role(db, project.company_id, role)
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


def _task_summary(task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "kind": task.kind,
        "status": task.status,
        "assignee_id": task.assignee_id,
    }


orchestrator = Orchestrator()
