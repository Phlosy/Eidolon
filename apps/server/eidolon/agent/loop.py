"""The durable agent loop (v2-architecture §4.2).

    start(spec)      agent.run.started, then continue(state)
    continue(state)  while the run is not terminal:
                       cancel requested?           → agent.run.cancelled, stop
                       tools of the last step left → execute them (same idempotency keys)
                       step requested, no answer   → ask the model again (attempt + 1)
                       otherwise                   → next step:
                           agent.step.requested  {context_ref}  ← committed BEFORE the call
                           model call            (Ledgered provider adds agent.model.called)
                           agent.step.completed  {response_ref, tool_calls, usage}
                           per tool call: agent.tool.requested → execute → agent.tool.completed
                           end_turn → agent.run.completed; anything else → agent.run.failed
    resume(run_id)   RunState.from_facts, then continue(state)

Every fact is its own committed transaction and every idempotency key is a pure
function of (run, step, attempt, call), so a process that dies anywhere and is
restarted writes the same facts again as no-ops and picks up exactly where the log
stops. There is no other recovery code.
"""

import asyncio
import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.context import ContextBuilder
from eidolon.agent.memory.recall import MemoryRecall
from eidolon.agent.memory.repository import Viewer
from eidolon.agent.providers.base import (
    CallContext,
    Completed,
    Message,
    Provider,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    Usage,
)
from eidolon.agent.tools import Execution, Tools
from eidolon.kernel.facts import Actor, ActorKind, Fact, NewFact
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger

log = get_logger("eidolon.agent.loop")
PREVIEW = 200


def preview(text: str | None) -> str:
    return (text or "")[:PREVIEW]


@dataclass
class AgentRunner:
    session_factory: sessionmaker
    provider: Provider  # normally Ledgered(...) so every call leaves a fact
    blobs: BlobStore
    tools: Tools
    store: FactStore | None = None
    context_builder: ContextBuilder | None = None
    memory: "MemoryRecall | None" = None  # R2-d: injects recalled memory into the context
    # R3-b: authority check before every tool call; returns a refusal reason or None.
    tool_gate: "Callable[[ToolCall], str | None] | None" = None

    def __post_init__(self) -> None:
        self.store = self.store or FactStore()
        self.context_builder = self.context_builder or ContextBuilder()

    # -- public ------------------------------------------------------------------

    async def start(self, spec: R.RunSpec) -> R.RunOutcome:
        system_ref = self.blobs.put_json(spec.system)
        prompt_ref = self.blobs.put_json([Message.user(spec.prompt).as_dict()])
        started = await self._append(
            None,
            self._new(
                spec.company_id,
                spec.person_id,
                R.RUN_STARTED,
                {
                    "run_id": str(spec.run_id),
                    "person_id": spec.person_id,
                    "task_id": spec.task_id,
                    "model": spec.model,
                    "system_ref": system_ref,
                    "prompt_ref": prompt_ref,
                    "tools": [t.name for t in self.tools.specs()],
                    "budget": spec.budget.as_dict(),
                    "max_tokens_per_step": spec.max_tokens_per_step,
                    "temperature": spec.temperature,
                },
                R.run_key(spec.run_id, "started"),
                run_id=spec.run_id,
                causation_id=spec.causation_id,
            ),
        )
        state = R.RunState.from_facts(spec.run_id, [started])
        log.info("run.started", extra={"run_id": str(spec.run_id), "model": spec.model})
        return await self.continue_(state)

    def load(self, run_id: UUID) -> R.RunState:
        with self.session_factory() as db:
            facts = self.store.by_correlation(db, run_id, kinds=R.RUN_KINDS)
        return R.RunState.from_facts(run_id, facts)

    async def resume(self, run_id: UUID) -> R.RunOutcome:
        state = await asyncio.to_thread(self.load, run_id)
        log.info(
            "run.resumed",
            extra={
                "run_id": str(run_id),
                "status": state.status,
                "pending_step": state.pending_step,
                "pending_tools": [t.call_id for t in state.pending_tools()],
            },
        )
        return await self.continue_(state)

    def request_cancel(self, run_id: UUID, *, actor: Actor, reason: str) -> Fact:
        """Anyone with the authority to stop a run writes this fact; the loop sees it
        at its next checkpoint. Synchronous: it is a decision, not loop work."""
        state = self.load(run_id)
        new = NewFact(
            company_id=state.company_id,
            actor=actor,
            kind=R.RUN_CANCEL_REQUESTED,
            payload={"run_id": str(run_id), "reason": reason},
            idempotency_key=R.run_key(run_id, "cancel_requested"),
            causation_id=state.last_fact_id,
            correlation_id=run_id,
        )
        return self._write(new)

    # -- the loop ------------------------------------------------------------------

    async def continue_(self, state: R.RunState) -> R.RunOutcome:
        while not state.terminal:
            if await self._cancel_requested(state):
                await self._terminal(state, R.RUN_CANCELLED, {"step_no": state.last_step_no})
                break
            pending_tools = state.pending_tools()
            if pending_tools:
                await self._run_tools(state, pending_tools)
                continue
            if state.last_stop_reason == "end_turn" and state.status == "running":
                # died between step.completed and run.completed
                await self._complete_run(state)
                break
            if state.pending_step is not None:
                step_no, attempt = state.pending_step
                await self._step(state, step_no, attempt + 1)
                continue
            if state.steps_completed >= state.budget.max_steps:
                await self._terminal(
                    state,
                    R.RUN_BUDGET_EXCEEDED,
                    {"reason": "max_steps", "steps": state.steps_completed},
                )
                break
            if (
                state.budget.max_total_tokens is not None
                and state.usage_total.input_tokens + state.usage_total.output_tokens
                >= state.budget.max_total_tokens
            ):
                await self._terminal(
                    state,
                    R.RUN_BUDGET_EXCEEDED,
                    {"reason": "max_total_tokens", "usage": state.usage_total.as_dict()},
                )
                break
            await self._step(state, state.last_step_no + 1, 1)
        return self._outcome(state)

    async def _step(self, state: R.RunState, step_no: int, attempt: int) -> None:
        messages = state.messages(self.blobs)
        specs = self.tools.specs()
        actor = Actor(ActorKind.agent, state.person_id)
        memory_sections: list[str] = []
        if self.memory is not None:
            task = next((m.content for m in messages if m.role == "user" and m.content), "")
            memory_sections = await asyncio.to_thread(
                self.memory.sections, Viewer(state.company_id, state.person_id), task or ""
            )
        request = self.context_builder.build(
            system=state.system(self.blobs),
            messages=messages,
            tools=specs,
            state=state,
            context=CallContext(state.company_id, actor, correlation_id=state.run_id),
            memory_sections=memory_sections,
        )
        context_ref = self.blobs.put_json(request.as_dict())
        requested = await self._append(
            state,
            self._fact(
                state,
                R.STEP_REQUESTED,
                {
                    "step_no": step_no,
                    "attempt": attempt,
                    "context_ref": context_ref,
                    "context_hash": request.fingerprint(),
                    "message_count": len(messages),
                },
                R.step_key(state.run_id, step_no, attempt, "requested"),
            ),
        )
        state.pending_step = (step_no, attempt)
        state.last_step_no, state.last_attempt = step_no, attempt

        # The model call is caused by the request fact; the Ledgered provider records it.
        request = dataclasses.replace(
            request,
            context=CallContext(
                state.company_id,
                actor,
                correlation_id=state.run_id,
                causation_id=requested.id,
                run_id=str(state.run_id),
            ),
        )
        t0 = time.monotonic()
        text, calls, done = "", [], None
        try:
            async for event in self.provider.complete(request):
                if isinstance(event, TextDelta):
                    text += event.text
                elif isinstance(event, ToolCallEvent):
                    calls.append(event.call)
                elif isinstance(event, Completed):
                    done = event
            if done is None:
                raise ProviderError("stream ended without Completed", provider=self.provider.name)
        except ProviderError as exc:
            await self._terminal(
                state,
                R.RUN_FAILED,
                {
                    "step_no": step_no,
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                    "retryable": exc.retryable,
                },
            )
            return

        assistant = Message.assistant(text or None, *calls, provider_state=done.assistant_state)
        response_ref = self.blobs.put_json(assistant.as_dict())
        await self._append(
            state,
            self._fact(
                state,
                R.STEP_COMPLETED,
                {
                    "step_no": step_no,
                    "attempt": attempt,
                    "response_ref": response_ref,
                    "text_preview": preview(text),
                    "tool_calls": [{"id": c.id, "name": c.name} for c in calls],
                    "stop_reason": done.stop_reason,
                    "stop_detail": done.stop_detail,
                    "usage": done.usage.as_dict(),
                    "model": done.model,
                    "request_id": done.request_id,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                },
                R.step_key(state.run_id, step_no, attempt, "completed"),
            ),
        )
        state.pending_step = None
        state.steps_completed += 1
        state.current_calls = [{"id": c.id, "name": c.name} for c in calls]
        state.tools_requested, state.tools_completed = {}, set()
        state.history_refs.append(("assistant", response_ref))
        state.last_stop_reason = done.stop_reason
        u = done.usage
        state.usage_total = Usage(
            state.usage_total.input_tokens + u.input_tokens,
            state.usage_total.output_tokens + u.output_tokens,
            state.usage_total.cache_read_tokens + u.cache_read_tokens,
            state.usage_total.cache_write_tokens + u.cache_write_tokens,
        )
        log.info(
            "run.step",
            extra={
                "run_id": str(state.run_id),
                "step_no": step_no,
                "attempt": attempt,
                "stop_reason": done.stop_reason,
                "tool_calls": [c.name for c in calls],
                "usage": done.usage.as_dict(),
            },
        )
        if calls:
            return  # continue_ will run them (and re-run them after a crash)
        if done.stop_reason == "end_turn":
            await self._complete_run(state)
            return
        await self._terminal(
            state,
            R.RUN_FAILED,
            {
                "step_no": step_no,
                "attempt": attempt,
                "error": f"model stopped with {done.stop_reason}"
                + (f" ({done.stop_detail})" if done.stop_detail else ""),
                "retryable": False,
            },
        )

    async def _run_tools(self, state: R.RunState, pending: list[R.PendingTool]) -> None:
        # The last assistant turn (later refs may be tool results already completed).
        for role, ref in reversed(state.history_refs):
            if role == "assistant":
                assistant = Message.from_dict(self.blobs.get_json(ref))
                break
        by_id: dict[str, ToolCall] = {c.id: c for c in assistant.tool_calls}
        for item in pending:
            if await self._cancel_requested(state):
                return
            call = by_id[item.call_id]
            if item.call_id not in state.tools_requested:
                args_ref = self.blobs.put_json(dict(call.arguments))
                await self._append(
                    state,
                    self._fact(
                        state,
                        R.TOOL_REQUESTED,
                        {
                            "step_no": state.last_step_no,
                            "call_id": call.id,
                            "name": call.name,
                            "arguments_ref": args_ref,
                            "idempotency_key": item.idempotency_key,
                        },
                        item.idempotency_key + ":requested",
                    ),
                )
                state.tools_requested[call.id] = item.idempotency_key
            t0 = time.monotonic()
            refusal = self.tool_gate(call) if self.tool_gate is not None else None
            if refusal is not None:
                result = ToolResult(call.id, refusal, is_error=True)
            else:
                result = await self.tools.execute(
                    Execution(call, item.idempotency_key, str(state.run_id), state.last_step_no)
                )
            if not isinstance(result, ToolResult):
                raise TypeError(f"tool {call.name!r} returned {type(result).__name__}")
            tool_message = Message.tool(call.id, result.content, is_error=result.is_error)
            result_ref = self.blobs.put_json(tool_message.as_dict())
            await self._append(
                state,
                self._fact(
                    state,
                    R.TOOL_COMPLETED,
                    {
                        "step_no": state.last_step_no,
                        "call_id": call.id,
                        "name": call.name,
                        "result_ref": result_ref,
                        "result_preview": preview(result.content),
                        "is_error": result.is_error,
                        "refused": refusal is not None,
                        "idempotency_key": item.idempotency_key,
                        "duration_ms": round((time.monotonic() - t0) * 1000),
                    },
                    item.idempotency_key + ":completed",
                ),
            )
            state.tools_completed.add(call.id)
            state.history_refs.append(("tool", result_ref))
            log.info(
                "run.tool",
                extra={
                    "run_id": str(state.run_id),
                    "step_no": state.last_step_no,
                    "tool": call.name,
                    "is_error": result.is_error,
                },
            )

    async def _complete_run(self, state: R.RunState) -> None:
        final = Message.from_dict(self.blobs.get_json(state.history_refs[-1][1]))
        await self._terminal(
            state,
            R.RUN_COMPLETED,
            {
                "steps": state.steps_completed,
                "final_ref": state.history_refs[-1][1],
                "final_preview": preview(final.content),
                "usage": state.usage_total.as_dict(),
            },
        )
        state.final_ref = state.history_refs[-1][1]

    async def _terminal(self, state: R.RunState, kind: str, payload: dict[str, Any]) -> None:
        await self._append(state, self._fact(state, kind, payload, R.run_key(state.run_id, kind)))
        state.status = {
            R.RUN_COMPLETED: "completed",
            R.RUN_FAILED: "failed",
            R.RUN_CANCELLED: "cancelled",
            R.RUN_BUDGET_EXCEEDED: "budget_exceeded",
        }[kind]
        if kind == R.RUN_FAILED:
            state.error = payload.get("error")
        log.info("run.ended", extra={"run_id": str(state.run_id), "status": state.status})

    async def _cancel_requested(self, state: R.RunState) -> bool:
        if state.cancel_requested:
            return True

        def check() -> bool:
            with self.session_factory() as db:
                return bool(
                    self.store.by_correlation(db, state.run_id, kinds=[R.RUN_CANCEL_REQUESTED])
                )

        state.cancel_requested = await asyncio.to_thread(check)
        return state.cancel_requested

    # -- plumbing ------------------------------------------------------------------

    def _fact(self, state: R.RunState, kind: str, payload: dict[str, Any], key: str) -> NewFact:
        return self._new(
            state.company_id,
            state.person_id,
            kind,
            {"run_id": str(state.run_id), **payload},
            key,
            run_id=state.run_id,
            causation_id=state.last_fact_id,
        )

    @staticmethod
    def _new(company_id, person_id, kind, payload, key, *, run_id, causation_id) -> NewFact:
        return NewFact(
            company_id=company_id,
            actor=Actor(ActorKind.agent, person_id),
            kind=kind,
            payload=payload,
            idempotency_key=key,
            causation_id=causation_id,
            correlation_id=run_id,
        )

    def _write(self, new: NewFact) -> Fact:
        db = self.session_factory()
        try:
            fact = self.store.append(db, new)
            db.commit()
            return fact
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    async def _append(self, state: R.RunState | None, new: NewFact) -> Fact:
        """Commit one fact, then remember it as the cause of the next one."""
        fact = await asyncio.to_thread(self._write, new)
        if state is not None:
            state.last_fact_id = fact.id
        return fact

    def _outcome(self, state: R.RunState) -> R.RunOutcome:
        final_text = None
        if state.final_ref:
            final_text = Message.from_dict(self.blobs.get_json(state.final_ref)).content
        return R.RunOutcome(
            state.run_id,
            state.status,
            state.steps_completed,
            final_text,
            state.usage_total,
            state.error,
        )
