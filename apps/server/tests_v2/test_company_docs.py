"""R8-a scenario acceptance: a real "code + user guide" mission through the whole studio,
replayed from recorded fixtures. The criteria A1–A6 were written down before the first
recording (kernel-plan §8 R8-a); this file only answers pass or fail."""

import json
import re

from company_support import (
    DOCS,
    company_project_id,
    drive_company,
    propose_mission,
    set_up_company,
    studio_jobs,
)
from loop_support import replay_provider
from project_support import store, svc

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.briefing import delivery_decision_id
from eidolon.agent.project_tools import NAMES as PROJECT_TOOL_NAMES
from eidolon.agent.project_tools import TOOLS_HINT
from eidolon.agent.prompts import DOCUMENT_REVIEWER_SYSTEM, WRITER_SYSTEM
from eidolon.agent.providers import fixture_dir
from eidolon.api.commands import Commands
from eidolon.api.commands import Person as Member
from eidolon.api.narrative import SECTIONS, project_report
from eidolon.config import settings
from eidolon.db import SessionLocal
from eidolon.identity import IdentityService
from eidolon.institution import REQUESTED, load
from eidolon.kernel.consumers import process
from eidolon.work import WorkProjection
from eidolon.work import facts as F


def test_company_docs_fixtures_are_recorded():
    seen = sum(
        1
        for p in fixture_dir(settings).glob("chat-*.json")
        if json.loads(p.read_text())["request"]["messages"][0]["content"].startswith(
            ("Mission: " + DOCS.title, "Project: " + DOCS.title)
        )
    )
    assert seen >= 2, (
        "R8-a fixtures missing: run `make v2-record-fixtures V2_ARGS='--only company-docs'`"
    )


def test_a_code_plus_guide_mission_is_delivered_with_a_real_document(db, tmp_path):
    company = set_up_company(db)
    mission_id = propose_mission(db, company, DOCS)
    blobs = BlobStore(tmp_path / "blobs")
    jobs = studio_jobs(SessionLocal, replay_provider(SessionLocal), blobs, tmp_path / "ws")
    trail = drive_company(SessionLocal, db, mission_id, jobs, max_cycles=30)
    final = trail[-1]
    pid = company_project_id(db, mission_id)
    state = svc.project(db, pid)

    # A1 — accepted, planned with a document task that builds on the code
    assert final["mission"] == "accepted", trail
    assert 2 <= len(state.tasks) <= 5, [t.title for t in state.tasks.values()]
    docs = [t for t in state.tasks.values() if t.kind == "document"]
    builds = [t for t in state.tasks.values() if t.kind == "build"]
    assert docs and builds, {t.title: t.kind for t in state.tasks.values()}
    assert any(
        set(d.depends_on) & {b.task_id for b in builds}
        or set(d.inputs) & {b.task_id for b in builds}
        for d in docs
    ), "the guide must depend on (or take as input) a build task"

    # A6 — a failure, if any, got a disposition (Triage) rather than a dead end
    triage = [
        f
        for f in store.stream(db, kinds=[REQUESTED], company_id=company, limit=500)
        if f.payload["gate"] == "task.triage"
    ]
    failed = [f for f in store.by_correlation(db, pid) if f.kind in (F.TASK_FAILED, F.TASK_BLOCKED)]
    if failed:
        assert triage, "a task failed/blocked but nobody proposed what to do"
        assert all(load(db, f.correlation_id).status != "pending" for f in triage)

    # A2 — everything done; the guide was written as a document and judged as one
    assert final["tasks"] and all(s == "done" for s in final["tasks"].values()), trail
    runs = {
        f.correlation_id: f
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=500)
    }
    guide = docs[0]
    writer_runs = [f for f in runs.values() if f.payload.get("task_id") == str(guide.task_id)]
    assert writer_runs
    systems = {blobs.get_json(f.payload["system_ref"]) for f in writer_runs}
    assert WRITER_SYSTEM in systems and DOCUMENT_REVIEWER_SYSTEM in systems, systems
    md = [
        a for a in state.artifacts.values() if a.task_id == guide.task_id and a.path.endswith(".md")
    ]
    assert md, [a.path for a in state.artifacts.values()]
    text = blobs.get_bytes(md[-1].blob_ref).decode("utf-8", "replace").lower()
    assert all(word in text for word in ("add", "list", "delete")), text[:400]
    assert guide.reviews[-1]["verdict"] == "pass" and guide.reviews[-1]["reviewer"] == "qa-1"

    # A3 — the PM proposed delivery with a brief; the Owner settled the inbox as recommended
    proposal = load(db, delivery_decision_id(pid))
    assert proposal.status == "pending" and proposal.brief and proposal.proposer == "pm-1"
    owner = next(
        e.person_id
        for e in IdentityService(store).company(db, company).active()
        if e.role == "owner"
    )
    results = Commands(store).follow_recommendations(db, Member(company, owner, "owner"))
    assert [r["applied"] for r in results] == ["project.delivered"], results
    db.commit()
    process(SessionLocal, WorkProjection())
    assert svc.project(db, pid).status == "delivered"

    # A4 — the story, in plain words, with the guide and the brief in it
    report = project_report(db, pid, lang="zh")
    assert [s.key for s in report.sections] == list(SECTIONS)
    story = report.to_text()
    assert f"提出使命《{DOCS.title}》" in story and f"任务《{guide.title}》由" in story
    assert "pm-1 建议批准：" in story and "项目交付" in story and "没有了" in story
    assert all(store.get(db, i).company_id == company for i in report.fact_ids())

    # A5 — managers only looked; no fixture echoes an id; the chain holds
    looked = {}
    for f in store.stream(db, kinds=[R.TOOL_REQUESTED], company_id=company, limit=5000):
        if f.actor.id in ("tl-1", "pm-1", "ceo-1"):
            looked.setdefault(f.actor.id, set()).add(f.payload["name"])
    assert looked and set().union(*looked.values()) <= set(PROJECT_TOOL_NAMES), looked
    uuid_like = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    for p in fixture_dir(settings).glob("chat-*.json"):
        request = json.loads(p.read_text())["request"]
        if TOOLS_HINT in (request.get("system") or ""):
            for m in request["messages"]:
                for call in m.get("tool_calls") or ():
                    assert not uuid_like.search(json.dumps(call.get("arguments"))), p.name
    assert store.verify_chain(db, company).ok
    # nothing left for the Owner; another cycle changes nothing
    assert not Commands(store).pending_decisions(db, company)
    assert (
        drive_company(SessionLocal, db, mission_id, jobs, max_cycles=1)[-1]["project"]
        == "delivered"
    )
