"""The R3-d scenario shared by the fixture recorder and the tests: one company on
software-studio-v1, one project with one task, driven through dispatch → run →
submit → review by the real worker-side components. Change anything the model
sees (task text, criteria, prompts) and re-record (`make v2-record-fixtures`)."""

from pathlib import Path
from uuid import UUID, uuid4

from eidolon.agent.blobs import BlobStore
from eidolon.agent.scheduler import ReviewExecutor, RunLauncher, TaskRunExecutor, Workspaces
from eidolon.institution import GateContext, adopt, conclude, load, request, sign
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore
from eidolon.work import WorkProjection, WorkService
from eidolon.work.dispatch import request_runs

store = FactStore()
svc = WorkService(store)

TASK_TITLE = "Greeting CLI"
TASK_DESCRIPTION = (
    "Create `hello.py`: when run it prints exactly `Hello, Eidolon!`. "
    "Create `test_hello.py` with one pytest test that runs hello.py with `sys.executable` "
    "via subprocess and asserts the output. Run the tests with run_tests until they pass."
)
CRITERIA = [
    "hello.py prints exactly `Hello, Eidolon!` followed by a newline",
    "test_hello.py exists and `python -m pytest -q` passes in the workspace",
    "standard library only",
]
ENGINEER = "eng-1"
REVIEWER = "qa-1"
OWNER = Actor(ActorKind.human, "owner-1")


def decide(db, company, gate, subject, *, signers, proposer=None, author=None):
    req, _ = request(
        T,
        GateContext(gate, company, proposer=proposer, author=author, subject=subject),
        Actor(ActorKind.agent, proposer or "pm-1"),
    )
    did = store.append(db, req).correlation_id
    for person, role in signers:
        store.append(
            db,
            sign(
                T,
                load(db, did),
                Actor(ActorKind.agent, person),
                role=role,
                verdict="approve",
                reason="ok",
            ),
        )
    store.append(db, conclude(load(db, did)))
    db.commit()
    return did


def set_up_project(db, company: UUID | None = None) -> tuple[UUID, UUID, UUID]:
    """Adopt the institution, create + plan + assign the one-task project.
    Returns (company_id, project_id, task_id)."""
    company = company or uuid4()
    store.append(db, adopt(company, OWNER, T))
    pid = UUID(
        svc.create_project(
            db,
            company,
            Actor(ActorKind.agent, "pm-1"),
            title="Greeting",
            budget=10_000,
            manager="pm-1",
        ).payload["project_id"]
    )
    task = uuid4()
    svc.create_task(
        db,
        pid,
        Actor(ActorKind.agent, "pm-1"),
        task_id=task,
        title=TASK_TITLE,
        description=TASK_DESCRIPTION,
        acceptance_criteria=CRITERIA,
        role="engineer",
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
    svc.approve_plan(db, pid, Actor(ActorKind.agent, "pm-1"), decision_id=plan)
    assign = decide(
        db,
        company,
        "task.assign",
        {"task_id": str(task), "assignee": ENGINEER},
        signers=[("tl-1", "tech_lead")],
    )
    svc.assign_task(
        db, pid, task, Actor(ActorKind.agent, "tl-1"), assignee=ENGINEER, decision_id=assign
    )
    db.commit()
    return company, pid, task


def components(session_factory, provider, blobs: BlobStore, workspace_root: Path, **kw):
    workspaces = Workspaces(workspace_root)
    runs = TaskRunExecutor(session_factory, provider, blobs, workspaces, svc=svc, store=store, **kw)
    reviews = ReviewExecutor(
        session_factory,
        provider,
        blobs,
        workspaces,
        reviewer=REVIEWER,
        role="qa",
        svc=svc,
        store=store,
    )
    return runs, reviews


def drive(
    session_factory,
    db,
    pid: UUID,
    runs: TaskRunExecutor,
    reviews: ReviewExecutor,
    *,
    max_cycles: int = 6,
) -> list[str]:
    """What the worker does each cycle, in order, until the task settles. Returns the
    task status after each cycle."""
    trail = []
    for _ in range(max_cycles):
        request_runs(db, svc, pid)
        db.commit()
        process(session_factory, WorkProjection())
        process(session_factory, RunLauncher(svc))
        process(session_factory, WorkProjection())
        runs.tick()
        process(session_factory, WorkProjection())
        reviews.tick()
        process(session_factory, WorkProjection())
        status = next(iter(svc.project(db, pid).tasks.values())).status
        trail.append(status)
        if status in ("done", "blocked", "failed", "cancelled"):
            break
    return trail
