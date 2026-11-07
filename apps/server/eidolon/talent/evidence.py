"""Evidence: what a fact says about a person's work. Derived, never written.

No new fact kinds: evidence is read off the facts the other layers already record
(reviews, failures, deliveries, artifacts, run outcomes, signed decisions). Each
piece names its source fact, so "why is this person rated so" is one hop away.

Signals are coarse on purpose (+1 / 0 / −1 with a weight): the point is a
reviewable trail, not a scoring model. Humans get no evidence here: the Owner's
acts are decisions, not labour the ledger measures.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.identity import IdentityError, IdentityService
from eidolon.institution.decisions import SIGNED
from eidolon.kernel.facts import ActorKind, Fact
from eidolon.kernel.store import FactStore
from eidolon.work import facts as WF
from eidolon.work.service import WorkService

TASK_PASSED = "task_passed"
TASK_REWORK = "task_rework"
TASK_ESCALATED = "task_escalated"
TASK_FAILED = "task_failed"
REVIEW_GIVEN = "review_given"
PROJECT_DELIVERED = "project_delivered"
ARTIFACT_PRODUCED = "artifact_produced"
RUN_COMPLETED = "run_completed"
RUN_FAILED = "run_failed"
RUN_BUDGET_EXCEEDED = "run_budget_exceeded"
DECISION_SIGNED = "decision_signed"

# kind -> (signal, weight)
SIGNALS: dict[str, tuple[int, float]] = {
    TASK_PASSED: (1, 1.0),
    TASK_REWORK: (-1, 0.5),
    TASK_ESCALATED: (0, 0.25),
    TASK_FAILED: (-1, 0.5),
    REVIEW_GIVEN: (0, 0.25),
    PROJECT_DELIVERED: (1, 1.0),
    ARTIFACT_PRODUCED: (0, 0.1),
    RUN_COMPLETED: (0, 0.1),
    RUN_FAILED: (-1, 0.25),
    RUN_BUDGET_EXCEEDED: (-1, 0.25),
    DECISION_SIGNED: (0, 0.25),
}

SOURCE_KINDS = frozenset(
    {
        WF.TASK_REVIEWED,
        WF.TASK_FAILED,
        WF.PROJECT_DELIVERED,
        WF.ARTIFACT_PRODUCED,
        R.RUN_COMPLETED,
        R.RUN_FAILED,
        R.RUN_BUDGET_EXCEEDED,
        SIGNED,
    }
)
GENERAL = "general"  # area when no role can be found


@dataclass(frozen=True, slots=True)
class Evidence:
    fact_id: UUID
    person_id: str
    company_id: UUID
    area: str
    kind: str
    signal: int
    weight: float
    task_id: UUID | None
    project_id: UUID | None
    run_id: UUID | None
    occurred_at: datetime
    seq: int


class EvidenceSource:
    """Turns one fact into zero or more pieces of evidence. The lookups it needs
    (who held the task, which role someone had) are folds of facts, so the result is
    the same on rebuild as it was the first time."""

    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()
        self.work = WorkService(self.store)
        self.identity = IdentityService(self.store)

    def derive(self, db: Session, fact: Fact) -> list[Evidence]:
        if fact.kind not in SOURCE_KINDS:
            return []
        handler = {
            WF.TASK_REVIEWED: self._task_reviewed,
            WF.TASK_FAILED: self._task_failed,
            WF.PROJECT_DELIVERED: self._project_delivered,
            WF.ARTIFACT_PRODUCED: self._artifact_produced,
            R.RUN_COMPLETED: self._run_ended,
            R.RUN_FAILED: self._run_ended,
            R.RUN_BUDGET_EXCEEDED: self._run_ended,
            SIGNED: self._decision_signed,
        }[fact.kind]
        return [e for e in handler(db, fact) if e is not None]

    # -- per kind -------------------------------------------------------------------

    def _task_reviewed(self, db, fact):
        p = fact.payload
        project_id, task_id = UUID(p["project_id"]), UUID(p["task_id"])
        task = self._task(db, project_id, task_id)
        assignee = task.assignee if task else None
        kind = {"pass": TASK_PASSED, "rework": TASK_REWORK, "escalate": TASK_ESCALATED}.get(
            p["verdict"]
        )
        out = []
        if assignee and kind:
            out.append(
                self._piece(
                    db, fact, assignee, kind, area=task.role, task_id=task_id, project_id=project_id
                )
            )
        reviewer = p.get("reviewer")
        if reviewer and reviewer != assignee:
            out.append(
                self._piece(
                    db, fact, reviewer, REVIEW_GIVEN, task_id=task_id, project_id=project_id
                )
            )
        return out

    def _task_failed(self, db, fact):
        p = fact.payload
        project_id, task_id = UUID(p["project_id"]), UUID(p["task_id"])
        task = self._task(db, project_id, task_id)
        if task is None or not task.assignee:
            return []
        return [
            self._piece(
                db,
                fact,
                task.assignee,
                TASK_FAILED,
                area=task.role,
                task_id=task_id,
                project_id=project_id,
                run_id=_uuid(p.get("run_id")),
            )
        ]

    def _project_delivered(self, db, fact):
        project_id = UUID(fact.payload["project_id"])
        state = self.work.project(db, project_id)
        if not state.manager:
            return []
        return [self._piece(db, fact, state.manager, PROJECT_DELIVERED, project_id=project_id)]

    def _artifact_produced(self, db, fact):
        if fact.actor.kind is not ActorKind.agent:
            return []
        p = fact.payload
        project_id, task_id = UUID(p["project_id"]), UUID(p["task_id"])
        task = self._task(db, project_id, task_id)
        return [
            self._piece(
                db,
                fact,
                fact.actor.id,
                ARTIFACT_PRODUCED,
                area=task.role if task else None,
                task_id=task_id,
                project_id=project_id,
                run_id=_uuid(p.get("run_id")),
            )
        ]

    def _run_ended(self, db, fact):
        run_id = _uuid(fact.payload.get("run_id")) or fact.correlation_id
        started = self.store.by_correlation(db, run_id, kinds=[R.RUN_STARTED])
        if not started:
            return []
        s = started[0].payload
        kind = {
            R.RUN_COMPLETED: RUN_COMPLETED,
            R.RUN_FAILED: RUN_FAILED,
            R.RUN_BUDGET_EXCEEDED: RUN_BUDGET_EXCEEDED,
        }[fact.kind]
        task_id = _uuid(s.get("task_id"))
        area = None
        project_id = None
        if task_id is not None:
            cause = started[0].causation_id
            cause_fact = self.store.get(db, cause) if cause else None
            if cause_fact is not None and cause_fact.kind.startswith("work."):
                project_id = cause_fact.correlation_id
                task = self._task(db, project_id, task_id)
                area = task.role if task else None
        return [
            self._piece(
                db,
                fact,
                s["person_id"],
                kind,
                area=area,
                task_id=task_id,
                project_id=project_id,
                run_id=run_id,
            )
        ]

    def _decision_signed(self, db, fact):
        if fact.actor.kind is not ActorKind.agent:
            return []
        p = fact.payload
        if p.get("gate") == "task.review":
            return []  # the verdict fact (work.task.reviewed) is the reviewer's evidence
        return [self._piece(db, fact, p["person_id"], DECISION_SIGNED, area=p.get("role"))]

    # -- helpers --------------------------------------------------------------------

    def _piece(
        self,
        db,
        fact: Fact,
        person_id: str,
        kind: str,
        *,
        area: str | None = None,
        task_id: UUID | None = None,
        project_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> Evidence | None:
        if not self._is_agent(db, person_id):
            return None
        signal, weight = SIGNALS[kind]
        return Evidence(
            fact.id,
            person_id,
            fact.company_id,
            area or self._role(db, fact.company_id, person_id) or GENERAL,
            kind,
            signal,
            weight,
            task_id,
            project_id,
            run_id,
            fact.created_at,
            fact.seq,
        )

    def _task(self, db, project_id: UUID, task_id: UUID):
        try:
            return self.work.project(db, project_id).tasks.get(task_id)
        except Exception:  # noqa: BLE001 — a project we cannot fold yields no evidence
            return None

    def _role(self, db, company_id: UUID, person_id: str) -> str | None:
        try:
            return self.identity.company(db, company_id).role_of(person_id)
        except IdentityError:
            return None

    def _is_agent(self, db, person_id: str) -> bool:
        person = self.identity.person(db, person_id)
        # unknown persons (tests without identity facts) count as agents; humans never
        return person is None or person.kind == "agent"


def _uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
