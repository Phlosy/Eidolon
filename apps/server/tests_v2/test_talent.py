"""Talent (R5-a): evidence derived from facts, competencies as EWMA projections,
profile / resume queries, and the guarantee that nothing below reads any of it."""

import ast
import io
import json
from pathlib import Path
from uuid import uuid4

from project_support import ENGINEER, set_up_project, store, svc
from sqlalchemy import text
from test_cost_trace import run_facts
from test_money import OWNER, found
from test_work import actor, decide

from eidolon.agent import run as R
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.identity import IdentityService
from eidolon.institution import staffing
from eidolon.kernel.consumers import process, rebuild
from eidolon.kernel.facts import Actor, ActorKind, NewFact
from eidolon.talent import (
    Competency,
    CompetencyRow,
    EvidenceRow,
    TalentProjection,
    competencies_of,
    evidence_of,
    profile,
    resume,
)
from eidolon.talent import evidence as E

ids = IdentityService(store)
EIDOLON = Path(__file__).resolve().parents[1] / "eidolon"


def staffed_project(db):
    """A real company with hired agents and the one-task project, assigned to eng-1."""
    company = found(db)
    for pid, role in (("eng-1", "engineer"), ("qa-1", "qa"), ("pm-1", "pm"), ("tl-1", "tech_lead")):
        ids.create_person(db, OWNER, pid, name=pid, kind="agent")
        staffing.hire(db, company, OWNER, person_id=pid, role=role)
    db.commit()
    _, pid, task = set_up_project(db, company)
    return company, pid, task


def ended(db, company, run_id, kind, **payload):
    return store.append(
        db,
        NewFact(
            company_id=company,
            actor=Actor(ActorKind.agent, "x"),
            kind=kind,
            payload={"run_id": str(run_id), **payload},
            idempotency_key=R.run_key(run_id, kind.rsplit(".", 1)[1]),
            correlation_id=run_id,
        ),
    )


def snapshot(db):
    ev = {
        (r.fact_id, r.person_id): (r.area, r.kind, r.signal, r.weight, r.task_id, r.run_id)
        for r in db.query(EvidenceRow)
    }
    comp = {
        (r.person_id, r.area): (r.score, r.raw, r.evidence_count, r.positive, r.negative)
        for r in db.query(CompetencyRow)
    }
    return ev, comp


def test_evidence_is_derived_from_facts_and_rebuilds(db):
    company, pid, task = staffed_project(db)
    owner = next(e.person_id for e in ids.company(db, company).active() if e.role == "owner")

    # attempt 1: artifact, submit, rework
    run_a = uuid4()
    started = svc.start_task(db, pid, task, Actor.system(), run_id=run_a, person_id=ENGINEER)
    run_facts(db, company, run_a, task_id=task, cause=started.id)
    svc.produce_artifact(
        db,
        pid,
        task,
        actor(ENGINEER, "engineer"),
        run_id=run_a,
        path="a.py",
        blob_ref="sha256:" + "a" * 64,
        size=1,
    )
    svc.submit_task(db, pid, task, actor(ENGINEER, "engineer"), run_id=run_a)
    review = decide(
        db,
        company,
        "task.review",
        {"task_id": str(task)},
        signers=[("qa-1", "qa")],
        author=ENGINEER,
        verdict="rework",
    )
    svc.review_task(
        db,
        pid,
        task,
        actor("qa-1", "qa"),
        decision_id=review,
        verdict="rework",
        reviewer="qa-1",
        reason="tests missing",
    )
    ended(db, company, run_a, R.RUN_COMPLETED, steps=3)
    # attempt 2: pass
    run_b = uuid4()
    started_b = svc.start_task(db, pid, task, Actor.system(), run_id=run_b, person_id=ENGINEER)
    run_facts(db, company, run_b, task_id=task, cause=started_b.id)
    svc.submit_task(db, pid, task, actor(ENGINEER, "engineer"), run_id=run_b)
    review = decide(
        db,
        company,
        "task.review",
        {"task_id": str(task)},
        signers=[("qa-1", "qa")],
        author=ENGINEER,
    )
    svc.review_task(
        db, pid, task, actor("qa-1", "qa"), decision_id=review, verdict="pass", reviewer="qa-1"
    )
    ended(db, company, run_b, R.RUN_COMPLETED, steps=2)
    # a Tech Lead decision run that blew its budget (no task)
    tl_run = uuid4()
    run_facts(db, company, tl_run, task_id=None, cause=started_b.id, person="tl-1")
    ended(db, company, tl_run, R.RUN_BUDGET_EXCEEDED, reason="max_steps")
    # the Owner delivers: the PM gets the credit, the Owner (human) gets no evidence
    deliver = decide(
        db,
        company,
        "project.deliver",
        {"project_id": str(pid)},
        signers=[(owner, "owner")],
        action={"kind": "project.deliver", "project_id": str(pid)},
    )
    svc.deliver_project(db, pid, Actor(ActorKind.human, owner), decision_id=deliver)
    db.commit()

    process(SessionLocal, TalentProjection())
    process(SessionLocal, TalentProjection())  # idempotent
    by_person: dict[str, list[tuple[str, str, int]]] = {}
    for r in db.query(EvidenceRow).order_by(EvidenceRow.last_seq):
        by_person.setdefault(r.person_id, []).append((r.kind, r.area, r.signal))
    assert by_person[ENGINEER] == [
        (E.ARTIFACT_PRODUCED, "engineer", 0),
        (E.TASK_REWORK, "engineer", -1),
        (E.RUN_COMPLETED, "engineer", 0),
        (E.TASK_PASSED, "engineer", 1),
        (E.RUN_COMPLETED, "engineer", 0),
    ]
    assert by_person["qa-1"] == [(E.REVIEW_GIVEN, "qa", 0)] * 2
    assert by_person["tl-1"] == [
        (E.DECISION_SIGNED, "tech_lead", 0),  # plan.approve and task.assign signatures
        (E.DECISION_SIGNED, "tech_lead", 0),  # from set_up_project
        (E.RUN_BUDGET_EXCEEDED, "tech_lead", -1),
    ]
    assert by_person["pm-1"] == [(E.PROJECT_DELIVERED, "pm", 1)]
    assert by_person["pm-2"] == [(E.DECISION_SIGNED, "pm", 0)]  # area = the role it signed as
    assert owner not in by_person
    # every piece names its source and its subject
    for r in db.query(EvidenceRow):
        assert store.get(db, r.fact_id) is not None and r.company_id == company
        if r.kind in (E.TASK_PASSED, E.TASK_REWORK, E.ARTIFACT_PRODUCED, E.REVIEW_GIVEN):
            assert r.task_id == task and r.project_id == pid
        if r.kind.startswith("run_"):
            assert r.run_id is not None

    # competencies are the EWMA of the person's evidence, in seq order
    for person in (ENGINEER, "qa-1", "tl-1", "pm-1"):
        for row in competencies_of(db, person):
            expected = Competency()
            for ev in evidence_of(db, person):
                if ev.area == row.area:
                    expected = expected.absorb(ev.signal, ev.weight)
            assert (row.raw, row.score, row.evidence_count, row.positive, row.negative) == (
                expected.raw,
                expected.score,
                expected.evidence_count,
                expected.positive,
                expected.negative,
            )
    eng = competencies_of(db, ENGINEER)[0]
    assert eng.area == "engineer" and eng.positive == 1 and eng.negative == 1 and eng.score > 50
    assert competencies_of(db, "qa-1")[0].score == 50  # neutral evidence only: unrated

    # tamper → rebuild reproduces exactly
    before = snapshot(db)
    db.execute(text("UPDATE talent_competencies SET score = 99, raw = 1.0"))
    db.execute(text("DELETE FROM talent_evidence WHERE kind = 'task_passed'"))
    db.commit()
    assert snapshot(db) != before
    db.commit()  # release read locks: rebuild truncates in its own session
    assert rebuild(SessionLocal, TalentProjection()) > 0
    db.expire_all()
    assert snapshot(db) == before

    # profile and resume are queries over the read models
    prof = profile(db, ENGINEER)
    assert prof is not None and prof.person.kind == "agent" and prof.spend_micro == 0
    assert [e.role for e in prof.employments] == ["engineer"] or prof.employments == []
    d = prof.as_dict()
    assert d["competencies"][0]["area"] == "engineer" and d["evidence_count"] == 5
    timeline = resume(db, ENGINEER)
    assert [r.kind for r in timeline][-5:] == [
        E.ARTIFACT_PRODUCED,
        E.TASK_REWORK,
        E.RUN_COMPLETED,
        E.TASK_PASSED,
        E.RUN_COMPLETED,
    ]
    assert profile(db, "nobody") is None
    out = io.StringIO()
    assert (
        main(["talent-profile", ENGINEER, "--resume"], session_factory=SessionLocal, out=out) == 0
    )
    text_out = out.getvalue()
    assert f"engineer: {eng.score}" in text_out and "task_passed engineer" in text_out
    out = io.StringIO()
    assert main(["talent-profile", "qa-1", "--json"], session_factory=SessionLocal, out=out) == 0
    assert json.loads(out.getvalue())["competencies"][0]["rated"] is False
    assert main(["talent-profile", "ghost"], session_factory=SessionLocal, out=io.StringIO()) == 2


def test_competency_is_a_projection_nothing_below_reads():
    below = ("kernel", "identity", "institution", "work", "agent", "money")
    offenders = []
    for layer in below:
        for path in (EIDOLON / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                if any(n == "eidolon.talent" or n.startswith("eidolon.talent.") for n in names):
                    offenders.append(str(path.relative_to(EIDOLON)))
    assert offenders == []
    # the arithmetic: neutral evidence counts but does not move the score; signed evidence
    # moves it by alpha × weight toward ±1 and never leaves [-1, 1]
    c = Competency().absorb(0, 1.0)
    assert c.score == 50 and not c.rated and c.evidence_count == 1
    c = c.absorb(1, 1.0)
    assert c.raw == 0.25 and c.score == 62 and c.rated and c.positive == 1
    c = c.absorb(-1, 0.5)
    assert c.raw == 0.25 + 0.25 * 0.5 * (-1 - 0.25) and c.negative == 1
    for _ in range(50):
        c = c.absorb(1, 1.0)
    assert c.raw <= 1.0 and c.score == 100
