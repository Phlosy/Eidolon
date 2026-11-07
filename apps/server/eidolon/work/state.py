"""Strongly consistent views folded from facts: one project's whole state from
``by_correlation(project_id)``. Service functions validate against this, not
against the (eventually consistent) projection tables."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from eidolon.kernel.facts import Fact
from eidolon.work import facts as F
from eidolon.work.graph import Node


@dataclass
class TaskState:
    task_id: UUID
    project_id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    depends_on: tuple[UUID, ...]
    inputs: tuple[UUID, ...]
    kind: str
    role: str | None
    status: str = "todo"
    assignee: str | None = None
    rework_count: int = 0
    current_run_id: UUID | None = None
    pending_run_id: UUID | None = None  # requested, not yet started
    runs: list[UUID] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_fact_id: UUID | None = None

    def node(self) -> Node:
        return Node(self.task_id, self.status, self.depends_on, self.inputs, self.assignee)

    @property
    def dispatchable(self) -> bool:
        return self.pending_run_id is None and self.current_run_id is None


@dataclass
class ArtifactState:
    artifact_id: UUID
    task_id: UUID
    run_id: UUID | None
    path: str
    blob_ref: str
    size: int
    media_type: str
    uses: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class ProjectState:
    project_id: UUID
    company_id: UUID
    mission_id: UUID | None
    title: str
    budget: int
    manager: str | None
    status: str = "planning"
    plan_decision_id: UUID | None = None
    tasks: dict[UUID, TaskState] = field(default_factory=dict)
    artifacts: dict[UUID, ArtifactState] = field(default_factory=dict)
    last_fact_id: UUID | None = None
    created_at: datetime | None = None

    def nodes(self) -> list[Node]:
        return [t.node() for t in self.tasks.values()]

    def task(self, task_id: UUID) -> TaskState:
        try:
            return self.tasks[task_id]
        except KeyError:
            raise F.WorkError(f"no task {task_id} in project {self.project_id}") from None

    @classmethod
    def from_facts(cls, project_id: UUID, facts: list[Fact]) -> "ProjectState":
        state: ProjectState | None = None
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.kind not in F.PROJECT_KINDS or fact.payload.get("project_id") != str(
                project_id
            ):
                continue
            p = fact.payload
            if fact.kind == F.PROJECT_CREATED:
                state = cls(
                    project_id=project_id,
                    company_id=fact.company_id,
                    mission_id=UUID(p["mission_id"]) if p.get("mission_id") else None,
                    title=p["title"],
                    budget=p["budget"],
                    manager=p.get("manager"),
                    created_at=fact.created_at,
                )
            elif state is None:
                raise F.WorkError(f"project {project_id}: {fact.kind} before {F.PROJECT_CREATED}")
            elif fact.kind == F.PROJECT_PLAN_APPROVED:
                state.status = "active"
                state.plan_decision_id = UUID(p["decision_id"])
            elif fact.kind == F.PROJECT_DELIVERED:
                state.status = "delivered"
            elif fact.kind == F.PROJECT_CANCELLED:
                state.status = "cancelled"
            elif fact.kind == F.TASK_CREATED:
                tid = UUID(p["task_id"])
                state.tasks[tid] = TaskState(
                    task_id=tid,
                    project_id=project_id,
                    title=p["title"],
                    description=p["description"],
                    acceptance_criteria=list(p.get("acceptance_criteria") or []),
                    depends_on=tuple(UUID(d) for d in p.get("depends_on") or []),
                    inputs=tuple(UUID(i) for i in p.get("inputs") or []),
                    kind=p.get("kind", "build"),
                    role=p.get("role"),
                    created_at=fact.created_at,
                    updated_at=fact.created_at,
                    last_fact_id=fact.id,
                )
            elif fact.kind == F.ARTIFACT_PRODUCED:
                aid = UUID(p["artifact_id"])
                state.artifacts[aid] = ArtifactState(
                    artifact_id=aid,
                    task_id=UUID(p["task_id"]),
                    run_id=UUID(p["run_id"]) if p.get("run_id") else None,
                    path=p["path"],
                    blob_ref=p["blob_ref"],
                    size=p["size"],
                    media_type=p.get("media_type", "text/plain"),
                    created_at=fact.created_at,
                )
            elif fact.kind == F.ARTIFACT_CONSUMED:
                art = state.artifacts.get(UUID(p["artifact_id"]))
                if art is not None:
                    art.uses.append(
                        {
                            "task_id": p["task_id"],
                            "run_id": p.get("run_id"),
                            "at": fact.created_at.isoformat(),
                        }
                    )
            else:
                task = state.tasks.get(UUID(p["task_id"]))
                if task is None:
                    continue
                _apply_task_event(task, fact)
            state.last_fact_id = fact.id
        if state is None:
            raise F.WorkError(f"project {project_id}: no {F.PROJECT_CREATED} fact")
        return state


def _apply_task_event(task: TaskState, fact: Fact) -> None:
    p = fact.payload
    kind = fact.kind
    if kind == F.TASK_ASSIGNED:
        task.assignee = p["assignee"]
    elif kind == F.RUN_REQUESTED:
        task.pending_run_id = UUID(p["run_id"])
    elif kind == F.TASK_STARTED:
        task.status = "in_progress"
        task.current_run_id = UUID(p["run_id"])
        task.pending_run_id = None
        task.runs.append(UUID(p["run_id"]))
    elif kind == F.TASK_SUBMITTED:
        task.status = "in_review"
    elif kind == F.TASK_REVIEWED:
        task.reviews.append(
            {
                "decision_id": p["decision_id"],
                "verdict": p["verdict"],
                "reviewer": p["reviewer"],
                "reason": p.get("reason"),
            }
        )
        task.status = F.VERDICT_STATUS[p["verdict"]]
        if p["verdict"] == "rework":
            task.rework_count += 1
            task.current_run_id = None
        elif p["verdict"] == "pass":
            task.current_run_id = None
    elif kind == F.TASK_BLOCKED:
        task.status = "blocked"
    elif kind == F.TASK_UNBLOCKED:
        task.status = "todo"
    elif kind == F.TASK_FAILED:
        task.status = "failed"
        task.current_run_id = None
    elif kind == F.TASK_REOPENED:
        task.status = "todo"
    elif kind == F.TASK_CANCELLED:
        task.status = "cancelled"
        task.current_run_id = None
        task.pending_run_id = None
    task.updated_at = fact.created_at
    task.last_fact_id = fact.id


@dataclass
class MissionState:
    mission_id: UUID
    company_id: UUID
    title: str
    goal: str
    success_criteria: list[str]
    constraints: list[str]
    budget: int | None
    deadline: str | None
    status: str = "proposed"
    decision_id: UUID | None = None
    last_fact_id: UUID | None = None

    @classmethod
    def from_facts(cls, mission_id: UUID, facts: list[Fact]) -> "MissionState":
        state: MissionState | None = None
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.kind not in F.MISSION_KINDS or fact.payload.get("mission_id") != str(
                mission_id
            ):
                continue
            p = fact.payload
            if fact.kind == F.MISSION_CREATED:
                state = cls(
                    mission_id,
                    fact.company_id,
                    p["title"],
                    p["goal"],
                    list(p.get("success_criteria") or []),
                    list(p.get("constraints") or []),
                    p.get("budget"),
                    p.get("deadline"),
                )
            elif state is None:
                raise F.WorkError(f"mission {mission_id}: {fact.kind} before created")
            elif fact.kind == F.MISSION_ACCEPTED:
                state.status, state.decision_id = "accepted", UUID(p["decision_id"])
            elif fact.kind == F.MISSION_REJECTED:
                state.status, state.decision_id = "rejected", UUID(p["decision_id"])
            state.last_fact_id = fact.id
        if state is None:
            raise F.WorkError(f"mission {mission_id}: no created fact")
        return state
