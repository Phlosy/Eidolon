"""Episodic memory is written by reflection: when a run completes, one model call
turns its transcript into a diary entry, recorded as ``agent.memory.recorded``.

``Reflector`` is a kernel consumer of ``agent.run.completed``, so reflection runs
in the worker, after the fact, and never blocks or fails the run itself. The
request is built only from the transcript (never from ids or timestamps), so a
replayed run produces the same request fingerprint and the same fixture answers.
"""

import asyncio
import dataclasses
import json
import re
from collections.abc import Sequence
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobMissing, BlobStore
from eidolon.agent.memory import facts as M
from eidolon.agent.providers.base import (
    CallContext,
    ChatRequest,
    Completed,
    Message,
    Provider,
    TextDelta,
)
from eidolon.kernel.facts import Actor, ActorKind, Fact
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger

log = get_logger("eidolon.agent.memory.reflection")

REFLECTION_SYSTEM = (
    "You write the private work diary of an AI employee. Given a finished task, write "
    "an entry future-you can retrieve. Reply with JSON only, no prose, exactly this shape: "
    '{"title": "<= 12 words", "summary": "2-4 sentences: what was asked, what you did, the '
    'outcome", "lessons": ["short reusable lessons"], "tags": ["lowercase", "keywords"], '
    '"importance": 0.0-1.0}'
)
MAX_FINAL_CHARS = 2000
MAX_TOOL_LINES = 20


def reflection_id(run_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:run:{run_id}:reflection")


def transcript_summary(state: R.RunState, blobs: BlobStore) -> str:
    """Deterministic text about the run: task, tool calls, final answer, step count."""
    messages = state.messages(blobs)
    task = next((m.content for m in messages if m.role == "user" and m.content), "") or ""
    tool_lines: list[str] = []
    for m in messages:
        if m.role == "assistant":
            for call in m.tool_calls:
                args = json.dumps(dict(call.arguments), ensure_ascii=False, sort_keys=True)
                tool_lines.append(f"- {call.name}({args[:200]})")
        elif m.role == "tool" and m.tool_result is not None:
            status = "error" if m.tool_result.is_error else "ok"
            tool_lines.append(f"  -> {status}: {m.tool_result.content[:160]!r}")
    if len(tool_lines) > MAX_TOOL_LINES:
        tool_lines = tool_lines[:MAX_TOOL_LINES] + [
            f"  ... {len(tool_lines) - MAX_TOOL_LINES} more"
        ]
    final = ""
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            final = m.content
            break
    parts = [f"Task:\n{task.strip()}", f"Steps taken: {state.steps_completed}"]
    parts.append("Tool calls:\n" + ("\n".join(tool_lines) if tool_lines else "(none)"))
    parts.append(f"Final answer:\n{final.strip()[:MAX_FINAL_CHARS]}")
    return "\n\n".join(parts)


def build_request(state: R.RunState, blobs: BlobStore, *, model: str | None, max_tokens: int):
    return ChatRequest(
        model=model or state.model,
        system=REFLECTION_SYSTEM,
        messages=(Message.user(transcript_summary(state, blobs)),),
        max_tokens=max_tokens,
        temperature=0,
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_reflection(text: str) -> tuple[dict[str, Any], bool]:
    """Returns (fields, parsed). A model that did not produce JSON still yields a
    usable entry (raw text as body) so the consumer never wedges on one run."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        try:
            data = json.loads(cleaned[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or "summary" not in data:
            first = cleaned.splitlines()[0] if cleaned.strip() else "Reflection"
            return (
                {
                    "title": first[: M.MAX_TITLE],
                    "summary": cleaned[: M.MAX_BODY] or "(empty reflection)",
                    "lessons": [],
                    "tags": [],
                    "importance": 0.5,
                },
                False,
            )
    try:
        importance = float(data.get("importance", 0.5))
    except (TypeError, ValueError):
        importance = 0.5
    lessons = data.get("lessons") or []
    return (
        {
            "title": str(data.get("title") or "Reflection")[: M.MAX_TITLE],
            "summary": str(data.get("summary") or "")[: M.MAX_BODY] or "(empty reflection)",
            "lessons": [str(x) for x in lessons if str(x).strip()][:10]
            if isinstance(lessons, list)
            else [],
            "tags": list(M.tags_of(data.get("tags") or [])),
            "importance": min(max(importance, 0.0), 1.0),
        },
        True,
    )


def entry_from(fields: dict[str, Any], state: R.RunState, *, parsed: bool, model: str) -> M.Entry:
    body = fields["summary"]
    if fields["lessons"]:
        body += "\n\nLessons:\n" + "\n".join(f"- {x}" for x in fields["lessons"])
    return M.Entry(
        memory_id=reflection_id(state.run_id),
        person_id=state.person_id,
        layer="episodic",
        scope="private",
        kind="reflection",
        title=fields["title"],
        body=body[: M.MAX_BODY],
        tags=tuple(fields["tags"]),
        source={
            "run_id": str(state.run_id),
            "task_id": state.task_id,
            "steps": state.steps_completed,
            "model": model,
            "parsed": parsed,
        },
        importance=fields["importance"],
    )


class Reflector:
    """Consumer: ``agent.run.completed`` → one model call → ``agent.memory.recorded``."""

    name = "memory_reflector"
    handles = frozenset({R.RUN_COMPLETED})

    def __init__(
        self,
        provider: Provider,
        blobs: BlobStore,
        *,
        store: FactStore | None = None,
        model: str | None = None,
        max_tokens: int = 512,
    ):
        self.provider = provider
        self.blobs = blobs
        self.store = store or FactStore()
        self.model = model
        self.max_tokens = max_tokens

    def handle(self, db: Session, fact: Fact) -> None:
        run_id = UUID(fact.payload["run_id"])
        wanted = str(reflection_id(run_id))
        for existing in self.store.by_correlation(db, run_id, kinds=[M.RECORDED]):
            if existing.payload.get("memory_id") == wanted:
                return  # already reflected (e.g. by an earlier process); no second model call
        facts = self.store.by_correlation(db, run_id, kinds=R.RUN_KINDS)
        state = R.RunState.from_facts(run_id, facts)
        try:
            request = build_request(state, self.blobs, model=self.model, max_tokens=self.max_tokens)
        except BlobMissing as exc:
            # Transcript gone (retention): nothing to reflect on; move on, do not wedge.
            log.warning("reflection.skipped", extra={"run_id": str(run_id), "error": str(exc)})
            return
        actor = Actor(ActorKind.agent, state.person_id)
        request = dataclasses.replace(
            request,
            context=CallContext(
                state.company_id,
                actor,
                correlation_id=run_id,
                causation_id=fact.id,
                run_id=str(run_id),
            ),
        )
        text = run_sync(self._complete(request))
        fields, parsed = parse_reflection(text)
        entry = entry_from(fields, state, parsed=parsed, model=request.model)
        new = M.record(
            state.company_id,
            actor,
            entry,
            idempotency_key=f"run:{run_id}:reflection",
            causation_id=fact.id,
            correlation_id=run_id,
        )
        recorded = self.store.append(db, new)  # same transaction as the cursor (K6)
        log.info(
            "reflection.recorded",
            extra={"run_id": str(run_id), "memory_id": str(entry.memory_id), "parsed": parsed},
        )
        return recorded

    async def _complete(self, request: ChatRequest) -> str:
        text = ""
        done: Completed | None = None
        async for event in self.provider.complete(request):
            if isinstance(event, TextDelta):
                text += event.text
            elif isinstance(event, Completed):
                done = event
        if done is None:
            raise RuntimeError("reflection stream ended without Completed")
        return text


def run_sync(coro):
    """Run a coroutine from sync consumer code (the worker has no event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Inside a running loop (tests): run on a fresh loop in a helper thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


__all__: Sequence[str] = ["Reflector", "build_request", "parse_reflection", "reflection_id"]
