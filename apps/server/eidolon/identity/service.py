"""Identity service: strongly consistent folds from facts plus guarded writes.

Hiring and firing are management acts (gates ``hire`` / ``fire``): an agent needs
a decision, a human may act directly. Roles must exist in the company's current
institution, and a role's kind (human/agent) must match the person's kind.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.orm import Session

from eidolon.identity import facts as F
from eidolon.kernel.facts import Actor, ActorKind, Fact
from eidolon.kernel.store import FactStore


@dataclass(frozen=True, slots=True)
class Employment:
    employment_id: UUID
    person_id: str
    role: str
    department_id: str | None
    title: str | None
    status: str
    decision_id: UUID | None
    fact_id: UUID


@dataclass
class CompanyState:
    company_id: UUID
    name: str
    slug: str
    currency: str
    owner_user_id: UUID
    employments: dict[UUID, Employment] = field(default_factory=dict)
    last_fact_id: UUID | None = None

    def active(self) -> list[Employment]:
        return [e for e in self.employments.values() if e.status == "active"]

    def holding(self, role: str) -> list[Employment]:
        return [e for e in self.active() if e.role == role]

    def role_of(self, person_id: str) -> str | None:
        return next((e.role for e in self.active() if e.person_id == person_id), None)

    @classmethod
    def from_facts(cls, company_id: UUID, facts: list[Fact]) -> "CompanyState":
        state: CompanyState | None = None
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.kind not in F.KINDS or fact.company_id != company_id:
                continue
            p = fact.payload
            if fact.kind == F.COMPANY_CREATED:
                state = cls(
                    company_id, p["name"], p["slug"], p["currency"], UUID(p["owner_user_id"])
                )
            elif state is None:
                continue
            elif fact.kind == F.EMPLOYMENT_STARTED:
                eid = UUID(p["employment_id"])
                state.employments[eid] = Employment(
                    eid,
                    p["person_id"],
                    p["role"],
                    p.get("department_id"),
                    p.get("title"),
                    "active",
                    UUID(p["decision_id"]) if p.get("decision_id") else None,
                    fact.id,
                )
            elif fact.kind == F.EMPLOYMENT_ENDED:
                eid = UUID(p["employment_id"])
                old = state.employments.get(eid)
                if old is not None:
                    state.employments[eid] = Employment(
                        old.employment_id,
                        old.person_id,
                        old.role,
                        old.department_id,
                        old.title,
                        "ended",
                        old.decision_id,
                        fact.id,
                    )
            state.last_fact_id = fact.id
        if state is None:
            raise F.IdentityError(f"company {company_id}: no {F.COMPANY_CREATED} fact")
        return state


@dataclass(frozen=True, slots=True)
class PersonState:
    person_id: str
    name: str
    kind: str
    user_id: UUID | None
    persona: dict[str, Any]


class IdentityService:
    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()

    # -- reads ---------------------------------------------------------------------

    def company(self, db: Session, company_id: UUID) -> CompanyState:
        return CompanyState.from_facts(
            company_id, self.store.by_correlation(db, company_id, kinds=F.KINDS)
        )

    def person(self, db: Session, person_id: str) -> PersonState | None:
        for fact in self.store.stream(
            db, kinds=[F.PERSON_CREATED], company_id=F.PLATFORM, limit=100_000
        ):
            if fact.payload["person_id"] == person_id:
                p = fact.payload
                return PersonState(
                    person_id,
                    p["name"],
                    p["kind"],
                    UUID(p["user_id"]) if p.get("user_id") else None,
                    dict(p.get("persona") or {}),
                )
        return None

    # -- writes --------------------------------------------------------------------

    def register_user(
        self, db: Session, *, email: str, display_name: str, user_id: UUID | None = None
    ) -> Fact:
        user_id = user_id or uuid4()
        return self.store.append(
            db, F.user_registered(Actor.system(), user_id, email=email, display_name=display_name)
        )

    def create_person(
        self,
        db: Session,
        actor: Actor,
        person_id: str,
        *,
        name: str,
        kind: str,
        user_id: UUID | None = None,
        persona=None,
    ) -> Fact:
        return self.store.append(
            db,
            F.person_created(
                actor, person_id, name=name, kind=kind, user_id=user_id, persona=persona
            ),
        )

    def create_company(
        self,
        db: Session,
        *,
        owner_user_id: UUID,
        owner_person_id: str,
        name: str,
        slug: str,
        currency: str = "CNY",
        company_id: UUID | None = None,
    ) -> tuple[Fact, Fact]:
        """A company and its first employment: the owner, as role ``owner``. Both are
        the owner's own acts (human actor)."""
        company_id = company_id or uuid4()
        owner = self.person(db, owner_person_id)
        if owner is None or owner.kind != "human" or owner.user_id != owner_user_id:
            raise F.IdentityError("the owner must be a human person of that user")
        actor = Actor(ActorKind.human, owner_person_id)
        created = self.store.append(
            db,
            F.company_created(
                actor,
                company_id,
                name=name,
                slug=slug,
                currency=currency,
                owner_user_id=owner_user_id,
            ),
        )
        # The owner's employment id is a function of the company, so a retried
        # creation (same company_id) hits K2 instead of seating the owner twice.
        employment = self.store.append(
            db,
            F.employment_started(
                actor,
                company_id,
                uuid5(NAMESPACE_URL, f"eidolon:employment:{company_id}:owner"),
                person_id=owner_person_id,
                role="owner",
                title="Owner",
                causation_id=created.id,
            ),
        )
        return created, employment

    def hire(
        self,
        db: Session,
        company_id: UUID,
        actor: Actor,
        *,
        person_id: str,
        role: str,
        department_id: str | None = None,
        title: str | None = None,
        decision_id: UUID | None = None,
        employment_id: UUID | None = None,
        allowed_roles: Mapping[str, str] | None = None,
    ) -> Fact:
        """``allowed_roles`` (role code → human|agent) comes from the institution layer
        (``institution.staffing.hire``); identity itself knows no templates."""
        if decision_id is None and actor.kind is not ActorKind.human:
            raise F.IdentityError("hiring needs a decision_id (gate hire) unless a human does it")
        person = self.person(db, person_id)
        if person is None:
            raise F.IdentityError(f"no person {person_id}")
        state = self.company(db, company_id)
        if state.role_of(person_id) is not None:
            raise F.IdentityError(
                f"{person_id} is already employed by {company_id} as {state.role_of(person_id)}"
            )
        if allowed_roles is not None:
            kind = allowed_roles.get(role)
            if kind is None:
                raise F.IdentityError(f"role {role!r} does not exist in the company's institution")
            if kind != person.kind:
                raise F.IdentityError(f"role {role!r} is for {kind}s; {person_id} is {person.kind}")
        return self.store.append(
            db,
            F.employment_started(
                actor,
                company_id,
                employment_id or uuid4(),
                person_id=person_id,
                role=role,
                department_id=department_id,
                title=title,
                decision_id=decision_id,
                causation_id=state.last_fact_id,
            ),
        )

    def end_employment(
        self,
        db: Session,
        company_id: UUID,
        actor: Actor,
        *,
        employment_id: UUID,
        reason: str,
        decision_id: UUID | None = None,
    ) -> Fact:
        if decision_id is None and actor.kind is not ActorKind.human:
            raise F.IdentityError(
                "ending an employment needs a decision_id (gate fire) unless a human does it"
            )
        state = self.company(db, company_id)
        emp = state.employments.get(employment_id)
        if emp is None or emp.status != "active":
            raise F.IdentityError(f"no active employment {employment_id}")
        if emp.role == "owner":
            raise F.IdentityError("the owner's employment cannot be ended")
        return self.store.append(
            db,
            F.employment_ended(
                actor,
                company_id,
                employment_id,
                reason=reason,
                decision_id=decision_id,
                causation_id=emp.fact_id,
            ),
        )
