"""A decision run cut off by the token limit is retried with more room (R8-b): the same
request again would only replay the same cut-off."""

import tempfile
from uuid import uuid4

from test_money import found

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.managers import MAX_TOKENS_CAP, Deciders, Person
from eidolon.agent.providers import Completed, TextDelta, Usage
from eidolon.db import SessionLocal
from eidolon.kernel.store import FactStore


class CutOff:
    """Runs out of room until it is given at least 8192 tokens."""

    name = "cutoff"

    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if request.max_tokens < 8192:
            yield TextDelta('{"verdict": "appr')
            yield Completed(
                "max_tokens",
                Usage(10, request.max_tokens),
                request.model,
                "cutoff",
                request_id=str(uuid4()),
            )
        else:
            yield TextDelta('{"verdict": "approve", "reason": "fits now"}')
            yield Completed(
                "end_turn", Usage(10, 20), request.model, "cutoff", request_id=str(uuid4())
            )

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


def test_a_cut_off_answer_is_retried_with_twice_the_room(db):
    company = found(db)
    provider = CutOff()
    deciders = Deciders(SessionLocal, provider, BlobStore(tempfile.mkdtemp()))
    # attempts happen across worker cycles (each ask picks the next attempt after a
    # failed one), so three asks: cut off, cut off with twice the room, then it fits
    answers = [
        deciders.ask(
            company_id=company,
            person=Person("pm-1", "pm"),
            system="S",
            prompt="plan it",
            run_key="plan-long",
            max_tokens=2048,
        )
        for _ in range(3)
    ]
    assert answers == [None, None, {"verdict": "approve", "reason": "fits now"}]
    assert [r.max_tokens for r in provider.requests] == [2048, 4096, 8192]
    runs = [f for f in FactStore().stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=10)]
    assert sorted(f.payload["max_tokens_per_step"] for f in runs) == [2048, 4096, 8192]
    statuses = {
        f.payload["max_tokens_per_step"]: R.RunState.from_facts(
            f.correlation_id, FactStore().by_correlation(db, f.correlation_id)
        ).status
        for f in runs
    }
    assert statuses == {2048: "failed", 4096: "failed", 8192: "completed"}
    assert MAX_TOKENS_CAP == 16_384
