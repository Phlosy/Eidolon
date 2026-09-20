"""Bounded wakeups for persisted management/review facts and owned conversations.

The event handler only nudges a scanner. Startup and periodic scans recover
missed broadcasts from durable source facts; they never replay the event bus or
restart an earlier running session. Budget hooks are mandatory admission seams,
not accounting: their provider/relay must independently enforce its spending cap.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from weakref import WeakSet

from sqlalchemy import update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.redaction import redact
from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project, WorkSession
from app.runtimes.base import RuntimeSession, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.gateway import gateway
from app.work import management_dispatch as dispatch
from app.work import tool_executor
from app.work.dispatch_notices import begin_observation
from app.work.runtime_conversation import ConversationLimits, run_conversation

logger = get_logger(__name__)
_DEFAULT_LIMITS = ConversationLimits()


class ManagementRunner:
    def __init__(
        self,
        *,
        session_factory=SessionLocal,
        enabled: bool = False,
        budget_available: Callable[[int], Awaitable[bool]] | None = None,
        budget_check: Callable[[int, str], Awaitable[bool]] | None = None,
        limits: ConversationLimits = _DEFAULT_LIMITS,
        poll_interval: float = 1,
        batch_size: int = 25,
        max_concurrency: int = 2,
        admission_timeout: float = 5,
        startup_timeout: float = 10,
    ):
        for name, value, maximum in (
            ("poll_interval", poll_interval, 300),
            ("admission_timeout", admission_timeout, 60),
            ("startup_timeout", startup_timeout, 60),
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise ValueError(f"{name} must be finite, positive and at most {maximum}")
        for name, value, maximum in (
            ("batch_size", batch_size, dispatch.MAX_BATCH),
            ("max_concurrency", max_concurrency, 16),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer in 1..{maximum}")
        self._session_factory = session_factory
        self._enabled = enabled is True
        self._budget_available, self._budget_check = budget_available, budget_check
        self._limits = limits
        self._poll_interval, self._batch_size = poll_interval, batch_size
        self._max_concurrency = max_concurrency
        self._admission_timeout, self._startup_timeout = admission_timeout, startup_timeout
        self._running = False
        self._scanner: asyncio.Task | None = None
        self._stop_task: asyncio.Task | None = None
        self._jobs: dict[int, asyncio.Task] = {}
        self._wake: asyncio.Event | None = None
        self._engines = WeakSet()

    def register(self, engine) -> None:
        """Idempotently attach to the existing consumer; never subscribe separately."""
        if engine not in self._engines:
            engine.register("management_runner", dispatch.SOURCE_EVENTS, self._nudge)
            self._engines.add(engine)

    def _nudge(self, _message: dict) -> None:
        # Do not read targets, actors, goals, or even event IDs from the broadcast.
        if self._running and self._wake is not None:
            self._wake.set()

    def start(self) -> None:
        """Schedule startup discovery. Calls during a draining stop are no-ops."""
        if self._running or (self._stop_task is not None and not self._stop_task.done()):
            return
        self._running = True
        self._stop_task = None
        self._wake = asyncio.Event()
        self._scanner = asyncio.create_task(self._scan_loop(), name="management-source-scanner")

    async def stop(self) -> None:
        """Stop admission, then cancel/drain only conversations claimed by this runner."""
        if self._stop_task is None:
            if not self._running:
                return
            self._running = False
            self._stop_task = asyncio.create_task(self._shutdown(), name="management-runner-stop")
        cleanup = self._stop_task
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated cancellation of a lifespan waiter still must not
                # abandon cleanup or cancel the independent draining task.
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        if self._scanner is not None:
            self._scanner.cancel()
            await asyncio.gather(self._scanner, return_exceptions=True)
            self._scanner = None
        jobs = list(self._jobs.values())
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self._jobs.clear()

    def _check_running(self) -> None:
        if not self._running or asyncio.current_task().cancelling():
            raise asyncio.CancelledError

    async def _scan_loop(self) -> None:
        while self._running:
            self._wake.clear()
            try:
                dispatch.discover_sources(
                    limit=self._batch_size, session_factory=self._session_factory
                )
                sources = dispatch.pending_source_ids(
                    limit=self._batch_size, session_factory=self._session_factory
                )
                for source_id in sources:
                    self._check_running()
                    if source_id not in self._jobs:
                        try:
                            view = dispatch.inspect_source(
                                source_id,
                                enabled=self._enabled,
                                session_factory=self._session_factory,
                            )
                            if (
                                view is not None
                                and view.waiting_reason == "budget_unavailable"
                                and callable(self._budget_available)
                                and callable(self._budget_check)
                                and len(self._jobs) < self._max_concurrency
                            ):
                                self._jobs[source_id] = asyncio.create_task(
                                    self._admit_and_run(source_id),
                                    name=f"management-source-{source_id}",
                                )
                        except Exception:
                            logger.warning(
                                "management source inspection failed: source=%s", source_id
                            )
                    # Fairness is persisted by inspect_source's checked_at; a waiting
                    # source cannot monopolize this bounded batch or the event loop.
                    await asyncio.sleep(0)
            except Exception:
                logger.warning("management source scan failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass

    async def _admit_and_run(self, source_id: int) -> None:
        try:
            allowed = False
            try:
                async with asyncio.timeout(self._admission_timeout) as admission:
                    allowed = await self._budget_available(source_id)
                if admission.expired():
                    allowed = False
            except Exception:
                pass
            self._check_running()
            # Re-read all source/actor/target facts after admission's await. There
            # is no await between an atomic claim and entry into its cleanup scope.
            claim = dispatch.claim_source(
                source_id,
                enabled=self._enabled,
                budget_available=allowed is True,
                session_factory=self._session_factory,
            )
            if claim.created:
                await self._run_owned(claim.dispatch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("management source execution failed: source=%s", source_id)
        finally:
            self._jobs.pop(source_id, None)

    def _runtime_type(self, view: dispatch.DispatchView) -> str:
        """Validate the original actor/target again before and after setup awaits."""
        with self._session_factory() as db:
            begin_observation(db)
            ctx = tool_executor.context_for_work_session(db, view.work_session_id)
            work = db.get(WorkSession, view.work_session_id)
            actor = db.get(Employee, ctx.employee_id)
            project = db.get(Project, ctx.project_id)
            if (
                not self._enabled
                or settings.runtime_mode != "auto"
                or project.planning_fixture not in (None, "", "none")
                or work.runtime_type not in {"hermes", "openclaw"}
                or actor.runtime_type != work.runtime_type
                or work.purpose != view.purpose
                or work.project_id != view.project_id
                or work.employee_id != view.recipient_employee_id
                or work.review_request_id != view.review_request_id
            ):
                raise tool_executor.ToolExecutionError("runtime_binding_mismatch", "setup changed")
            return work.runtime_type

    @staticmethod
    def _goal(view: dispatch.DispatchView) -> str:
        fact = (
            f"Source event {view.source_event_id} ({view.source_type}), Project {view.project_id}. "
        )
        if view.purpose == "review":
            return fact + (
                f"ReviewRequest {view.review_request_id}, source Task {view.task_id}. "
                "Read inspect_task_review using that task_id, evaluate the actual review facts, "
                "and acknowledge the actual verdict receipt using the registered tools."
            )
        return fact + (
            "Read inspect_project using that project_id. Decide toward the actual project goal "
            "using the registered tools and acknowledge their actual execution receipts."
        )

    async def _run_owned(self, view: dispatch.DispatchView) -> None:
        outcome = {"status": "failed", "reason": "runtime_setup_failed"}
        runtime_session = None
        try:
            self._check_running()
            runtime_type = self._runtime_type(view)
            adapter = gateway.adapter_for(runtime_type)
            if adapter.type != runtime_type or not adapter.supports_controlled_turn:
                outcome["reason"] = "controlled_turn_unsupported"
                return
            instance = resolve_container_instance(
                view.recipient_employee_id, runtime_type, session_factory=self._session_factory
            )
            goal = self._goal(view)
            context = TaskContext(
                task_id=None,
                project_id=view.project_id,
                title=f"{view.purpose.capitalize()} for Project {view.project_id}",
                kind=view.purpose,
                description=goal,
            )
            async with asyncio.timeout(self._startup_timeout) as startup:
                runtime_session = await adapter.create_session(instance, context)
            self._check_running()
            if startup.expired():
                outcome["reason"] = "runtime_setup_timeout"
                return
            self._runtime_type(view)

            async def budget_check(work_session_id: int, request_id: str) -> bool:
                self._check_running()
                self._runtime_type(view)
                async with asyncio.timeout(self._admission_timeout) as admission:
                    allowed = await self._budget_check(work_session_id, request_id)
                self._check_running()
                self._runtime_type(view)
                if admission.expired():
                    raise TimeoutError
                return allowed is True

            outcome = await run_conversation(
                view.work_session_id,
                adapter=adapter,
                runtime_session=runtime_session,
                goal=goal,
                budget_check=budget_check,
                limits=self._limits,
                session_factory=self._session_factory,
            )
            self._check_running()
        except asyncio.CancelledError:
            outcome = {"status": "cancelled", "reason": "runner_cancelled"}
            raise
        except tool_executor.ToolExecutionError as exc:
            outcome = {"status": "failed", "reason": exc.reason}
        except TimeoutError:
            outcome = {"status": "failed", "reason": "runtime_setup_timeout"}
        except Exception:
            # Never persist provider/connection/exception bodies.
            pass
        finally:
            self._finalize(view, outcome, runtime_session)

    def _finalize(self, view: dispatch.DispatchView, outcome: dict, runtime_session) -> None:
        finished = outcome.get("status") == "finished" and outcome.get("reason") == "finish"
        error = None if finished else str(outcome.get("reason") or "conversation_failed")
        if isinstance(runtime_session, RuntimeSession):
            stop_unknown = (
                outcome.get("status") == "cancelled" and runtime_session.conversation_started
            ) or any("remote_stop" in turn for turn in outcome.get("turns", []))
            if runtime_session.cancel_confirmed is False or (
                stop_unknown and runtime_session.cancel_confirmed is not True
            ):
                reason = (
                    "remote_stop_unconfirmed"
                    if runtime_session.cancel_confirmed is False
                    else "remote_stop_unknown"
                )
                error = f"{error or 'conversation_failed'}; {reason}"
        with self._session_factory() as db:
            begin_observation(db)
            db.execute(
                update(WorkSession)
                .where(
                    WorkSession.id == view.work_session_id,
                    WorkSession.purpose == view.purpose,
                    WorkSession.task_id.is_(None),
                    WorkSession.project_id == view.project_id,
                    WorkSession.employee_id == view.recipient_employee_id,
                    WorkSession.status == "running",
                    WorkSession.ended_at.is_(None),
                )
                .values(
                    status=(
                        "completed"
                        if finished
                        else "cancelled"
                        if outcome.get("status") == "cancelled"
                        else "failed"
                    ),
                    summary=redact(str(outcome.get("summary") or ""))[:2000],
                    error=redact(error)[:512] if error else None,
                    ended_at=utcnow(),
                )
            )
            db.commit()


# Lifespan separately gates registration/start. Without supplied budget hooks,
# explicit enablement records budget_unavailable and performs no runtime I/O.
management_runner = ManagementRunner(enabled=True)
