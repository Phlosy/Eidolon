"""Written work and decision rhythm (R7-e): document / design / research are task kinds
with their own worker and reviewer prompts; the inbox settles in one click by following
the company's recommendations."""

import tempfile
from uuid import uuid4

from project_support import components, drive, set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_api import PREFIX, csrf, project_everything
from test_api_commands import owner_client, post
from test_briefing import GOOD
from test_work import actor, assign, decide, run_through

from eidolon.agent.blobs import BlobStore
from eidolon.agent.briefing import DeliveryProposer
from eidolon.agent.managers import Deciders, Person, Planner, Roster, StaticRoster
from eidolon.agent.prompts import (
    DESIGNER_SYSTEM,
    DOCUMENT_REVIEWER_SYSTEM,
    ENGINEER_SYSTEM,
    REVIEWER_SYSTEM,
    WRITER_SYSTEM,
    reviewer_for,
    system_for,
)
from eidolon.agent.providers import TextDelta
from eidolon.db import SessionLocal
from eidolon.identity import IdentityProjection
from eidolon.institution import GateContext, bind, load, request
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import facts as F


def test_kinds_pick_prompts_and_are_validated(db):
    assert system_for("build", "engineer") is ENGINEER_SYSTEM
    assert system_for("document", "engineer") is WRITER_SYSTEM
    assert system_for("design", None) is DESIGNER_SYSTEM
    assert (
        reviewer_for("build") is REVIEWER_SYSTEM
        and reviewer_for("research") is DOCUMENT_REVIEWER_SYSTEM
    )
    company, pid, _ = set_up_project(db)
    try:
        svc.create_task(
            db, pid, actor("pm-1", "pm"), task_id=uuid4(), title="x", description="d", kind="poem"
        )
    except F.WorkError as exc:
        assert "task kind" in str(exc)
    else:
        raise AssertionError("unknown kind accepted")
    # the PM's plan carries kinds; unknown ones are refused as a whole (plan.task_rejected)
    planner = Planner(
        SessionLocal,
        Deciders(SessionLocal, ScriptedLoop([]), BlobStore(tempfile.mkdtemp())),
        StaticRoster(Roster([])),
        svc=svc,
    )
    state = svc.project(db, pid)
    ok = planner._create_tasks(
        db,
        state,
        Person("pm-1", "pm"),
        [
            {"key": "a", "title": "Write the guide", "description": "d", "kind": "Document"},
            {"key": "b", "title": "Build it", "description": "d", "depends_on": ["a"]},
        ],
    )
    assert ok
    kinds = {t.title: t.kind for t in svc.project(db, pid).tasks.values()}
    assert kinds["Write the guide"] == "document" and kinds["Build it"] == "build"


def test_a_document_task_is_written_and_judged_as_a_document(db, tmp_path):
    company, _, _ = set_up_project(db)  # adopts the institution; its build task stays unassigned
    pid = uuid4()
    svc.create_project(
        db, company, actor("pm-1", "pm"), project_id=pid, title="Docs", budget=5_000, manager="pm-1"
    )
    doc = uuid4()
    svc.create_task(
        db,
        pid,
        actor("pm-1", "pm"),
        task_id=doc,
        title="User guide",
        description="Write README.md explaining add/list/delete for end users.",
        acceptance_criteria=["covers all three commands", "has an example session"],
        role="engineer",
        kind="document",
    )
    db.commit()
    plan = decide(
        db,
        company,
        "plan.approve",
        {"project_id": str(pid)},
        signers=[("tl-1", "tech_lead"), ("pm-2", "pm")],
        proposer="pm-1",
    )
    svc.approve_plan(db, pid, actor("pm-1", "pm"), decision_id=plan)
    db.commit()
    assign(db, company, pid, doc, "eng-1")
    provider = ScriptedLoop(
        [
            [TextDelta("Here is the guide.")],  # the writer
            [TextDelta('{"verdict": "pass", "reason": "covers everything"}')],  # the reviewer
        ]
    )
    blobs = BlobStore(tmp_path / "b")
    runs, reviews = components(SessionLocal, provider, blobs, tmp_path / "ws")
    assert drive(SessionLocal, db, pid, runs, reviews, max_cycles=2)[-1] == "done"
    started = [
        f
        for f in store.stream(db, kinds=["agent.run.started"], company_id=company, limit=20)
        if f.payload.get("task_id") == str(doc)
    ]
    by_person = {f.payload["person_id"]: f for f in started}
    # the writer got the writer's prompt, the reviewer the document reviewer's, and the
    # review brief says there are no tests to run
    assert blobs.get_json(by_person["eng-1"].payload["system_ref"]) == WRITER_SYSTEM
    assert blobs.get_json(by_person["qa-1"].payload["system_ref"]) == DOCUMENT_REVIEWER_SYSTEM
    review_prompt = blobs.get_json(by_person["qa-1"].payload["prompt_ref"])[0]["content"]
    assert "This is a document task" in review_prompt and "no tests to run" in review_prompt


def test_the_inbox_settles_by_recommendation_in_one_call(db):
    client, user_id = owner_client()
    company = __import__("uuid").UUID(
        post(client, "/companies", {"name": "S", "slug": "s"}).json()["company_id"]
    )
    for pid_, role in (("eng-1", "engineer"), ("pm-1", "pm"), ("tl-1", "tech_lead")):
        post(client, f"/companies/{company}/employees", {"person_id": pid_, "role": role})
    process(SessionLocal, IdentityProjection())
    # two finished projects whose PMs proposed delivery with a brief, one bare decision without
    briefed = []
    for title in ("P1", "P2"):
        pid = __import__("uuid").UUID(
            svc.create_project(
                db, company, actor("pm-1", "pm"), title=title, budget=10_000, manager="pm-1"
            ).payload["project_id"]
        )
        task = uuid4()
        svc.create_task(
            db, pid, actor("pm-1", "pm"), task_id=task, title="T", description="d", role="engineer"
        )
        db.commit()
        plan = decide(
            db,
            company,
            "plan.approve",
            {"project_id": str(pid)},
            signers=[("tl-1", "tech_lead"), ("pm-2", "pm")],
            proposer="pm-1",
        )
        svc.approve_plan(db, pid, actor("pm-1", "pm"), decision_id=plan)
        db.commit()
        run_through(db, company, pid, task, "eng-1")
        project_everything()
        briefed.append(pid)
    proposer = DeliveryProposer(
        SessionLocal,
        Deciders(
            SessionLocal,
            ScriptedLoop([[TextDelta(GOOD)], [TextDelta(GOOD)]]),
            BlobStore(tempfile.mkdtemp()),
        ),
        StaticRoster(Roster([Person("pm-1", "pm")])),
    )
    assert proposer.tick() == 2
    bare_action = {"kind": "hire", "person_id": "eng-9", "role": "engineer"}
    bare, _ = request(
        T,
        GateContext(
            "hire", company, subject={**bind(bare_action), "person_id": "eng-9", "role": "engineer"}
        ),
        Actor(ActorKind.agent, "ceo-1"),
    )
    store.append(db, bare)
    db.commit()
    pending = client.get(f"{PREFIX}/companies/{company}/decisions").json()
    assert len(pending) == 3
    r = client.post(f"{PREFIX}/companies/{company}/decisions/follow", json={}, headers=csrf(client))
    assert r.status_code == 200, r.text
    results = {x["gate"]: x for x in r.json()}
    assert (
        results["project.deliver"]["status"] == "approved"
        and results["project.deliver"]["applied"] == "project.delivered"
    )
    assert sum(1 for x in r.json() if x.get("applied") == "project.delivered") == 2
    assert (
        results["hire"]["status"] == "skipped" and "no recommendation" in results["hire"]["reason"]
    )
    assert all(svc.project(db, p).status == "delivered" for p in briefed)
    left = client.get(f"{PREFIX}/companies/{company}/decisions").json()
    assert [d["gate"] for d in left] == ["hire"]
    # the signatures say why: the brief's own words, signed by the owner
    for p in briefed:
        from eidolon.agent.briefing import delivery_decision_id

        d = load(db, delivery_decision_id(p))
        assert d.signatures[0].role == "owner" and d.signatures[0].reason == d.brief.summary
    # a second call finds nothing with a recommendation
    again = client.post(
        f"{PREFIX}/companies/{company}/decisions/follow",
        json={"decision_ids": [str(uuid4())]},
        headers=csrf(client),
    )
    assert again.status_code == 200 and again.json() == []
