"""MockAdapter — fully implemented simulated runtime. See docs/architecture.md §4.3.

Event stream after send_task: thinking → message (role 化工作描述) → working beats
(status events) → artifact → completed. Total duration = EIDOLON_MOCK_TASK_SECONDS
(default 8). Supports cancel_task and failure injection via
``runtime_config.mock_fail_rate`` (default 0).
"""

import asyncio
import random
import uuid
from collections.abc import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import RuntimeType
from app.runtimes.base import (
    EmployeeRef,
    ProducedArtifact,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeInfo,
    RuntimeInstance,
    RuntimeSession,
    RuntimeStatus,
    TaskContext,
)
from app.runtimes.mock.templates import build_artifact, role_message

logger = get_logger(__name__)

WORKING_BEATS = ("分析输入与约束", "执行核心工作", "整理输出与自检")


class MockAdapter(RuntimeAdapter):
    type = RuntimeType.mock
    implemented = True

    def detect(self) -> bool:
        return True

    def get_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            chat=True,
            task=True,
            filesystem=True,
            terminal=True,
            web=True,
            memory=True,
            skills=True,
            scheduler=True,
            streaming=True,
            artifacts=True,
        )

    async def create_instance(self, employee: EmployeeRef, config: dict) -> RuntimeInstance:
        home = f"{settings.workspace_root}/{employee.slug}"
        return RuntimeInstance(
            employee_id=employee.id,
            profile=f"mock-profile-{employee.slug}",
            home_path=home,
            status=RuntimeStatus.stopped,
        )

    async def start(self, instance: RuntimeInstance) -> None:
        instance.status = RuntimeStatus.running

    async def stop(self, instance: RuntimeInstance) -> None:
        instance.status = RuntimeStatus.stopped

    async def get_status(self, instance: RuntimeInstance) -> RuntimeStatus:
        return instance.status

    async def create_session(self, instance: RuntimeInstance, task: TaskContext) -> RuntimeSession:
        return RuntimeSession(
            id=f"mock-{uuid.uuid4().hex[:12]}",
            instance=instance,
            task_id=task.task_id,
        )

    async def send_task(self, session: RuntimeSession, prompt: str, context: dict) -> None:
        ctx: TaskContext = context["task_context"]
        config: dict = context.get("runtime_config") or {}
        session.background = asyncio.create_task(
            self._run(session, ctx, config), name=f"mock-task-{ctx.task_id}"
        )

    async def send_message(self, session: RuntimeSession, message: str) -> None:
        await session.queue.put(
            RuntimeEvent(RuntimeEventKind.message, {"role": "user", "content": message})
        )

    async def stream_events(self, session: RuntimeSession) -> AsyncIterator[RuntimeEvent]:
        while True:
            event = await session.queue.get()
            yield event
            if event.kind in (RuntimeEventKind.completed, RuntimeEventKind.error):
                break

    async def cancel_task(self, session: RuntimeSession) -> None:
        if session.background and not session.background.done():
            session.background.cancel()
        session.status = "cancelled"

    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]:
        return list(session.artifacts)

    async def get_runtime_info(self, instance: RuntimeInstance) -> RuntimeInfo:
        return RuntimeInfo(
            type=self.type,
            version="mock-0.1.0",
            details={"profile": instance.profile, "home_path": instance.home_path},
        )

    async def _run(self, session: RuntimeSession, ctx: TaskContext, config: dict) -> None:
        total = max(settings.mock_task_seconds, 0.01)
        fail_rate = float(config.get("mock_fail_rate", 0) or 0)
        will_fail = random.random() < fail_rate
        log = get_logger(__name__, employee_id=session.instance.employee_id, task_id=ctx.task_id)

        async def emit(kind: RuntimeEventKind, data: dict) -> None:
            await session.queue.put(RuntimeEvent(kind, data))

        try:
            await emit(RuntimeEventKind.thinking, {"message": "梳理任务目标与方案..."})
            await asyncio.sleep(total * 0.2)
            await emit(
                RuntimeEventKind.message,
                {"role": ctx.employee_role, "content": role_message(ctx)},
            )
            if ctx.prior_knowledge:
                # learning retrieval is observable in the simulated output
                topics = ", ".join(ctx.prior_knowledge)
                skills = (
                    f"；运用已验证技能：{', '.join(ctx.validated_skills)}"
                    if ctx.validated_skills
                    else ""
                )
                await emit(
                    RuntimeEventKind.message,
                    {
                        "role": ctx.employee_role,
                        "content": f"using prior knowledge: {topics}{skills}",
                    },
                )
            for i, beat in enumerate(WORKING_BEATS):
                await asyncio.sleep(total * 0.6 / len(WORKING_BEATS))
                await emit(
                    RuntimeEventKind.status,
                    {"progress": round((i + 1) / (len(WORKING_BEATS) + 1), 2), "detail": beat},
                )
            if will_fail:
                await asyncio.sleep(total * 0.2)
                session.status = "failed"
                await emit(
                    RuntimeEventKind.error,
                    {"message": f"mock_fail_rate={fail_rate} 注入失败：任务未通过"},
                )
                return
            artifact = build_artifact(ctx)
            session.artifacts.append(artifact)
            await asyncio.sleep(total * 0.2)
            await emit(RuntimeEventKind.artifact, {"type": artifact.type, "title": artifact.title})
            session.status = "completed"
            await emit(RuntimeEventKind.completed, {"summary": f"完成任务：{ctx.title}"})
        except asyncio.CancelledError:
            session.status = "cancelled"
            log.info("mock task cancelled")
            raise
        except Exception as exc:  # pragma: no cover - defensive
            session.status = "failed"
            log.exception("mock task crashed")
            await emit(RuntimeEventKind.error, {"message": str(exc)})
