#!/usr/bin/env python3
"""Record the canonical provider cases against a real model into replay fixtures.

    python scripts/record_provider_fixtures.py [--kind openai_compatible|anthropic] [--dir DIR] [--loop]

``--loop`` additionally drives the real agent loop (tests_v2/loop_support.py's
weather scenario) through the recording provider, so the loop's own requests get
fixtures too. That writes a genuine run into the configured v2 database (facts +
transcripts), i.e. run it with .env loaded: `make v2-record-fixtures`.

Needs the provider's API key in the environment (.env or shell); spends a few
hundred tokens. Fixtures land in apps/server/tests_v2/fixtures/providers/ by
default and are committed: tests replay them, so they are real model output
yet deterministic. Re-run whenever tests_v2/provider_cases.py changes.
"""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "apps" / "server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER / "tests_v2"))

from eidolon.agent.providers import (
    Completed,
    Recording,
    TextDelta,
    ToolCallEvent,
    fixture_dir,
    live_provider,
)
from eidolon.config import settings
from provider_cases import (
    ANTHROPIC_MODEL,
    DEEPSEEK_MODEL,
    EXPECTATIONS,
    cases,
)


async def run(kind: str, directory: Path) -> int:
    model = ANTHROPIC_MODEL if kind == "anthropic" else DEEPSEEK_MODEL
    provider = Recording(live_provider(settings, kind=kind), directory)
    failures = 0
    for name, request in cases(model).items():
        text, calls, done = "", [], None
        async for event in provider.complete(request):
            if isinstance(event, TextDelta):
                text += event.text
            elif isinstance(event, ToolCallEvent):
                calls.append(event.call)
            elif isinstance(event, Completed):
                done = event
        ok = EXPECTATIONS[name](text, calls, done)
        failures += not ok
        print(
            f"{'ok ' if ok else 'BAD'} {kind}/{name}: fingerprint={request.fingerprint()[:16]} "
            f"stop={done.stop_reason} usage={done.usage.as_dict()} request_id={done.request_id} "
            f"text={text[:60]!r} calls={[c.name for c in calls]}"
        )
    close = getattr(provider.inner, "aclose", None)
    if close:
        await close()
    return 1 if failures else 0


async def run_loop(kind: str, directory: Path) -> int:
    from uuid import uuid4

    from eidolon.agent.blobs import BlobStore
    from eidolon.agent.loop import AgentRunner
    from eidolon.agent.providers import Ledgered, ModelCallLedger
    from eidolon.db import SessionLocal
    from loop_support import new_spec, weather_tools

    live = live_provider(settings, kind=kind)
    provider = Ledgered(
        Recording(live, directory), ModelCallLedger(SessionLocal), mode="record"
    )
    blobs = BlobStore(SERVER / settings.transcript_dir)
    keys: list[str] = []
    runner = AgentRunner(SessionLocal, provider, blobs, weather_tools(keys))
    spec = new_spec(uuid4())
    outcome = await runner.start(spec)
    close = getattr(live, "aclose", None)
    if close:
        await close()
    ok = outcome.status == "completed" and "22" in (outcome.final_text or "")
    print(
        f"{'ok ' if ok else 'BAD'} {kind}/loop run={spec.run_id} status={outcome.status} "
        f"steps={outcome.steps} usage={outcome.usage.as_dict()} tools={len(keys)} "
        f"final={(outcome.final_text or '')[:80]!r}"
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", default="openai_compatible")
    parser.add_argument("--dir", default=None)
    parser.add_argument(
        "--loop", action="store_true", help="also record the agent-loop scenario"
    )
    args = parser.parse_args()
    directory = Path(args.dir) if args.dir else fixture_dir(settings)
    code = asyncio.run(run(args.kind, directory))
    if args.loop:
        code = asyncio.run(run_loop(args.kind, directory)) or code
    return code


if __name__ == "__main__":
    sys.exit(main())
