"""Narrative reports (R7-b): the facts folded into a story, every line carrying the
fact ids it came from; deterministic, rebuildable, checkable against the log."""

import asyncio
import io
import tempfile
from uuid import uuid4

from project_support import set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_api import PREFIX, project_everything, register
from test_briefing import GOOD
from test_cost_trace import metered_call, run_facts
from test_money import OWNER, found
from test_work import actor, run_through

from eidolon.agent.blobs import BlobStore
from eidolon.agent.briefing import DeliveryProposer, delivery_decision_id
from eidolon.agent.managers import Deciders, Person, Roster, StaticRoster
from eidolon.agent.providers import TextDelta
from eidolon.api.narrative import SECTIONS, mission_report, polish, project_report
from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.institution import conclude, load, sign
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.registry import default_registry
from eidolon.work import WorkProjection


def test_project_report_tells_the_story_and_every_line_points_at_facts(db):
    company, pid, task = set_up_project(db)
    # a second task that fails, so "stuck" has something to say
    failed = uuid4()
    svc.create_task(
        db,
        pid,
        Actor(ActorKind.agent, "pm-1"),
        task_id=failed,
        title="Flaky bit",
        description="d",
        role="engineer",
    )
    db.commit()
    from test_work import assign

    assign(db, company, pid, failed, "eng-2")
    run_id = uuid4()
    svc.start_task(db, pid, failed, Actor.system(), run_id=run_id, person_id="eng-2")
    db.commit()
    svc.fail_task(
        db, pid, failed, Actor.system(), run_id=run_id, reason="run cancelled by the owner"
    )
    db.commit()
    run_through(db, company, pid, task, "eng-1", artifact=True)
    process(SessionLocal, WorkProjection())

    r = project_report(db, pid, lang="zh")
    assert [s.key for s in r.sections] == list(SECTIONS)
    text = r.to_text()
    assert "立项《Greeting》" in text and "方案通过" in text and "tl-1（tech_lead，批准）" in text
    assert "任务《Greeting CLI》由 eng-1 完成，qa-1 评审通过，产出 out.txt" in text
    assert "任务《Flaky bit》失败：run cancelled by the owner" in text
    assert "还没有花钱" in text  # no cost facts yet
    assert "所有任务已完成" not in text  # one task failed, so delivery is not pending
    # every fact id a line names exists in the log and belongs to this company
    ids = r.fact_ids()
    assert ids and all((f := store.get(db, i)) is not None and f.company_id == company for i in ids)
    assert r.as_of_fact_id == svc.project(db, pid).last_fact_id
    # drill-down links name the task, the decisions and the mission
    links = {(ln.link["kind"], ln.link["id"]) for s in r.sections for ln in s.lines if ln.link}
    assert ("task", str(task)) in links and ("task", str(failed)) in links
    assert any(k == "decision" for k, _ in links)
    # the same report in English, same facts
    en = project_report(db, pid, lang="en")
    assert en.fact_ids() == ids and "Task “Flaky bit” is failed: run cancelled" in en.to_text()
    # deterministic: twice the same
    assert project_report(db, pid, lang="zh").as_dict() == r.as_dict()


def test_report_folds_costs_briefs_and_what_you_must_do_next(db):
    company, pid, task = set_up_project(db, found(db))  # a real company: the meter needs a currency
    run_id = uuid4()
    _, step = run_facts(db, company, run_id, task_id=task, cause=svc.project(db, pid).last_fact_id)
    metered_call(db, company, run_id, step)
    run_through(db, company, pid, task, "eng-1")
    project_everything()
    # the PM proposes delivery with a brief (R7-a); the report's "next" repeats it in plain words
    proposer = DeliveryProposer(
        SessionLocal,
        Deciders(SessionLocal, ScriptedLoop([[TextDelta(GOOD)]]), BlobStore(tempfile.mkdtemp())),
        StaticRoster(Roster([Person("pm-1", "pm")])),
    )
    assert proposer.tick() == 1
    r = project_report(db, pid, lang="zh")
    sections = {s.key: s for s in r.sections}
    assert any(
        "project.deliver 等你决定——pm-1 建议批准：" in ln.text for ln in sections["next"].lines
    )
    assert r.needs_you and r.needs_you[0]["recommendation"] == "approve"
    assert r.needs_you[0]["decision_id"] == str(delivery_decision_id(pid))
    cost = sections["cost"].lines[0]
    assert cost.text.startswith("到目前花了 ") and "占预算 100.00 CNY 的 " in cost.text
    assert cost.facts  # the ledger facts behind the number
    assert any(
        "project.deliver：待定，尚无人签字；pm-1 建议批准" in ln.text
        for ln in sections["decided"].lines
    )
    # the Owner accepts; the story ends with the delivery and nothing is needed any more
    did = delivery_decision_id(pid)
    action = {"kind": "project.deliver", "project_id": str(pid)}
    store.append(
        db,
        sign(T, load(db, did), OWNER, role="owner", verdict="approve", reason="ok", action=action),
    )
    store.append(db, conclude(load(db, did)))
    db.commit()
    svc.deliver_project(db, pid, OWNER, decision_id=did)
    db.commit()
    process(SessionLocal, WorkProjection())
    after = project_report(db, pid, lang="en")
    assert after.status == "delivered" and not after.needs_you
    text = after.to_text()
    assert "project delivered, accepted by" in text and "nothing more" in text
    # CLI prints the same story, --facts lists the ids, --json is the dict
    out = io.StringIO()
    assert (
        main(
            ["report", "--project", str(pid), "--lang", "en", "--facts"],
            registry=default_registry(),
            session_factory=SessionLocal,
            out=out,
        )
        == 0
    )
    assert "## What it cost" in out.getvalue() and "<- " in out.getvalue()
    out = io.StringIO()
    main(
        ["report", "--project", str(pid), "--json"],
        registry=default_registry(),
        session_factory=SessionLocal,
        out=out,
    )
    assert '"kind": "project"' in out.getvalue()


def test_mission_report_composes_its_projects(db):
    company, pid, task = set_up_project(db)
    mission = svc.create_mission(
        db,
        company,
        OWNER,
        title="Greet people",
        goal="A greeting tool",
        success_criteria=["prints hello"],
        budget=500,
    )
    db.commit()
    mid = mission.payload["mission_id"]
    from uuid import UUID

    mid = UUID(mid)
    from test_work import decide

    accept = decide(
        db,
        company,
        "mission.accept",
        {"mission_id": str(mid)},
        signers=[("ceo-1", "ceo")],
        amount=500,
    )
    svc.decide_mission(db, mid, actor("ceo-1", "ceo"), decision_id=accept, reason="worth it")
    pid2 = UUID(
        svc.create_project(
            db,
            company,
            actor("pm-1", "pm"),
            title="Hello project",
            budget=200,
            manager="pm-1",
            mission_id=mid,
        ).payload["project_id"]
    )
    db.commit()
    process(SessionLocal, WorkProjection())
    r = mission_report(db, mid, lang="zh")
    text = r.to_text()
    assert r.kind == "mission" and r.title == "Greet people"
    assert "提出使命《Greet people》：A greeting tool" in text and "验收标准：prints hello" in text
    assert "ceo-1 接受了使命：worth it" in text
    assert "Hello project · 做了什么" in text and "还没有开始做" in text
    assert "Hello project · 要了什么" in text and "立项《Hello project》" in text
    assert ("mission.accept：已批准" in text) and pid2 in {
        UUID(s.key.split(":")[1]) for s in r.sections if ":" in s.key
    }
    ids = r.fact_ids()
    assert ids and all(store.get(db, i) is not None for i in ids)
    # a mission nobody has accepted yet says who it waits for
    other = svc.create_mission(db, company, OWNER, title="Later", goal="later", budget=50)
    db.commit()
    process(SessionLocal, WorkProjection())
    r2 = mission_report(db, UUID(other.payload["mission_id"]), lang="en")
    assert "waits for" in r2.to_text() and r2.sections[-1].key == "next"


def test_polish_is_one_model_call_on_top_of_the_source(db):
    company, pid, task = set_up_project(db)
    r = project_report(db, pid, lang="en")
    provider = ScriptedLoop(
        [[TextDelta("The greeting project is planned; nothing is needed yet.")]]
    )
    text = asyncio.run(polish(r, provider, model="scripted"))
    assert text.startswith("The greeting project is planned")
    assert provider.calls == 1
    assert provider.last_request.messages[0].content == r.to_text()
    assert provider.last_request.context.company_id == company
    assert provider.last_request.context.correlation_id == pid


def test_report_routes_are_readable_by_members_only(db):
    from fastapi.testclient import TestClient

    from eidolon.api.app import create_app
    from eidolon.identity import IdentityProjection

    client = TestClient(create_app(SessionLocal))
    client.__enter__()
    register(client, "owner-r@example.com", name="Owner", person_id="owner-r")
    process(SessionLocal, IdentityProjection())
    from test_api import csrf

    r = client.post(f"{PREFIX}/companies", json={"name": "R", "slug": "r"}, headers=csrf(client))
    company = r.json()["company_id"]
    process(SessionLocal, IdentityProjection())
    from uuid import UUID

    company = UUID(company)
    pid = UUID(
        svc.create_project(
            db, company, actor("pm-1", "pm"), title="Routed", budget=100, manager="pm-1"
        ).payload["project_id"]
    )
    db.commit()
    process(SessionLocal, WorkProjection())
    body = client.get(f"{PREFIX}/projects/{pid}/report?lang=en-US").json()
    assert body["kind"] == "project" and body["lang"] == "en" and len(body["sections"]) == 6
    assert body["sections"][0]["lines"][0]["facts"]
    assert client.get(f"{PREFIX}/projects/{pid}/report?lang=fr").status_code == 422
    assert client.get(f"{PREFIX}/projects/{uuid4()}/report").status_code == 404
    # another company's project is not readable
    _, foreign, _ = set_up_project(db)
    process(SessionLocal, WorkProjection())
    assert client.get(f"{PREFIX}/projects/{foreign}/report").status_code == 403
    mid = svc.create_mission(db, company, OWNER, title="M", goal="g").payload["mission_id"]
    db.commit()
    process(SessionLocal, WorkProjection())
    body = client.get(f"{PREFIX}/missions/{mid}/report").json()
    assert body["kind"] == "mission" and body["sections"][0]["key"] == "asked"
    assert client.get(f"{PREFIX}/missions/{uuid4()}/report").status_code == 404
