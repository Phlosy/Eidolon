"""Metrics (R6-c): what the fact log already says, as Prometheus gauges and counters.

Two sources, one registry (``prometheus_client``'s default):

* process metrics the API and the worker record as they go — requests, batches,
  cursor lag, job ticks and failures;
* ``MetricsConsumer`` — a reactor that reads facts as they are recorded and counts
  runs, steps, model calls, booked cost and gate waits. It starts at the head (it
  is not a projection: a counter is a view of *this process's* life, and history is
  in the facts, not in memory).

The API serves ``GET /metrics``; the worker serves the same registry on
``settings.metrics_port`` (0 = off, the default for tests).
"""

from datetime import UTC, datetime
from uuid import UUID

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.institution.decisions import ESCALATED, RECORDED, REQUESTED
from eidolon.kernel.facts import Fact
from eidolon.kernel.store import FactStore
from eidolon.money.facts import COST_RECORDED

# -- process metrics -----------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "eidolon_http_requests_total", "API requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram("eidolon_http_request_seconds", "API request latency", ["method", "route"])
BATCHES = Counter("eidolon_worker_batches_total", "consumer batches", ["consumer"])
BATCH_FAILURES = Counter("eidolon_worker_batch_failures_total", "failed batches", ["consumer"])
HANDLED = Counter("eidolon_worker_facts_handled_total", "facts handled", ["consumer"])
LAG = Gauge("eidolon_consumer_lag", "facts behind the head", ["consumer"])
HEAD = Gauge("eidolon_fact_head_seq", "last fact seq seen by the worker")
JOB_TICKS = Counter("eidolon_worker_job_ticks_total", "job ticks", ["job"])
JOB_DID = Counter("eidolon_worker_job_actions_total", "units of work jobs did", ["job"])
JOB_FAILURES = Counter("eidolon_worker_job_failures_total", "failed job ticks", ["job"])
JOB_SECONDS = Histogram("eidolon_worker_job_seconds", "job tick duration", ["job"])

# -- fact-derived metrics ------------------------------------------------------------

FACTS = Counter("eidolon_facts_total", "facts recorded, by kind", ["kind"])
RUNS_STARTED = Counter("eidolon_runs_started_total", "agent runs started")
RUNS_ENDED = Counter("eidolon_runs_ended_total", "agent runs ended", ["status"])
STEPS = Counter("eidolon_steps_total", "agent steps completed")
MODEL_CALLS = Counter("eidolon_model_calls_total", "model calls", ["provider", "model", "mode"])
TOKENS = Counter("eidolon_model_tokens_total", "tokens", ["direction"])
COST_BOOKED = Counter("eidolon_cost_booked_micro_total", "booked model cost", ["currency"])
COST_UNBOOKED = Counter("eidolon_cost_unbooked_total", "costs not booked", ["reason"])
DECISIONS = Counter("eidolon_decisions_total", "decisions by gate and outcome", ["gate", "status"])
ESCALATIONS = Counter("eidolon_decision_escalations_total", "decisions escalated", ["gate"])
GATE_WAIT = Histogram(
    "eidolon_gate_wait_seconds",
    "time from request to record",
    ["gate"],
    buckets=(1, 10, 60, 300, 900, 3600, 4 * 3600, 24 * 3600, float("inf")),
)


class MetricsConsumer:
    name = "metrics"
    handles = frozenset(
        {
            R.RUN_STARTED,
            R.STEP_COMPLETED,
            R.RUN_COMPLETED,
            R.RUN_FAILED,
            R.RUN_CANCELLED,
            R.RUN_BUDGET_EXCEEDED,
            MODEL_CALLED,
            COST_RECORDED,
            REQUESTED,
            RECORDED,
            ESCALATED,
        }
    )
    start_at_head = True  # a counter of this process's life, not a replay of history

    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        FACTS.labels(fact.kind).inc()
        if fact.kind == R.RUN_STARTED:
            RUNS_STARTED.inc()
        elif fact.kind == R.STEP_COMPLETED:
            STEPS.inc()
        elif fact.kind in (R.RUN_COMPLETED, R.RUN_FAILED, R.RUN_CANCELLED, R.RUN_BUDGET_EXCEEDED):
            RUNS_ENDED.labels(fact.kind.rsplit(".", 1)[1]).inc()
        elif fact.kind == MODEL_CALLED:
            MODEL_CALLS.labels(
                p.get("provider", "?"), p.get("model", "?"), p.get("mode", "?")
            ).inc()
            usage = p.get("usage") or {}
            TOKENS.labels("input").inc(usage.get("input_tokens", 0))
            TOKENS.labels("output").inc(usage.get("output_tokens", 0))
        elif fact.kind == COST_RECORDED:
            if p.get("posted") and p.get("booked"):
                COST_BOOKED.labels(p["booked"]["currency"]).inc(p["booked"]["amount_micro"])
            else:
                COST_UNBOOKED.labels(p.get("reason") or "unknown").inc()
        elif fact.kind == REQUESTED:
            DECISIONS.labels(p.get("gate", "?"), "requested").inc()
        elif fact.kind == ESCALATED:
            ESCALATIONS.labels(p.get("gate", "?")).inc()
        elif fact.kind == RECORDED:
            DECISIONS.labels(p.get("gate", "?"), p.get("verdict_status", "?")).inc()
            requested = self.store.by_correlation(db, fact.correlation_id, kinds=[REQUESTED])
            if requested:
                GATE_WAIT.labels(p.get("gate", "?")).observe(
                    (fact.created_at - requested[0].created_at).total_seconds()
                )


def observe_batch(consumer: str, handled: int, cursor: int, head: int) -> None:
    BATCHES.labels(consumer).inc()
    HANDLED.labels(consumer).inc(handled)
    LAG.labels(consumer).set(max(head - cursor, 0))
    HEAD.set(head)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def serve(port: int, host: str = "0.0.0.0") -> bool:
    """Expose the registry over HTTP (the worker has no web server of its own)."""
    if port <= 0:
        return False
    start_http_server(port, addr=host)
    return True


def now() -> datetime:
    return datetime.now(UTC)


def lag_report(db: Session, consumers: list[str], store: FactStore | None = None) -> dict:
    """For /health: head seq and each consumer's lag, from the cursor table."""
    from eidolon.kernel.consumers import stored_cursor

    store = store or FactStore()
    head = store.last_seq(db)
    lags = {}
    for name in consumers:
        cursor = stored_cursor(db, name)
        lags[name] = None if cursor is None else max(head - cursor, 0)
    return {"head_seq": head, "cursors": lags}


__all__ = ["MetricsConsumer", "lag_report", "observe_batch", "render", "serve", "UUID"]
