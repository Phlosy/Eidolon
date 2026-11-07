"""Transcript retention (R6-e): blobs expire by the institution, facts never do.

A run's transcript — system prompt, context, assistant turns, tool results, final
text — lives in the content-addressed blob store and is referenced from the run's
facts by hash. The institution's ``retention.transcript_days`` says how long to keep
it. Once every run that references a blob has ended longer ago than that, the blob
is deleted; the facts stay (hashes, usage, previews), the ledger stays, replay of
that run becomes impossible — which is the point (v2-ADR-8: dangling refs affect
replay, not the ledger). Artifact content is work product, not transcript, and is
never touched here.

One ``agent.transcripts.purged`` fact per company per purge keeps the act auditable.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.institution.adoption import current as current_institution
from eidolon.kernel.facts import Actor, Fact, NewFact
from eidolon.kernel.store import FactStore
from eidolon.logging import get_logger
from eidolon.work.facts import ARTIFACT_PRODUCED

log = get_logger("eidolon.agent.retention")

PURGED = "agent.transcripts.purged"
REF_KEYS = (
    "system_ref",
    "prompt_ref",
    "context_ref",
    "response_ref",
    "result_ref",
    "final_ref",
    "arguments_ref",
)
TERMINAL = (R.RUN_COMPLETED, R.RUN_FAILED, R.RUN_CANCELLED, R.RUN_BUDGET_EXCEEDED)


def refs_in(fact: Fact) -> set[str]:
    return {v for k, v in fact.payload.items() if k in REF_KEYS and isinstance(v, str)}


@dataclass
class PurgeReport:
    company_id: UUID
    runs_expired: int = 0
    blobs_deleted: int = 0
    blobs_kept_shared: int = 0
    cutoff: datetime | None = None


@dataclass
class TranscriptRetention:
    """Worker job. Runs at most every ``interval_s`` (retention is a daily matter, the
    worker cycle is seconds)."""

    session_factory: sessionmaker
    blobs: BlobStore
    store: FactStore = field(default_factory=FactStore)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    interval_s: float = 3600.0
    name: str = "transcript_retention"
    _last: datetime | None = None

    def tick(self) -> int:
        now = self.clock()
        if self._last is not None and (now - self._last).total_seconds() < self.interval_s:
            return 0
        self._last = now
        with self.session_factory() as db:
            companies = self.store.companies(db)
        deleted = 0
        for company_id in companies:
            report = self.purge(company_id, now=now)
            deleted += report.blobs_deleted if report else 0
        return deleted

    def purge(self, company_id: UUID, *, now: datetime | None = None) -> PurgeReport | None:
        now = now or self.clock()
        report = PurgeReport(company_id)
        with self.session_factory() as db:
            institution = current_institution(db, company_id, store=self.store)
            if institution is None:
                return None  # no institution, no policy: keep everything
            days = institution.template.retention.transcript_days
            cutoff = now - timedelta(days=days)
            report.cutoff = cutoff
            runs = self.store.stream(
                db, kinds=[R.RUN_STARTED], company_id=company_id, limit=1_000_000
            )
            expired_refs: set[str] = set()
            live_refs: set[str] = set()
            for started in runs:
                run_id = UUID(started.payload["run_id"])
                facts = self.store.by_correlation(db, run_id, kinds=R.RUN_KINDS)
                ended = [f for f in facts if f.kind in TERMINAL]
                refs = set().union(*(refs_in(f) for f in facts))
                if ended and ended[-1].created_at < cutoff:
                    report.runs_expired += 1
                    expired_refs |= refs
                else:
                    live_refs |= refs
            # work product is not transcript: whatever an artifact names stays
            for art in self.store.stream(
                db, kinds=[ARTIFACT_PRODUCED], company_id=company_id, limit=1_000_000
            ):
                live_refs.add(art.payload["blob_ref"])
            doomed = expired_refs - live_refs
            report.blobs_kept_shared = len(expired_refs & live_refs)
            for ref in sorted(doomed):
                if self.blobs.delete(ref):
                    report.blobs_deleted += 1
            if report.runs_expired and (report.blobs_deleted or doomed):
                self.store.append(
                    db,
                    NewFact(
                        company_id=company_id,
                        actor=Actor.system(),
                        kind=PURGED,
                        payload={
                            "cutoff": cutoff.isoformat(),
                            "transcript_days": days,
                            "runs_expired": report.runs_expired,
                            "blobs_deleted": report.blobs_deleted,
                            "blobs_kept_shared": report.blobs_kept_shared,
                        },
                        idempotency_key=f"transcripts:{company_id}:purged:{now.date().isoformat()}:{report.blobs_deleted}",
                    ),
                )
                db.commit()
                log.info(
                    "transcripts.purged",
                    extra={
                        "company_id": str(company_id),
                        "runs_expired": report.runs_expired,
                        "blobs_deleted": report.blobs_deleted,
                        "cutoff": cutoff.isoformat(),
                    },
                )
        return report
