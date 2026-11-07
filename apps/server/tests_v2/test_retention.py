"""Retention and recovery (R6-e): transcripts expire by the institution while facts
and the ledger stay; the restore self-check rebuilds every projection and compares."""

import asyncio
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from loop_support import new_spec, replay_provider, weather_tools
from test_money import OWNER, found

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.retention import PURGED, TranscriptRetention, refs_in
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.institution import adopt
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.store import FactStore
from eidolon.money import CostMeter, LedgerProjection
from eidolon.registry import default_registry

store = FactStore()


def test_transcripts_expire_by_institution_but_facts_and_ledger_stay(db, tmp_path):
    company = found(db)
    store.append(db, adopt(company, OWNER, T))  # retention.transcript_days = 90
    db.commit()
    blobs = BlobStore(tmp_path / "blobs")
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools())
    done = new_spec(company)
    assert asyncio.run(runner.start(done)).status == "completed"
    # a second run shares the system prompt blob and is still running (no terminal fact)
    live = new_spec(company)
    started = store.append(
        db,
        R.cancel_request(company, uuid4(), OWNER, reason="unused", causation_id=None),
    )  # just to have an unrelated fact; not a run
    assert started.kind == R.RUN_CANCEL_REQUESTED
    live_started = store.append(
        db,
        __import__("eidolon.kernel.facts", fromlist=["NewFact"]).NewFact(
            company_id=company,
            actor=OWNER,
            kind=R.RUN_STARTED,
            payload={
                "run_id": str(live.run_id),
                "person_id": "eng-1",
                "task_id": None,
                "system_ref": blobs.put_json(live.system),
                "prompt_ref": blobs.put_json("other prompt"),
            },
            idempotency_key=R.run_key(live.run_id, "started"),
            correlation_id=live.run_id,
        ),
    )
    db.commit()
    process(SessionLocal, CostMeter())
    process(SessionLocal, LedgerProjection())

    done_refs = set().union(*(refs_in(f) for f in store.by_correlation(db, done.run_id)))
    shared = refs_in(live_started) & done_refs
    assert shared, "the two runs share the system prompt blob"
    assert all(blobs.exists(r) for r in done_refs)
    chain_before = store.verify_chain(db, company)

    job = TranscriptRetention(SessionLocal, blobs, clock=lambda: datetime.now(UTC))
    assert job.tick() == 0  # nothing older than 90 days
    far = datetime.now(UTC) + timedelta(days=91)
    job = TranscriptRetention(SessionLocal, blobs, clock=lambda: far)
    deleted = job.tick()
    assert deleted == len(done_refs - shared) > 0
    assert all(not blobs.exists(r) for r in done_refs - shared)
    assert all(blobs.exists(r) for r in shared)  # still referenced by the live run
    purged = [f for f in store.stream(db, kinds=[PURGED], company_id=company, limit=10)]
    assert len(purged) == 1 and purged[0].payload["runs_expired"] == 1
    assert (
        purged[0].payload["blobs_deleted"] == deleted and purged[0].payload["transcript_days"] == 90
    )
    # facts untouched, chain intact, ledger intact; replay of the expired run is what is gone
    assert store.verify_chain(db, company).ok and chain_before.ok
    from eidolon.money import verify as ledger_verify

    assert ledger_verify(db, company).ok
    assert job.tick() == 0  # throttled: once an hour
    job._last = None
    assert job.tick() == 0  # and nothing left to purge
    # a company without an institution keeps everything
    assert TranscriptRetention(SessionLocal, blobs, clock=lambda: far).purge(uuid4()) is None


def test_restore_check_rebuilds_every_projection_and_compares(db):
    company = found(db)
    store.append(db, adopt(company, OWNER, T))
    db.commit()
    for projection in default_registry().projections():
        process(SessionLocal, projection)
    out = io.StringIO()
    code = main(
        ["restore-check"], registry=default_registry(), session_factory=SessionLocal, out=out
    )
    text = out.getvalue()
    assert code == 0, text
    assert "RESTORE CHECK OK" in text and f"chain {company}: ok" in text
    assert "projection identity: rebuilt" in text and "rows unchanged" in text
