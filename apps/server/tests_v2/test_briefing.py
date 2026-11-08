"""Briefs (R7-a): every decision left to a human carries the company's own opinion.
The Advisor briefs what others opened; the PM proposes delivery with a brief of their
own; the API hands the brief over; nothing here decides for the human."""

import tempfile
from uuid import uuid4

import pytest
from project_support import set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_money import OWNER
from test_work import run_through

from eidolon.agent.blobs import BlobStore
from eidolon.agent.briefing import Advisor, DeliveryProposer, delivery_decision_id
from eidolon.agent.managers import Deciders, Person, Roster, StaticRoster
from eidolon.agent.providers import TextDelta
from eidolon.db import SessionLocal
from eidolon.institution import (
    ADVISED,
    BRIEF_SUMMARY_MAX,
    REQUESTED,
    DecisionError,
    GateContext,
    advise,
    bind,
    conclude,
    customize,
    load,
    request,
    sign,
)
from eidolon.institution.decisions import validate_brief
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import WorkProjection

GOOD = (
    '{"recommendation": "approve", "summary": "The greeting tool works and its test passes; '
    'accept it.", "rationale": "One task, reviewed by QA, all criteria met.", '
    '"risks": ["No manual check of the printed text"], "alternatives": ["Ask for a demo first"]}'
)


def scripted(*answers):
    provider = ScriptedLoop([[TextDelta(a)] for a in answers])
    return provider, Deciders(SessionLocal, provider, BlobStore(tempfile.mkdtemp()))


def open_deliver(db, company, pid, actor=None):
    actor = actor or Actor.system()
    action = {"kind": "project.deliver", "project_id": str(pid)}
    req, _ = request(
        T,
        GateContext("project.deliver", company, subject={**bind(action), "project_id": str(pid)}),
        actor,
    )
    did = store.append(db, req).correlation_id
    db.commit()
    return did, action


def test_advisor_briefs_what_a_human_must_decide(db):
    company, pid, task = set_up_project(db)
    did, _ = open_deliver(db, company, pid)
    assert load(db, did).brief is None
    provider, deciders = scripted(GOOD, '{"recommendation": "maybe", "summary": "?"}', GOOD)
    roster = StaticRoster(Roster([Person("ceo-1", "ceo"), Person("pm-1", "pm")]))
    advisor = Advisor(SessionLocal, deciders, roster)

    assert advisor.tick() == 1
    d = load(db, did)
    assert d.status == "pending"  # advised, not decided
    assert d.brief and d.brief.recommendation == "approve" and d.brief.advisor == "ceo-1"
    assert d.brief.advisor_role == "ceo" and d.brief.risks and d.brief.alternatives
    prompt = provider.last_request.messages[0].content
    assert prompt.startswith("Gate: project.deliver") and "Project: Greeting" in prompt
    advised = next(f for f in store.by_correlation(db, did) if f.kind == ADVISED)
    assert advised.actor.id == "ceo-1" and advised.payload["brief"]["summary"] == d.brief.summary
    assert advisor.tick() == 0  # one brief is enough

    # the institution may name another advisor; an answer that is not a verdict is dropped
    store.append(
        db,
        customize(
            db,
            company,
            OWNER,
            {"gates": {"project.deliver": {"advisor": "pm"}}},
            reason="the PM knows the delivery best",
        ),
    )
    db.commit()
    pid2 = uuid4()
    svc.create_project(
        db,
        company,
        Actor(ActorKind.agent, "pm-1"),
        project_id=pid2,
        title="Second",
        budget=1_000,
        manager="pm-1",
    )
    did2, _ = open_deliver(db, company, pid2)
    assert advisor.tick() == 0 and load(db, did2).brief is None  # "maybe" is no recommendation
    assert advisor.tick() == 0  # the completed run is reused, so nothing is asked twice
    pid3 = uuid4()
    svc.create_project(
        db,
        company,
        Actor(ActorKind.agent, "pm-1"),
        project_id=pid3,
        title="Third",
        budget=1_000,
        manager="pm-1",
    )
    did3, _ = open_deliver(db, company, pid3)
    assert advisor.tick() == 1 and load(db, did3).brief.advisor == "pm-1"
    # decided decisions are left alone, and nobody advises for the system
    action = {"kind": "project.deliver", "project_id": str(pid)}
    store.append(
        db,
        sign(T, load(db, did), OWNER, role="owner", verdict="reject", reason="no", action=action),
    )
    store.append(db, conclude(load(db, did)))
    db.commit()
    with pytest.raises(DecisionError, match="already"):
        advise(
            load(db, did),
            Actor(ActorKind.agent, "ceo-1"),
            role="ceo",
            brief=validate_brief({"recommendation": "approve", "summary": "x"}),
        )
    with pytest.raises(DecisionError, match="no opinion"):
        advise(
            load(db, did3),
            Actor.system(),
            role=None,
            brief={"recommendation": "approve", "summary": "x"},
        )


def test_brief_validation_and_defaults():
    long = "很长 " * 200
    b = validate_brief({"recommendation": " Approve ", "summary": long, "risks": ["", " a  b "]})
    assert b["recommendation"] == "approve" and len(b["summary"]) == BRIEF_SUMMARY_MAX
    assert b["risks"] == ["a b"] and b["alternatives"] == [] and b["cost"] is None
    with pytest.raises(DecisionError, match="recommendation"):
        validate_brief({"recommendation": "yes", "summary": "x"})
    with pytest.raises(DecisionError, match="summary"):
        validate_brief({"recommendation": "approve", "summary": "  "})
    # who briefs whom: the rule's advisor, else the decider's direct report, else the top agent
    assert T.advisor_for("project.deliver", "owner") == "ceo"
    assert T.advisor_for("mission.accept", "owner") == "ceo"
    assert T.advisor_for("task.review", "nobody") == "ceo"
    changed = T.with_changes({"gates": {"project.deliver": {"advisor": "pm"}}})
    assert changed.advisor_for("project.deliver", "owner") == "pm"
    assert changed.as_dict()["gates"]["project.deliver"]["advisor"] == "pm"


def test_pm_proposes_delivery_with_a_brief_and_the_owner_only_judges(db):
    company, pid, task = set_up_project(db)
    provider, deciders = scripted(GOOD, GOOD)
    roster = StaticRoster(Roster([Person("pm-1", "pm"), Person("ceo-1", "ceo")]))
    proposer = DeliveryProposer(SessionLocal, deciders, roster)
    assert proposer.tick() == 0  # nothing done yet
    run_through(db, company, pid, task, "eng-1", artifact=True)
    process(SessionLocal, WorkProjection())
    assert proposer.tick() == 1
    did = delivery_decision_id(pid)
    d = load(db, did)
    assert d.status == "pending" and d.proposer == "pm-1" and d.resolution.decider == "human"
    assert d.brief and d.brief.advisor == "pm-1" and d.brief.recommendation == "approve"
    assert "action_hash" in d.subject
    prompt = provider.last_request.messages[0].content
    assert prompt.startswith("Project: Greeting") and "out.txt" in prompt and "qa-1 pass" in prompt
    assert proposer.tick() == 0  # one open proposal at a time
    requested = [f for f in store.by_correlation(db, did) if f.kind == REQUESTED]
    assert requested[0].payload["brief"]["advisor"] == "pm-1"
    # the Owner rejects; the PM may propose again under a new attempt
    action = {"kind": "project.deliver", "project_id": str(pid)}
    store.append(
        db, sign(T, d, OWNER, role="owner", verdict="reject", reason="not yet", action=action)
    )
    store.append(db, conclude(load(db, did)))
    db.commit()
    assert proposer.tick() == 1
    again = load(db, delivery_decision_id(pid, 1))
    assert again.status == "pending" and again.brief is not None
    # Advisor leaves a briefed decision alone (the PM already spoke)
    assert Advisor(SessionLocal, deciders, roster).tick() == 0
    # the Owner approves exactly the bound action and the project is delivered
    store.append(
        db, sign(T, again, OWNER, role="owner", verdict="approve", reason="ok", action=action)
    )
    store.append(db, conclude(load(db, again.decision_id)))
    db.commit()
    svc.deliver_project(db, pid, OWNER, decision_id=again.decision_id)
    db.commit()
    process(SessionLocal, WorkProjection())
    assert svc.project(db, pid).status == "delivered"
    assert proposer.tick() == 0


def test_contract_b1_brief_writers_never_sign():
    """B1: the Advisor and the DeliveryProposer form opinions; they hold no pen. The
    module that writes briefs must not be able to sign, conclude or record a decision."""
    import ast
    from pathlib import Path

    import eidolon.agent.briefing as briefing

    tree = ast.parse(Path(briefing.__file__).read_text())
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }
    assert not imported & {"sign", "conclude", "system_record", "SIGNED", "RECORDED"}, imported
    assert {"advise", "request"} <= imported
