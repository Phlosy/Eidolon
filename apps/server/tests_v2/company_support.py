"""The R3-e scenario: a whole studio on software-studio-v1 takes a mission through
CEO intake → PM plan → Tech Lead approval and assignment → engineers → QA → Owner
delivery, every manager decision made by an agent on the same loop. Shared by the
fixture recorder and the tests; change any text the models see and re-record."""

from pathlib import Path
from uuid import UUID, uuid4

from project_support import store, svc
from sqlalchemy.orm import Session

from eidolon.agent.blobs import BlobStore
from eidolon.agent.managers import Assigner, Deciders, MissionIntake, Planner, default_roster
from eidolon.agent.scheduler import ReviewExecutor, RunLauncher, TaskRunExecutor, Workspaces
from eidolon.institution import GateContext, adopt, bind, conclude, customize, load, request, sign
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.work import WorkProjection
from eidolon.work.dispatch import request_runs

OWNER = Actor(ActorKind.human, "owner-1")
MISSION_TITLE = "Tiny notes CLI"
MISSION_GOAL = (
    "A small command-line note keeper in Python. Users can add a note, list notes and delete "
    "a note by number. Notes live in a JSON file in the working directory. Ship it with pytest "
    "tests."
)
CRITERIA = [
    "a module exposes add / list / delete over a JSON file",
    "a CLI wraps them with argparse subcommands add, list, delete",
    "pytest tests cover all three operations and pass",
    "standard library only",
]
BUDGET = 20_000


def set_up_company(db: Session) -> UUID:
    """Adopt the default institution; the Owner relaxes plan.approve to a single
    Tech Lead signature because this studio has one PM and one Tech Lead."""
    company = uuid4()
    store.append(db, adopt(company, OWNER, T))
    db.commit()
    store.append(
        db,
        customize(
            db,
            company,
            OWNER,
            {"gates": {"plan.approve": {"quorum": 1, "conditions": {"co_signers": ["tech_lead"]}}}},
            reason="one PM and one Tech Lead: the Tech Lead alone countersigns plans",
        ),
    )
    db.commit()
    return company


def propose_mission(db: Session, company: UUID) -> UUID:
    fact = svc.create_mission(
        db,
        company,
        OWNER,
        title=MISSION_TITLE,
        goal=MISSION_GOAL,
        success_criteria=CRITERIA,
        budget=BUDGET,
    )
    db.commit()
    return UUID(fact.payload["mission_id"])


def studio_jobs(session_factory, provider, blobs: BlobStore, workspace_root: Path, **kw):
    deciders = Deciders(session_factory, provider, blobs)
    roster = default_roster()
    workspaces = Workspaces(workspace_root)
    return [
        MissionIntake(session_factory, deciders, roster, svc=svc),
        Planner(session_factory, deciders, roster, svc=svc),
        Assigner(session_factory, deciders, roster, svc=svc),
        TaskRunExecutor(session_factory, provider, blobs, workspaces, svc=svc, store=store, **kw),
        ReviewExecutor(
            session_factory,
            provider,
            blobs,
            workspaces,
            reviewer="qa-1",
            role="qa",
            svc=svc,
            store=store,
        ),
    ]


def company_project_id(db: Session, mission_id: UUID) -> UUID | None:
    from sqlalchemy import select

    from eidolon.work.projection import ProjectRow

    row = db.scalar(select(ProjectRow).where(ProjectRow.mission_id == mission_id))
    return row.project_id if row else None


def drive_company(
    session_factory, db: Session, mission_id: UUID, jobs, *, max_cycles: int = 24
) -> list[dict]:
    """One worker cycle at a time until every task of the project is done (or nothing
    moves any more). Returns a trail of {project status, task statuses} per cycle."""
    trail: list[dict] = []
    for _ in range(max_cycles):
        process(session_factory, WorkProjection())
        pid = company_project_id(db, mission_id)
        if pid is not None:
            request_runs(db, svc, pid)
            db.commit()
            process(session_factory, WorkProjection())
            process(session_factory, RunLauncher(svc))
            process(session_factory, WorkProjection())
        for job in jobs:
            job.tick()
            process(session_factory, WorkProjection())
        snap = {"mission": svc.mission(db, mission_id).status, "project": None, "tasks": {}}
        pid = company_project_id(db, mission_id)
        if pid is not None:
            state = svc.project(db, pid)
            snap["project"] = state.status
            snap["tasks"] = {t.title: t.status for t in state.tasks.values()}
        trail.append(snap)
        if snap["mission"] == "rejected":
            break
        if snap["tasks"] and all(
            s in ("done", "cancelled", "blocked", "failed") for s in snap["tasks"].values()
        ):
            break
    return trail


def owner_delivers(db: Session, company: UUID, pid: UUID) -> None:
    """V6: the Owner accepts the delivery — a human decision bound to the action."""
    action = {"kind": "project.deliver", "project_id": str(pid)}
    req, _ = request(
        T,
        GateContext("project.deliver", company, subject={**bind(action), "project_id": str(pid)}),
        Actor(ActorKind.agent, "pm-1"),
    )
    did = store.append(db, req).correlation_id
    store.append(
        db,
        sign(
            T,
            load(db, did),
            OWNER,
            role="owner",
            verdict="approve",
            reason="looks good",
            action=action,
        ),
    )
    store.append(db, conclude(load(db, did)))
    db.commit()
    svc.deliver_project(db, pid, OWNER, decision_id=did)
    db.commit()
