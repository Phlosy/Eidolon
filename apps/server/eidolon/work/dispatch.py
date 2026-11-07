"""Dispatch (§6): turn *dispatchable* tasks into run requests. Nothing here chooses
anyone: a task is dispatchable only when it is READY and an approved decision
already named its assignee (R1–R4, R12). This module emits facts; executing a run
is the agent layer's job (``agent.scheduler``)."""

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from eidolon.kernel.facts import Actor, Fact
from eidolon.work import facts as F
from eidolon.work import graph
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState


def dispatchable_tasks(state: ProjectState):
    by_id = {n.task_id: n for n in state.nodes()}
    return [
        t
        for t in state.tasks.values()
        if t.assignee and t.dispatchable and graph.is_ready(by_id, t.task_id)
    ]


def request_runs(
    db: Session, svc: WorkService, project_id: UUID, *, actor: Actor | None = None
) -> list[Fact]:
    """One ``work.run.requested`` per dispatchable task of an active project.
    Idempotent per task state: calling it again before anything changed returns the
    same facts."""
    state = svc.project(db, project_id)
    if state.status != "active":
        return []
    actor = actor or Actor.system()
    out: list[Fact] = []
    for task in dispatchable_tasks(state):
        fact = F.run_requested(
            state.company_id,
            actor,
            project_id,
            task.task_id,
            run_id=uuid4(),
            assignee=task.assignee,
            role=task.role,
            state_fact_id=task.last_fact_id,
            causation_id=task.last_fact_id,
        )
        out.append(svc.write(db, fact))
    return out
