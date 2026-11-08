"""API write side (R5-c): every command is a DTO over a domain service; the API
decides nothing. A human acts within their grants; management acts go through the
institution's decision envelope; a concluded decision is applied mechanically."""

import tempfile
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from project_support import store, svc
from test_api import PREFIX, csrf, project_everything, register
from test_cost_trace import run_facts
from test_work import actor, run_through

from eidolon.agent import run as R
from eidolon.agent.providers import TextDelta
from eidolon.api.app import create_app
from eidolon.db import SessionLocal
from eidolon.identity import IdentityProjection, IdentityService
from eidolon.institution import current as current_institution
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import WorkProjection

ids = IdentityService(store)


def owner_client():
    app = create_app(SessionLocal)
    client = TestClient(app)
    client.__enter__()
    me = register(client, "owner@example.com", name="Owner", person_id="owner-1")
    process(SessionLocal, IdentityProjection())
    return client, UUID(me["user_id"])


def post(client, path, json=None, **headers):
    return client.post(f"{PREFIX}{path}", json=json or {}, headers={**csrf(client), **headers})


def test_found_company_hire_and_propose_mission(db):
    client, user_id = owner_client()
    # CSRF is enforced on commands too
    r = client.post(f"{PREFIX}/companies", json={"name": "Tiny Studio", "slug": "tiny"})
    assert r.status_code == 403

    r = post(
        client, "/companies", {"name": "Tiny Studio", "slug": "tiny"}, **{"Idempotency-Key": "k1"}
    )
    assert r.status_code == 201, r.text
    company = UUID(r.json()["company_id"])
    again = post(
        client, "/companies", {"name": "Tiny Studio", "slug": "tiny"}, **{"Idempotency-Key": "k1"}
    )
    assert again.status_code == 201 and again.json()["company_id"] == str(company)  # K2
    assert post(client, "/companies", {"name": "T", "slug": "Bad Slug"}).status_code == 422
    process(SessionLocal, IdentityProjection())
    me = client.get(f"{PREFIX}/auth/me").json()
    assert me["companies"] == [
        {"company_id": str(company), "person_id": "owner-1", "role": "owner", "title": "Owner"}
    ]
    assert current_institution(db, company).template.id == "software-studio-v1"
    # the institution: adopting again is refused by the owner check only for others;
    # customizing needs the grant (the owner has "*")
    r = post(
        client,
        f"/companies/{company}/institution/customize",
        {"changes": {"review_policy": {"rework_limit": 1}}, "reason": "small team"},
    )
    assert r.status_code == 201 and r.json()["version"] == 2
    r = post(
        client,
        f"/companies/{company}/institution/customize",
        {"changes": {"gates": {"task.assign": {"role": "ghost"}}}, "reason": "oops"},
    )
    assert r.status_code == 422
    r = post(client, f"/companies/{company}/institution/adopt", {"template_id": "nope"})
    assert r.status_code == 409

    # hiring agents is the Owner's own act (humans need no decision); roles come from
    # the institution, so a wizard is refused and a human role is refused for an agent
    for pid, role in (("eng-1", "engineer"), ("qa-1", "qa"), ("pm-1", "pm"), ("tl-1", "tech_lead")):
        r = post(
            client,
            f"/companies/{company}/employees",
            {"person_id": pid, "role": role, "name": pid.upper(), "persona": {"style": "terse"}},
        )
        assert r.status_code == 201, r.text
    assert (
        post(
            client, f"/companies/{company}/employees", {"person_id": "x-1", "role": "wizard"}
        ).status_code
        == 422
    )
    assert (
        post(
            client, f"/companies/{company}/employees", {"person_id": "x-2", "role": "owner"}
        ).status_code
        == 422
    )
    assert (
        post(
            client, f"/companies/{company}/employees", {"person_id": "eng-1", "role": "qa"}
        ).status_code
        == 422
    )  # already employed
    process(SessionLocal, IdentityProjection())
    employees = client.get(f"{PREFIX}/companies/{company}/employees").json()
    assert {e["person_id"] for e in employees} == {"owner-1", "eng-1", "qa-1", "pm-1", "tl-1"}
    assert ids.person(db, "eng-1").persona == {"style": "terse"}

    # ending an employment; the owner's own cannot be ended
    eng = next(e for e in employees if e["person_id"] == "eng-1")
    r = post(
        client, f"/companies/{company}/employments/{eng['employment_id']}/end", {"reason": "done"}
    )
    assert r.status_code == 200
    owner_emp = next(e for e in employees if e["person_id"] == "owner-1")
    r = post(
        client,
        f"/companies/{company}/employments/{owner_emp['employment_id']}/end",
        {"reason": "x"},
    )
    assert r.status_code == 422
    r = post(
        client, f"/companies/{company}/employments/{eng['employment_id']}/end", {"reason": "again"}
    )
    assert r.status_code == 422

    # a mission, idempotent on the client key
    body = {
        "title": "Notes CLI",
        "goal": "A tiny note keeper.",
        "success_criteria": ["adds", "lists"],
        "budget": 20_000,
    }
    r = post(client, f"/companies/{company}/missions", body, **{"Idempotency-Key": "m1"})
    assert r.status_code == 201
    mission_id = r.json()["mission_id"]
    r2 = post(client, f"/companies/{company}/missions", body, **{"Idempotency-Key": "m1"})
    assert r2.json()["mission_id"] == mission_id
    r3 = post(client, f"/companies/{company}/missions", body)
    assert r3.json()["mission_id"] != mission_id
    process(SessionLocal, WorkProjection())
    missions = client.get(f"{PREFIX}/companies/{company}/missions").json()
    assert {m["mission_id"] for m in missions} == {mission_id, r3.json()["mission_id"]}
    assert missions[0]["status"] == "proposed"  # the CEO agent decides it in the worker

    # a stranger has no grants anywhere
    stranger = TestClient(client.app)
    stranger.__enter__()
    register(stranger, "s@example.com", name="S")
    process(SessionLocal, IdentityProjection())
    for path, payload in (
        (f"/companies/{company}/missions", body),
        (f"/companies/{company}/employees", {"person_id": "z", "role": "qa"}),
        (f"/companies/{company}/institution/customize", {"changes": {}, "reason": "r"}),
    ):
        assert post(stranger, path, payload).status_code == 403, path
    client.__exit__(None, None, None)
    stranger.__exit__(None, None, None)


def test_decisions_are_signed_by_humans_within_the_envelope(db):
    client, user_id = owner_client()
    company = UUID(post(client, "/companies", {"name": "S", "slug": "s"}).json()["company_id"])
    for pid, role in (("eng-1", "engineer"), ("qa-1", "qa"), ("pm-1", "pm"), ("tl-1", "tech_lead")):
        post(client, f"/companies/{company}/employees", {"person_id": pid, "role": role})
    process(SessionLocal, IdentityProjection())
    # a planned, assigned, finished project (agents did the work in the domain layer)
    pid = UUID(
        svc.create_project(
            db, company, actor("pm-1", "pm"), title="P", budget=10_000, manager="pm-1"
        ).payload["project_id"]
    )
    task = uuid4()
    svc.create_task(
        db, pid, actor("pm-1", "pm"), task_id=task, title="T", description="d", role="engineer"
    )
    db.commit()
    from test_work import decide

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
    project_everything()

    # the owner cannot deliver an unfinished project: the sign is rolled back with it
    r = post(client, f"/projects/{pid}/deliver", {"reason": "ship it"})
    assert r.status_code == 422 and "not done" in r.text
    assert client.get(f"{PREFIX}/companies/{company}/decisions").json() == []  # nothing lingered

    run_through(db, company, pid, task, "eng-1")
    project_everything()
    # pending human decisions: none yet; open the delivery gate explicitly and look at it
    action = {"kind": "project.deliver", "project_id": str(pid)}
    r = post(
        client,
        f"/companies/{company}/decisions",
        {"gate": "project.deliver", "subject": {"project_id": str(pid)}, "action": action},
    )
    assert r.status_code == 201, r.text
    did = r.json()["decision_id"]
    assert r.json()["resolution"]["decider"] == "human" and r.json()["subject"]["action"] == action
    pending = client.get(f"{PREFIX}/companies/{company}/decisions").json()
    assert [d["decision_id"] for d in pending] == [did]
    assert client.get(f"{PREFIX}/decisions/{did}").json()["status"] == "pending"
    # a wrong restatement of the action is refused; the bound one is accepted and applied
    r = post(
        client,
        f"/decisions/{did}/sign",
        {"verdict": "approve", "action": {"kind": "project.deliver", "project_id": str(uuid4())}},
    )
    assert r.status_code == 422 and "exact bound action" in r.text
    r = post(client, f"/decisions/{did}/sign", {"verdict": "approve", "reason": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["applied"] == "project.delivered"
    assert svc.project(db, pid).status == "delivered"
    assert client.get(f"{PREFIX}/companies/{company}/decisions").json() == []
    r = post(client, f"/decisions/{did}/sign", {"verdict": "approve"})
    assert r.status_code == 422  # already decided
    assert post(client, f"/decisions/{uuid4()}/sign", {"verdict": "approve"}).status_code == 404

    # the convenience route does open + sign + deliver in one go (on a fresh project)
    pid2 = UUID(
        svc.create_project(
            db, company, actor("pm-1", "pm"), title="P2", budget=10_000, manager="pm-1"
        ).payload["project_id"]
    )
    task2 = uuid4()
    svc.create_task(
        db, pid2, actor("pm-1", "pm"), task_id=task2, title="T2", description="d", role="engineer"
    )
    db.commit()
    plan2 = decide(
        db,
        company,
        "plan.approve",
        {"project_id": str(pid2)},
        signers=[("tl-1", "tech_lead"), ("pm-2", "pm")],
        proposer="pm-1",
    )
    svc.approve_plan(db, pid2, actor("pm-1", "pm"), decision_id=plan2)
    db.commit()
    run_through(db, company, pid2, task2, "eng-1")
    project_everything()
    # R7-a: the PM has already proposed delivery with a brief; the Owner's click signs
    # that very envelope and the brief travels with it
    from test_agent_loop import ScriptedLoop
    from test_briefing import GOOD

    from eidolon.agent.blobs import BlobStore
    from eidolon.agent.briefing import DeliveryProposer, delivery_decision_id
    from eidolon.agent.managers import Deciders, Roster, StaticRoster
    from eidolon.agent.managers import Person as Agent

    proposer = DeliveryProposer(
        SessionLocal,
        Deciders(SessionLocal, ScriptedLoop([[TextDelta(GOOD)]]), BlobStore(tempfile.mkdtemp())),
        StaticRoster(Roster([Agent("pm-1", "pm")])),
    )
    assert proposer.tick() == 1
    pending = client.get(f"{PREFIX}/companies/{company}/decisions").json()
    assert [d["decision_id"] for d in pending] == [str(delivery_decision_id(pid2))]
    assert pending[0]["brief"]["recommendation"] == "approve"
    assert pending[0]["brief"]["advisor"] == "pm-1" and pending[0]["brief"]["summary"]
    r = post(client, f"/projects/{pid2}/deliver", {"reason": "great"})
    assert r.status_code == 200 and r.json()["applied"] == "project.delivered"
    assert r.json()["decision_id"] == str(delivery_decision_id(pid2))
    assert r.json()["brief"]["advisor"] == "pm-1"
    assert post(client, f"/projects/{pid2}/deliver", {}).status_code == 409  # already decided
    assert post(client, f"/projects/{uuid4()}/deliver", {}).status_code == 404

    # separation of duties: the owner proposes a hire, so the owner may not approve it
    r = post(
        client,
        f"/companies/{company}/decisions",
        {
            "gate": "hire",
            "subject": {"person_id": "eng-9", "role": "engineer"},
            "action": {"kind": "hire", "person_id": "eng-9", "role": "engineer"},
        },
    )
    assert r.status_code == 201
    hire_id = r.json()["decision_id"]
    r = post(client, f"/decisions/{hire_id}/sign", {"verdict": "approve", "reason": "need hands"})
    assert r.status_code == 422 and "separation" in r.text
    # an agent gate: the owner may open it but cannot sign it (needs an agent)
    r = post(
        client,
        f"/companies/{company}/decisions",
        {"gate": "task.assign", "subject": {"task_id": str(uuid4()), "assignee": "eng-1"}},
    )
    assert r.status_code == 201 and r.json()["resolution"]["decider"] == "agent"
    r = post(client, f"/decisions/{r.json()['decision_id']}/sign", {"verdict": "approve"})
    assert r.status_code == 422 and "needs a agent" in r.text
    # an unknown gate is refused by the envelope itself (the owner's "*" grant passes)
    r = post(client, f"/companies/{company}/decisions", {"gate": "nope", "subject": {}})
    assert r.status_code == 422
    client.__exit__(None, None, None)


def test_cancel_run_is_a_human_act_with_a_grant(db):
    client, _ = owner_client()
    company = UUID(post(client, "/companies", {"name": "S", "slug": "s2"}).json()["company_id"])
    post(client, f"/companies/{company}/employees", {"person_id": "eng-1", "role": "engineer"})
    process(SessionLocal, IdentityProjection())
    run_id = uuid4()
    started, _ = run_facts(db, company, run_id, task_id=None, cause=None)
    db.commit()
    r = post(client, f"/runs/{run_id}/cancel", {"reason": "wrong task"})
    assert r.status_code == 200 and r.json()["kind"] == R.RUN_CANCEL_REQUESTED
    again = post(client, f"/runs/{run_id}/cancel", {"reason": "wrong task"})
    assert again.status_code == 200 and again.json()["fact_id"] == r.json()["fact_id"]  # K2
    facts = store.by_correlation(db, run_id)
    cancel = next(f for f in facts if f.kind == R.RUN_CANCEL_REQUESTED)
    assert cancel.actor == Actor(ActorKind.human, "owner-1") and cancel.causation_id is not None
    assert post(client, f"/runs/{uuid4()}/cancel", {"reason": "x"}).status_code == 404
    # a finished run cannot be cancelled
    done = uuid4()
    run_facts(db, company, done, task_id=None, cause=None)
    from test_talent import ended

    ended(db, company, done, R.RUN_COMPLETED, steps=1)
    db.commit()
    assert post(client, f"/runs/{done}/cancel", {"reason": "x"}).status_code == 409
    client.__exit__(None, None, None)


def test_owner_can_force_escalate_an_open_decision(db):
    client, _ = owner_client()
    company = UUID(post(client, "/companies", {"name": "S", "slug": "s3"}).json()["company_id"])
    post(client, f"/companies/{company}/employees", {"person_id": "tl-1", "role": "tech_lead"})
    process(SessionLocal, IdentityProjection())
    r = post(
        client,
        f"/companies/{company}/decisions",
        {"gate": "task.assign", "subject": {"task_id": str(uuid4()), "assignee": "eng-1"}},
    )
    did = r.json()["decision_id"]
    assert r.json()["resolution"]["role"] == "tech_lead"
    r = post(client, f"/decisions/{did}/escalate")
    assert r.status_code == 200, r.text
    assert r.json()["resolution"]["role"] == "ceo" and r.json()["escalations"] == 1
    assert post(client, f"/decisions/{did}/escalate").status_code == 409  # ceo: no escalate_to
    assert post(client, f"/decisions/{uuid4()}/escalate").status_code == 404
    client.__exit__(None, None, None)
