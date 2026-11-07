"""Budgets and quotas (R6-a): the institution's budget_policy enforced with the
ledger's numbers at three levels — run, project tranche, company."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from loop_support import new_spec, weather_tools
from project_support import ENGINEER, set_up_project, store, svc
from sqlalchemy import text
from test_agent_loop import ScriptedLoop
from test_cost_trace import metered_call, run_facts
from test_money import found
from test_work import actor

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.managers import Person, Roster, SpendApprover, StaticRoster
from eidolon.agent.providers import TextDelta, Usage
from eidolon.agent.providers.pricing import estimate
from eidolon.agent.scheduler import run_allowance
from eidolon.db import SessionLocal
from eidolon.institution import load, validate
from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.money import CostMeter, LedgerProjection, budgets
from eidolon.money.guard import BudgetGuard, tranche_decision_id
from eidolon.work import WorkProjection

OWNER = Actor(ActorKind.human, "owner-1")


def spend(db, company, run_id, task, cause, usage: Usage):
    """Book one live model call's worth of spend on a task (deepseek-flash, CNY)."""
    _, step = run_facts(db, company, run_id, task_id=task, cause=cause)
    metered_call(db, company, run_id, step, usage=usage)
    db.commit()
    process(SessionLocal, WorkProjection())
    process(SessionLocal, CostMeter())
    process(SessionLocal, LedgerProjection())


def test_run_stops_when_its_money_allowance_is_spent(db, tmp_path):
    """B1: each step's price-list estimate accumulates; the run ends with
    budget_exceeded(max_cost) once the allowance is spent."""
    per_step = estimate("deepseek-flash", Usage(10, 5)).amount_micro  # the ScriptedLoop's usage
    assert per_step > 0
    answers = [[TextDelta("thinking…")] for _ in range(10)]
    provider = ScriptedLoop(answers)
    runner = AgentRunner(SessionLocal, provider, BlobStore(tmp_path / "b"), weather_tools())
    # the scripted answers end_turn each time, so a plain spec would complete in one step;
    # give it a tool-less loop that keeps going by making every answer a tool call instead
    from test_agent_loop import tool_answer

    provider.answers = [tool_answer(n) for n in range(1, 10)]
    spec = new_spec(
        uuid4(),
        budget=R.RunBudget(max_steps=20, max_cost_micro=per_step * 3, cost_currency="CNY"),
    )
    outcome = __import__("asyncio").run(runner.start(spec))
    assert outcome.status == "budget_exceeded" and outcome.steps == 3
    facts = store.by_correlation(db, spec.run_id)
    ended = [f for f in facts if f.kind == R.RUN_BUDGET_EXCEEDED][0]
    assert ended.payload["reason"] == "max_cost"
    assert ended.payload["cost"] == {"CNY": per_step * 3}
    assert ended.payload["max_cost_micro"] == per_step * 3
    steps = [f for f in facts if f.kind == R.STEP_COMPLETED]
    assert all(f.payload["cost"]["amount_micro"] == per_step for f in steps)
    # the fold agrees, and a run billed in another currency is not compared
    state = runner.load(spec.run_id)
    assert state.cost_total == {"CNY": per_step * 3} and state.over_cost_budget()
    other = new_spec(
        uuid4(), budget=R.RunBudget(max_steps=2, max_cost_micro=1, cost_currency="USD")
    )
    provider.answers = [tool_answer(1), [TextDelta("done")]]
    assert __import__("asyncio").run(runner.start(other)).status == "completed"
    # the allowance comes from the institution: run_default units in the company currency
    assert run_allowance(T, "CNY") == (T.budget_policy.run_default * MICRO_PER_UNIT, "CNY")
    assert run_allowance(None, "CNY") == (None, None) and run_allowance(T, None) == (None, None)


def test_project_tranches_need_spend_approval_past_warn_ratio(db):
    """B2: within warn_ratio nothing is asked; past it each tranche is a spend.approve
    decision whose decider the amount tiers pick; pending / rejected hold the run."""
    company = found(db)
    _, pid, task = set_up_project(db, company)  # budget 10_000 units (100.00 CNY)
    guard = BudgetGuard(store)
    started = svc.start_task(db, pid, task, Actor.system(), run_id=uuid4(), person_id=ENGINEER)
    db.commit()
    v = guard.check(db, company, pid, T)
    assert (
        v.allow and v.reason == "within_budget" and v.spent_units == 0 and v.budget_units == 10_000
    )

    # spend ~85 CNY on the task: 8 500 units, past 80% of 10 000
    spend(db, company, uuid4(), task, started.id, Usage(10_000_000, 8_125_000))
    spent = budgets.to_units(budgets.P.project_spend_micro(db, pid))
    assert 8_400 <= spent <= 8_600
    v = guard.check(db, company, pid, T)
    # next tranche: spent + 5 000 > 0.8 × 10 000 → decision; amount ≈ 13 500 falls in the
    # PM (agent) tier of software-studio-v1 (≤ 80% of its 200 000 project default)
    assert not v.allow and v.reason == "waiting_decision"
    did = tranche_decision_id(pid, spent // T.budget_policy.run_default)
    assert v.decision_id == did
    decision = load(db, did)
    assert decision.gate == "spend.approve" and decision.status == "pending"
    assert decision.resolution.decider == "agent" and "pm" in decision.resolution.allowed_roles
    assert decision.subject["project_id"] == str(pid) and decision.amount == spent + 5_000
    assert guard.check(db, company, pid, T).decision_id == did  # idempotent while pending

    # the SpendApprover job lets the PM sign it (scripted model answer), then the run may go
    provider = ScriptedLoop([[TextDelta('{"verdict": "approve", "reason": "on track"}')]])
    from eidolon.agent.managers import Deciders

    job = SpendApprover(
        SessionLocal,
        Deciders(SessionLocal, provider, BlobStore(__import__("tempfile").mkdtemp())),
        StaticRoster(Roster([Person("pm-1", "pm")])),
    )
    assert job.tick() == 1
    decision = load(db, did)
    assert decision.status == "approved" and decision.signatures[0].person_id == "pm-1"
    assert job.tick() == 0  # nothing left to sign
    v = guard.check(db, company, pid, T)
    assert v.allow and v.reason == "tranche_approved" and v.decision_id == did

    # a rejected tranche holds the run; hard_stop=False only records
    spend(db, company, uuid4(), task, started.id, Usage(10_000_000, 100_000))  # +20.8 CNY
    spent2 = budgets.to_units(budgets.P.project_spend_micro(db, pid))
    assert spent2 // 5_000 > spent // 5_000  # a new tranche
    v = guard.check(db, company, pid, T)
    assert not v.allow and v.reason == "waiting_decision" and v.decision_id != did
    provider.answers = [[TextDelta('{"verdict": "reject", "reason": "over budget"}')]]
    assert job.tick() == 1
    v = guard.check(db, company, pid, T)
    assert not v.allow and v.reason == "rejected"
    soft = T.with_changes({"budget_policy": {"hard_stop": False}})
    v = guard.check(db, company, pid, soft)
    assert v.allow and v.reason == "soft:rejected"

    # a tranche within the system tier is recorded mechanically (tiny budget, tiny spend)
    pid2 = __import__("uuid").UUID(
        svc.create_project(
            db, company, actor("pm-1", "pm"), title="P2", budget=1_000, manager="pm-1"
        ).payload["project_id"]
    )
    db.commit()
    v = guard.check(db, company, pid2, T)  # 0 + 5 000 > 800 → decision; amount 5 000 ≤ run_default
    assert v.allow and v.reason == "tranche_approved"
    rec = load(db, v.decision_id)
    assert rec.resolution.decider == "system" and rec.status == "approved"


def test_company_concurrency_and_daily_cap_hold_runs(db):
    """B3: more tasks in progress than max_concurrent_runs, or today's spend at the
    daily cap, hold the next run — no decision, just waiting."""
    company = found(db)
    _, pid, task = set_up_project(db, company)
    guard = BudgetGuard(store)
    started = svc.start_task(db, pid, task, Actor.system(), run_id=uuid4(), person_id=ENGINEER)
    db.commit()
    process(SessionLocal, WorkProjection())
    one_at_a_time = T.with_changes({"budget_policy": {"max_concurrent_runs": 1}})
    assert guard.check(db, company, pid, one_at_a_time).allow  # one in progress: fine
    db.execute(
        text("UPDATE work_tasks SET status = 'in_progress' WHERE project_id = :p"),
        {"p": str(pid)},
    )
    # a second in-progress task of the same company (synthetic row)
    db.execute(
        text(
            "INSERT INTO work_tasks (task_id, company_id, project_id, title, description, "
            "acceptance_criteria, kind, status, depends_on, inputs, rework_count, reviews, "
            "created_at, updated_at, last_seq) VALUES (:t, :c, :p, 'x', 'x', '[]', 'work', "
            "'in_progress', '[]', '[]', 0, '[]', now(), now(), 0)"
        ),
        {"t": str(uuid4()), "c": str(company), "p": str(pid)},
    )
    db.commit()
    assert guard.check(db, company, pid, one_at_a_time).reason == "max_concurrent_runs"
    assert guard.check(db, company, pid, T).allow  # default cap is 4

    # daily cap: 1 unit; a booked call today exceeds it
    capped = T.with_changes({"budget_policy": {"daily_cap": 1}})
    assert guard.check(db, company, pid, capped).allow
    spend(db, company, uuid4(), task, started.id, Usage(100_000, 50_000))
    v = guard.check(db, company, pid, capped)
    assert not v.allow and v.reason == "daily_cap"
    # yesterday's spend does not count
    db.execute(
        text("UPDATE money_journal SET posted_at = :y"),
        {"y": datetime.now(UTC) - timedelta(days=1)},
    )
    db.commit()
    assert guard.check(db, company, pid, capped).allow
    # the new policy fields validate like the rest of the template
    assert any(
        "max_concurrent_runs" in p
        for p in validate(T.with_changes({"budget_policy": {"max_concurrent_runs": 0}}))
    )
    assert any(
        "daily_cap" in p for p in validate(T.with_changes({"budget_policy": {"daily_cap": 0}}))
    )
    assert validate(capped) == [] and T.as_dict()["budget_policy"]["max_concurrent_runs"] == 4
