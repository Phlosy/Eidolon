"""Route each model call to the provider configured for the paying company.

``RoutedProvider`` implements ``Provider``; the request's ``CallContext.company_id``
picks the underlying provider through a resolver callback (the worker supplies one
that decrypts ``provider_credentials``), with the process-wide default as fallback.
Providers are built once per company and rebuilt when the resolver says the
configuration changed (``generation``).
"""

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from eidolon.agent.providers.base import ChatEvent, ChatRequest, Provider


@dataclass(frozen=True, slots=True)
class Resolved:
    provider: Provider
    generation: Any  # anything comparable; a changed value rebuilds the cache entry


Resolver = Callable[[UUID], Resolved | None]


class RoutedProvider:
    def __init__(self, default: Provider, resolve: Resolver):
        self.default = default
        self.resolve = resolve
        self.name = f"routed({default.name})"
        self._cache: dict[UUID, Resolved] = {}

    def for_company(self, company_id: UUID | None) -> Provider:
        if company_id is None:
            return self.default
        resolved = self.resolve(company_id)
        if resolved is None:
            self._cache.pop(company_id, None)
            return self.default
        cached = self._cache.get(company_id)
        if cached is None or cached.generation != resolved.generation:
            self._cache[company_id] = resolved
            return resolved.provider
        return cached.provider

    async def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        ctx = request.context
        provider = self.for_company(ctx.company_id if ctx is not None else None)
        async for event in provider.complete(request):
            yield event

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        return await self.default.embed(texts, model=model)


__all__ = ["Resolved", "Resolver", "RoutedProvider"]
