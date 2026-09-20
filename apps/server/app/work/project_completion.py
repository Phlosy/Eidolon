"""Project completion derived from domain tasks, shared by live work and recovery."""

from dataclasses import dataclass

from sqlalchemy import select

from app.core.request_context import get_request_identity
from app.events.bus import bus
from app.models.enums import MilestoneStatus, ProjectStatus
from app.models.event import Event
from app.repositories import project as project_repo
from app.work import contracts as C
from app.work import dispatch, work_defaults

COMPLETION_EVENTS = ("project.delivery_ready", "project.completed")
ACTIVE_STATUSES = {ProjectStatus.in_progress.value, ProjectStatus.in_review.value}
REPAIRABLE = {"needs_completion", "needs_notifications"}


@dataclass(frozen=True)
class CompletionObservation:
    project_id: int
    outcome: str
    status: str | None


def _missing_events(db, project):
    existing = set(
        db.scalars(
            select(Event.type).where(
                Event.company_id == project.company_id,
                Event.project_id == project.id,
                Event.type.in_(COMPLETION_EVENTS),
            )
        )
    )
    return tuple(kind for kind in COMPLETION_EVENTS if kind not in existing)


def observe(db, project, *, state=None):
    identity = get_request_identity()
    if identity is not None and identity.company_id != project.company_id:
        return CompletionObservation(project.id, "outside_scope", None)
    if project.status not in ACTIVE_STATUSES | {ProjectStatus.completed.value}:
        outcome = "ineligible_project_status"
    else:
        if state is None:
            state = dispatch.project_runtime_state(db, project.id)
        if state.invalid_graph:
            outcome = "invalid_graph"
        elif not state.all_tasks_done:
            outcome = "tasks_incomplete"
        elif project.status in ACTIVE_STATUSES:
            outcome = "needs_completion"
        elif _missing_events(db, project):
            outcome = "needs_notifications"
        else:
            outcome = "already_recorded"
    return CompletionObservation(project.id, outcome, project.status)


def reconcile(db, project, *, state=None):
    """Caller owns the short writer transaction; historical completion only fills events."""
    observed = observe(db, project, state=state)
    if observed.outcome not in REPAIRABLE:
        return observed
    if observed.outcome == "needs_completion":
        project.status = ProjectStatus.completed.value
        for milestone in project_repo.list_milestones(db, project.id):
            milestone.status = MilestoneStatus.completed.value
        work_defaults.promote_after_project_completion(db, project.company_id, commit=False)
    for kind in _missing_events(db, project):
        assert kind in C.FACT_EVENTS, f"unregistered project fact: {kind}"
        bus.publish(
            kind,
            {"id": project.id, "name": project.name},
            company_id=project.company_id,
            project_id=project.id,
            db=db,
        )
    return CompletionObservation(
        project.id,
        "completed" if observed.outcome == "needs_completion" else "notified",
        project.status,
    )
