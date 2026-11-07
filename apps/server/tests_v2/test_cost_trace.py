"""Cost provenance (R4-c): a ledger line walks back to the model call, step, run,
task and project; a project rolls its spend up per task, per run and for the
management runs it called for; a bundle can carry the ledger with the work."""

import dataclasses
import io
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from project_support import ENGINEER, set_up_project, store, svc
from test_money import call, found

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.kernel import provenance
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind, NewFact
from eidolon.money import (
    COST_RECORDED,
    ENTRY_POSTED,
    CostMeter,
    JournalRow,
    LedgerProjection,
    TraceError,
    cost_trace,
    ledger_fact_ids,
    project_costs,
    project_spend_micro,
)
from eidolon.work import WorkProjection

VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "verify_bundle.py"


def run_facts(db, company, run_id, *, task_id, cause, person=ENGINEER):
    """``agent.run.started`` (caused by ``cause``) and one ``agent.step.requested``,
    as the loop writes them."""
    actor = Actor(ActorKind.agent, person)
    started = store.append(
        db,
        NewFact(
            company_id=company,
            actor=actor,
            kind=R.RUN_STARTED,
            payload={
                "run_id": str(run_id),
                "person_id": person,
                "task_id": str(task_id) if task_id else None,
            },
            idempotency_key=R.run_key(run_id, "started"),
            correlation_id=run_id,
            causation_id=cause,
        ),
    )
    step = store.append(
        db,
        NewFact(
            company_id=company,
            actor=actor,
            kind=R.STEP_REQUESTED,
            payload={"run_id": str(run_id), "step_no": 1, "attempt": 1},
            idempotency_key=f"run:{run_id}:step:1:1:requested",
            correlation_id=run_id,
            causation_id=started.id,
        ),
    )
    return started, step


def metered_call(db, company, run_id, step, **kw):
    new = call(company, run_id=run_id, **kw)
    new = dataclasses.replace(new, causation_id=step.id)
    return store.append(db, new)


def settle(db):
    db.commit()
    process(SessionLocal, WorkProjection())
    process(SessionLocal, CostMeter())
    process(SessionLocal, LedgerProjection())


def test_cost_trace_walks_from_entry_to_project(db):
    company = found(db)
    _, pid, task = set_up_project(db, company)
    run_id = uuid4()
    started_task = svc.start_task(db, pid, task, Actor.system(), run_id=run_id, person_id=ENGINEER)
    started, step = run_facts(db, company, run_id, task_id=task, cause=started_task.id)
    posted_call = metered_call(db, company, run_id, step)
    replayed_call = metered_call(db, company, run_id, step, mode="replay")
    stray = store.append(db, call(company))  # outside any run
    settle(db)

    row = db.query(JournalRow).filter_by(run_id=run_id, line_no=0).one()
    by_entry_fact = cost_trace(db, row.entry_fact_id)
    by_entry_id = cost_trace(db, row.entry_id)
    cost_fact_id = row.cause_fact_id
    by_cost = cost_trace(db, cost_fact_id)
    by_call = cost_trace(db, posted_call.id)
    for t in (by_entry_fact, by_entry_id, by_cost, by_call):
        assert t.entry is not None and t.entry.id == row.entry_fact_id
        assert t.cost.id == cost_fact_id and t.model_call.id == posted_call.id
        assert t.step is not None and t.step.id == step.id
        assert t.run_started is not None and t.run_started.id == started.id
        assert t.cause is not None and t.cause.id == started_task.id
        assert t.run_id == run_id and t.task_id == task and t.project_id == pid
        assert t.task_title == "Greeting CLI" and t.missing == []
        assert t.booked_micro == posted_call.payload["cost"]["amount_micro"]
        assert [f.kind for f in t.chain] == [
            "work.task.started",
            R.RUN_STARTED,
            R.STEP_REQUESTED,
            MODEL_CALLED,
            COST_RECORDED,
            ENTRY_POSTED,
        ]
    d = by_entry_fact.as_dict()
    assert d["person_id"] == ENGINEER and d["posted"] and d["cause_kind"] == "work.task.started"
    assert d["entry_id"] == str(row.entry_id) and d["mission_id"] is None

    # an unposted (replayed) cost traces the same way, just without an entry
    t = cost_trace(db, replayed_call.id)
    assert t.entry is None and not t.cost.payload["posted"] and t.project_id == pid
    assert t.chain[-1].kind == COST_RECORDED
    # a call outside any run explains what it cannot say
    t = cost_trace(db, stray.id)
    assert t.run_id is None and t.project_id is None
    assert t.missing == ["step: the call was made outside a run", "run: the call carried no run_id"]

    with pytest.raises(TraceError, match="no fact"):
        cost_trace(db, uuid4())
    with pytest.raises(TraceError, match="starts from"):
        cost_trace(db, started.id)
    unmetered = store.append(db, call(company))
    db.commit()
    with pytest.raises(TraceError, match="not been metered"):
        cost_trace(db, unmetered.id)

    out = io.StringIO()
    assert main(["cost-trace", str(row.entry_id)], session_factory=SessionLocal, out=out) == 0
    text = out.getvalue()
    assert f"project_id: {pid}" in text and "task_title: Greeting CLI" in text and "posted" in text
    assert "work.task.started" in text and ENTRY_POSTED in text
    out = io.StringIO()
    assert (
        main(["cost-trace", "--json", str(cost_fact_id)], session_factory=SessionLocal, out=out)
        == 0
    )
    assert json.loads(out.getvalue())["project_id"] == str(pid)
    assert main(["cost-trace", str(uuid4())], session_factory=SessionLocal, out=io.StringIO()) == 2


def test_project_costs_roll_up_tasks_and_management_runs(db, tmp_path):
    company = found(db)
    _, pid, task = set_up_project(db, company)
    # two engineer runs on the task (an attempt and a rework), one management run the
    # project called for (a plan review by the tech lead), one CEO run caused by a
    # fact outside the project.
    run_a, run_b, plan_run, other_run = uuid4(), uuid4(), uuid4(), uuid4()
    started_task = svc.start_task(db, pid, task, Actor.system(), run_id=run_a, person_id=ENGINEER)
    _, step_a = run_facts(db, company, run_a, task_id=task, cause=started_task.id)
    _, step_b = run_facts(db, company, run_b, task_id=task, cause=started_task.id)
    project_fact = svc.project(db, pid).last_fact_id
    _, step_p = run_facts(db, company, plan_run, task_id=None, cause=project_fact, person="tl-1")
    unrelated = store.append(db, call(company))  # a fact that is not the project's
    _, step_o = run_facts(db, company, other_run, task_id=None, cause=unrelated.id, person="ceo-1")
    calls = {
        run_a: [metered_call(db, company, run_a, step_a) for _ in range(2)],
        run_b: [metered_call(db, company, run_b, step_b)],
        plan_run: [
            metered_call(
                db, company, plan_run, step_p, model="claude-opus-5-5", provider="anthropic"
            )
        ],
        other_run: [metered_call(db, company, other_run, step_o)],
    }
    settle(db)

    costs = project_costs(db, pid)
    assert costs.currency == "CNY" and len(costs.tasks) == 1
    t = costs.tasks[0]
    assert t.task_id == task and t.title == "Greeting CLI" and t.status == "in_progress"
    assert {r.run_id: r.micro for r in t.runs} == {
        run_a: sum(f.payload["cost"]["amount_micro"] for f in calls[run_a]),
        run_b: calls[run_b][0].payload["cost"]["amount_micro"],
    }
    assert all(r.person_id == ENGINEER for r in t.runs)
    assert t.micro == project_spend_micro(db, pid) == costs.task_micro
    assert [(r.run_id, r.person_id) for r in costs.management] == [(plan_run, "tl-1")]
    assert costs.management_micro == cost_trace(db, calls[plan_run][0].id).booked_micro
    assert costs.total_micro == costs.task_micro + costs.management_micro
    d = costs.as_dict()
    assert d["total_micro"] == costs.total_micro and d["tasks"][0]["runs"]
    assert project_costs(db, uuid4()).total_micro == 0

    ids = ledger_fact_ids(db, pid)
    kinds = sorted(store.get(db, i).kind for i in ids)
    assert kinds == [COST_RECORDED] * 4 + [ENTRY_POSTED] * 4  # 3 engineer calls + 1 plan call
    assert all(store.get(db, i).company_id == company for i in ids)

    out = io.StringIO()
    assert (
        main(["project-costs", "--project", str(pid)], session_factory=SessionLocal, out=out) == 0
    )
    text = out.getvalue()
    assert f"task {task} [in_progress] Greeting CLI: {t.micro}" in text
    assert f"management: {costs.management_micro}" in text and str(plan_run) in text
    out = io.StringIO()
    assert (
        main(
            ["project-costs", "--project", str(pid), "--json"],
            session_factory=SessionLocal,
            out=out,
        )
        == 0
    )
    assert json.loads(out.getvalue())["management"][0]["run_id"] == str(plan_run)

    # a bundle of the project can carry its ledger and still verifies offline
    path = tmp_path / "project.json"
    code = main(
        [
            "export-bundle",
            "--company",
            str(company),
            "--correlation",
            str(pid),
            "--with-ledger",
            "-o",
            str(path),
        ],
        session_factory=SessionLocal,
        out=io.StringIO(),
    )
    assert code == 0
    data = json.loads(path.read_text())
    bundled = {f["kind"] for f in data["facts"]}
    assert {ENTRY_POSTED, COST_RECORDED, "work.task.started", "work.project.created"} <= bundled
    assert {f["id"] for f in data["facts"]} >= {str(i) for i in ids}
    assert data["bundle_hash"] == provenance.seal(data)
    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        main(
            ["export-bundle", "--company", str(company), "--with-ledger"],
            session_factory=SessionLocal,
            out=io.StringIO(),
        )
        == 2
    )
