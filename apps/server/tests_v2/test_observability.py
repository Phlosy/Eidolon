"""Observability (R6-c): Prometheus metrics from the API and the worker, counters
read off the fact stream, a health endpoint that answers 'is the DB there and how far
behind are we', and bundles narrowed to a time window."""

import io
import json
import socket
import urllib.request
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from support import seed, store
from test_api import PREFIX, register
from test_money import call, found
from test_work import decide

from eidolon.agent import run as R
from eidolon.api.app import create_app
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.identity import IdentityProjection
from eidolon.kernel import provenance
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind, NewFact
from eidolon.kernel.notify import listening
from eidolon.money import CostMeter
from eidolon.observability import MetricsConsumer
from eidolon.worker import Worker


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels or None) or 0.0


def test_api_serves_metrics_and_a_health_report(db):
    app = create_app(SessionLocal)
    with TestClient(app) as client:
        before = sample(
            "eidolon_http_requests_total", method="GET", route="/api/v2/health", status="200"
        )
        health = client.get(f"{PREFIX}/health").json()
        assert health["status"] == "ok" and health["db"] == "ok"
        assert "metrics" in health["cursors"] and health["max_lag"] >= 0
        assert isinstance(health["head_seq"], int)
        text = client.get("/metrics").text
        assert "eidolon_http_requests_total" in text and "eidolon_facts_total" in text
        after = sample(
            "eidolon_http_requests_total", method="GET", route="/api/v2/health", status="200"
        )
        assert after == before + 1
        assert (
            sample(
                "eidolon_http_requests_total", method="GET", route="/api/v2/auth/me", status="401"
            )
            >= 0
        )
        client.get(f"{PREFIX}/auth/me")
        assert (
            sample(
                "eidolon_http_requests_total", method="GET", route="/api/v2/auth/me", status="401"
            )
            >= 1
        )

    def broken():
        raise RuntimeError("database gone")

    with TestClient(create_app(broken)) as client:
        r = client.get(f"{PREFIX}/health")
        assert r.status_code == 503 and r.json()["status"] == "degraded"
        assert "database gone" in r.json()["db"]


def test_metrics_consumer_counts_from_the_head(db):
    company = found(db)
    seed(db, company, 1, kind="agent.run.completed", prefix="old-")
    db.commit()
    consumer = MetricsConsumer(store)
    process(SessionLocal, consumer)  # registers at head: the fact above is history
    runs_before = sample("eidolon_runs_ended_total", status="completed")
    calls_before = sample(
        "eidolon_model_calls_total", provider="deepseek", model="deepseek-flash", mode="live"
    )
    booked_before = sample("eidolon_cost_booked_micro_total", currency="CNY")
    decided_before = sample("eidolon_decisions_total", gate="task.assign", status="approved")
    waits_before = sample("eidolon_gate_wait_seconds_count", gate="task.assign")

    run_id = uuid4()
    store.append(
        db,
        NewFact(
            company_id=company,
            actor=Actor(ActorKind.agent, "eng-1"),
            kind=R.RUN_STARTED,
            payload={"run_id": str(run_id), "person_id": "eng-1", "task_id": None},
            idempotency_key=R.run_key(run_id, "started"),
            correlation_id=run_id,
        ),
    )
    call_fact = store.append(db, call(company, run_id=run_id))
    store.append(
        db,
        NewFact(
            company_id=company,
            actor=Actor(ActorKind.agent, "eng-1"),
            kind=R.RUN_COMPLETED,
            payload={"run_id": str(run_id), "steps": 1},
            idempotency_key=R.run_key(run_id, "completed"),
            correlation_id=run_id,
        ),
    )
    decide(
        db,
        company,
        "task.assign",
        {"task_id": str(uuid4()), "assignee": "eng-1"},
        signers=[("tl-1", "tech_lead")],
    )
    db.commit()
    process(SessionLocal, CostMeter())  # books the call → money.cost.recorded
    process(SessionLocal, consumer)

    assert sample("eidolon_runs_ended_total", status="completed") == runs_before + 1
    assert (
        sample(
            "eidolon_model_calls_total", provider="deepseek", model="deepseek-flash", mode="live"
        )
        == calls_before + 1
    )
    assert (
        sample("eidolon_cost_booked_micro_total", currency="CNY")
        == booked_before + call_fact.payload["cost"]["amount_micro"]
    )
    assert (
        sample("eidolon_decisions_total", gate="task.assign", status="approved")
        == decided_before + 1
    )
    assert sample("eidolon_gate_wait_seconds_count", gate="task.assign") == waits_before + 1
    assert sample("eidolon_facts_total", kind="agent.run.completed") >= 1
    assert process(SessionLocal, consumer)[0].handled == 0  # idempotent: nothing new


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_worker_exposes_the_registry_and_records_lag(database):
    port = free_port()
    from eidolon.kernel.consumers import Registry, function_consumer

    registry = Registry()
    registry.register(function_consumer("kind_counts", None, lambda _db, _fact: None))
    with SessionLocal() as db:
        seed(db, uuid4(), 3)
        db.commit()
    with listening() as listener:
        worker = Worker(registry, SessionLocal, listener, jobs=(), metrics_port=port)
        try:
            worker.start()
            body = (
                urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5)
                .read()
                .decode()
            )
        finally:
            worker.close()
    assert 'eidolon_consumer_lag{consumer="kind_counts"} 0.0' in body
    assert "eidolon_worker_batches_total" in body and "eidolon_fact_head_seq" in body
    assert sample("eidolon_worker_facts_handled_total", consumer="kind_counts") >= 3


def test_bundles_can_be_narrowed_to_a_time_window(db, tmp_path):
    company = uuid4()
    first = seed(db, company, 2, prefix="a-")
    db.commit()
    cut = first[-1].created_at + timedelta(microseconds=1)
    seed(db, company, 2, prefix="b-")
    db.commit()
    whole = provenance.bundle(db, company)
    assert len(whole["facts"]) == 4 and "since" not in whole["selection"]
    late = provenance.bundle(db, company, since=cut)
    assert [f["idempotency_key"] for f in late["facts"]] == [
        f["idempotency_key"] for f in whole["facts"][2:]
    ]
    assert late["selection"]["since"] and late["selection"]["until"] is None
    assert late["bundle_hash"] == provenance.seal(late) != whole["bundle_hash"]
    early = provenance.bundle(db, company, until=cut)
    assert len(early["facts"]) == 2
    out = io.StringIO()
    code = main(
        ["export-bundle", "--company", str(company), "--since", cut.isoformat()],
        session_factory=SessionLocal,
        out=out,
    )
    assert code == 0 and len(json.loads(out.getvalue())["facts"]) == 2


def test_bundle_window_over_the_api(db):
    """The API takes the same window parameters."""
    app = create_app(SessionLocal)
    with TestClient(app) as client:
        register(client, "obs@example.com", person_id="obs-1")
        process(SessionLocal, IdentityProjection())
        assert (
            client.get(
                f"{PREFIX}/companies/{uuid4()}/bundle?since=2026-01-01T00:00:00Z"
            ).status_code
            == 403
        )
