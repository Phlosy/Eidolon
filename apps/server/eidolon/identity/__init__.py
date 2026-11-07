"""Identity (R4-a): users, companies, persons, employments — facts, folds, projection.
Depends on institution (role validation) and kernel."""

from eidolon.identity.facts import (
    COMPANY_CREATED,
    EMPLOYMENT_ENDED,
    EMPLOYMENT_STARTED,
    KINDS,
    PERSON_CREATED,
    PLATFORM,
    USER_REGISTERED,
    IdentityError,
)
from eidolon.identity.projection import (
    CompanyRow,
    EmploymentRow,
    IdentityProjection,
    PersonRow,
    UserRow,
    active_employments,
)
from eidolon.identity.service import CompanyState, Employment, IdentityService, PersonState

__all__ = [
    "COMPANY_CREATED",
    "EMPLOYMENT_ENDED",
    "EMPLOYMENT_STARTED",
    "KINDS",
    "PERSON_CREATED",
    "PLATFORM",
    "USER_REGISTERED",
    "CompanyRow",
    "CompanyState",
    "Employment",
    "EmploymentRow",
    "IdentityError",
    "IdentityProjection",
    "IdentityService",
    "PersonRow",
    "PersonState",
    "UserRow",
    "active_employments",
]
