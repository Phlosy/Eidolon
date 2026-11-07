"""What the loop needs from tools: their specs and an idempotent executor.

The runner-backed implementation (sandboxed shell / files, R2-c) and the
service-tool registry with side-effect levels and gates (R3) both satisfy this
protocol. ``FunctionTools`` is the in-process implementation: tests, and simple
read-only helpers.

Idempotency: the loop hands every execution the same ``idempotency_key`` it wrote
into ``agent.tool.requested``. After a crash the key is handed over again, so an
executor with side effects must check whether that key already ran (the runner
keeps a ledger; the loop never guesses).
"""

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from eidolon.agent.providers.base import ToolCall, ToolResult, ToolSpec

Handler = Callable[..., Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Execution:
    call: ToolCall
    idempotency_key: str
    run_id: str
    step_no: int


@runtime_checkable
class Tools(Protocol):
    def specs(self) -> tuple[ToolSpec, ...]: ...

    async def execute(self, execution: Execution) -> ToolResult: ...


@dataclass
class FunctionTools:
    """Tools backed by Python callables. A handler receives the call arguments as
    keyword arguments (plus ``_execution`` if it declares it) and returns a string
    or JSON-able value; an exception becomes an error result the model can see."""

    _tools: dict[str, tuple[ToolSpec, Handler]] = field(default_factory=dict)

    def add(self, spec: ToolSpec, handler: Handler) -> "FunctionTools":
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = (spec, handler)
        return self

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec, _ in self._tools.values())

    async def execute(self, execution: Execution) -> ToolResult:
        call = execution.call
        entry = self._tools.get(call.name)
        if entry is None:
            return ToolResult(call.id, f"unknown tool {call.name!r}", is_error=True)
        _, handler = entry
        kwargs: dict[str, Any] = dict(call.arguments)
        if "_execution" in inspect.signature(handler).parameters:
            kwargs["_execution"] = execution
        try:
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # the model gets to see tool failures
            return ToolResult(call.id, f"{type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(call.id, _as_text(result))


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping | list | tuple | int | float | bool) or value is None:
        return json.dumps(value, ensure_ascii=False)
    return str(value)


__all__ = ["Execution", "FunctionTools", "Handler", "ToolCall", "ToolResult", "ToolSpec", "Tools"]
