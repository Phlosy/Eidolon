"""Record / replay providers: the v2 replacement for template mocks.

``Recording`` wraps a live provider and writes every completed stream to
``<dir>/<fingerprint>.json`` (request + events). ``Replaying`` serves those
files by request fingerprint and refuses anything it has not seen — a missing
fixture is a loud failure, never an improvised answer. Fixtures are real model
output, so tests see the real distribution while staying deterministic.
"""

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eidolon.agent.providers.base import (
    ChatEvent,
    ChatRequest,
    Provider,
    ProviderError,
    canonical_json,
    event_from_dict,
    event_to_dict,
)

FIXTURE_FORMAT = "eidolon.provider.fixture/1"


class FixtureMissing(ProviderError):
    pass


def embed_fingerprint(texts: Sequence[str], model: str | None) -> str:
    return hashlib.sha256(canonical_json({"model": model, "texts": list(texts)})).hexdigest()


class Recording:
    def __init__(self, inner: Provider, directory: str | Path):
        self.inner = inner
        self.directory = Path(directory)
        self.name = f"record:{inner.name}"

    async def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        events: list[ChatEvent] = []
        async for event in self.inner.complete(request):
            events.append(event)
            yield event
        self._write(
            f"chat-{request.fingerprint()}.json",
            {
                "format": FIXTURE_FORMAT,
                "kind": "chat",
                "provider": self.inner.name,
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "fingerprint": request.fingerprint(),
                "request": request.as_dict(),
                "events": [event_to_dict(e) for e in events],
            },
        )

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        vectors = await self.inner.embed(texts, model=model)
        self._write(
            f"embed-{embed_fingerprint(texts, model)}.json",
            {
                "format": FIXTURE_FORMAT,
                "kind": "embed",
                "provider": self.inner.name,
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "model": model,
                "texts": list(texts),
                "vectors": vectors,
            },
        )
        return vectors

    def _write(self, filename: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / filename
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


class Replaying:
    name = "replay"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    async def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        data = self._load(f"chat-{request.fingerprint()}.json", request.fingerprint())
        for raw in data["events"]:
            yield event_from_dict(raw)

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        fp = embed_fingerprint(texts, model)
        return self._load(f"embed-{fp}.json", fp)["vectors"]

    def _load(self, filename: str, fingerprint: str) -> dict[str, Any]:
        path = self.directory / filename
        if not path.exists():
            raise FixtureMissing(
                f"no recorded fixture {path.name} in {self.directory} (fingerprint {fingerprint}); "
                "run with EIDOLON_V2_PROVIDER_MODE=record against a real provider first",
                provider=self.name,
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FIXTURE_FORMAT:
            raise ProviderError(f"{path}: unknown fixture format", provider=self.name)
        return data
