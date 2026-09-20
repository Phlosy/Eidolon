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
from dataclasses import dataclass
from weakref import WeakSet

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.redaction import redact
from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project, WorkSession
from app.models.runtime_execution import STOP_REASONS, RuntimeAttempt
from app.runtimes.base import RuntimeSession, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.gateway import gateway
from app.work import management_dispatch as dispatch
from app.work import runtime_admission, tool_executor
from app.work.dispatch_notices import begin_observation
from app.work.runtime_conversation import ConversationLimits, run_conversation
from app.work.telemetry import record_stop_intent

logger = get_logger(__name__)
_DEFAULT_LIMITS = ConversationLimits()
_FINALIZATION_DELAYS = (0, 0.1, 0.5)


@dataclass
class _OwnedConversation:
    view: dispatch.DispatchView
    task: asyncio.Task
    finalizing: bool = False


class ManagementFinalizationError(RuntimeError):
    """Owned terminal writes exhausted their live-process retry allowance."""

    def __init__(self, work_session_ids):
        self.work_session_ids = tuple(sorted(work_session_ids))
        super().__init__(f"Management session finalization exhausted: {self.work_session_ids}")


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
        self._owned: dict[int, _OwnedConversation] = {}
        self._finalization_failures: dict[int, dict] = {}
        self._wake: asyncio.Event | None = None
        self._engines = WeakSet()

    @property
    def finalization_failures(self) -> dict[int, dict]:
        """Sanitized intended terminal facts retained after exhausted local cleanup."""
        return {key: dict(value) for key, value in self._finalization_failures.items()}

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
        if self._finalization_failures:
            raise ManagementFinalizationError(self._finalization_failures)
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
        cancelled = await self._drain(cleanup)
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        # Admission is already fenced. Cancel every owner before draining a
        # potentially slow scan, so a late model reply cannot write meanwhile.
        tasks = set(self._jobs.values()) | {owned.task for owned in self._owned.values()}
        if self._scanner is not None:
            tasks.add(self._scanner)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._scanner = None
        self._jobs.clear()
        if self._finalization_failures:
            raise ManagementFinalizationError(self._finalization_failures)

    @staticmethod
    async def _drain(task: asyncio.Task) -> bool:
        """Drain through repeated cancellation; the owner must read the result."""
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                # Let the owner apply cancellation policy before propagating a
                # transaction error, or retain an exhausted cleanup failure.
                break
        return cancelled

    async def _database(self, operation, *args, **kwargs):
        # to_thread preserves request company scope. Each operation creates and
        # closes its own session in that thread; no Session crosses an await.
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled = await self._drain(task)
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()

    def _check_running(self) -> None:
        if not self._running or asyncio.current_task().cancelling():
            raise asyncio.CancelledError

    async def _scan_loop(self) -> None:
        while self._running:
            self._wake.clear()
            try:
                await self._interrupt_invalid_owned()
                self._check_running()
                await self._database(
                    dispatch.discover_sources,
                    limit=self._batch_size,
                    session_factory=self._session_factory,
                )
                self._check_running()
                sources = await self._database(
                    dispatch.pending_source_ids,
                    limit=self._batch_size,
                    session_factory=self._session_factory,
                )
                for source_id in sources:
                    self._check_running()
                    if source_id not in self._jobs:
                        try:
                            view = await self._database(
                                dispatch.inspect_source,
                                source_id,
                                enabled=self._enabled,
                                session_factory=self._session_factory,
                            )
                            self._check_running()
                            if (
                                view is not None
                                and (
                                    view.waiting_reason == "budget_unavailable"
                                    or view.details.get("unresolved_attempt_ids")
                                )
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
            # The ordinary path keeps its existing single atomic claim. Only a
            # rejected claim with original unresolved runs gets one query pass.
            for admission_pass in range(2):
                allowed = False
                try:
                    async with asyncio.timeout(self._admission_timeout) as admission:
                        allowed = await self._budget_available(source_id)
                    if admission.expired():
                        allowed = False
                except Exception:
                    pass
                self._check_running()
                # Recheck all facts and budget after a query. Cancellation drains
                # the atomic claim and delivers any committed session to cleanup.
                claiming = asyncio.create_task(
                    asyncio.to_thread(
                        dispatch.claim_source,
                        source_id,
                        enabled=self._enabled,
                        budget_available=allowed is True,
                        session_factory=self._session_factory,
                    )
                )
                cancelled = await self._drain(claiming)
                claim = claiming.result()
                if claim.created:
                    await self._run_owned(claim.dispatch)
                if cancelled:
                    raise asyncio.CancelledError
                pending = (claim.dispatch.details if claim.dispatch else {}).get(
                    "unresolved_attempt_ids", ()
                )
                if claim.created or admission_pass or allowed is not True or not pending:
                    return
                await runtime_admission.reconcile_attempts(
                    pending,
                    adapter_for=gateway.adapter_for,
                    session_factory=self._session_factory,
                )
                self._check_running()
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
            return self._validate_runtime_type(db, view)

    def _validate_runtime_type(self, db, view: dispatch.DispatchView) -> str:
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

    def _stop_reason(self, view: dispatch.DispatchView) -> str | None:
        """Observe committed withdrawal and save intent before cancelling an owner."""
        with self._session_factory() as db:
            begin_observation(db)
            attempts = list(
                db.scalars(
                    select(RuntimeAttempt)
                    .where(RuntimeAttempt.work_session_id == view.work_session_id)
                    .order_by(RuntimeAttempt.id)
                )
            )
            for attempt in attempts:
                if attempt.stop_requested_at is not None:
                    return attempt.stop_reason
            try:
                self._validate_runtime_type(db, view)
            except tool_executor.ToolExecutionError as exc:
                reason = (
                    "reassigned" if exc.reason == "work_session_manager_invalid" else "cancelled"
                )
                for attempt in attempts:
                    # A scanner snapshot can outlive normal completion. Completed
                    # turns remain historical facts without a synthetic stop request.
                    if attempt.ended_at is None:
                        record_stop_intent(db, attempt.id, reason)
                db.commit()
                return reason
        return None

    async def _interrupt_invalid_owned(self) -> None:
        for work_id, owned in list(self._owned.items()):
            owner = owned.task
            if owned.finalizing or owner.done() or owner.cancelling():
                continue
            try:
                reason = await self._database(self._stop_reason, owned.view)
            except Exception:
                # A transient read/write failure is not proof of scope withdrawal.
                logger.warning("management stop observation failed: session=%s", work_id)
                continue
            if (
                reason is not None
                and self._owned.get(work_id) is owned
                and not owned.finalizing
                and not owner.done()
                and not owner.cancelling()
            ):
                owner.cancel(reason)

    async def _checked_runtime_type(self, view: dispatch.DispatchView) -> str:
        self._check_running()
        runtime_type = await self._database(self._runtime_type, view)
        self._check_running()
        return runtime_type

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
        owned = _OwnedConversation(view, asyncio.current_task())
        self._owned[view.work_session_id] = owned
        try:
            self._check_running()
            runtime_type = await self._checked_runtime_type(view)
            adapter = gateway.adapter_for(runtime_type)
            if adapter.type != runtime_type or not adapter.supports_controlled_turn:
                outcome["reason"] = "controlled_turn_unsupported"
                return
            instance = await self._database(
                resolve_container_instance,
                view.recipient_employee_id,
                runtime_type,
                session_factory=self._session_factory,
            )
            await self._checked_runtime_type(view)
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
            await self._checked_runtime_type(view)

            async def budget_check(work_session_id: int, request_id: str) -> bool:
                self._check_running()
                await self._checked_runtime_type(view)
                async with asyncio.timeout(self._admission_timeout) as admission:
                    allowed = await self._budget_check(work_session_id, request_id)
                self._check_running()
                await self._checked_runtime_type(view)
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
        except asyncio.CancelledError as exc:
            reason = exc.args[0] if exc.args and exc.args[0] in STOP_REASONS else "runner_cancelled"
            outcome = {"status": "cancelled", "reason": reason}
            raise
        except tool_executor.ToolExecutionError as exc:
            outcome = {"status": "failed", "reason": exc.reason}
        except TimeoutError:
            outcome = {"status": "failed", "reason": "runtime_setup_timeout"}
        except Exception:
            # Never persist provider/connection/exception bodies.
            pass
        finally:
            owned.finalizing = True
            # The terminal outcome is fixed before this await. Shutdown must not
            # replace a completed result or abandon a retry already in progress.
            try:
                cleanup = asyncio.create_task(
                    self._retry_finalization(view, outcome, runtime_session),
                    name=f"management-finalize-{view.work_session_id}",
                )
                cancelled = await self._drain(cleanup)
                cleanup.result()
                if cancelled:
                    raise asyncio.CancelledError
            finally:
                if self._owned.get(view.work_session_id) is owned:
                    self._owned.pop(view.work_session_id)

    async def _retry_finalization(self, view, outcome, runtime_session) -> None:
        for attempt, delay in enumerate(_FINALIZATION_DELAYS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._database(self._finalize, view, outcome, runtime_session)
                return
            except Exception:
                # No provider, connection, or exception body enters these facts.
                logger.warning(
                    "management finalization failed: source=%s session=%s attempt=%s",
                    view.source_event_id,
                    view.work_session_id,
                    attempt,
                )
        self._finalization_failures[view.work_session_id] = {
            "source_event_id": view.source_event_id,
            **self._terminal_values(outcome, runtime_session),
        }
        logger.error(
            "management finalization exhausted: source=%s session=%s",
            view.source_event_id,
            view.work_session_id,
        )
        raise ManagementFinalizationError([view.work_session_id])

    @staticmethod
    def _terminal_values(outcome: dict, runtime_session) -> dict:
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
        return {
            "status": (
                "completed"
                if finished
                else "cancelled"
                if outcome.get("status") == "cancelled"
                else "failed"
            ),
            "summary": redact(str(outcome.get("summary") or ""))[:2000],
            "error": redact(error)[:512] if error else None,
            "ended_at": utcnow(),
        }

    def _finalize(self, view: dispatch.DispatchView, outcome: dict, runtime_session) -> None:
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
                .values(**self._terminal_values(outcome, runtime_session))
            )
            db.commit()


# Lifespan separately gates registration/start. Without supplied budget hooks,
# explicit enablement records budget_unavailable and performs no runtime I/O.
management_runner = ManagementRunner(enabled=True)
