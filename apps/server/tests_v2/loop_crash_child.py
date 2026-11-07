"""A real process that starts a run and dies (os._exit) at a chosen point.

    python loop_crash_child.py <after_step_requested|in_tool> <blob_dir> <company_id> <run_id>

Uses the replay provider and the weather tools from loop_support; the database
comes from EIDOLON_V2_DATABASE_URL (the test harness exports the *_test URL).
Exit code 3 is the crash; anything else means the crash point was not reached.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/server: the eidolon package

from loop_support import new_spec, replay_provider, weather_tools  # noqa: E402

from eidolon.agent.blobs import BlobStore  # noqa: E402
from eidolon.agent.loop import AgentRunner  # noqa: E402
from eidolon.db import SessionLocal  # noqa: E402

CRASH_EXIT = 3


class DiesBeforeAnswering:
    """Lets the loop commit agent.step.requested, then the process dies."""

    name = "dies"

    def __init__(self, inner):
        self.inner = inner

    async def complete(self, request):
        print("crash: after_step_requested", flush=True)
        os._exit(CRASH_EXIT)
        yield  # pragma: no cover

    async def embed(self, texts, *, model=None):
        return await self.inner.embed(texts, model=model)


def main() -> int:
    point, blob_dir, company, run_id = sys.argv[1:5]
    provider = replay_provider(SessionLocal)
    if point == "after_step_requested":
        provider = DiesBeforeAnswering(provider)

    def die(execution):
        print(f"crash: in_tool key={execution.idempotency_key}", flush=True)
        os._exit(CRASH_EXIT)

    tools = weather_tools(before=die if point == "in_tool" else None)
    runner = AgentRunner(SessionLocal, provider, BlobStore(blob_dir), tools)
    outcome = asyncio.run(runner.start(new_spec(UUID(company), UUID(run_id))))
    print(f"no crash: {outcome}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
