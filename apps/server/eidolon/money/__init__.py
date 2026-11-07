"""Money (R4-b): a double-entry ledger per company and the metering that books
every model call into it. Depends on agent (the call fact), work (project roll-up),
identity (the company's currency) and kernel."""

from eidolon.money import budgets, fx
from eidolon.money.costs import COMPUTE, CostMeter, payable
from eidolon.money.facts import (
    ACCOUNT_OPENED,
    COST_RECORDED,
    ENTRY_POSTED,
    KINDS,
    LEDGER_KINDS,
    MoneyError,
    normal_side,
)
from eidolon.money.ledger import Account, Entry, Ledger, LedgerState, Line
from eidolon.money.projection import (
    AccountRow,
    JournalRow,
    LedgerProjection,
    accounts,
    company_spend_micro,
    project_spend_micro,
    run_spend_micro,
    task_spend_micro,
)
from eidolon.money.trace import (
    CostTrace,
    ProjectCosts,
    TraceError,
    cost_trace,
    ledger_fact_ids,
    project_costs,
)
from eidolon.money.verify import LedgerReport, verify

__all__ = [
    "ACCOUNT_OPENED",
    "COMPUTE",
    "COST_RECORDED",
    "ENTRY_POSTED",
    "KINDS",
    "LEDGER_KINDS",
    "Account",
    "AccountRow",
    "CostMeter",
    "CostTrace",
    "Entry",
    "JournalRow",
    "Ledger",
    "LedgerProjection",
    "LedgerReport",
    "LedgerState",
    "Line",
    "MoneyError",
    "ProjectCosts",
    "TraceError",
    "accounts",
    "budgets",
    "company_spend_micro",
    "cost_trace",
    "fx",
    "ledger_fact_ids",
    "normal_side",
    "payable",
    "project_costs",
    "project_spend_micro",
    "run_spend_micro",
    "task_spend_micro",
    "verify",
]
