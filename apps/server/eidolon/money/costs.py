"""Cost metering: ``agent.model.called`` → ``money.cost.recorded`` → ``money.entry.posted``.

The meter is a reactor, not a projection: its output is facts, which the ledger
projection then reads like any other. Per call it

1. takes the provider-currency estimate the agent layer put on the call fact,
2. converts it to the company's accounting currency at the versioned book rate,
3. records the measurement (always — also for replays and unpriced models), and
4. books it, debit ``compute`` (expense) / credit ``ap:<provider>`` (liability),
   when the call was live and could be priced and converted.

Everything is keyed on the call fact's id, so re-handling after a crash hits K2.
"""

import logging
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.identity import IdentityError, IdentityService
from eidolon.kernel.facts import Actor, Fact
from eidolon.kernel.store import FactStore
from eidolon.money import facts as F
from eidolon.money import fx
from eidolon.money.ledger import Ledger

log = logging.getLogger("eidolon.money.costs")

COMPUTE = "compute"


def payable(provider: str) -> str:
    return f"ap:{provider}"


def entry_id_for(cost_fact_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"eidolon:money:cost:{cost_fact_id}")


@dataclass
class CostMeter:
    store: FactStore = field(default_factory=FactStore)
    identity: IdentityService | None = None
    ledger: Ledger | None = None
    book_replays: bool = False  # tests that replay fixtures may want the full chain
    name: str = "cost_meter"
    handles: frozenset[str] = frozenset({MODEL_CALLED})

    def __post_init__(self) -> None:
        self.identity = self.identity or IdentityService(self.store)
        self.ledger = self.ledger or Ledger(self.store)

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        company = fact.company_id
        run_id = _uuid(p.get("run_id"))  # the loop's CallContext; absent outside a run
        task_id = self._task_of(db, run_id)
        source = p.get("cost")
        currency = self._currency(db, company)
        booked = None
        reason = None
        if source is None:
            reason = "unpriced"
        elif currency is None:
            reason = "no_company"
        else:
            converted = fx.convert(source["amount_micro"], source["currency"], currency)
            if converted is None:
                reason = "no_fx_rate"
            else:
                amount, rate = converted
                booked = {
                    "amount_micro": amount,
                    "currency": currency,
                    "fx_rate": str(rate),
                    "fx_version": fx.FX_VERSION,
                }
                if p.get("mode") != "live" and not self.book_replays:
                    reason = p.get("mode") or "replay"
                elif amount == 0:
                    reason = "zero"
        posted = booked is not None and reason is None
        cost = self.store.append(
            db,
            F.cost_recorded(
                Actor.system(),
                company,
                model_call_fact_id=fact.id,
                provider=p["provider"],
                model=p["model"],
                mode=p.get("mode", "live"),
                source=source,
                booked=booked,
                posted=posted,
                reason=reason,
                run_id=run_id,
                task_id=task_id,
                correlation_id=fact.correlation_id,
            ),
        )
        if not posted:
            log.info(
                "cost.recorded",
                extra={"company_id": str(company), "posted": False, "reason": reason},
            )
            return
        assert booked is not None and currency is not None
        ap = payable(p["provider"])
        self.ledger.open_account(
            db,
            company,
            Actor.system(),
            code=COMPUTE,
            name="Compute",
            kind="expense",
            currency=currency,
        )
        self.ledger.open_account(
            db,
            company,
            Actor.system(),
            code=ap,
            name=f"Payable: {p['provider']}",
            kind="liability",
            currency=currency,
        )
        self.ledger.post(
            db,
            company,
            Actor.system(),
            entry_id=entry_id_for(cost.id),
            lines=[
                {"account": COMPUTE, "side": "debit", "amount_micro": booked["amount_micro"]},
                {"account": ap, "side": "credit", "amount_micro": booked["amount_micro"]},
            ],
            currency=currency,
            memo=f"model call {p['model']} via {p['provider']}",
            source={"kind": F.COST_RECORDED, "fact_id": str(cost.id)},
            run_id=run_id,
            task_id=task_id,
            correlation_id=fact.correlation_id,
            causation_id=cost.id,
            state=self.ledger.state(db, company),
        )
        log.info(
            "cost.posted",
            extra={
                "company_id": str(company),
                "amount_micro": booked["amount_micro"],
                "currency": currency,
            },
        )

    # -- lookups --------------------------------------------------------------------

    def _currency(self, db: Session, company: UUID) -> str | None:
        try:
            return self.identity.company(db, company).currency
        except IdentityError:
            return None

    def _task_of(self, db: Session, run_id: UUID | None) -> UUID | None:
        if run_id is None:
            return None
        for started in self.store.by_correlation(db, run_id, kinds=[R.RUN_STARTED]):
            return _uuid(started.payload.get("task_id"))
        return None


def _uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
