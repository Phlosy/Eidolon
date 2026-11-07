"""Money (R4-b): double-entry facts, the ledger fold, cost metering of model calls,
the projection, ledger-verify, and budgets in units vs micro."""

import io
from decimal import Decimal
from uuid import uuid4

import pytest
from project_support import set_up_project
from sqlalchemy import text

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.agent.providers.accounting import model_called_fact
from eidolon.agent.providers.base import CallContext, ChatRequest, Completed, Message, Usage
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.identity import IdentityService
from eidolon.kernel import provenance
from eidolon.kernel.consumers import process, rebuild
from eidolon.kernel.facts import Actor, ActorKind, NewFact
from eidolon.kernel.store import FactStore
from eidolon.money import (
    COMPUTE,
    AccountRow,
    CostMeter,
    JournalRow,
    Ledger,
    LedgerProjection,
    MoneyError,
    accounts,
    budgets,
    company_spend_micro,
    fx,
    payable,
    project_spend_micro,
    run_spend_micro,
    task_spend_micro,
    verify,
)
from eidolon.money import facts as F
from eidolon.work import WorkProjection

store = FactStore()
ids = IdentityService(store)
ledger = Ledger(store)
SYSTEM = Actor.system()
OWNER = Actor(ActorKind.human, "owner-1")


def found(db, currency="CNY"):
    """A real company (identity facts) so the meter knows its accounting currency."""
    user_id = uuid4()
    ids.register_user(db, email=f"o-{user_id.hex[:6]}@x.io", display_name="O", user_id=user_id)
    person = f"owner-{user_id.hex[:6]}"
    ids.create_person(db, OWNER, person, name="O", kind="human", user_id=user_id)
    created, _ = ids.create_company(
        db,
        owner_user_id=user_id,
        owner_person_id=person,
        name="S",
        slug=f"s-{user_id.hex[:6]}",
        currency=currency,
    )
    db.commit()
    return created.company_id


def call(company, *, model="deepseek-flash", usage=None, mode="live", run_id=None, **kw):
    """An ``agent.model.called`` fact as the agent layer would write it."""
    usage = Usage(1000, 500) if usage is None else usage
    ctx = CallContext(
        company,
        Actor(ActorKind.agent, "eng-1"),
        correlation_id=run_id,
        run_id=str(run_id) if run_id else None,
    )
    req = ChatRequest(model=model, messages=(Message.user("hi"),), max_tokens=16, context=ctx)
    done = Completed(
        "end_turn", usage, model, kw.pop("provider", "deepseek"), request_id=uuid4().hex
    )
    return model_called_fact(req, done, mode=mode)


def open_std(db, company, currency="CNY"):
    ledger.open_account(
        db, company, SYSTEM, code="cash", name="Cash", kind="asset", currency=currency
    )
    ledger.open_account(
        db, company, SYSTEM, code=COMPUTE, name="Compute", kind="expense", currency=currency
    )


def lines(debit, credit, amount):
    return [
        {"account": debit, "side": "debit", "amount_micro": amount},
        {"account": credit, "side": "credit", "amount_micro": amount},
    ]


def test_fx_and_units_are_integer_arithmetic():
    assert fx.convert(1_000_000, "CNY", "CNY") == (1_000_000, Decimal(1))
    assert fx.convert(1_000_000, "USD", "CNY") == (7_100_000, Decimal("7.10"))
    assert fx.convert(1, "USD", "CNY") == (7, Decimal("7.10"))
    assert fx.convert(5, "EUR", "CNY") is None
    assert budgets.to_micro(200_000) == 2_000_000_000  # 2 000.00 in micro
    assert budgets.to_units(10_000) == 1 and budgets.to_units(4_999) == 0
    assert budgets.to_units(5_000) == 1  # half up: a half cent counts
    status = budgets.BudgetStatus(200_000, 1_500_000_000)
    assert status.spent_units == 150_000 and status.remaining_units == 50_000
    assert status.ratio == 0.75 and status.exceeds(0.7) and not status.exceeds()
    assert budgets.BudgetStatus(None, 5).ratio is None and not budgets.BudgetStatus(0, 5).exceeds()


def test_entries_must_balance_and_post_to_known_accounts(db):
    company = uuid4()
    eid = uuid4()
    for bad, match in (
        (lines("cash", "compute", 5)[:1], "two lines"),
        (lines("cash", "compute", 0), "positive integer"),
        (lines("cash", "compute", 1.5), "positive integer"),
        (lines("cash", "compute", True), "positive integer"),
        ([{"account": "cash", "side": "up", "amount_micro": 5}] * 2, "debit or credit"),
        ([{"account": "Cash!", "side": "debit", "amount_micro": 5}] * 2, "account code"),
        (
            [
                {"account": "cash", "side": "debit", "amount_micro": 5},
                {"account": "compute", "side": "credit", "amount_micro": 6},
            ],
            "does not balance",
        ),
    ):
        with pytest.raises(MoneyError, match=match):
            F.entry_posted(SYSTEM, company, eid, lines=bad, currency="CNY", memo="m")
    with pytest.raises(MoneyError, match="currency"):
        F.entry_posted(SYSTEM, company, eid, lines=lines("a", "b", 1), currency="rmb", memo="m")
    with pytest.raises(MoneyError, match="memo"):
        F.entry_posted(SYSTEM, company, eid, lines=lines("a", "b", 1), currency="CNY", memo=" ")
    with pytest.raises(MoneyError, match="kind"):
        F.account_opened(SYSTEM, company, code="x", name="X", kind="fund", currency="CNY")
    with pytest.raises(MoneyError, match="posted cost needs"):
        F.cost_recorded(
            SYSTEM,
            company,
            model_call_fact_id=uuid4(),
            provider="p",
            model="m",
            mode="live",
            source=None,
            booked=None,
            posted=True,
            reason=None,
            run_id=None,
            task_id=None,
        )

    # the service refuses unknown accounts and currency mismatches before appending
    open_std(db, company)
    ledger.open_account(db, company, SYSTEM, code="usd", name="USD", kind="asset", currency="USD")
    db.commit()
    with pytest.raises(MoneyError, match="no account"):
        ledger.post(db, company, SYSTEM, lines=lines("ghost", "cash", 5), currency="CNY", memo="m")
    with pytest.raises(MoneyError, match="kept in USD"):
        ledger.post(db, company, SYSTEM, lines=lines("usd", "cash", 5), currency="CNY", memo="m")
    posted = ledger.post(
        db, company, SYSTEM, lines=lines(COMPUTE, "cash", 700), currency="CNY", memo="first"
    )
    db.commit()
    assert posted.kind == F.ENTRY_POSTED and posted.correlation_id == posted.id
    state = ledger.state(db, company)
    assert state.balance(COMPUTE) == 700 and state.balance("cash") == -700  # asset paid out
    assert state.trial_balance() == (700, 700) and len(state.entries) == 1
    assert state.accounts["usd"].normal_side == "debit" and F.normal_side("income") == "credit"

    # the fold and the projection refuse what slipped past the service (fact appended raw)
    raw = F.entry_posted(
        SYSTEM, company, uuid4(), lines=lines("ghost", "cash", 5), currency="CNY", memo="raw"
    )
    store.append(db, raw)
    db.commit()
    with pytest.raises(MoneyError, match="unknown account"):
        ledger.state(db, company)
    with pytest.raises(MoneyError, match="unknown account"):
        process(SessionLocal, LedgerProjection())
    report = verify(db, company)
    assert not report.ok and report.problems[0].startswith("M1") and "ghost" in report.problems[0]


def test_cost_meter_books_live_calls_and_records_the_rest(db):
    company = found(db)
    nobody = uuid4()  # a company id with no identity.company.created fact
    live = store.append(db, call(company))
    usd = store.append(db, call(company, model="claude-opus-5-5", provider="anthropic"))
    replay = store.append(db, call(company, mode="replay"))
    unpriced = store.append(db, call(company, model="mystery-9000"))
    zero = store.append(db, call(company, usage=Usage(0, 0)))
    orphan = store.append(db, call(nobody))
    db.commit()

    process(SessionLocal, CostMeter())
    process(SessionLocal, CostMeter())  # idempotent: K2 returns the same facts
    by_id = {str(x.id): x.id for x in (live, usd, replay, unpriced, zero, orphan)}
    costs = {
        by_id[f.payload["model_call_fact_id"]]: f
        for f in store.stream(db, kinds=[F.COST_RECORDED], limit=100)
    }
    assert len(costs) == 6 and all(f.actor.kind.value == "system" for f in costs.values())

    c = costs[live.id].payload
    assert c["posted"] and c["reason"] is None and c["mode"] == "live"
    assert c["source"] == live.payload["cost"]
    assert c["booked"] == {
        "amount_micro": live.payload["cost"]["amount_micro"],
        "currency": "CNY",
        "fx_rate": "1",
        "fx_version": fx.FX_VERSION,
    }
    c = costs[usd.id].payload
    assert c["posted"] and c["source"]["currency"] == "USD" and c["booked"]["currency"] == "CNY"
    assert c["booked"]["amount_micro"] == fx.convert(c["source"]["amount_micro"], "USD", "CNY")[0]
    assert c["booked"]["fx_rate"] == "7.10"
    assert costs[replay.id].payload["booked"] and not costs[replay.id].payload["posted"]
    assert costs[replay.id].payload["reason"] == "replay"
    assert costs[unpriced.id].payload["source"] is None
    assert costs[unpriced.id].payload["reason"] == "unpriced"
    assert costs[zero.id].payload["reason"] == "zero"
    assert costs[orphan.id].payload["reason"] == "no_company"
    assert costs[orphan.id].company_id == nobody

    state = ledger.state(db, company)
    assert set(state.accounts) == {COMPUTE, payable("deepseek"), payable("anthropic")}
    assert state.accounts[payable("deepseek")].kind == "liability"
    expected = (
        live.payload["cost"]["amount_micro"] + costs[usd.id].payload["booked"]["amount_micro"]
    )
    assert state.balance(COMPUTE) == expected
    assert state.balance(payable("deepseek")) == live.payload["cost"]["amount_micro"]
    assert len(state.entries) == 2 and all(len(e.lines) == 2 for e in state.entries)
    entry = next(e for e in state.entries if e.source["fact_id"] == str(costs[live.id].id))
    # provenance: entry ← cost ← model call, walkable with the kernel's trace
    trace = provenance.trace(db, entry.fact_id)
    chain = [f.kind for f in trace.facts if f.kind in (MODEL_CALLED, *F.KINDS)]
    assert chain == [MODEL_CALLED, F.COST_RECORDED, F.ENTRY_POSTED]
    report = verify(db, company)
    assert report.ok and report.projection_lag == 2  # not projected yet: lag, not corruption
    assert ledger.state(db, nobody).entries == []


def test_cost_meter_can_book_replays_for_fixture_driven_tests(db):
    """The booking policy is a deployment constant (two meters with different policies
    over the same log would conflict under K2); tests that drive replayed fixtures
    run one meter with ``book_replays=True`` on their own log."""
    company = found(db)
    store.append(db, call(company, mode="replay"))
    db.commit()
    process(SessionLocal, CostMeter(book_replays=True))
    assert ledger.state(db, company).balance(COMPUTE) > 0
    cost = store.stream(db, kinds=[F.COST_RECORDED], company_id=company, limit=10)[0]
    assert cost.payload["posted"] and cost.payload["mode"] == "replay"


def test_ledger_verify_detects_tampering_and_rebuild_repairs(db):
    company = found(db)
    store.append(db, call(company))
    store.append(db, call(company, model="claude-sonnet-5-5", provider="anthropic"))
    db.commit()
    process(SessionLocal, CostMeter())
    process(SessionLocal, LedgerProjection())
    state = ledger.state(db, company)
    rows = {r.code: r for r in accounts(db, company)}
    assert set(rows) == set(state.accounts)
    assert all(
        (r.debit_total_micro, r.credit_total_micro)
        == (state.debits.get(code, 0), state.credits.get(code, 0))
        for code, r in rows.items()
    )
    assert rows[COMPUTE].balance_micro == state.balance(COMPUTE) > 0
    journal = list(db.query(JournalRow).filter_by(company_id=company).order_by(JournalRow.last_seq))
    assert len(journal) == 4 and all(j.source_kind == F.COST_RECORDED for j in journal)
    assert all(j.cause_fact_id is not None and j.run_id is None for j in journal)
    assert verify(db, company).ok

    out = io.StringIO()
    assert main(["ledger-verify"], session_factory=SessionLocal, out=out) == 0
    assert f"{company} accounts=3 entries=2 costs=2 unposted=0" in out.getvalue()
    assert "1 companies, 0 broken" in out.getvalue()
    out = io.StringIO()
    assert main(["balances", "--company", str(company)], session_factory=SessionLocal, out=out) == 0
    assert "compute" in out.getvalue() and "ap:deepseek" in out.getvalue()

    # tamper with the projection: verify is red, the CLI exits 1, rebuild repairs
    db.execute(text("UPDATE money_accounts SET debit_total_micro = debit_total_micro + 1"))
    db.execute(text("DELETE FROM money_journal WHERE line_no = 1"))
    db.commit()
    db.expire_all()
    report = verify(db, company)
    assert not report.ok and any(p.startswith("M3 account") for p in report.problems)
    assert any(p.startswith("M3 journal") for p in report.problems)
    out = io.StringIO()
    assert main(["ledger-verify", "--company", str(company)], session_factory=SessionLocal, out=out)
    assert "BROKEN" in out.getvalue()
    assert rebuild(SessionLocal, LedgerProjection()) == 5  # 3 accounts + 2 entries
    db.expire_all()
    assert verify(db, company).ok
    assert db.get(AccountRow, (company, COMPUTE)).balance_micro == state.balance(COMPUTE)

    # M4: a cost that claims to be posted but has no entry
    store.append(
        db,
        F.cost_recorded(
            SYSTEM,
            company,
            model_call_fact_id=uuid4(),
            provider="p",
            model="m",
            mode="live",
            source={"amount_micro": 1, "currency": "CNY", "pricing_version": "x"},
            booked={"amount_micro": 1, "currency": "CNY", "fx_rate": "1", "fx_version": "x"},
            posted=True,
            reason=None,
            run_id=None,
            task_id=None,
        ),
    )
    db.commit()
    report = verify(db, company)
    assert not report.ok and report.problems == [
        p for p in report.problems if p.startswith("M4") and "booked by 0 entries" in p
    ]
    # ledgers with no facts at all are fine
    assert verify(db, uuid4()).ok


def test_budgets_roll_up_spend_by_run_task_and_project(db):
    company = found(db)
    _, pid, task = set_up_project(db, company)
    run_a, run_b, decision_run = uuid4(), uuid4(), uuid4()

    def started(run_id, task_id):
        return NewFact(
            company_id=company,
            actor=Actor(ActorKind.agent, "eng-1"),
            kind=R.RUN_STARTED,
            payload={"run_id": str(run_id), "task_id": str(task_id) if task_id else None},
            idempotency_key=R.run_key(run_id, "started"),
            correlation_id=run_id,
        )

    for run_id, task_id in ((run_a, task), (run_b, task), (decision_run, None)):
        store.append(db, started(run_id, task_id))
    calls = {
        run_a: [store.append(db, call(company, run_id=run_a)) for _ in range(2)],
        run_b: [store.append(db, call(company, run_id=run_b, usage=Usage(4000, 2000)))],
        decision_run: [store.append(db, call(company, run_id=decision_run))],
    }
    db.commit()
    process(SessionLocal, WorkProjection())
    process(SessionLocal, CostMeter())
    process(SessionLocal, LedgerProjection())

    def micro(run_id):
        return sum(f.payload["cost"]["amount_micro"] for f in calls[run_id])

    assert run_spend_micro(db, run_a) == micro(run_a) > 0
    assert run_spend_micro(db, run_b) == micro(run_b) > micro(run_a)
    assert task_spend_micro(db, task) == micro(run_a) + micro(run_b)
    assert project_spend_micro(db, pid) == task_spend_micro(db, task)
    assert company_spend_micro(db, company) == project_spend_micro(db, pid) + micro(decision_run)
    assert project_spend_micro(db, uuid4()) == 0 and run_spend_micro(db, uuid4()) == 0
    # the cost facts carry the attribution the journal rows were built from
    cost = next(
        f
        for f in store.stream(db, kinds=[F.COST_RECORDED], company_id=company, limit=50)
        if f.payload["run_id"] == str(run_b)
    )
    assert cost.payload["task_id"] == str(task) and cost.correlation_id == run_b
    row = db.query(JournalRow).filter_by(run_id=run_b, side="debit").one()
    assert row.task_id == task and row.account_code == COMPUTE

    # units for the institution: the project budget is 10_000 units (100.00); spend is tiny
    status = budgets.project_status(db, pid, 10_000)
    assert status.spent_micro == project_spend_micro(db, pid)
    assert status.spent_units == budgets.to_units(status.spent_micro)
    assert status.remaining_units == 10_000 - status.spent_units and not status.exceeds(0.8)
    assert budgets.run_status(db, run_a, 5_000).spent_micro == micro(run_a)
    assert budgets.spend_amount(db, pid, proposed_units=300) == status.spent_units + 300
    assert verify(db, company).ok
