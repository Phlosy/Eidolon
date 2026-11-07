"""API (R5-b): accounts and sessions (cookie + CSRF), and the read side over the
projections — companies, employees, projects, runs, ledger, provenance — on a real
PostgreSQL through FastAPI's TestClient. The API never decides anything; it only
reads what the layers below recorded."""

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from project_support import ENGINEER, set_up_project, store, svc
from sqlalchemy import select
from test_cost_trace import metered_call, run_facts
from test_work import actor, decide

from eidolon.agent import run as R
from eidolon.api import AuthError, AuthService, CredentialRow, SessionRow
from eidolon.api.app import PREFIX, create_app
from eidolon.db import SessionLocal
from eidolon.identity import IdentityProjection, IdentityService
from eidolon.identity import facts as IF
from eidolon.institution import adopt, staffing
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.money import CostMeter, LedgerProjection
from eidolon.talent import TalentProjection
from eidolon.work import WorkProjection

VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "verify_bundle.py"
ids = IdentityService(store)
auth = AuthService(store)
PASSWORD = "correct horse battery"


@pytest.fixture()
def client():
    app = create_app(SessionLocal)
    with TestClient(app) as c:
        yield c


def project_everything():
    for projection in (
        IdentityProjection(),
        WorkProjection(),
        CostMeter(),
        LedgerProjection(),
        TalentProjection(),
    ):
        process(SessionLocal, projection)


def register(client, email, name="Owner", person_id=None):
    r = client.post(
        f"{PREFIX}/auth/register",
        json={"email": email, "display_name": name, "password": PASSWORD, "person_id": person_id},
    )
    assert r.status_code == 201, r.text
    return r.json()


def csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("eidolon_csrf")}


def found_company(db, owner_user_id: UUID, owner_person: str) -> UUID:
    created, _ = ids.create_company(
        db,
        owner_user_id=owner_user_id,
        owner_person_id=owner_person,
        name="Tiny Studio",
        slug=f"tiny-{owner_user_id.hex[:6]}",
    )
    store.append(db, adopt(created.company_id, Actor(ActorKind.human, owner_person), T))
    for pid, role in (("eng-1", "engineer"), ("qa-1", "qa"), ("pm-1", "pm"), ("tl-1", "tech_lead")):
        ids.create_person(db, Actor(ActorKind.human, owner_person), pid, name=pid, kind="agent")
        staffing.hire(
            db, created.company_id, Actor(ActorKind.human, owner_person), person_id=pid, role=role
        )
    db.commit()
    return created.company_id


def test_accounts_sessions_and_csrf(client, db):
    assert client.get(f"{PREFIX}/health").json() == {"status": "ok", "api": "v2"}
    assert client.get(f"{PREFIX}/auth/me").status_code == 401

    me = register(client, "Owner@Example.com", person_id="owner-9")
    user_id = UUID(me["user_id"])
    assert me["person_id"] == "owner-9"
    # the fact exists; the hash is only in the credential row, never in a fact
    users = store.stream(db, kinds=[IF.USER_REGISTERED], company_id=IF.PLATFORM, limit=50)
    assert any(
        f.payload["user_id"] == str(user_id) and f.payload["email"] == "owner@example.com"
        for f in users
    )
    assert all("password" not in json.dumps(f.payload) for f in users)
    cred = db.get(CredentialRow, user_id)
    assert cred.password_hash.startswith("$argon2id$") and PASSWORD not in cred.password_hash
    assert client.cookies.get("eidolon_session") and client.cookies.get("eidolon_csrf")
    assert client.cookies.get("eidolon_session") != cred.password_hash

    # duplicate email → 409; short password → 422
    r = client.post(
        f"{PREFIX}/auth/register",
        json={"email": "owner@example.com", "display_name": "X", "password": PASSWORD},
    )
    assert r.status_code == 409
    r = client.post(
        f"{PREFIX}/auth/register",
        json={"email": "b@example.com", "display_name": "X", "password": "short"},
    )
    assert r.status_code == 422

    process(SessionLocal, IdentityProjection())
    r = client.get(f"{PREFIX}/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "owner@example.com"
    assert r.json()["person_ids"] == ["owner-9"] and r.json()["companies"] == []

    # CSRF: a mutating request with a session but no header is refused
    assert client.post(f"{PREFIX}/auth/logout").status_code == 403
    assert client.post(f"{PREFIX}/auth/logout", headers={"X-CSRF-Token": "nope"}).status_code == 403
    sessions = client.get(f"{PREFIX}/auth/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"]

    # a second login from another client; revoke it from the first
    other = TestClient(client.app)
    r = other.post(
        f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert other.get(f"{PREFIX}/auth/me").status_code == 200
    sessions = client.get(f"{PREFIX}/auth/sessions").json()
    assert len(sessions) == 2
    victim = next(s["session_id"] for s in sessions if not s["current"])
    assert (
        client.delete(f"{PREFIX}/auth/sessions/{victim}", headers=csrf(client)).status_code == 204
    )
    assert other.get(f"{PREFIX}/auth/me").status_code == 401
    assert (
        client.delete(f"{PREFIX}/auth/sessions/{uuid4()}", headers=csrf(client)).status_code == 404
    )

    # wrong password; change password revokes the other sessions but keeps this one
    r = other.post(f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": "wrong"})
    assert r.status_code == 401
    other.post(f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    r = client.post(
        f"{PREFIX}/auth/password",
        json={"old_password": PASSWORD, "new_password": "even better horse"},
        headers=csrf(client),
    )
    assert r.status_code == 204
    assert other.get(f"{PREFIX}/auth/me").status_code == 401
    assert client.get(f"{PREFIX}/auth/me").status_code == 200
    db.expire_all()  # our session cached the old credential row
    with pytest.raises(AuthError, match="wrong password"):
        auth.change_password(db, user_id, old=PASSWORD, new="whatever else")

    # logout clears cookies and kills the session
    assert client.post(f"{PREFIX}/auth/logout", headers=csrf(client)).status_code == 204
    assert client.get(f"{PREFIX}/auth/me").status_code == 401
    assert all(
        s.revoked_at is not None
        for s in db.scalars(select(SessionRow).where(SessionRow.user_id == user_id))
    )
    # a forged token is just unknown
    client.cookies.set("eidolon_session", "x" * 64)
    assert client.get(f"{PREFIX}/auth/me").status_code == 401


def test_read_side_over_projections(client, db, tmp_path):
    me = register(client, "owner@example.com", person_id="owner-1")
    company = found_company(db, UUID(me["user_id"]), "owner-1")
    _, pid, task = set_up_project(db, company)
    run_id = uuid4()
    started_task = svc.start_task(db, pid, task, Actor.system(), run_id=run_id, person_id=ENGINEER)
    _, step = run_facts(db, company, run_id, task_id=task, cause=started_task.id)
    call = metered_call(db, company, run_id, step)
    svc.produce_artifact(
        db,
        pid,
        task,
        actor(ENGINEER, "engineer"),
        run_id=run_id,
        path="hello.py",
        blob_ref="sha256:" + "0" * 64,
        size=12,
        media_type="text/x-python",
    )
    svc.submit_task(db, pid, task, actor(ENGINEER, "engineer"), run_id=run_id)
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
    store.append(
        db,
        __import__("eidolon.kernel.facts", fromlist=["NewFact"]).NewFact(
            company_id=company,
            actor=Actor(ActorKind.agent, ENGINEER),
            kind=R.RUN_COMPLETED,
            payload={
                "run_id": str(run_id),
                "steps": 1,
                "final_ref": "sha256:" + "f" * 64,
                "final_preview": "done",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
            idempotency_key=R.run_key(run_id, "completed"),
            correlation_id=run_id,
        ),
    )
    db.commit()
    project_everything()

    # me: now employed as owner
    mine = client.get(f"{PREFIX}/auth/me").json()
    assert mine["companies"] == [
        {"company_id": str(company), "person_id": "owner-1", "role": "owner", "title": "Owner"}
    ]
    companies = client.get(f"{PREFIX}/companies").json()
    assert [c["slug"] for c in companies] == [f"tiny-{UUID(me['user_id']).hex[:6]}"]
    c = client.get(f"{PREFIX}/companies/{company}").json()
    assert c["currency"] == "CNY" and c["headcount"] == 5
    assert c["institution"]["template_id"] == "software-studio-v1"
    inst = client.get(f"{PREFIX}/companies/{company}/institution").json()
    assert "spend.approve" in inst["template"]["gates"] and inst["changes"] == 0

    # a stranger (another user, no employment) is refused everywhere
    stranger = TestClient(client.app)
    register(stranger, "stranger@example.com", name="S")
    process(SessionLocal, IdentityProjection())
    assert stranger.get(f"{PREFIX}/companies").json() == []
    for path in (
        f"/companies/{company}",
        f"/companies/{company}/employees",
        f"/companies/{company}/projects",
        f"/projects/{pid}",
        f"/tasks/{task}",
        f"/runs/{run_id}",
        f"/companies/{company}/ledger",
        f"/projects/{pid}/costs",
        f"/persons/{ENGINEER}",
    ):
        assert stranger.get(f"{PREFIX}{path}").status_code == 403, path
    assert client.get(f"{PREFIX}/companies/{uuid4()}").status_code == 403

    # employees carry their competencies; the person view carries the resume
    employees = client.get(f"{PREFIX}/companies/{company}/employees").json()
    by_person = {e["person_id"]: e for e in employees}
    assert set(by_person) == {"owner-1", "eng-1", "qa-1", "pm-1", "tl-1"}
    assert by_person["owner-1"]["kind"] == "human" and by_person["eng-1"]["kind"] == "agent"
    assert by_person["eng-1"]["competencies"][0]["area"] == "engineer"
    person = client.get(f"{PREFIX}/persons/{ENGINEER}?with_resume=true").json()
    assert (
        person["competencies"][0]["score"] > 50 and person["resume"][-1]["kind"] == "run_completed"
    )
    assert client.get(f"{PREFIX}/persons/ghost").status_code == 404

    # work: project with tasks, artifacts and spend; task; artifact; content from the blob store
    proj = client.get(f"{PREFIX}/projects/{pid}").json()
    assert proj["title"] == "Greeting" and proj["status"] == "active"
    assert [t["status"] for t in proj["tasks"]] == ["done"] and proj["spend_micro"] > 0
    assert proj["artifacts"][0]["path"] == "hello.py"
    assert client.get(f"{PREFIX}/companies/{company}/projects").json()[0]["project_id"] == str(pid)
    assert client.get(f"{PREFIX}/companies/{company}/missions").json() == []
    t = client.get(f"{PREFIX}/tasks/{task}").json()
    assert t["assignee"] == ENGINEER and t["reviews"][-1]["verdict"] == "pass"
    assert t["spend_micro"] == proj["spend_micro"]
    aid = proj["artifacts"][0]["artifact_id"]
    assert client.get(f"{PREFIX}/artifacts/{aid}").json()["blob_ref"].startswith("sha256:")
    assert client.get(f"{PREFIX}/artifacts/{aid}/content").status_code == 410  # not in the store
    assert client.get(f"{PREFIX}/artifacts/{uuid4()}").status_code == 404

    # runs: a timeline without chain-of-thought or blob refs
    run = client.get(f"{PREFIX}/runs/{run_id}").json()
    assert run["status"] == "completed" and run["person_id"] == ENGINEER
    assert run["task_id"] == str(task) and run["cause_fact_id"] == str(started_task.id)
    kinds = [e["kind"] for e in run["events"]]
    assert kinds[:2] == [R.RUN_STARTED, R.STEP_REQUESTED] and "agent.model.called" in kinds
    assert "money.entry.posted" in kinds and kinds[-1] == "money.entry.posted"
    dumped = json.dumps(run)
    assert "_ref" not in dumped and "reasoning" not in dumped and "provider_state" not in dumped
    assert run["spend_micro"] == proj["spend_micro"]
    runs = client.get(f"{PREFIX}/companies/{company}/runs").json()
    assert [r["run_id"] for r in runs] == [str(run_id)] and runs[0]["status"] == "completed"
    assert client.get(f"{PREFIX}/companies/{company}/runs?person_id=nobody").json() == []
    assert client.get(f"{PREFIX}/runs/{uuid4()}").status_code == 404

    # ledger: balances, verify, project costs, cost trace
    ledger = client.get(f"{PREFIX}/companies/{company}/ledger").json()
    assert {a["code"] for a in ledger["accounts"]} == {"compute", "ap:deepseek"}
    assert ledger["spend_micro"] == proj["spend_micro"]
    assert client.get(f"{PREFIX}/companies/{company}/ledger/verify").json()["ok"]
    costs = client.get(f"{PREFIX}/projects/{pid}/costs").json()
    assert (
        costs["total_micro"] == proj["spend_micro"]
        and costs["tasks"][0]["runs"][0]["person_id"] == ENGINEER
    )
    trace = client.get(f"{PREFIX}/cost-trace/{call.id}").json()
    assert trace["project_id"] == str(pid) and trace["chain"][0]["kind"] == "work.task.started"
    assert client.get(f"{PREFIX}/cost-trace/{uuid4()}").status_code == 404

    # provenance: a fact, its trace, a bundle that verifies offline
    f = client.get(f"{PREFIX}/facts/{call.id}").json()
    assert f["kind"] == "agent.model.called" and "request_fingerprint" not in f["payload"]
    tr = client.get(f"{PREFIX}/facts/{call.id}/trace").json()
    assert tr["root"] == str(call.id) and {x["kind"] for x in tr["facts"]} >= {
        R.STEP_REQUESTED,
        R.RUN_STARTED,
    }
    assert client.get(f"{PREFIX}/facts/{uuid4()}/trace").status_code == 404
    r = client.get(f"{PREFIX}/companies/{company}/bundle?correlation={pid}&with_ledger=true")
    assert r.status_code == 200
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(r.json()))
    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {x["kind"] for x in r.json()["facts"]} >= {"money.entry.posted", "work.project.created"}
