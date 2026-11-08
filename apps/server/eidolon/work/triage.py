"""Dispositions for failed work (R7-d): what an approved ``task.triage`` decision does.

A task that failed, got blocked or ran out of budget is not a red badge for the user
to puzzle over: a manager proposes what to do — reopen it, hand it to someone else,
cancel it, or split it into smaller tasks — as a decision with a brief, and once the
institution's decider approves, this module enacts it mechanically. The proposal is
validated here too, so the agent layer and the API share one notion of a well-formed
disposition.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from eidolon.institution import DecisionState
from eidolon.kernel.facts import Actor
from eidolon.work import facts as F
from eidolon.work.service import WorkService
from eidolon.work.state import ProjectState, TaskState

MAX_SPLIT = 4


def triage_decision_id(task_id: UUID, failure_fact_id: UUID | None, attempt: int = 0) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:task:{task_id}:triage:{failure_fact_id}:{attempt}")


def dependents(state: ProjectState, task_id: UUID) -> list[TaskState]:
    return [t for t in state.tasks.values() if task_id in t.depends_on or task_id in t.inputs]


def validate_disposition(
    state: ProjectState,
    task: TaskState,
    answer: Mapping[str, Any],
    *,
    candidates: Mapping[str, str],  # person_id -> role, people who may take the task
) -> dict[str, Any]:
    """Coerce a manager's answer into a disposition subject or raise WorkError."""
    disposition = str(answer.get("disposition") or "").strip().lower()
    if disposition not in F.DISPOSITIONS:
        raise F.WorkError(f"disposition must be one of {F.DISPOSITIONS}, got {disposition!r}")
    reason = " ".join(str(answer.get("reason") or "").split())
    if not reason:
        raise F.WorkError("a disposition needs a reason")
    subject: dict[str, Any] = {
        "task_id": str(task.task_id),
        "project_id": str(state.project_id),
        "disposition": disposition,
        "reason": reason[:1000],
        "previous_assignee": task.assignee,
    }
    if disposition == "reassign":
        assignee = str(answer.get("assignee") or "").strip()
        if assignee not in candidates:
            raise F.WorkError(f"reassign needs an assignee from {sorted(candidates)}")
        if assignee == task.assignee:
            raise F.WorkError("reassign to the same person is a reopen")
        subject["assignee"] = assignee
    if disposition == "split":
        if dependents(state, task.task_id):
            raise F.WorkError("cannot split a task other tasks depend on; reopen or reassign it")
        new_tasks = answer.get("new_tasks")
        if not isinstance(new_tasks, list) or not 2 <= len(new_tasks) <= MAX_SPLIT:
            raise F.WorkError(f"split needs 2..{MAX_SPLIT} new tasks")
        subject["new_tasks"] = [
            {
                "title": str(t.get("title") or f"part {i + 1}")[:300],
                "description": str(t.get("description") or ""),
                "acceptance_criteria": [str(c) for c in t.get("acceptance_criteria") or ()][:10],
                "role": str(t.get("role") or task.role or "engineer"),
            }
            for i, t in enumerate(new_tasks)
            if isinstance(t, Mapping)
        ]
    return subject


def apply_triage(db: Session, svc: WorkService, decision: DecisionState, actor: Actor) -> str:
    """Enact an approved disposition. Returns what happened (a fact kind-ish label)."""
    s = decision.subject
    if decision.gate != "task.triage" or decision.status != "approved":
        raise F.WorkError(f"decision {decision.decision_id} is not an approved triage")
    project_id, task_id = UUID(str(s["project_id"])), UUID(str(s["task_id"]))
    state = svc.project(db, project_id)
    task = state.task(task_id)
    disposition = s.get("disposition")
    reason = str(s.get("reason") or "triage")
    did = decision.decision_id
    if disposition == "reopen":
        if task.status in ("failed", "blocked"):
            svc.reopen_task(db, project_id, task_id, actor, decision_id=did, reason=reason)
        return "task.reopened"
    if disposition == "reassign":
        if task.status in ("failed", "blocked"):
            svc.reopen_task(db, project_id, task_id, actor, decision_id=did, reason=reason)
        if svc.project(db, project_id).task(task_id).assignee != s["assignee"]:
            svc.assign_task(db, project_id, task_id, actor, assignee=s["assignee"], decision_id=did)
        return "task.reassigned"
    if disposition == "cancel":
        if task.status != "cancelled":
            svc.cancel_task(db, project_id, task_id, actor, decision_id=did, reason=reason)
        return "task.cancelled"
    if disposition == "split":
        if task.status != "cancelled":
            svc.cancel_task(db, project_id, task_id, actor, decision_id=did, reason=reason)
        for i, part in enumerate(s.get("new_tasks") or ()):
            new_id = uuid5(NAMESPACE_URL, f"eidolon:triage:{did}:part:{i + 1}")
            if new_id in state.tasks:
                continue
            svc.create_task(
                db,
                project_id,
                actor,
                task_id=new_id,
                title=part["title"],
                description=part.get("description") or "",
                acceptance_criteria=list(part.get("acceptance_criteria") or ()),
                depends_on=list(task.depends_on),
                inputs=list(task.inputs),
                role=part.get("role") or task.role or "engineer",
            )
        return "task.split"
    raise F.WorkError(f"unknown disposition {disposition!r}")


__all__ = ["MAX_SPLIT", "apply_triage", "dependents", "triage_decision_id", "validate_disposition"]
