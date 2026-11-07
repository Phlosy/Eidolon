"""Every model call becomes an ``agent.model.called`` fact (v2-architecture §4.6):
usage, cost estimate, request id, latency — what the money layer later meters and
what provenance traces back to.

``Ledgered`` wraps any provider; the request must carry a ``CallContext`` (who pays,
who acts, what caused it). The fact is written after the stream completes, in its
own transaction, keyed by the provider's request id so a replayed call is not
double-counted.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import sessionmaker

from eidolon.agent.providers import pricing
from eidolon.agent.providers.base import (
    CallContext,
    ChatEvent,
    ChatRequest,
    Completed,
    Provider,
)
from eidolon.kernel.facts import Fact, NewFact
from eidolon.kernel.store import FactStore

KIND = "agent.model.called"


def model_called_fact(request: ChatRequest, completed: Completed, *, mode: str = "live") -> NewFact:
    ctx = request.context
    if ctx is None:
        raise ValueError("a ledgered call needs request.context (CallContext)")
    fingerprint = request.fingerprint()
    cost = pricing.estimate(completed.model, completed.usage)
    key_tail = completed.request_id or f"{fingerprint}:{completed.latency_ms}"
    payload: dict[str, Any] = {
        "provider": completed.provider,
        "model": completed.model,
        "requested_model": request.model,
        "mode": mode,
        "request_fingerprint": fingerprint,
        "request_id": completed.request_id,
        "stop_reason": completed.stop_reason,
        "stop_detail": completed.stop_detail,
        "usage": completed.usage.as_dict(),
        "cost": cost.as_dict() if cost else None,
        "latency_ms": completed.latency_ms,
        "attempts": completed.attempts,
        "run_id": ctx.run_id,
        "tools": [t.name for t in request.tools],
        "message_count": len(request.messages),
    }
    return NewFact(
        company_id=ctx.company_id,
        actor=ctx.actor,
        kind=KIND,
        payload=payload,
        idempotency_key=f"{KIND}:{completed.provider}:{key_tail}"[:200],
        causation_id=ctx.causation_id,
        correlation_id=ctx.correlation_id,
    )


@dataclass
class ModelCallLedger:
    session_factory: sessionmaker
    store: FactStore | None = None

    def record(self, request: ChatRequest, completed: Completed, *, mode: str = "live") -> Fact:
        store = self.store or FactStore()
        new = model_called_fact(request, completed, mode=mode)
        db = self.session_factory()
        try:
            fact = store.append(db, new)
            db.commit()
            return fact
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


class Ledgered:
    """A provider that writes one fact per completed call."""

    def __init__(self, inner: Provider, ledger: ModelCallLedger, *, mode: str = "live"):
        self.inner = inner
        self.ledger = ledger
        self.mode = mode
        self.name = inner.name
        self.last_fact: Fact | None = None

    async def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        if request.context is None:
            raise ValueError("a ledgered call needs request.context (CallContext)")
        async for event in self.inner.complete(request):
            if isinstance(event, Completed):
                self.last_fact = await asyncio.to_thread(
                    self.ledger.record, request, event, mode=self.mode
                )
            yield event

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        return await self.inner.embed(texts, model=model)


__all__ = ["KIND", "CallContext", "Ledgered", "ModelCallLedger", "model_called_fact"]
