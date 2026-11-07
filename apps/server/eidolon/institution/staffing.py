"""Staffing = identity + institution: hiring into a role the company's current
institution defines, for a person of the matching kind. Identity stores the
employment; this module supplies the rules (institution may import identity,
never the other way round)."""

from uuid import UUID

from sqlalchemy.orm import Session

from eidolon.identity import IdentityService
from eidolon.institution.adoption import current
from eidolon.kernel.facts import Actor, Fact


def allowed_roles(db: Session, company_id: UUID) -> dict[str, str] | None:
    state = current(db, company_id)
    if state is None:
        return None
    return {r.code: r.kind for r in state.template.roles}


def hire(
    db: Session,
    company_id: UUID,
    actor: Actor,
    *,
    person_id: str,
    role: str,
    department_id: str | None = None,
    title: str | None = None,
    decision_id: UUID | None = None,
    service: IdentityService | None = None,
) -> Fact:
    service = service or IdentityService()
    return service.hire(
        db,
        company_id,
        actor,
        person_id=person_id,
        role=role,
        department_id=department_id,
        title=title,
        decision_id=decision_id,
        allowed_roles=allowed_roles(db, company_id),
    )
