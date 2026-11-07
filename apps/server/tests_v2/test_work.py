"""Work domain (R3-c): the W-series invariants (M2's R/H/RV/RD carried over),
the status machines, decision-gated transitions, artifacts and the projection."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from eidolon.db import SessionLocal
from eidolon.institution import GateContext, bind, conclude, load, request, sign
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process, rebuild
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore
from eidolon.work import (
    ArtifactRow,
    DagError,
    MissionRow,
    Node,
    ProjectRow,
    TaskRow,
    WorkError,
    WorkProjection,
    WorkService,
    dispatchable,
    graph,
    lineage,
    resolve_ready,
)
from eidolon.work import facts as F

store = FactStore()
svc = WorkService(store)
SYS = Actor.system()
HUMAN_ROLE = {"owner"}


def actor(person: str, role: str) -> Actor:
    return Actor(ActorKind.human if role in HUMAN_ROLE else ActorKind.agent, person)


def decide(
    db,
    company,
    gate,
    subject,
    *,
    signers,
    proposer=None,
    author=None,
    verdict="approve",
    amount=None,
    action=None,
):
    """Open and settle a decision at ``gate`` through the real envelope."""
    if action is not None:
        subject = {**bind(action), **subject}
    req, _ = request(
        T,
        GateContext(
            gate, company, proposer=proposer, author=author, subject=subject, amount=amount
        ),
        Actor(ActorKind.agent, proposer or "pm-1"),
    )
    did = store.append(db, req).correlation_id
    for person, role in signers:
        store.append(
            db,
            sign(
                T,
                load(db, did),
                actor(person, role),
                role=role,
                verdict=verdict,
                reason="because",
                action=action,
            ),
        )
    recorded = conclude(load(db, did))
    if recorded is not None:
        store.append(db, recorded)
    db.commit()
    return did


def pending_decision(db, company, gate, subject):
    req, _ = request(T, GateContext(gate, company, subject=subject), Actor(ActorKind.agent, "pm-1"))
    did = store.append(db, req).correlation_id
    db.commit()
    return did


@pytest.fixture()
def project(db):
    """An active project with tasks A, B(depends A), C(inputs A)."""
    company = uuid4()
    pid = svc.create_project(
        db, company, actor("pm-1", "pm"), title="P", budget=10_000, manager="pm-1"
    ).payload
    pid = __import__("uuid").UUID(pid["project_id"])
    a = uuid4()
    b = uuid4()
    c = uuid4()
    svc.create_task(
        db, pid, actor("pm-1", "pm"), task_id=a, title="A", description="first", role="engineer"
    )
    svc.create_task(
        db, pid, actor("pm-1", "pm"), task_id=b, title="B", description="after A", depends_on=[a]
    )
    svc.create_task(
        db,
        pid,
        actor("pm-1", "pm"),
        task_id=c,
        title="C",
        description="uses A",
        depends_on=[a],
        inputs=[a],
    )
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
    return company, pid, a, b, c


def assign(db, company, pid, task, person):
    did = decide(
        db,
        company,
        "task.assign",
        {"task_id": str(task), "assignee": person},
        signers=[("tl-1", "tech_lead")],
    )
    svc.assign_task(db, pid, task, actor("tl-1", "tech_lead"), assignee=person, decision_id=did)
    db.commit()
    return did


def run_through(db, company, pid, task, person, *, artifact=False):
    """assign → start → (artifact) → submit → review pass; returns (run_id, artifact_id)."""
    assign(db, company, pid, task, person)
    run_id = uuid4()
    svc.start_task(db, pid, task, SYS, run_id=run_id, person_id=person)
    art = None
    if artifact:
        art = __import__("uuid").UUID(
            svc.produce_artifact(
                db,
                pid,
                task,
                actor(person, "engineer"),
                run_id=run_id,
                path="out.txt",
                blob_ref="sha256:" + "a" * 64,
                size=3,
            ).payload["artifact_id"]
        )
    svc.submit_task(
        db, pid, task, actor(person, "engineer"), run_id=run_id, artifact_ids=[art] if art else []
    )
    review = decide(
        db, company, "task.review", {"task_id": str(task)}, signers=[("qa-1", "qa")], author=person
    )
    svc.review_task(
        db,
        pid,
        task,
        actor("qa-1", "qa"),
        decision_id=review,
        verdict="pass",
        reviewer="qa-1",
        reason="ok",
    )
    db.commit()
    return run_id, art


# -- W2 / W1 / W3 ------------------------------------------------------------------------------


def test_readiness_is_derived_and_distinct_from_dispatchability(db, project):
    company, pid, a, b, c = project
    state = svc.project(db, pid)
    assert [n.task_id for n in resolve_ready(state.nodes())] == [a]
    assert dispatchable(state.nodes()) == []  # ready but unassigned: not dispatchable (R3)
    assert [n.task_id for n in graph.awaiting_assignment(state.nodes())] == [a]
    # no column stores readiness
    rows = {c.name for c in TaskRow.__table__.columns}
    assert not any("ready" in name for name in rows)
    run_through(db, company, pid, a, "eng-1")
    state = svc.project(db, pid)
    assert state.tasks[a].status == "done"
    assert sorted(str(n.task_id) for n in resolve_ready(state.nodes())) == sorted([str(b), str(c)])
    assert dispatchable(state.nodes()) == []


def test_assignment_requires_an_approved_decision_naming_the_assignee(db, project):
    company, pid, a, b, c = project
    with pytest.raises(WorkError, match="no assignee"):
        svc.start_task(db, pid, a, SYS, run_id=uuid4(), person_id="eng-1")
    other = decide(
        db,
        company,
        "task.assign",
        {"task_id": str(a), "assignee": "eng-9"},
        signers=[("tl-1", "tech_lead")],
    )
    with pytest.raises(WorkError, match="assignee"):
        svc.assign_task(db, pid, a, actor("tl-1", "tech_lead"), assignee="eng-1", decision_id=other)
    pend = pending_decision(db, company, "task.assign", {"task_id": str(a), "assignee": "eng-1"})
    with pytest.raises(WorkError, match="pending"):
        svc.assign_task(db, pid, a, actor("tl-1", "tech_lead"), assignee="eng-1", decision_id=pend)
    wrong_gate = decide(db, company, "task.cancel", {"task_id": str(a)}, signers=[("pm-1", "pm")])
    with pytest.raises(WorkError, match="gate task.cancel"):
        svc.assign_task(
            db, pid, a, actor("tl-1", "tech_lead"), assignee="eng-1", decision_id=wrong_gate
        )
    did = assign(db, company, pid, a, "eng-1")
    state = svc.project(db, pid)
    assert state.tasks[a].assignee == "eng-1" and state.tasks[a].status == "todo"
    assigned = next(f for f in store.by_correlation(db, pid) if f.kind == F.TASK_ASSIGNED)
    assert assigned.decision_id == did
    with pytest.raises(WorkError, match="R1"):
        svc.start_task(db, pid, a, SYS, run_id=uuid4(), person_id="eng-2")
    assign(db, company, pid, b, "eng-1")
    with pytest.raises(WorkError, match="not ready"):
        svc.start_task(db, pid, b, SYS, run_id=uuid4(), person_id="eng-1")  # A is not done
    svc.start_task(db, pid, a, SYS, run_id=uuid4(), person_id="eng-1")
    assert svc.project(db, pid).tasks[a].status == "in_progress"


def test_failed_task_returns_only_through_a_decision(db, project):
    company, pid, a, b, c = project
    assign(db, company, pid, a, "eng-1")
    run_id = uuid4()
    svc.start_task(db, pid, a, SYS, run_id=run_id, person_id="eng-1")
    with pytest.raises(WorkError, match="current run"):
        svc.fail_task(db, pid, a, SYS, run_id=uuid4(), reason="x")
    svc.fail_task(db, pid, a, SYS, run_id=run_id, reason="runtime died")
    state = svc.project(db, pid)
    assert state.tasks[a].status == "failed" and state.tasks[a].assignee == "eng-1"
    with pytest.raises(WorkError, match="needs one of"):
        svc.start_task(db, pid, a, SYS, run_id=uuid4(), person_id="eng-1")  # no automatic retry
    did = decide(
        db,
        company,
        "task.assign",
        {"task_id": str(a), "assignee": "eng-1"},
        signers=[("tl-1", "tech_lead")],
    )
    svc.reopen_task(db, pid, a, actor("tl-1", "tech_lead"), decision_id=did, reason="try again")
    assert svc.project(db, pid).tasks[a].status == "todo"
    svc.start_task(db, pid, a, SYS, run_id=uuid4(), person_id="eng-1")
    assert svc.project(db, pid).tasks[a].runs.__len__() == 2


# -- W4 / W5 / W6 -------------------------------------------------------------------------------


def test_artifacts_are_attributed_and_consumption_is_recorded(db, project):
    company, pid, a, b, c = project
    with pytest.raises(WorkError, match="only running tasks"):
        svc.produce_artifact(
            db, pid, a, SYS, run_id=None, path="x", blob_ref="sha256:" + "0" * 64, size=1
        )
    run_a, art_a = run_through(db, company, pid, a, "eng-1", artifact=True)
    state = svc.project(db, pid)
    art = state.artifacts[art_a]
    assert art.task_id == a and art.run_id == run_a and art.blob_ref.startswith("sha256:")
    # a task may not submit someone else's artifact as its own (H2)
    assign(db, company, pid, b, "eng-2")
    run_b = uuid4()
    svc.start_task(db, pid, b, SYS, run_id=run_b, person_id="eng-2")
    with pytest.raises(WorkError, match="H2"):
        svc.submit_task(db, pid, b, actor("eng-2", "engineer"), run_id=run_b, artifact_ids=[art_a])
    # C declares A as input: consumption is recorded with the using run (H3)
    assign(db, company, pid, c, "eng-3")
    run_c = uuid4()
    svc.start_task(db, pid, c, SYS, run_id=run_c, person_id="eng-3")
    assert [x.artifact_id for x in svc.inputs_for(db, pid, c)] == [art_a]
    svc.consume_artifact(db, pid, art_a, SYS, by_task_id=c, run_id=run_c)
    db.commit()
    uses = svc.project(db, pid).artifacts[art_a].uses
    assert uses == [{"task_id": str(c), "run_id": str(run_c), "at": uses[0]["at"]}]
    process(SessionLocal, WorkProjection())
    row = db.get(ArtifactRow, art_a)
    assert row.task_id == a and row.uses[0]["run_id"] == str(run_c)


def test_inputs_must_be_ancestors_and_only_done_work_is_consumable(db, project):
    company, pid, a, b, c = project
    with pytest.raises(DagError, match="inputs not guaranteed"):
        svc.create_task(
            db, pid, actor("pm-1", "pm"), title="D", description="bad input", inputs=[b]
        )  # B is not an ancestor of D
    with pytest.raises(WorkError, match="not a task"):
        svc.create_task(
            db, pid, actor("pm-1", "pm"), title="E", description="dangling", depends_on=[uuid4()]
        )
    assign(db, company, pid, a, "eng-1")
    run_a = uuid4()
    svc.start_task(db, pid, a, SYS, run_id=run_a, person_id="eng-1")
    art = __import__("uuid").UUID(
        svc.produce_artifact(
            db, pid, a, SYS, run_id=run_a, path="a.txt", blob_ref="sha256:" + "b" * 64, size=1
        ).payload["artifact_id"]
    )
    with pytest.raises(WorkError, match="H8"):
        svc.consume_artifact(db, pid, art, SYS, by_task_id=c, run_id=None)  # A not done yet
    assert svc.inputs_for(db, pid, c) == []
    svc.submit_task(db, pid, a, SYS, run_id=run_a, artifact_ids=[art])
    review = decide(
        db, company, "task.review", {"task_id": str(a)}, signers=[("qa-1", "qa")], author="eng-1"
    )
    svc.review_task(
        db, pid, a, actor("qa-1", "qa"), decision_id=review, verdict="pass", reviewer="qa-1"
    )
    db.commit()
    assert [x.artifact_id for x in svc.inputs_for(db, pid, c)] == [art]
    svc.consume_artifact(db, pid, art, SYS, by_task_id=c, run_id=None)
    # B depends on A (ancestor) so it may consume too, even without declaring inputs
    svc.consume_artifact(db, pid, art, SYS, by_task_id=b, run_id=None)
    d = uuid4()
    svc.create_task(db, pid, actor("pm-1", "pm"), task_id=d, title="D", description="unrelated")
    with pytest.raises(WorkError, match="H4/H5"):
        svc.consume_artifact(db, pid, art, SYS, by_task_id=d, run_id=None)


def test_lineage_walks_back_through_producers(db):
    a, b, c = uuid4(), uuid4(), uuid4()
    art_a, art_b, art_c = uuid4(), uuid4(), uuid4()
    produced_by = {art_a: a, art_b: b, art_c: c}
    consumed = {b: (art_a,), c: (art_b,)}
    assert lineage(produced_by, consumed, art_c) == [(art_c, c), (art_b, b), (art_a, a)]
    assert lineage(produced_by, consumed, art_c, depth=2) == [(art_c, c), (art_b, b)]
    assert lineage(produced_by, consumed, uuid4()) == []


# -- W7 / W8 ----------------------------------------------------------------------------------


def test_review_needs_a_signed_decision_and_maps_one_way(db, project):
    company, pid, a, b, c = project
    assign(db, company, pid, a, "eng-1")
    run_id = uuid4()
    svc.start_task(db, pid, a, SYS, run_id=run_id, person_id="eng-1")
    svc.submit_task(db, pid, a, SYS, run_id=run_id)
    assert svc.project(db, pid).tasks[a].status == "in_review"
    pend = pending_decision(db, company, "task.review", {"task_id": str(a)})
    with pytest.raises(WorkError, match="RV2"):
        svc.review_task(db, pid, a, SYS, decision_id=pend, verdict="pass", reviewer="qa-1")
    approved = decide(
        db, company, "task.review", {"task_id": str(a)}, signers=[("qa-1", "qa")], author="eng-1"
    )
    with pytest.raises(WorkError, match="RV4"):
        svc.review_task(db, pid, a, SYS, decision_id=approved, verdict="pass", reviewer="qa-2")
    with pytest.raises(WorkError, match="RV8"):
        svc.review_task(db, pid, a, SYS, decision_id=approved, verdict="rework", reviewer="qa-1")
    with pytest.raises(WorkError, match="verdict"):
        svc.review_task(db, pid, a, SYS, decision_id=approved, verdict="approve", reviewer="qa-1")
    reviewed = svc.review_task(
        db,
        pid,
        a,
        actor("qa-1", "qa"),
        decision_id=approved,
        verdict="pass",
        reviewer="qa-1",
        reason="good",
    )
    db.commit()
    assert reviewed.decision_id == approved and reviewed.actor == actor("qa-1", "qa")
    task = svc.project(db, pid).tasks[a]
    assert (
        task.status == "done"
        and task.reviews[0]["reviewer"] == "qa-1"
        and task.current_run_id is None
    )
    with pytest.raises(WorkError, match="needs one of"):
        svc.review_task(
            db, pid, a, SYS, decision_id=approved, verdict="pass", reviewer="qa-1"
        )  # append-only; done stays done


def test_rework_counts_and_escalate_blocks(db, project):
    company, pid, a, b, c = project
    assign(db, company, pid, a, "eng-1")
    run1 = uuid4()
    svc.start_task(db, pid, a, SYS, run_id=run1, person_id="eng-1")
    svc.submit_task(db, pid, a, SYS, run_id=run1)
    rework = decide(
        db,
        company,
        "task.review",
        {"task_id": str(a)},
        signers=[("qa-1", "qa")],
        author="eng-1",
        verdict="rework",
    )
    svc.review_task(
        db,
        pid,
        a,
        actor("qa-1", "qa"),
        decision_id=rework,
        verdict="rework",
        reviewer="qa-1",
        reason="tests missing",
    )
    db.commit()
    task = svc.project(db, pid).tasks[a]
    assert task.status == "todo" and task.rework_count == 1 and task.assignee == "eng-1"
    run2 = uuid4()
    svc.start_task(db, pid, a, SYS, run_id=run2, person_id="eng-1")
    svc.submit_task(db, pid, a, SYS, run_id=run2)
    esc = decide(
        db,
        company,
        "task.review",
        {"task_id": str(a)},
        signers=[("qa-1", "qa")],
        author="eng-1",
        verdict="reject",
    )
    svc.review_task(
        db,
        pid,
        a,
        actor("qa-1", "qa"),
        decision_id=esc,
        verdict="escalate",
        reviewer="qa-1",
        reason="needs a lead",
    )
    db.commit()
    task = svc.project(db, pid).tasks[a]
    assert task.status == "blocked" and task.rework_count == 1  # RV6: stops and waits
    with pytest.raises(WorkError, match="decision_id"):
        svc.unblock_task(db, pid, a, SYS, reason="go")
    svc.unblock_task(db, pid, a, actor("owner-1", "owner"), reason="lead reviewed it")
    db.commit()
    assert svc.project(db, pid).tasks[a].status == "todo"
    process(SessionLocal, WorkProjection())
    row = db.get(TaskRow, a)
    assert row.rework_count == 1 and len(row.reviews) == 2 and row.status == "todo"


# -- W9 / W10 ---------------------------------------------------------------------------------


def test_graph_validation_rejects_bad_structure(db, project):
    company, pid, a, b, c = project
    x, y, z = uuid4(), uuid4(), uuid4()
    report = graph.validate([Node(x, "todo", (x,)), Node(y, "todo", (z, z)), Node(z, "todo", (y,))])
    assert not report.is_valid
    assert report.self_loops == (x,) and report.duplicates == ((y, z),)
    assert report.cycles and set(report.cycles[0]) == {y, z}
    assert "self loops" in report.error and "cycles" in report.error
    assert graph.validate([Node(x, "todo", (uuid4(),))]).dangling
    assert graph.validate([Node(x, "todo"), Node(y, "todo", (x,), inputs=(x,))]).is_valid
    with pytest.raises(WorkError, match="itself"):
        F.task_created(company, SYS, pid, x, title="t", description="d", depends_on=[x])
    with pytest.raises(WorkError, match="duplicate"):
        svc.create_task(
            db, pid, actor("pm-1", "pm"), title="dup", description="d", depends_on=[a, a]
        )
    # plan approval re-validates and refuses an empty plan
    company2 = uuid4()
    pid2 = __import__("uuid").UUID(
        svc.create_project(db, company2, actor("pm-1", "pm"), title="empty", budget=1).payload[
            "project_id"
        ]
    )
    plan = decide(
        db,
        company2,
        "plan.approve",
        {"project_id": str(pid2)},
        signers=[("tl-1", "tech_lead"), ("pm-2", "pm")],
        proposer="pm-1",
    )
    with pytest.raises(WorkError, match="at least one task"):
        svc.approve_plan(db, pid2, actor("pm-1", "pm"), decision_id=plan)


def test_projection_matches_the_fold_and_rebuilds(db, project):
    company, pid, a, b, c = project
    run_through(db, company, pid, a, "eng-1", artifact=True)
    assign(db, company, pid, b, "eng-2")
    projection = WorkProjection()
    process(SessionLocal, projection)
    state = svc.project(db, pid)

    def snapshot():
        db.expire_all()
        rows = db.scalars(
            select(TaskRow).where(TaskRow.project_id == pid).order_by(TaskRow.title)
        ).all()
        return [
            (r.task_id, r.status, r.assignee, r.rework_count, len(r.reviews), r.current_run_id)
            for r in rows
        ]

    before = snapshot()
    assert before == sorted(
        [
            (t.task_id, t.status, t.assignee, t.rework_count, len(t.reviews), t.current_run_id)
            for t in state.tasks.values()
        ],
        key=lambda x: {a: "A", b: "B", c: "C"}[x[0]],
    )
    assert (
        db.get(ProjectRow, pid).status == "active"
        and db.get(ProjectRow, pid).plan_decision_id == state.plan_decision_id
    )
    assert db.scalar(select(ArtifactRow).where(ArtifactRow.project_id == pid)).task_id == a
    db.execute(text("UPDATE work_tasks SET status = 'cancelled', rework_count = 9"))
    db.commit()
    assert rebuild(SessionLocal, projection) > 0
    assert snapshot() == before
    # re-processing is idempotent (last_seq guard)
    assert process(SessionLocal, projection)[0].handled == 0


# -- mission → project → delivery -----------------------------------------------------------------


def test_mission_project_and_delivery_flow(db):
    company = uuid4()
    mission = svc.create_mission(
        db, company, actor("owner-1", "owner"), title="Ship v1", goal="A working CLI", budget=50_000
    )
    mid = __import__("uuid").UUID(mission.payload["mission_id"])
    db.commit()
    assert svc.mission(db, mid).status == "proposed"
    with pytest.raises(WorkError, match="only accepted"):
        svc.create_project(db, company, actor("pm-1", "pm"), title="P", budget=1000, mission_id=mid)
    accept = decide(
        db,
        company,
        "mission.accept",
        {"mission_id": str(mid)},
        signers=[("ceo-1", "ceo")],
        amount=50_000,
    )
    svc.decide_mission(
        db, mid, actor("ceo-1", "ceo"), decision_id=accept, reason="fits the quarter"
    )
    db.commit()
    assert svc.mission(db, mid).status == "accepted"
    with pytest.raises(WorkError, match="already accepted"):
        svc.decide_mission(db, mid, actor("ceo-1", "ceo"), decision_id=accept)
    with pytest.raises(WorkError, match="exceeds"):
        svc.create_project(
            db, company, actor("pm-1", "pm"), title="P", budget=60_000, mission_id=mid
        )
    pid = __import__("uuid").UUID(
        svc.create_project(
            db,
            company,
            actor("pm-1", "pm"),
            title="P",
            budget=20_000,
            mission_id=mid,
            manager="pm-1",
        ).payload["project_id"]
    )
    a = uuid4()
    svc.create_task(db, pid, actor("pm-1", "pm"), task_id=a, title="A", description="only task")
    db.commit()
    with pytest.raises(WorkError, match="approved plan"):
        assign(db, company, pid, a, "eng-1")
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
    action = {"kind": "project.deliver", "project_id": str(pid)}
    deliver = decide(
        db,
        company,
        "project.deliver",
        {"project_id": str(pid)},
        signers=[("owner-1", "owner")],
        action=action,
    )
    with pytest.raises(WorkError, match="not done"):
        svc.deliver_project(db, pid, actor("owner-1", "owner"), decision_id=deliver)
    run_through(db, company, pid, a, "eng-1")
    svc.deliver_project(db, pid, actor("owner-1", "owner"), decision_id=deliver)
    db.commit()
    state = svc.project(db, pid)
    assert state.status == "delivered"
    with pytest.raises(WorkError, match="delivered"):
        svc.create_task(db, pid, actor("pm-1", "pm"), title="late", description="no")
    process(SessionLocal, WorkProjection())
    assert (
        db.get(MissionRow, mid).status == "accepted"
        and db.get(ProjectRow, pid).status == "delivered"
    )
    # everything in the project shares its correlation; every management fact names its decision
    facts = store.by_correlation(db, pid)
    assert all(f.correlation_id == pid for f in facts)
    managed = [
        f
        for f in facts
        if f.kind
        in (F.PROJECT_PLAN_APPROVED, F.TASK_ASSIGNED, F.TASK_REVIEWED, F.PROJECT_DELIVERED)
    ]
    assert managed and all(f.decision_id is not None for f in managed)
