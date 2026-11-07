"""The institution layer: how a company runs, as validated data (§5).

Depends on kernel only. ``schema`` defines templates, ``validate`` checks them,
``templates`` ships the built-ins, ``adoption`` records which version a company
runs. Gate resolution and decision envelopes follow in R3-b.
"""

from eidolon.institution.adoption import (
    ADOPTED,
    CHANGED,
    InstitutionError,
    InstitutionState,
    adopt,
    current,
    customize,
    fold,
)
from eidolon.institution.schema import (
    DECIDERS,
    GATES,
    SEPARATIONS,
    AmountTier,
    BudgetPolicy,
    GateRule,
    InstitutionTemplate,
    Retention,
    ReviewPolicy,
    Role,
    SchemaError,
)
from eidolon.institution.templates import BUILTIN, DEFAULT_TEMPLATE_ID, get
from eidolon.institution.validate import assert_valid, validate

__all__ = [
    "ADOPTED",
    "BUILTIN",
    "CHANGED",
    "DECIDERS",
    "DEFAULT_TEMPLATE_ID",
    "GATES",
    "SEPARATIONS",
    "AmountTier",
    "BudgetPolicy",
    "GateRule",
    "InstitutionError",
    "InstitutionState",
    "InstitutionTemplate",
    "Retention",
    "ReviewPolicy",
    "Role",
    "SchemaError",
    "adopt",
    "assert_valid",
    "current",
    "customize",
    "fold",
    "get",
    "validate",
]
