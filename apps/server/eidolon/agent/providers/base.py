"""Provider-neutral chat protocol (v2-architecture §4.6).

A ``Provider`` turns a ``ChatRequest`` into a stream of ``ChatEvent``s:
``TextDelta`` as text arrives, ``ToolCallEvent`` for each complete tool call, and
exactly one ``Completed`` at the end carrying usage, stop reason and request id.
Errors are ``ProviderError`` with a ``retryable`` flag; a provider retries only
*before* it has emitted anything (a half-delivered stream is never silently
restarted). Everything here is plain data so requests and events can be hashed,
recorded and replayed (``recorder``) and turned into facts (``accounting``).
"""

import asyncio
import hashlib
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from eidolon.kernel.facts import Actor

# -- request ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass(frozen=True, slots=True)
class Message:
    """``user`` text, ``assistant`` text and/or tool calls, or one ``tool`` result."""

    role: str  # user | assistant | tool
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None
    # Provider-opaque state an assistant turn must carry when sent back (DeepSeek's
    # ``reasoning_content``, Anthropic's signed thinking blocks). Taken verbatim from
    # ``Completed.assistant_state``; the loop stores it with the step so a resumed
    # conversation replays exactly.
    provider_state: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant", "tool"}:
            raise ValueError(f"unknown role {self.role!r}")
        if self.role == "tool" and self.tool_result is None:
            raise ValueError("tool message needs a tool_result")
        if self.role != "tool" and self.tool_result is not None:
            raise ValueError("only tool messages carry a tool_result")
        if self.role != "assistant" and (self.tool_calls or self.provider_state):
            raise ValueError("only assistant messages carry tool_calls / provider_state")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [c.as_dict() for c in self.tool_calls]
        if self.tool_result is not None:
            out["tool_result"] = self.tool_result.as_dict()
        if self.provider_state:
            out["provider_state"] = dict(self.provider_state)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        calls = tuple(ToolCall(**c) for c in data.get("tool_calls", ()))
        result = data.get("tool_result")
        return cls(
            role=data["role"],
            content=data.get("content"),
            tool_calls=calls,
            tool_result=ToolResult(**result) if result else None,
            provider_state=data.get("provider_state"),
        )

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls("user", text)

    @classmethod
    def assistant(
        cls,
        text: str | None,
        *calls: ToolCall,
        provider_state: Mapping[str, Any] | None = None,
    ) -> "Message":
        return cls("assistant", text, tuple(calls), provider_state=provider_state)

    @classmethod
    def tool(cls, call_id: str, content: str, *, is_error: bool = False) -> "Message":
        return cls("tool", None, (), ToolResult(call_id, content, is_error))


@dataclass(frozen=True, slots=True)
class CallContext:
    """Who is paying for and causing this call. Not part of the request fingerprint:
    the same prompt replays identically whoever sends it."""

    company_id: UUID
    actor: Actor
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = 4096
    temperature: float | None = None
    context: CallContext | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("a request needs at least one message")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

    def as_dict(self) -> dict[str, Any]:
        """Everything the model sees, in canonical form. Excludes ``context``."""
        return {
            "model": self.model,
            "system": self.system,
            "messages": [m.as_dict() for m in self.messages],
            "tools": [t.as_dict() for t in self.tools],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChatRequest":
        return cls(
            model=data["model"],
            messages=tuple(Message.from_dict(m) for m in data["messages"]),
            system=data.get("system"),
            tools=tuple(ToolSpec(**t) for t in data.get("tools", ())),
            max_tokens=data.get("max_tokens", 4096),
            temperature=data.get("temperature"),
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict())).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


# -- events ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0  # input tokens served from a prompt cache
    cache_write_tokens: int = 0  # input tokens written to a prompt cache

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


STOP_REASONS = frozenset({"end_turn", "tool_use", "max_tokens", "refusal", "other"})


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class Completed:
    stop_reason: str
    usage: Usage
    model: str  # the model that actually answered (providers may alias)
    provider: str
    request_id: str | None = None
    latency_ms: int = 0
    attempts: int = 1
    stop_detail: str | None = None  # e.g. the refusal category
    # What ``Message.assistant(..., provider_state=...)`` must echo back next turn.
    assistant_state: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.stop_reason not in STOP_REASONS:
            raise ValueError(f"unknown stop_reason {self.stop_reason!r}")


ChatEvent = TextDelta | ToolCallEvent | Completed


def event_to_dict(event: ChatEvent) -> dict[str, Any]:
    if isinstance(event, TextDelta):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolCallEvent):
        return {"type": "tool_call", **event.call.as_dict()}
    if isinstance(event, Completed):
        return {
            "type": "completed",
            "stop_reason": event.stop_reason,
            "usage": event.usage.as_dict(),
            "model": event.model,
            "provider": event.provider,
            "request_id": event.request_id,
            "latency_ms": event.latency_ms,
            "attempts": event.attempts,
            "stop_detail": event.stop_detail,
            "assistant_state": dict(event.assistant_state) if event.assistant_state else None,
        }
    raise TypeError(f"not a ChatEvent: {event!r}")


def event_from_dict(data: Mapping[str, Any]) -> ChatEvent:
    kind = data.get("type")
    if kind == "text":
        return TextDelta(data["text"])
    if kind == "tool_call":
        return ToolCallEvent(ToolCall(data["id"], data["name"], data["arguments"]))
    if kind == "completed":
        return Completed(
            stop_reason=data["stop_reason"],
            usage=Usage(**data["usage"]),
            model=data["model"],
            provider=data["provider"],
            request_id=data.get("request_id"),
            latency_ms=data.get("latency_ms", 0),
            attempts=data.get("attempts", 1),
            stop_detail=data.get("stop_detail"),
            assistant_state=data.get("assistant_state"),
        )
    raise ValueError(f"unknown event type {kind!r}")


# -- errors ----------------------------------------------------------------------


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
        status: int | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    def __init__(self, message: str, *, provider: str, request_id: str | None = None):
        super().__init__(message, provider=provider, retryable=True, request_id=request_id)


# -- protocol --------------------------------------------------------------------


@runtime_checkable
class Provider(Protocol):
    name: str

    def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]: ...

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> list[list[float]]: ...


# -- retry -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.2

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        raw = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return raw * (1 + random.uniform(-self.jitter, self.jitter))


@dataclass
class Attempt:
    number: int = 1
    errors: list[ProviderError] = field(default_factory=list)


async def with_retry(
    policy: RetryPolicy,
    open_stream: Callable[[], Awaitable[AsyncIterator[ChatEvent]]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[ChatEvent]:
    """Call ``open_stream`` until it yields its first event, retrying retryable
    failures that happen before that point; once an event is out, failures
    propagate. The ``Completed`` event's ``attempts`` is patched to the real count."""
    attempt = 1
    while True:
        try:
            stream = await open_stream()
            first = await anext(stream)
        except ProviderError as exc:
            if not exc.retryable or attempt >= policy.max_attempts:
                raise
            await sleep(policy.delay(attempt, exc.retry_after))
            attempt += 1
            continue
        yield _with_attempts(first, attempt)
        async for event in stream:
            yield _with_attempts(event, attempt)
        return


def _with_attempts(event: ChatEvent, attempts: int) -> ChatEvent:
    if isinstance(event, Completed) and event.attempts != attempts:
        return Completed(
            stop_reason=event.stop_reason,
            usage=event.usage,
            model=event.model,
            provider=event.provider,
            request_id=event.request_id,
            latency_ms=event.latency_ms,
            attempts=attempts,
            stop_detail=event.stop_detail,
            assistant_state=event.assistant_state,
        )
    return event
