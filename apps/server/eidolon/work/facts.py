"""Work facts (v2-architecture §6): Mission → Project → Task graph → Review → Artifact.

Correlation: a mission's facts correlate on ``mission_id``; everything inside a
project (project, tasks, reviews, artifacts) correlates on ``project_id``, so one
``by_correlation`` read gives a strongly consistent ``ProjectState``.

Status machines (the only legal transitions; anything else is a WorkError):

    mission   proposed → accepted | rejected
    project   planning → active → delivered | cancelled
    task      todo → in_progress → in_review → done
              in_review → todo (rework, counted) | blocked (escalate)
              in_progress → failed → todo (reopen, a decision) ; blocked → todo (unblock)
              any non-terminal → cancelled (a decision)

Management transitions (assign, review verdicts, cancel, accept, approve plan,
deliver, reopen) carry the ``decision_id`` of the gate that authorised them —
the system records, it never decides (R2, R4, R10, RV1, RV7).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from eidolon.kernel.facts import Actor, NewFact

# -- kinds -------------------------------------------------------------------------------

MISSION_CREATED = "work.mission.created"
MISSION_ACCEPTED = "work.mission.accepted"
MISSION_REJECTED = "work.mission.rejected"
PROJECT_CREATED = "work.project.created"
PROJECT_PLAN_APPROVED = "work.project.plan_approved"
PROJECT_DELIVERED = "work.project.delivered"
PROJECT_CANCELLED = "work.project.cancelled"
TASK_CREATED = "work.task.created"
TASK_ASSIGNED = "work.task.assigned"
TASK_STARTED = "work.task.started"
TASK_SUBMITTED = "work.task.submitted"
TASK_REVIEWED = "work.task.reviewed"
TASK_BLOCKED = "work.task.blocked"
TASK_UNBLOCKED = "work.task.unblocked"
TASK_FAILED = "work.task.failed"
TASK_REOPENED = "work.task.reopened"
TASK_CANCELLED = "work.task.cancelled"
ARTIFACT_PRODUCED = "work.artifact.produced"
ARTIFACT_CONSUMED = "work.artifact.consumed"
RUN_REQUESTED = "work.run.requested"  # a dispatchable task is queued for its assignee

MISSION_KINDS = frozenset({MISSION_CREATED, MISSION_ACCEPTED, MISSION_REJECTED})
PROJECT_KINDS = frozenset(
    {
        PROJECT_CREATED,
        PROJECT_PLAN_APPROVED,
        PROJECT_DELIVERED,
        PROJECT_CANCELLED,
        TASK_CREATED,
        TASK_ASSIGNED,
        TASK_STARTED,
        TASK_SUBMITTED,
        TASK_REVIEWED,
        TASK_BLOCKED,
        TASK_UNBLOCKED,
        TASK_FAILED,
        TASK_REOPENED,
        TASK_CANCELLED,
        ARTIFACT_PRODUCED,
        ARTIFACT_CONSUMED,
        RUN_REQUESTED,
    }
)
KINDS = MISSION_KINDS | PROJECT_KINDS

TASK_STATUSES = ("todo", "in_progress", "in_review", "done", "blocked", "failed", "cancelled")
TASK_TERMINAL = frozenset({"done", "cancelled"})
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    TASK_STARTED: frozenset({"todo"}),
    TASK_SUBMITTED: frozenset({"in_progress"}),
    TASK_REVIEWED: frozenset({"in_review"}),
    TASK_BLOCKED: frozenset({"todo", "in_progress", "in_review"}),
    TASK_UNBLOCKED: frozenset({"blocked"}),
    TASK_FAILED: frozenset({"in_progress"}),
    TASK_REOPENED: frozenset({"failed", "blocked"}),
    TASK_CANCELLED: frozenset({"todo", "in_progress", "in_review", "blocked", "failed"}),
    TASK_ASSIGNED: frozenset({"todo", "blocked", "failed"}),
}
REVIEW_VERDICTS = ("pass", "rework", "escalate")
DISPOSITIONS = ("reopen", "reassign", "cancel", "split")  # R7-d: what to do with a failure
TRIAGE_STANDS_FOR = {
    "task.assign": frozenset({"reopen", "reassign"}),
    "task.cancel": frozenset({"cancel", "split"}),
}
VERDICT_STATUS = {"pass": "done", "rework": "todo", "escalate": "blocked"}


class WorkError(ValueError):
    pass


def _new(
    company_id, actor, kind, payload, key, *, correlation_id, causation_id=None, decision_id=None
):
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=kind,
        payload=payload,
        idempotency_key=key,
        correlation_id=correlation_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


# -- mission -----------------------------------------------------------------------------


def mission_created(
    company_id: UUID,
    actor: Actor,
    mission_id: UUID,
    *,
    title: str,
    goal: str,
    success_criteria: Sequence[str] = (),
    constraints: Sequence[str] = (),
    budget: int | None = None,
    deadline: str | None = None,
) -> NewFact:
    if not title.strip() or not goal.strip():
        raise WorkError("a mission needs a title and a goal")
    return _new(
        company_id,
        actor,
        MISSION_CREATED,
        {
            "mission_id": str(mission_id),
            "title": title,
            "goal": goal,
            "success_criteria": list(success_criteria),
            "constraints": list(constraints),
            "budget": budget,
            "deadline": deadline,
        },
        f"mission:{mission_id}:created",
        correlation_id=mission_id,
    )


def mission_decided(
    company_id,
    actor,
    mission_id: UUID,
    *,
    accepted: bool,
    decision_id: UUID,
    reason: str | None,
    causation_id=None,
) -> NewFact:
    kind = MISSION_ACCEPTED if accepted else MISSION_REJECTED
    return _new(
        company_id,
        actor,
        kind,
        {"mission_id": str(mission_id), "decision_id": str(decision_id), "reason": reason},
        f"mission:{mission_id}:{'accepted' if accepted else 'rejected'}",
        correlation_id=mission_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


# -- project -------------------------------------------------------------------------------


def project_created(
    company_id,
    actor,
    project_id: UUID,
    *,
    mission_id: UUID | None,
    title: str,
    budget: int,
    manager: str | None,
) -> NewFact:
    if not title.strip():
        raise WorkError("a project needs a title")
    if budget <= 0:
        raise WorkError("a project budget must be positive")
    return _new(
        company_id,
        actor,
        PROJECT_CREATED,
        {
            "project_id": str(project_id),
            "mission_id": str(mission_id) if mission_id else None,
            "title": title,
            "budget": budget,
            "manager": manager,
        },
        f"project:{project_id}:created",
        correlation_id=project_id,
    )


def project_plan_approved(
    company_id,
    actor,
    project_id: UUID,
    *,
    decision_id: UUID,
    task_ids: Sequence[UUID],
    causation_id=None,
) -> NewFact:
    return _new(
        company_id,
        actor,
        PROJECT_PLAN_APPROVED,
        {
            "project_id": str(project_id),
            "decision_id": str(decision_id),
            "task_ids": [str(t) for t in task_ids],
        },
        f"project:{project_id}:plan_approved:{decision_id}",
        correlation_id=project_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


def project_delivered(
    company_id, actor, project_id: UUID, *, decision_id: UUID, causation_id=None
) -> NewFact:
    return _new(
        company_id,
        actor,
        PROJECT_DELIVERED,
        {"project_id": str(project_id), "decision_id": str(decision_id)},
        f"project:{project_id}:delivered",
        correlation_id=project_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


def project_cancelled(
    company_id, actor, project_id: UUID, *, decision_id: UUID, reason: str, causation_id=None
) -> NewFact:
    return _new(
        company_id,
        actor,
        PROJECT_CANCELLED,
        {"project_id": str(project_id), "decision_id": str(decision_id), "reason": reason},
        f"project:{project_id}:cancelled",
        correlation_id=project_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


# -- task ----------------------------------------------------------------------------------


def task_created(
    company_id,
    actor,
    project_id: UUID,
    task_id: UUID,
    *,
    title: str,
    description: str,
    acceptance_criteria: Sequence[str] = (),
    depends_on: Sequence[UUID] = (),
    inputs: Sequence[UUID] = (),
    kind: str = "build",
    role: str | None = None,
    causation_id=None,
) -> NewFact:
    if not title.strip():
        raise WorkError("a task needs a title")
    if task_id in depends_on or task_id in inputs:
        raise WorkError("a task cannot depend on itself")
    if len(set(depends_on)) != len(depends_on):
        raise WorkError("duplicate dependencies")
    return _new(
        company_id,
        actor,
        TASK_CREATED,
        {
            "project_id": str(project_id),
            "task_id": str(task_id),
            "title": title,
            "description": description,
            "acceptance_criteria": list(acceptance_criteria),
            "depends_on": [str(d) for d in depends_on],
            "inputs": [str(i) for i in inputs],
            "kind": kind,
            "role": role,
        },
        f"task:{task_id}:created",
        correlation_id=project_id,
        causation_id=causation_id,
    )


def task_event(
    company_id,
    actor,
    project_id: UUID,
    task_id: UUID,
    kind: str,
    payload: Mapping[str, Any],
    key_tail: str,
    *,
    decision_id: UUID | None = None,
    causation_id=None,
) -> NewFact:
    return _new(
        company_id,
        actor,
        kind,
        {"project_id": str(project_id), "task_id": str(task_id), **payload},
        f"task:{task_id}:{key_tail}",
        correlation_id=project_id,
        causation_id=causation_id,
        decision_id=decision_id,
    )


# -- artifact --------------------------------------------------------------------------------


def artifact_produced(
    company_id,
    actor,
    project_id: UUID,
    artifact_id: UUID,
    *,
    task_id: UUID,
    run_id: UUID | None,
    path: str,
    blob_ref: str,
    size: int,
    media_type: str = "text/plain",
    causation_id=None,
) -> NewFact:
    if not path or not blob_ref:
        raise WorkError("an artifact needs a path and a blob_ref")
    return _new(
        company_id,
        actor,
        ARTIFACT_PRODUCED,
        {
            "project_id": str(project_id),
            "artifact_id": str(artifact_id),
            "task_id": str(task_id),  # H2: attribution is written, never guessed
            "run_id": str(run_id) if run_id else None,
            "path": path,
            "blob_ref": blob_ref,  # H1: content lives in the blob store, nowhere else
            "size": size,
            "media_type": media_type,
        },
        f"artifact:{artifact_id}:produced",
        correlation_id=project_id,
        causation_id=causation_id,
    )


def artifact_consumed(
    company_id,
    actor,
    project_id: UUID,
    artifact_id: UUID,
    *,
    by_task_id: UUID,
    run_id: UUID | None,
    causation_id=None,
) -> NewFact:
    return _new(
        company_id,
        actor,
        ARTIFACT_CONSUMED,
        {
            "project_id": str(project_id),
            "artifact_id": str(artifact_id),
            "task_id": str(by_task_id),
            "run_id": str(run_id) if run_id else None,  # H3: the using session
        },
        f"artifact:{artifact_id}:consumed:{by_task_id}:{run_id or '-'}",
        correlation_id=project_id,
        causation_id=causation_id,
    )


# -- dispatch ----------------------------------------------------------------------------------


def run_requested(
    company_id,
    actor,
    project_id: UUID,
    task_id: UUID,
    *,
    run_id: UUID,
    assignee: str,
    role: str | None,
    state_fact_id: UUID | None,
    causation_id=None,
) -> NewFact:
    """Queue a run for a dispatchable task. The key includes the task's last fact id,
    so repeated dispatch ticks over an unchanged task are the same request (R1)."""
    return _new(
        company_id,
        actor,
        RUN_REQUESTED,
        {
            "project_id": str(project_id),
            "task_id": str(task_id),
            "run_id": str(run_id),
            "assignee": assignee,
            "role": role,
        },
        f"task:{task_id}:run_requested:{state_fact_id}",
        correlation_id=project_id,
        causation_id=causation_id,
    )
