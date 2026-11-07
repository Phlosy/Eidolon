"""Timeouts and escalation (R6-b): an unanswered gate moves to its escalate_to and
the next decider acts through the same envelope; a cancelled run fails its task."""

import tempfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from project_support import components, drive, set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_money import OWNER, found
from test_work import decide

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.escalation import EscalatedDecider, Escalator
from eidolon.agent.managers import Deciders, Person, Roster, StaticRoster
from eidolon.agent.providers import Completed, TextDelta, Usage
from eidolon.agent.scheduler import RunLauncher
from eidolon.api.commands import Commands, Conflict
from eidolon.api.commands import Person as Member
from eidolon.db import SessionLocal
from eidolon.identity import IdentityService
from eidolon.institution import ESCALATED, GateContext, load, request, staffing
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import WorkProjection
from eidolon.work import facts as F
from eidolon.work.dispatch import request_runs

ids = IdentityService(store)


class Mute:
    """Does the task, but never gives a review verdict (as in test_dispatch)."""

    name = "mute"

    async def complete(self, request):
        yield TextDelta(
            "I am not sure." if "Review task" in request.messages[0].content else "done"
        )
        yield Completed("end_turn", Usage(1, 1), request.model, "mute", request_id=str(uuid4()))

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


def staffed(db):
    company = found(db)
    for pid, role in (
        ("eng-1", "engineer"),
        ("qa-1", "qa"),
        ("tl-1", "tech_lead"),
        ("ceo-1", "ceo"),
    ):
        ids.create_person(db, OWNER, pid, name=pid, kind="agent")
        staffing.hire(db, company, OWNER, person_id=pid, role=role)
    db.commit()
    return company


def test_timeouts_escalate_and_the_next_decider_acts(db, tmp_path):
    company = staffed(db)
    _, pid, task = set_up_project(db, company)
    runs, reviews = components(SessionLocal, Mute(), BlobStore(tmp_path / "b"), tmp_path / "ws")
    assert drive(SessionLocal, db, pid, runs, reviews, max_cycles=2) == ["in_review", "in_review"]
    review_id = next(
        f.correlation_id
        for f in store.stream(
            db, kinds=["institution.decision.requested"], company_id=company, limit=50
        )
        if f.payload["gate"] == "task.review"
    )
    pending = load(db, review_id)
    assert pending.status == "pending" and pending.resolution.role == "qa"

    # within the 8h timeout nothing moves; past it the decision goes to the Tech Lead
    now = datetime.now(UTC)
    assert Escalator(SessionLocal, clock=lambda: now).tick() == 0
    late = Escalator(SessionLocal, clock=lambda: now + timedelta(hours=9))
    assert late.tick() >= 1
    escalated = load(db, review_id)
    assert escalated.escalations == 1 and escalated.resolution.branch == "escalated"
    assert escalated.resolution.role == "tech_lead" and escalated.resolution.decider == "agent"
    esc_fact = next(f for f in store.by_correlation(db, review_id) if f.kind == ESCALATED)
    assert esc_fact.payload["from"] == {"decider": "agent", "role": "qa"}
    assert "no answer within 28800s" in esc_fact.payload["reason"]
    assert late.tick() == 0  # escalated once; the new resolution has no timeout

    # the QA executor can no longer sign it; the Tech Lead decides it on its merits
    assert reviews.tick() == 0
    provider = ScriptedLoop([[TextDelta('{"verdict": "approve", "reason": "tests pass"}')]])
    decider = EscalatedDecider(
        SessionLocal,
        Deciders(SessionLocal, provider, BlobStore(tempfile.mkdtemp())),
        StaticRoster(Roster([Person("tl-1", "tech_lead"), Person("qa-1", "qa")])),
    )
    assert decider.tick() == 1
    decided = load(db, review_id)
    assert decided.status == "approved" and decided.signatures[0].person_id == "tl-1"
    assert "escalated 1×" in provider_prompt(provider)
    assert decider.tick() == 0

    # the review executor applies it with the signer as reviewer of record
    assert reviews.tick() == 1
    process(SessionLocal, WorkProjection())
    state = svc.project(db, pid)
    assert state.task(task).status == "done"
    reviewed = next(f for f in store.by_correlation(db, pid) if f.kind == F.TASK_REVIEWED)
    assert reviewed.payload["reviewer"] == "tl-1" and reviewed.payload["verdict"] == "pass"

    # forcing: the Owner may push an open decision up right now; nowhere left → conflict
    requested, _ = request(
        T,
        GateContext("task.assign", company, subject={"task_id": str(uuid4()), "assignee": "eng-1"}),
        Actor.system(),
    )
    did = store.append(db, requested).correlation_id
    db.commit()
    owner = next(e.person_id for e in ids.company(db, company).active() if e.role == "owner")
    cmd = Commands(store)
    forced = cmd.escalate_decision(db, Member(company, owner, "owner"), decision_id=did)
    assert forced.resolution.role == "ceo" and forced.escalations == 1
    db.commit()
    try:
        cmd.escalate_decision(db, Member(company, owner, "owner"), decision_id=did)
    except Conflict as exc:
        assert "nothing to escalate" in str(exc)
    else:
        raise AssertionError("a decision with no escalate_to must not escalate")


def provider_prompt(provider: ScriptedLoop) -> str:
    return provider.last_request.messages[0].content


def test_cancelled_runs_fail_their_task_and_release_it(db, tmp_path):
    company, pid, task = set_up_project(db)
    provider = ScriptedLoop([[TextDelta("done")]])
    runs, reviews = components(SessionLocal, provider, BlobStore(tmp_path / "b"), tmp_path / "ws")
    request_runs(db, svc, pid)
    db.commit()
    process(SessionLocal, WorkProjection())
    process(SessionLocal, RunLauncher(svc))
    process(SessionLocal, WorkProjection())
    run_id = svc.project(db, pid).task(task).current_run_id
    assert run_id is not None
    # the Owner cancels before the executor gets to it
    cancel = store.append(
        db,
        R.cancel_request(
            company,
            run_id,
            Actor(ActorKind.human, "owner-1"),
            reason="wrong task",
            causation_id=None,
        ),
    )
    db.commit()
    assert runs.tick() == 1
    process(SessionLocal, WorkProjection())
    state = svc.project(db, pid)
    assert state.task(task).status == "failed"
    assert provider.calls == 0  # cancelled at the first checkpoint: no model call
    kinds = [f.kind for f in store.by_correlation(db, run_id)]
    assert kinds[-1] == R.RUN_CANCELLED and R.RUN_CANCEL_REQUESTED in kinds
    failed = next(f for f in store.by_correlation(db, pid) if f.kind == F.TASK_FAILED)
    assert "cancelled" in failed.payload["reason"]
    # W3: nothing re-dispatches; only a decision brings it back
    assert request_runs(db, svc, pid) == []
    # cancelling again is the same fact (K2)
    again = store.append(
        db,
        R.cancel_request(
            company,
            run_id,
            Actor(ActorKind.human, "owner-1"),
            reason="wrong task",
            causation_id=None,
        ),
    )
    assert again.id == cancel.id
    reopen = decide(
        db,
        company,
        "task.assign",
        {"task_id": str(task), "assignee": "eng-1"},
        signers=[("tl-1", "tech_lead")],
    )
    svc.reopen_task(
        db, pid, task, Actor(ActorKind.agent, "tl-1"), decision_id=reopen, reason="retry"
    )
    db.commit()
    assert svc.project(db, pid).task(task).status == "todo"
