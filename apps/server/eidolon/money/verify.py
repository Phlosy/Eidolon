"""``ledger-verify``: the ledger re-derived from facts agrees with itself and with
its projection.

Checked per company:
  M1  every entry balances and posts only to known accounts in their currency
      (the fold itself refuses otherwise);
  M2  the trial balance holds: Σ debits = Σ credits over the whole ledger;
  M3  the projection's account totals and journal lines equal the fold's;
  M4  every posted ``money.cost.recorded`` is booked by exactly one entry, and
      every cost-sourced entry names a recorded cost.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eidolon.kernel.consumers import cursor
from eidolon.kernel.store import FactStore
from eidolon.money import facts as F
from eidolon.money.ledger import LedgerState
from eidolon.money.projection import AccountRow, JournalRow

LEDGER_PROJECTION = "money_ledger"


@dataclass
class LedgerReport:
    company_id: UUID
    ok: bool = True
    accounts: int = 0
    entries: int = 0
    costs: int = 0
    unposted: int = 0
    projection_lag: int = 0  # entries the projection has not consumed yet
    debit_total: int = 0
    credit_total: int = 0
    problems: list[str] = field(default_factory=list)

    def fail(self, problem: str) -> None:
        self.ok = False
        self.problems.append(problem)


def verify(db: Session, company_id: UUID, *, store: FactStore | None = None) -> LedgerReport:
    store = store or FactStore()
    report = LedgerReport(company_id)
    facts = store.stream(db, kinds=F.KINDS, company_id=company_id, limit=1_000_000)

    # M1: fold (refuses unbalanced / unknown-account / wrong-currency entries)
    state = LedgerState(company_id)
    for fact in facts:
        try:
            state.apply(fact)
        except F.MoneyError as exc:
            report.fail(f"M1 seq={fact.seq} {fact.kind}: {exc}")
    report.accounts, report.entries = len(state.accounts), len(state.entries)
    report.debit_total, report.credit_total = state.trial_balance()
    full = state

    # M2: trial balance
    if report.debit_total != report.credit_total:
        report.fail(
            f"M2 trial balance: debits {report.debit_total} != credits {report.credit_total}"
        )

    # M3: projection == fold of the facts the projection has consumed so far (lag is
    # not corruption; a projection behind its cursor is simply not yet compared)
    projected_seq = cursor(db, LEDGER_PROJECTION)
    state = LedgerState(company_id)
    for fact in facts:
        if fact.seq > projected_seq:
            break
        try:
            state.apply(fact)
        except F.MoneyError:
            break  # already reported under M1
    report.projection_lag = report.entries - len(state.entries)
    rows = {
        r.code: r for r in db.scalars(select(AccountRow).where(AccountRow.company_id == company_id))
    }
    if set(rows) != set(state.accounts):
        report.fail(
            f"M3 accounts: projection has {sorted(rows)}, facts have {sorted(state.accounts)}"
        )
    for code, row in rows.items():
        if code not in state.accounts:
            continue
        expected = (state.debits.get(code, 0), state.credits.get(code, 0))
        if (row.debit_total_micro, row.credit_total_micro) != expected:
            report.fail(
                f"M3 account {code!r}: projection totals "
                f"{(row.debit_total_micro, row.credit_total_micro)} != facts {expected}"
            )
    journal_lines = db.scalar(
        select(func.count()).select_from(JournalRow).where(JournalRow.company_id == company_id)
    )
    fold_lines = sum(len(e.lines) for e in state.entries)
    if journal_lines != fold_lines:
        report.fail(f"M3 journal: projection has {journal_lines} lines, facts have {fold_lines}")
    journal_sum = db.scalar(
        select(func.coalesce(func.sum(JournalRow.amount_micro), 0)).where(
            JournalRow.company_id == company_id
        )
    )
    projected_total = sum(state.trial_balance())
    if int(journal_sum or 0) != projected_total:
        report.fail(f"M3 journal: projection amounts {journal_sum} != facts {projected_total}")

    # M4: costs ↔ entries
    costs = {f.id: f for f in facts if f.kind == F.COST_RECORDED}
    report.costs = len(costs)
    report.unposted = sum(1 for f in costs.values() if not f.payload["posted"])
    booked_by: dict[UUID, list[UUID]] = {}
    for entry in full.entries:
        src = entry.source or {}
        if src.get("kind") == F.COST_RECORDED:
            cost_id = UUID(src["fact_id"])
            booked_by.setdefault(cost_id, []).append(entry.entry_id)
            if cost_id not in costs:
                report.fail(f"M4 entry {entry.entry_id} books unknown cost {cost_id}")
    for cost_id, fact in costs.items():
        entries = booked_by.get(cost_id, [])
        if fact.payload["posted"] and len(entries) != 1:
            report.fail(f"M4 cost {cost_id} posted but booked by {len(entries)} entries")
        if not fact.payload["posted"] and entries:
            report.fail(f"M4 cost {cost_id} not posted but booked by {entries}")
    return report
