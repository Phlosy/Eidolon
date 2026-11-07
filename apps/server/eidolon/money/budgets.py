"""Budgets meet the ledger.

The institution expresses budgets and ``spend.approve`` amounts in *units*: the
minor unit of the company's accounting currency (分 / cents), so a template can
say ``project_default=200_000`` and mean 2 000.00. The ledger keeps micro-units
(1 000 000 per 1.00) because a single model call costs fractions of a cent.
Everything that crosses the line goes through this module.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.money import projection as P


def to_units(micro: int) -> int:
    """Micro → units, rounded half up (so a budget check never under-counts by rounding down
    a partially spent cent to zero while a cent later becomes a full one)."""
    return (micro + MICRO_PER_UNIT // 2) // MICRO_PER_UNIT


def to_micro(units: int) -> int:
    return units * MICRO_PER_UNIT


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    budget_units: int | None
    spent_micro: int

    @property
    def spent_units(self) -> int:
        return to_units(self.spent_micro)

    @property
    def remaining_units(self) -> int | None:
        return None if self.budget_units is None else self.budget_units - self.spent_units

    @property
    def ratio(self) -> float | None:
        if not self.budget_units:
            return None
        return self.spent_units / self.budget_units

    def exceeds(self, warn_ratio: float = 1.0) -> bool:
        return self.ratio is not None and self.ratio >= warn_ratio


def project_status(db: Session, project_id: UUID, budget_units: int | None) -> BudgetStatus:
    return BudgetStatus(budget_units, P.project_spend_micro(db, project_id))


def run_status(db: Session, run_id: UUID, budget_units: int | None) -> BudgetStatus:
    return BudgetStatus(budget_units, P.run_spend_micro(db, run_id))


def spend_amount(db: Session, project_id: UUID, *, proposed_units: int = 0) -> int:
    """The ``amount`` a ``spend.approve`` gate is resolved on: what the project has
    already spent plus what is being asked for, in units."""
    return to_units(P.project_spend_micro(db, project_id)) + proposed_units
