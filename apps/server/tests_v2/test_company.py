"""R3-e: the three-role real project, replayed. CEO accepts, PM plans, Tech Lead
approves and assigns, engineers build with handoff, QA reviews, the Owner delivers."""

import json
from pathlib import Path

from company_support import (
    MISSION_TITLE,
    company_project_id,
    drive_company,
    owner_signs_delivery,
    propose_mission,
    set_up_company,
    studio_jobs,
)
from loop_support import replay_provider
from project_support import store, svc

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.managers import parse_json
from eidolon.agent.providers import fixture_dir
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.config import settings
from eidolon.db import SessionLocal
from eidolon.institution import BRIEF_SUMMARY_MAX, SIGNED, load
from eidolon.institution import RECORDED as DECISION_RECORDED
from eidolon.kernel import provenance
from eidolon.kernel.consumers import process
from eidolon.money import (
    COST_RECORDED,
    CostMeter,
    JournalRow,
    LedgerProjection,
    company_spend_micro,
    cost_trace,
    ledger_fact_ids,
    project_costs,
    project_spend_micro,
)
from eidolon.money import verify as ledger_verify
from eidolon.talent import EvidenceRow, TalentProjection, competencies_of
from eidolon.work import facts as F


def test_parse_json_is_lenient():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here: {"verdict": "approve", "reason": "x"} thanks')["verdict"] == "approve"
    assert parse_json("[1, 2]") is None and parse_json("nope") is None


def test_company_fixtures_are_recorded():
    seen = sum(
        1
        for p in fixture_dir(settings).glob("chat-*.json")
        if json.loads(p.read_text())["request"]["messages"][0]["content"].startswith(
            ("Mission: " + MISSION_TITLE, "Project: " + MISSION_TITLE)
        )
    )
    assert seen >= 2, (
        "R3-e fixtures missing: run `make v2-record-fixtures V2_ARGS='--only company'`"
    )


def test_three_roles_deliver_a_project_from_replayed_fixtures(db, tmp_path):
    company = set_up_company(db)
    mission_id = propose_mission(db, company)
    jobs = studio_jobs(
        SessionLocal, replay_provider(SessionLocal), BlobStore(tmp_path / "blobs"), tmp_path / "ws"
    )
    trail = drive_company(SessionLocal, db, mission_id, jobs)
    final = trail[-1]
    assert final["mission"] == "accepted" and final["project"] == "active", trail
    assert final["tasks"] and all(s == "done" for s in final["tasks"].values()), trail
    pid = company_project_id(db, mission_id)
    state = svc.project(db, pid)
    assert 2 <= len(state.tasks) <= 4
    assert any(t.depends_on for t in state.tasks.values())  # the PM produced an ordered plan

    # Every management act is a decision signed by the right role; the system signed nothing.
    signed = [f for f in store.stream(db, kinds=[SIGNED], company_id=company, limit=200)]
    by_gate = {}
    for f in signed:
        by_gate.setdefault(f.payload["gate"], set()).add((f.actor.id, f.payload["role"]))
    assert by_gate["mission.accept"] == {("ceo-1", "ceo")}
    assert by_gate["plan.approve"] == {("tl-1", "tech_lead")}
    assert by_gate["task.assign"] == {("tl-1", "tech_lead")}
    assert by_gate["task.review"] == {("qa-1", "qa")}
    assert all(f.actor.kind.value == "agent" for f in signed)
    recorded = [
        f for f in store.stream(db, kinds=[DECISION_RECORDED], company_id=company, limit=200)
    ]
    assert all(f.actor.kind.value == "system" for f in recorded)  # recording is mechanical
    # the customised institution was in force and is referenced by the plan decision
    plan_req = next(
        f
        for f in store.stream(
            db, kinds=["institution.decision.requested"], company_id=company, limit=200
        )
        if f.payload["gate"] == "plan.approve"
    )
    assert (
        plan_req.payload["resolution"]["quorum"] == 1
        and plan_req.payload["resolution"]["template_version"] == 2
    )

    # Handoff: a downstream task consumed its input and had the file in its workspace.
    consumed = [f for f in store.by_correlation(db, pid) if f.kind == F.ARTIFACT_CONSUMED]
    downstream = [t for t in state.tasks.values() if t.inputs]
    if downstream:
        assert consumed, "input declared but nothing consumed"
        t = downstream[0]
        ws = tmp_path / "ws" / str(company) / str(pid) / str(t.task_id)
        upstream_paths = {a.path for a in state.artifacts.values() if a.task_id in t.inputs}
        assert any((ws / p).exists() for p in upstream_paths)
    # Each task's assignee is an engineer from the roster; reviewers never reviewed their own work.
    for t in state.tasks.values():
        assert t.assignee in ("eng-1", "eng-2")
        assert all(r["reviewer"] != t.assignee for r in t.reviews)
        assert t.reviews[-1]["verdict"] == "pass"
    # Manager runs and engineer runs are all on the same loop, all accounted for.
    runs = {
        f.payload["person_id"]
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=200)
    }
    assert {"ceo-1", "pm-1", "tl-1", "qa-1"} <= runs and runs & {"eng-1", "eng-2"}
    calls = [f for f in store.stream(db, kinds=[MODEL_CALLED], company_id=company, limit=500)]
    assert calls and all(f.payload["cost"]["currency"] == "CNY" for f in calls)
    total = sum(f.payload["cost"]["amount_micro"] for f in calls) / 1_000_000
    assert total < 2.0  # CNY
    # Money (R4-b): every call is metered; replays are recorded but not booked by default,
    # and a meter that books replays carries each one into the ledger, attributed to its
    # task, so the project's spend is a query over the journal.
    process(SessionLocal, CostMeter(book_replays=True))
    process(SessionLocal, LedgerProjection())
    costs = store.stream(db, kinds=[COST_RECORDED], company_id=company, limit=500)
    assert len(costs) == len(calls) and all(f.payload["mode"] == "replay" for f in costs)
    assert all(f.payload["posted"] for f in costs)
    assert 0 < project_spend_micro(db, pid) < company_spend_micro(db, company)  # + manager runs
    assert company_spend_micro(db, company) == sum(f.payload["cost"]["amount_micro"] for f in calls)
    assert ledger_verify(db, company).ok
    # R4-c: a ledger line of an engineer run traces back to its step, run, task and this
    # project; the roll-up covers the engineers' runs per task plus the PM / Tech Lead
    # management runs the project called for, but not the CEO's mission run.
    row = (
        db.query(JournalRow)
        .filter(JournalRow.company_id == company, JournalRow.task_id.is_not(None))
        .first()
    )
    t = cost_trace(db, row.entry_id)
    assert t.project_id == pid and t.task_id in state.tasks and t.missing == []
    assert t.cause is not None and t.cause.kind.startswith("work.task.")
    # engineers, QA and the Tech Lead's assignment runs all carry the task they are about
    assert t.step is not None
    assert t.run_started.payload["person_id"] in ("eng-1", "eng-2", "qa-1", "tl-1")
    costs = project_costs(db, pid)
    assert {tc.task_id for tc in costs.tasks} == set(state.tasks)
    assert costs.task_micro == project_spend_micro(db, pid) > 0
    assert {r.person_id for r in costs.management} == {"pm-1", "tl-1"}
    assert 0 < costs.total_micro < company_spend_micro(db, company)
    ids = ledger_fact_ids(db, pid)
    bundle = provenance.bundle(db, company, correlation_id=pid, fact_ids=ids)
    assert {f["kind"] for f in bundle["facts"]} >= {COST_RECORDED, "money.entry.posted"}
    assert bundle["bundle_hash"] == provenance.seal(bundle)
    # R5-a: everyone who worked has evidence in the area they worked in; the Owner has none;
    # competencies exist for people to read and nothing below talent imports them.
    process(SessionLocal, TalentProjection())
    evidence = {}
    for r in db.query(EvidenceRow).filter_by(company_id=company):
        evidence.setdefault(r.person_id, set()).add((r.kind, r.area))
    assert {"qa-1", "pm-1", "tl-1", "ceo-1"} <= set(evidence) and evidence.keys() & {
        "eng-1",
        "eng-2",
    }
    assert all(area == "engineer" for p in ("eng-1", "eng-2") for _, area in evidence.get(p, ()))
    assert ("task_passed", "engineer") in set().union(
        *(evidence[p] for p in ("eng-1", "eng-2") if p in evidence)
    )
    assert ("review_given", "qa") in evidence["qa-1"] and ("decision_signed", "ceo") in evidence[
        "ceo-1"
    ]
    assert not any(p.startswith("owner-") for p in evidence)
    assert all(
        c.positive > 0 for p in ("eng-1", "eng-2") if p in evidence for c in competencies_of(db, p)
    )

    # R7-a: the PM proposed delivery with a plain-language brief; the Owner only judges it.
    did = owner_signs_delivery(db, company, pid)
    delivered = load(db, did)
    assert delivered.status == "approved" and delivered.brief.recommendation == "approve"
    assert 0 < len(delivered.brief.summary) <= BRIEF_SUMMARY_MAX and delivered.brief.rationale
    assert delivered.signatures[-1].person_id.startswith("owner-")
    assert svc.project(db, pid).status == "delivered"
    process(SessionLocal, TalentProjection())
    assert ("project_delivered", "pm") in {
        (r.kind, r.area) for r in db.query(EvidenceRow).filter_by(person_id="pm-1")
    }
    assert store.verify_chain(db, company).ok
    bundle = provenance.bundle(db, company, correlation_id=pid)
    assert bundle["facts"] and bundle["facts"][0]["kind"] == F.PROJECT_CREATED
    # Another cycle does nothing.
    assert (
        drive_company(SessionLocal, db, mission_id, jobs, max_cycles=1)[-1]["project"]
        == "delivered"
    )


def test_fixture_prompts_for_managers_carry_no_ids():
    for p in Path(fixture_dir(settings)).glob("chat-*.json"):
        first = json.loads(p.read_text())["request"]["messages"][0]["content"]
        if first.startswith(("Mission: ", "Project: ", "Task: ")):
            head = first.splitlines()[0]
            assert (
                "-" not in head.replace(MISSION_TITLE, "").replace("Tiny notes", "")
                or "Task:" in head
            ), head
