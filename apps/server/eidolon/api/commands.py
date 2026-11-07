"""Commands: what a signed-in human may *do* through the API, each one a thin
composition of domain services. The API decides nothing — every management act
is either a human's own act (allowed by their grants) or a decision at a gate
that the domain layers validate; when a decision concludes, its consequence is
applied mechanically, the same way the manager jobs apply agent decisions.
"""

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.identity import EmploymentRow, IdentityError, IdentityService
from eidolon.institution import (
    DecisionError,
    DecisionState,
    GateContext,
    GateError,
    InstitutionError,
    adopt,
    bind,
    conclude,
    current,
    customize,
    has_authority,
    load,
    request,
    sign,
    staffing,
)
from eidolon.institution import decisions as D
from eidolon.institution.schema import SchemaError
from eidolon.institution.templates import BUILTIN
from eidolon.kernel.facts import Actor, ActorKind, Fact
from eidolon.kernel.store import FactStore
from eidolon.work import facts as WF
from eidolon.work.facts import WorkError
from eidolon.work.graph import DagError
from eidolon.work.projection import TaskRow
from eidolon.work.service import WorkService

DomainError = (
    DecisionError,
    GateError,
    InstitutionError,
    IdentityError,
    WorkError,
    DagError,
    SchemaError,
    ValueError,
)


class Forbidden(PermissionError):
    """The signed-in person lacks the grant for this act."""


class Conflict(ValueError):
    """The act is well-formed but the domain refuses it in the current state."""


@dataclass(frozen=True, slots=True)
class Person:
    """The signed-in human as they stand in one company."""

    company_id: UUID
    person_id: str
    role: str

    @property
    def actor(self) -> Actor:
        return Actor(ActorKind.human, self.person_id)


def idem(user_id: UUID, key: str | None) -> UUID:
    """A client ``Idempotency-Key`` becomes the entity id, so a retried request
    hits K2 and returns the same thing; no key means a fresh id."""
    return uuid5(NAMESPACE_URL, f"eidolon:idem:{user_id}:{key}") if key else uuid4()


class Commands:
    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()
        self.identity = IdentityService(self.store)
        self.work = WorkService(self.store)

    # -- authority ----------------------------------------------------------------------

    def template(self, db: Session, company_id: UUID):
        state = current(db, company_id, store=self.store)
        return state.template if state else None

    def require(self, db: Session, who: Person, grant: str) -> None:
        template = self.template(db, who.company_id)
        if template is None:
            raise Conflict("this company has adopted no institution yet")
        if not has_authority(template, who.role, grant):
            raise Forbidden(f"role {who.role!r} has no authority {grant!r}")

    # -- companies & institution --------------------------------------------------------

    def create_company(
        self,
        db: Session,
        *,
        owner_user_id: UUID,
        owner_person_id: str,
        name: str,
        slug: str,
        currency: str = "CNY",
        template_id: str | None = "software-studio-v1",
        company_id: UUID | None = None,
    ) -> UUID:
        created, _ = self.identity.create_company(
            db,
            owner_user_id=owner_user_id,
            owner_person_id=owner_person_id,
            name=name,
            slug=slug,
            currency=currency,
            company_id=company_id,
        )
        if template_id:
            self.store.append(
                db,
                adopt(created.company_id, Actor(ActorKind.human, owner_person_id), template_id),
            )
        return created.company_id

    def adopt(self, db: Session, who: Person, template_id: str, *, owner_user_id: UUID) -> Fact:
        if template_id not in BUILTIN:
            raise Conflict(f"unknown template {template_id!r}; known: {sorted(BUILTIN)}")
        state = self.identity.company(db, who.company_id)
        if state.owner_user_id != owner_user_id:
            raise Forbidden("only the owner adopts an institution")
        return self.store.append(db, adopt(who.company_id, who.actor, template_id))

    def customize(self, db: Session, who: Person, changes: dict[str, Any], *, reason: str) -> Fact:
        self.require(db, who, "institution.customize")
        return self.store.append(
            db, customize(db, who.company_id, who.actor, changes, reason=reason)
        )

    # -- staffing -------------------------------------------------------------------------

    def hire(
        self,
        db: Session,
        who: Person,
        *,
        person_id: str,
        role: str,
        name: str | None = None,
        persona: dict[str, Any] | None = None,
        title: str | None = None,
        department_id: str | None = None,
    ) -> Fact:
        self.require(db, who, "hire")
        if self.identity.person(db, person_id) is None:
            self.identity.create_person(
                db, who.actor, person_id, name=name or person_id, kind="agent", persona=persona
            )
        return staffing.hire(
            db,
            who.company_id,
            who.actor,
            person_id=person_id,
            role=role,
            title=title,
            department_id=department_id,
        )

    def fire(self, db: Session, who: Person, *, employment_id: UUID, reason: str) -> Fact:
        self.require(db, who, "fire")
        return self.identity.end_employment(
            db, who.company_id, who.actor, employment_id=employment_id, reason=reason
        )

    # -- missions -------------------------------------------------------------------------

    def propose_mission(self, db: Session, who: Person, *, mission_id: UUID, **fields) -> Fact:
        self.require(db, who, "mission.propose")
        return self.work.write(
            db, WF.mission_created(who.company_id, who.actor, mission_id, **fields)
        )

    # -- decisions ------------------------------------------------------------------------

    def open_decision(
        self,
        db: Session,
        who: Person,
        *,
        gate: str,
        subject: dict[str, Any],
        action: dict[str, Any] | None = None,
        amount: int | None = None,
        author: str | None = None,
        decision_id: UUID | None = None,
    ) -> DecisionState:
        template = self.template(db, who.company_id)
        if template is None:
            raise Conflict("this company has adopted no institution yet")
        if not (
            has_authority(template, who.role, f"{gate}.propose")
            or has_authority(template, who.role, gate)
        ):
            raise Forbidden(f"role {who.role!r} may not raise {gate}")
        full_subject = {**(bind(action) if action else {}), **subject}
        ctx = GateContext(
            gate,
            who.company_id,
            proposer=who.person_id,
            proposer_role=who.role,
            author=author,
            amount=amount,
            subject=full_subject,
        )
        req, _ = request(template, ctx, who.actor, decision_id=decision_id)
        fact = self.store.append(db, req)
        return load(db, fact.correlation_id, store=self.store)

    def sign_decision(
        self,
        db: Session,
        who: Person,
        *,
        decision_id: UUID,
        verdict: str,
        reason: str | None,
        action: dict[str, Any] | None,
    ) -> tuple[DecisionState, str | None]:
        """Sign; conclude when the signatures settle it; apply the consequence."""
        template = self.template(db, who.company_id)
        state = load(db, decision_id, store=self.store)
        if state.company_id != who.company_id or template is None:
            raise Forbidden("this decision belongs to another company")
        if state.resolution.decider == "human" and action is None:
            action = state.subject.get("action")  # restating the bound action is the point
        self.store.append(
            db,
            sign(
                template,
                state,
                who.actor,
                role=who.role,
                verdict=verdict,
                reason=reason,
                action=action,
            ),
        )
        state = load(db, decision_id, store=self.store)
        recorded = conclude(state)
        applied = None
        if recorded is not None:
            self.store.append(db, recorded)
            state = load(db, decision_id, store=self.store)
            applied = self.apply(db, who, state)
        return state, applied

    def apply(self, db: Session, who: Person, d: DecisionState) -> str | None:
        """The mechanical consequence of a concluded decision, by gate. Gates with
        no built-in consequence (spend, purchase, knowledge, escalation) are just
        recorded; whoever acts on them cites the decision id."""
        s, actor = d.subject, who.actor
        approved = d.status == "approved"
        if d.gate == "mission.accept" and "mission_id" in s:
            self.work.decide_mission(db, UUID(s["mission_id"]), actor, decision_id=d.decision_id)
            return "mission.decided"
        if d.gate == "plan.approve" and approved and "project_id" in s:
            self.work.approve_plan(db, UUID(s["project_id"]), actor, decision_id=d.decision_id)
            return "project.plan_approved"
        if d.gate == "project.deliver" and approved and "project_id" in s:
            self.work.deliver_project(db, UUID(s["project_id"]), actor, decision_id=d.decision_id)
            return "project.delivered"
        if d.gate == "task.assign" and approved and "task_id" in s and "assignee" in s:
            task = db.get(TaskRow, UUID(s["task_id"]))
            if task is None:
                raise Conflict("task not projected yet; retry shortly")
            self.work.assign_task(
                db,
                task.project_id,
                task.task_id,
                actor,
                assignee=s["assignee"],
                decision_id=d.decision_id,
            )
            return "task.assigned"
        if d.gate == "task.cancel" and approved:
            reason = s.get("reason") or "cancelled by decision"
            if "task_id" in s:
                task = db.get(TaskRow, UUID(s["task_id"]))
                if task is None:
                    raise Conflict("task not projected yet; retry shortly")
                self.work.cancel_task(
                    db,
                    task.project_id,
                    task.task_id,
                    actor,
                    decision_id=d.decision_id,
                    reason=reason,
                )
                return "task.cancelled"
            if "project_id" in s:
                self.work.cancel_project(
                    db, UUID(s["project_id"]), actor, decision_id=d.decision_id, reason=reason
                )
                return "project.cancelled"
        if d.gate == "hire" and approved and "person_id" in s and "role" in s:
            if self.identity.person(db, s["person_id"]) is None:
                self.identity.create_person(
                    db,
                    actor,
                    s["person_id"],
                    name=s.get("name") or s["person_id"],
                    kind="agent",
                    persona=s.get("persona"),
                )
            staffing.hire(
                db,
                d.company_id,
                actor,
                person_id=s["person_id"],
                role=s["role"],
                title=s.get("title"),
                decision_id=d.decision_id,
            )
            return "employment.started"
        if d.gate == "fire" and approved and "employment_id" in s:
            self.identity.end_employment(
                db,
                d.company_id,
                actor,
                employment_id=UUID(s["employment_id"]),
                reason=s.get("reason") or "ended by decision",
                decision_id=d.decision_id,
            )
            return "employment.ended"
        return None

    def deliver_project(
        self, db: Session, who: Person, *, project_id: UUID, reason: str | None
    ) -> tuple[DecisionState, str | None]:
        """V6: the Owner accepts a delivery — open the human gate bound to the exact
        action, sign it, and (when that settles it) deliver."""
        project = self.work.project(db, project_id)
        if project.company_id != who.company_id:
            raise Forbidden("this project belongs to another company")
        if project.status != "active":
            raise Conflict(f"project {project_id} is {project.status}, not active")
        action = {"kind": "project.deliver", "project_id": str(project_id)}
        decision_id = uuid5(
            NAMESPACE_URL, f"eidolon:project:{project_id}:deliver:{project.last_fact_id}"
        )
        try:
            state = load(db, decision_id, store=self.store)
        except DecisionError:
            state = self.open_decision(
                db,
                who,
                gate="project.deliver",
                subject={"project_id": str(project_id)},
                action=action,
                decision_id=decision_id,
            )
        if state.decided:
            raise Conflict(f"delivery decision {decision_id} is already {state.status}")
        return self.sign_decision(
            db, who, decision_id=decision_id, verdict="approve", reason=reason, action=action
        )

    def pending_decisions(
        self,
        db: Session,
        company_id: UUID,
        *,
        decider: str | None = "human",
        status: str | None = "pending",
    ) -> list[DecisionState]:
        out = []
        for f in self.store.stream(db, kinds=[D.REQUESTED], company_id=company_id, limit=100_000):
            state = load(db, f.correlation_id, store=self.store)
            if decider and state.resolution.decider != decider:
                continue
            if status and state.status != status:
                continue
            out.append(state)
        return out

    # -- runs -----------------------------------------------------------------------------

    def cancel_run(self, db: Session, who: Person, *, run_id: UUID, reason: str) -> Fact:
        self.require(db, who, "run.cancel")
        facts = self.store.by_correlation(db, run_id, kinds=R.RUN_KINDS)
        if not facts or facts[0].kind != R.RUN_STARTED:
            raise Conflict("no such run")
        if facts[0].company_id != who.company_id:
            raise Forbidden("this run belongs to another company")
        if any(
            f.kind in (R.RUN_COMPLETED, R.RUN_FAILED, R.RUN_CANCELLED, R.RUN_BUDGET_EXCEEDED)
            for f in facts
        ):
            raise Conflict("the run has already ended")
        return self.store.append(
            db,
            R.cancel_request(
                who.company_id, run_id, who.actor, reason=reason, causation_id=facts[-1].id
            ),
        )


def person_in(db: Session, employment: EmploymentRow) -> Person:
    return Person(employment.company_id, employment.person_id, employment.role)


def employment_of(db: Session, company_id: UUID, person_id: str) -> EmploymentRow | None:
    return db.scalar(
        select(EmploymentRow).where(
            EmploymentRow.company_id == company_id,
            EmploymentRow.person_id == person_id,
            EmploymentRow.status == "active",
        )
    )


def decision_dict(d: DecisionState) -> dict[str, Any]:
    return {
        "decision_id": str(d.decision_id),
        "company_id": str(d.company_id),
        "gate": d.gate,
        "status": d.status,
        "resolution": d.resolution.as_dict(),
        "subject": d.subject,
        "proposer": d.proposer,
        "author": d.author,
        "amount": d.amount,
        "requested_at": d.requested_at.isoformat(),
        "signatures": [
            {"person_id": s.person_id, "role": s.role, "verdict": s.verdict, "reason": s.reason}
            for s in d.signatures
        ],
        "escalations": d.escalations,
    }
