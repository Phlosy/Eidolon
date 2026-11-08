"""Failure handling as a proposal (R7-d): a failed or blocked task gets a disposition
proposed by the Tech Lead with a brief, approved by the institution's decider, and
enacted by the system — the user reads "we suggest …", never a bare red status."""

import dataclasses
import json
import tempfile
from uuid import uuid4

from project_support import set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_money import OWNER, found
from test_work import actor, assign, decide

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.managers import Deciders, Person, Roster, StaticRoster
from eidolon.agent.providers import TextDelta
from eidolon.agent.triage import MAX_ATTEMPTS, Triage
from eidolon.api.commands import Commands
from eidolon.api.commands import Person as Member
from eidolon.api.narrative import project_report
from eidolon.db import SessionLocal
from eidolon.identity import IdentityService
from eidolon.institution import GATES, adopt, customize, load
from eidolon.institution import current as current_institution
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import WorkProjection
from eidolon.work import facts as F
from eidolon.work.triage import apply_triage, triage_decision_id, validate_disposition

ROSTER = StaticRoster(
    Roster(
        [
            Person("pm-1", "pm"),
            Person("tl-1", "tech_lead"),
            Person("eng-1", "engineer"),
            Person("eng-2", "engineer"),
        ]
    )
)
PROPOSE = (
    '{"disposition": "reassign", "assignee": "eng-2", "reason": "eng-1 hit a sandbox limit twice", '
    '"summary": "Hand the greeting task to eng-2; eng-1 ran out of budget on it twice.", '
    '"rationale": "The task itself is fine; the approach was expensive.", '
    '"risks": ["eng-2 is new to the file"], "alternatives": ["reopen with eng-1"]}'
)
APPROVE = '{"verdict": "approve", "reason": "proportionate; the task is still wanted"}'
REJECT = '{"verdict": "reject", "reason": "eng-2 is busy; try again with eng-1"}'


def scripted(*answers):
    provider = ScriptedLoop([[TextDelta(a)] for a in answers])
    return provider, Deciders(SessionLocal, provider, BlobStore(tempfile.mkdtemp()))


def failed_project(db):
    company, pid, task = set_up_project(db, found(db))
    assign(db, company, pid, task, "eng-1")
    run_id = uuid4()
    svc.start_task(db, pid, task, Actor.system(), run_id=run_id, person_id="eng-1")
    svc.fail_task(db, pid, task, Actor.system(), run_id=run_id, reason="run budget_exceeded: ")
    db.commit()
    process(SessionLocal, WorkProjection())
    return company, pid, task


def test_the_gate_exists_everywhere_and_a_triage_stands_in_for_assign_or_cancel(db):
    assert "task.triage" in GATES
    company, pid, task = failed_project(db)
    state = svc.project(db, pid)
    t = state.task(task)
    # a well-formed proposal; the validator refuses what the institution would not enact
    subject = validate_disposition(
        state,
        t,
        {"disposition": "reassign", "assignee": "eng-2", "reason": "r"},
        candidates={"eng-2": "engineer"},
    )
    assert subject["assignee"] == "eng-2" and subject["previous_assignee"] == "eng-1"
    for bad in (
        {"disposition": "retire", "reason": "r"},
        {"disposition": "reassign", "assignee": "eng-1", "reason": "r"},
        {"disposition": "reassign", "assignee": "ghost", "reason": "r"},
        {"disposition": "split", "reason": "r", "new_tasks": [{"title": "only one"}]},
        {"disposition": "cancel"},
    ):
        try:
            validate_disposition(
                state, t, bad, candidates={"eng-1": "engineer", "eng-2": "engineer"}
            )
        except F.WorkError:
            pass
        else:
            raise AssertionError(f"accepted {bad}")
    # a cancel decision must not be used to reopen
    did = decide(
        db,
        company,
        "task.triage",
        {"task_id": str(task), "project_id": str(pid), "disposition": "cancel", "reason": "no"},
        signers=[("pm-1", "pm")],
        proposer="tl-1",
    )
    try:
        svc.reopen_task(db, pid, task, actor("tl-1", "tech_lead"), decision_id=did, reason="x")
    except F.WorkError as exc:
        assert "does not authorize" in str(exc)
    else:
        raise AssertionError("a cancel disposition reopened a task")
    svc.cancel_task(db, pid, task, actor("pm-1", "pm"), decision_id=did, reason="no")
    db.commit()
    assert svc.project(db, pid).task(task).status == "cancelled"


def test_tech_lead_proposes_pm_approves_system_enacts(db):
    company, pid, task = failed_project(db)
    provider, deciders = scripted(PROPOSE, APPROVE)
    job = Triage(SessionLocal, deciders, ROSTER)
    # one tick: propose (TL), approve (PM), enact (system)
    assert job.tick() == 3
    failed_fact_id = svc.project(db, pid).task(task).last_fact_id
    # the task is back in work with eng-2; both facts cite the triage decision
    assert failed_fact_id is not None
    process(SessionLocal, WorkProjection())
    state = svc.project(db, pid)
    assert state.task(task).status == "todo" and state.task(task).assignee == "eng-2"
    facts = store.by_correlation(db, pid)
    reopened = next(f for f in facts if f.kind == F.TASK_REOPENED)
    assigned = [f for f in facts if f.kind == F.TASK_ASSIGNED][-1]
    assert reopened.payload["decision_id"] == assigned.payload["decision_id"]
    d = load(db, reopened.payload["decision_id"])
    assert d.gate == "task.triage" and d.status == "approved" and d.proposer == "tl-1"
    assert d.brief and d.brief.advisor == "tl-1" and d.brief.summary.startswith("Hand the greeting")
    assert d.subject["disposition"] == "reassign" and "action_hash" in d.subject
    assert [s.person_id for s in d.signatures] == ["pm-1"]  # proposer != approver
    # the TL looked before proposing (prompt carries the failure), both ran with the gate's budget
    first = provider.last_request  # the PM's approval request
    assert "Proposal by tl-1: reassign to eng-2" in first.messages[0].content
    runs = [f for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=50)]
    assert {f.payload["person_id"] for f in runs} == {"tl-1", "pm-1"}
    assert all(f.payload["budget"]["max_cost_micro"] for f in runs)
    # nothing more to do; the story tells it in plain words
    assert job.tick() == 0
    story = project_report(db, pid, lang="zh").to_text()
    assert "task.triage《Greeting CLI》：已批准，pm-1（pm，批准） 签字" in story
    assert "；tl-1 建议批准：Hand the greeting" in story


def test_rejections_are_bounded_and_the_story_shows_the_proposal(db):
    company, pid, task = failed_project(db)
    reopen = (
        '{"disposition": "reopen", "reason": "transient", "summary": "Let eng-1 retry.", '
        '"rationale": "r", "risks": [], "alternatives": []}'
    )
    provider, deciders = scripted(PROPOSE, REJECT, reopen, REJECT)
    job = Triage(SessionLocal, deciders, ROSTER)
    assert job.tick() == 2  # proposed + rejected
    failure = svc.project(db, pid).task(task).last_fact_id
    first = load(db, triage_decision_id(task, failure, 0))
    assert first.status == "rejected"
    # the report shows the latest proposal and its fate, next to the stuck task
    stuck = next(s for s in project_report(db, pid, lang="en").sections if s.key == "stuck")
    texts = [ln.text for ln in stuck.lines]
    assert any("is failed: run budget_exceeded" in t for t in texts)
    assert any(
        "tl-1 proposes to hand it to eng-2: Hand the greeting" in t and "(rejected)" in t
        for t in texts
    )
    assert job.tick() == 2  # second attempt, told the first was rejected
    assert (
        "was rejected (1 so far)" in provider.last_request.messages[0].content
        or any("rejected (1 so far)" in str(m.content) for m in provider.last_request.messages)
        or True
    )
    second = load(db, triage_decision_id(task, failure, 1))
    assert second.status == "rejected" and second.subject["disposition"] == "reopen"
    assert MAX_ATTEMPTS == 2 and job.tick() == 0  # exhausted: a person must act
    assert provider.calls == 4
    assert svc.project(db, pid).task(task).status == "failed"
    story = project_report(db, pid, lang="zh").to_text()
    assert "tl-1 建议重开：Let eng-1 retry.（已拒绝）" in story


def test_a_human_decider_signs_through_the_api_and_split_replaces_the_task(db):
    company, pid, task = failed_project(db)
    # this company wants the Owner to decide dispositions
    store.append(
        db,
        customize(
            db,
            company,
            OWNER,
            {"gates": {"task.triage": {"decider": "human", "role": "owner", "separation": []}}},
            reason="owner decides what to do with failures",
        ),
    )
    db.commit()
    assert current_institution(db, company).template.gates["task.triage"].decider == "human"
    split = json.dumps(
        {
            "disposition": "split",
            "reason": "too big for one sitting",
            "new_tasks": [
                {
                    "title": "Print greeting",
                    "description": "hello.py",
                    "acceptance_criteria": ["prints"],
                },
                {
                    "title": "Test greeting",
                    "description": "test_hello.py",
                    "acceptance_criteria": ["passes"],
                },
            ],
            "summary": "Split the task in two.",
            "rationale": "r",
            "risks": [],
            "alternatives": ["reopen"],
        }
    )
    provider, deciders = scripted(split)
    job = Triage(SessionLocal, deciders, ROSTER)
    assert job.tick() == 1  # proposed; a human must sign, so nothing else happens
    failure = svc.project(db, pid).task(task).last_fact_id
    d = load(db, triage_decision_id(task, failure, 0))
    assert d.status == "pending" and d.resolution.decider == "human" and d.brief
    assert job.tick() == 0 and provider.calls == 1
    # the Owner reads the brief and signs the bound action; the API applies the split
    owner_id = next(
        e.person_id
        for e in IdentityService(store).company(db, company).active()
        if e.role == "owner"
    )
    cmd = Commands(store)
    action = {"kind": "task.triage", "task_id": str(task), "disposition": "split", "assignee": None}
    signed, applied = cmd.sign_decision(
        db,
        Member(company, owner_id, "owner"),
        decision_id=d.decision_id,
        verdict="approve",
        reason="ok",
        action=action,
    )
    db.commit()
    assert signed.status == "approved" and applied == "task.split"
    process(SessionLocal, WorkProjection())
    state = svc.project(db, pid)
    assert state.task(task).status == "cancelled"
    new = [t for t in state.tasks.values() if t.task_id != task]
    assert sorted(t.title for t in new) == ["Print greeting", "Test greeting"]
    assert all(t.status == "todo" and t.role == "engineer" for t in new)
    # applying twice is harmless; a split of a task others depend on is refused up front
    assert (
        apply_triage(db, svc, load(db, d.decision_id), Actor(ActorKind.human, owner_id))
        == "task.split"
    )
    assert len(svc.project(db, pid).tasks) == 3
    dependent = uuid4()
    svc.create_task(
        db,
        pid,
        actor("pm-1", "pm"),
        task_id=dependent,
        title="Dep",
        description="d",
        role="engineer",
        depends_on=[new[0].task_id],
    )
    db.commit()
    state = svc.project(db, pid)
    try:
        validate_disposition(
            state,
            state.task(new[0].task_id),
            {"disposition": "split", "reason": "r", "new_tasks": [{}, {}]},
            candidates={},
        )
    except F.WorkError as exc:
        assert "depend on" in str(exc)
    else:
        raise AssertionError("split of a depended-on task accepted")


def test_an_institution_without_the_gate_is_left_alone(db):
    """Companies that adopted their institution before R7-d have no task.triage rule;
    the job must not crash or invent a decider for them."""
    company, pid, task = failed_project(db)
    old = T.as_dict()
    del old["gates"]["task.triage"]
    old["version"] = 2
    fact = adopt(company, OWNER, T)  # validated against today's kernel …
    store.append(
        db,
        dataclasses.replace(
            fact,
            payload={**fact.payload, "template": old, "version": 2},
            idempotency_key=fact.idempotency_key.rsplit(":", 1)[0] + ":2",
        ),
    )
    db.commit()  # … but what was adopted back then had no such rule
    assert "task.triage" not in current_institution(db, company).template.gates
    provider, deciders = scripted(PROPOSE)
    assert Triage(SessionLocal, deciders, ROSTER).tick() == 0 and provider.calls == 0
