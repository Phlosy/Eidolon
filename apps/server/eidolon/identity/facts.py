"""Identity facts (§2.3 identity): users, companies, persons, employments.

Users and persons are platform-wide (a person walks between companies; §4.1
"随人走的命名空间"), so their facts live under the fixed ``PLATFORM`` company id.
Companies and employments are company-scoped and correlate on the company.

    identity.user.registered        a human account (credentials come with the API, R5)
    identity.company.created        a company with its accounting currency and owner
    identity.person.created         a human's person or an agent's identity (+ persona)
    identity.employment.started     person ↔ company with a role code (and department)
    identity.employment.ended
"""

import re
from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from eidolon.kernel.facts import Actor, NewFact

PLATFORM = uuid5(NAMESPACE_URL, "eidolon:platform")

USER_REGISTERED = "identity.user.registered"
COMPANY_CREATED = "identity.company.created"
PERSON_CREATED = "identity.person.created"
EMPLOYMENT_STARTED = "identity.employment.started"
EMPLOYMENT_ENDED = "identity.employment.ended"
KINDS = frozenset(
    {USER_REGISTERED, COMPANY_CREATED, PERSON_CREATED, EMPLOYMENT_STARTED, EMPLOYMENT_ENDED}
)

PERSON_KINDS = ("human", "agent")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class IdentityError(ValueError):
    pass


def user_registered(actor: Actor, user_id: UUID, *, email: str, display_name: str) -> NewFact:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise IdentityError(f"invalid email {email!r}")
    if not display_name.strip():
        raise IdentityError("display_name is required")
    return NewFact(
        company_id=PLATFORM,
        actor=actor,
        kind=USER_REGISTERED,
        payload={"user_id": str(user_id), "email": email, "display_name": display_name.strip()},
        idempotency_key=f"user:{email}:registered",
        correlation_id=user_id,
    )


def company_created(
    actor: Actor, company_id: UUID, *, name: str, slug: str, currency: str, owner_user_id: UUID
) -> NewFact:
    if not name.strip():
        raise IdentityError("company name is required")
    if not _SLUG.match(slug):
        raise IdentityError(f"invalid slug {slug!r} (lowercase letters, digits, dashes)")
    if not re.match(r"^[A-Z]{3}$", currency):
        raise IdentityError("currency must be a 3-letter ISO code")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=COMPANY_CREATED,
        payload={
            "company_id": str(company_id),
            "name": name.strip(),
            "slug": slug,
            "currency": currency,
            "owner_user_id": str(owner_user_id),
        },
        idempotency_key=f"company:{company_id}:created",
        correlation_id=company_id,
    )


def person_created(
    actor: Actor,
    person_id: str,
    *,
    name: str,
    kind: str,
    user_id: UUID | None = None,
    persona: Mapping[str, Any] | None = None,
) -> NewFact:
    if kind not in PERSON_KINDS:
        raise IdentityError(f"kind must be one of {PERSON_KINDS}")
    if (
        not person_id
        or len(person_id) > 200
        or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", person_id)
    ):
        raise IdentityError(f"invalid person_id {person_id!r}")
    if not name.strip():
        raise IdentityError("name is required")
    if kind == "human" and user_id is None:
        raise IdentityError("a human person needs a user_id")
    if kind == "agent" and user_id is not None:
        raise IdentityError("an agent person has no user account")
    return NewFact(
        company_id=PLATFORM,
        actor=actor,
        kind=PERSON_CREATED,
        payload={
            "person_id": person_id,
            "name": name.strip(),
            "kind": kind,
            "user_id": str(user_id) if user_id else None,
            "persona": dict(persona or {}),
        },
        idempotency_key=f"person:{person_id}:created",
    )


def employment_started(
    actor: Actor,
    company_id: UUID,
    employment_id: UUID,
    *,
    person_id: str,
    role: str,
    department_id: str | None = None,
    title: str | None = None,
    decision_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> NewFact:
    if not role or not re.match(r"^[a-z][a-z0-9_]*$", role):
        raise IdentityError(f"invalid role code {role!r}")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=EMPLOYMENT_STARTED,
        payload={
            "employment_id": str(employment_id),
            "person_id": person_id,
            "role": role,
            "department_id": department_id,
            "title": title,
            "decision_id": str(decision_id) if decision_id else None,
        },
        idempotency_key=f"employment:{employment_id}:started",
        correlation_id=company_id,
        decision_id=decision_id,
        causation_id=causation_id,
    )


def employment_ended(
    actor: Actor,
    company_id: UUID,
    employment_id: UUID,
    *,
    reason: str,
    decision_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> NewFact:
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=EMPLOYMENT_ENDED,
        payload={
            "employment_id": str(employment_id),
            "reason": reason,
            "decision_id": str(decision_id) if decision_id else None,
        },
        idempotency_key=f"employment:{employment_id}:ended",
        correlation_id=company_id,
        decision_id=decision_id,
        causation_id=causation_id,
    )
