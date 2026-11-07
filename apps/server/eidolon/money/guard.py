"""The budget guard: the institution's ``budget_policy`` applied with the ledger's
numbers before a run is started (v2-architecture §4.7).

Three checks, in order:

* company concurrency — more tasks in progress than ``max_concurrent_runs`` → wait;
* company daily cap — today's booked spend at or above ``daily_cap`` → wait;
* project tranches — once the next tranche (``run_default``) would take the project
  past ``warn_ratio`` of its budget, that tranche needs a ``spend.approve`` decision.
  The gate's amount tiers say who: the system records it mechanically within the
  first tier, an agent role signs it in the middle (``SpendApprover`` job), a human
  above that (the decisions inbox). ``hard_stop=False`` opens the decision but never
  blocks.

The guard never decides anything itself: it asks the gate and waits for the answer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eidolon.institution import (
    DecisionError,
    DecisionState,
    GateContext,
    InstitutionTemplate,
    load,
    request,
    system_record,
)
from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.kernel.facts import Actor
from eidolon.kernel.store import FactStore
from eidolon.money import budgets
from eidolon.money.projection import company_spend_since_micro, project_spend_micro
from eidolon.work.projection import TaskRow
from eidolon.work.service import WorkService

GATE = "spend.approve"


@dataclass(frozen=True, slots=True)
class Verdict:
    allow: bool
    reason: str  # within_budget | tranche_approved | ... | waiting_decision | rejected | ...
    decision_id: UUID | None = None
    spent_units: int = 0
    budget_units: int | None = None

    @property
    def waiting(self) -> bool:
        return not self.allow


def tranche_decision_id(project_id: UUID, tranche: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:project:{project_id}:spend:{tranche}")


class BudgetGuard:
    """Implements ``agent.scheduler.BudgetGuard`` (duck-typed; agent never imports money)."""

    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()
        self.work = WorkService(self.store)

    def check(
        self, db: Session, company_id: UUID, project_id: UUID, template: InstitutionTemplate
    ) -> Verdict:
        policy = template.budget_policy
        running = db.scalar(
            select(func.count())
            .select_from(TaskRow)
            .where(TaskRow.company_id == company_id, TaskRow.status == "in_progress")
        )
        if (running or 0) > policy.max_concurrent_runs:
            return Verdict(False, "max_concurrent_runs")
        if policy.daily_cap is not None:
            today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            if (
                company_spend_since_micro(db, company_id, today)
                >= policy.daily_cap * MICRO_PER_UNIT
            ):
                return Verdict(False, "daily_cap")
        project = self.work.project(db, project_id)
        budget = project.budget
        spent = budgets.to_units(project_spend_micro(db, project_id))
        tranche = policy.run_default
        if spent + tranche <= policy.warn_ratio * budget:
            return Verdict(True, "within_budget", spent_units=spent, budget_units=budget)
        k = spent // max(tranche, 1)
        decision_id = tranche_decision_id(project_id, k)
        decision = self._decision(
            db, template, company_id, project_id, decision_id, k, spent, budget, tranche
        )
        if decision.status == "approved":
            return Verdict(True, "tranche_approved", decision_id, spent, budget)
        if not policy.hard_stop:
            return Verdict(True, f"soft:{decision.status}", decision_id, spent, budget)
        if decision.status == "pending":
            return Verdict(False, "waiting_decision", decision_id, spent, budget)
        return Verdict(False, "rejected", decision_id, spent, budget)

    def _decision(
        self, db, template, company_id, project_id, decision_id, k, spent, budget, tranche
    ) -> DecisionState:
        try:
            return load(db, decision_id, store=self.store)
        except DecisionError:
            pass
        ctx = GateContext(
            GATE,
            company_id,
            subject={
                "project_id": str(project_id),
                "tranche": k,
                "spent_units": spent,
                "budget_units": budget,
                "tranche_units": tranche,
            },
            amount=spent + tranche,
        )
        requested, resolution = request(template, ctx, Actor.system(), decision_id=decision_id)
        if resolution.decider == "system":
            requested, recorded = system_record(
                template,
                ctx,
                check=f"tranche {k} within the system tier (amount {spent + tranche})",
                decision_id=decision_id,
            )
            self.store.append(db, requested)
            self.store.append(db, recorded)
        else:
            self.store.append(db, requested)
        db.commit()
        return load(db, decision_id, store=self.store)
