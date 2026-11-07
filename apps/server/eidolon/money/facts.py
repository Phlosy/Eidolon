"""Money facts (§2.3 money): a double-entry ledger per company, and the metering
that turns model calls into ledger entries.

    money.account.opened    a ledger account: code, name, kind, currency
    money.entry.posted      one balanced journal entry: ≥ 2 lines, Σdebit = Σcredit
    money.cost.recorded     agent.model.called measured in the company's currency

Amounts are integers in micro-units (1 000 000 = 1.00) of the company's accounting
currency, never floats. Entries are immutable: there is no "entry changed" fact; a
mistake is corrected by posting a reversing entry.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from eidolon.kernel.facts import Actor, NewFact

ACCOUNT_OPENED = "money.account.opened"
ENTRY_POSTED = "money.entry.posted"
COST_RECORDED = "money.cost.recorded"
KINDS = frozenset({ACCOUNT_OPENED, ENTRY_POSTED, COST_RECORDED})
LEDGER_KINDS = frozenset({ACCOUNT_OPENED, ENTRY_POSTED})

ACCOUNT_KINDS = ("asset", "liability", "equity", "expense", "income")
DEBIT_NORMAL = frozenset({"asset", "expense"})
SIDES = ("debit", "credit")
_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,62}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class MoneyError(ValueError):
    pass


def normal_side(kind: str) -> str:
    return "debit" if kind in DEBIT_NORMAL else "credit"


def _check_currency(currency: str) -> str:
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise MoneyError(f"currency must be a 3-letter ISO code, got {currency!r}")
    return currency


def account_opened(
    actor: Actor,
    company_id: UUID,
    *,
    code: str,
    name: str,
    kind: str,
    currency: str,
    causation_id: UUID | None = None,
) -> NewFact:
    if not isinstance(code, str) or not _CODE.match(code):
        raise MoneyError(f"invalid account code {code!r} (lowercase, digits, _ . : -)")
    if kind not in ACCOUNT_KINDS:
        raise MoneyError(f"account kind must be one of {ACCOUNT_KINDS}, got {kind!r}")
    if not name.strip():
        raise MoneyError("account name is required")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=ACCOUNT_OPENED,
        payload={
            "code": code,
            "name": name.strip(),
            "kind": kind,
            "currency": _check_currency(currency),
        },
        idempotency_key=f"account:{company_id}:{code}:opened",
        correlation_id=company_id,
        causation_id=causation_id,
    )


def _check_lines(lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(lines) < 2:
        raise MoneyError("an entry needs at least two lines")
    out: list[dict[str, Any]] = []
    debits = credits = 0
    for line in lines:
        account, side, amount = line.get("account"), line.get("side"), line.get("amount_micro")
        if not isinstance(account, str) or not _CODE.match(account):
            raise MoneyError(f"invalid account code {account!r} in entry line")
        if side not in SIDES:
            raise MoneyError(f"line side must be debit or credit, got {side!r}")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise MoneyError(f"line amount_micro must be a positive integer, got {amount!r}")
        if side == "debit":
            debits += amount
        else:
            credits += amount
        out.append({"account": account, "side": side, "amount_micro": amount})
    if debits != credits:
        raise MoneyError(f"entry does not balance: debits {debits} != credits {credits}")
    return out


def entry_posted(
    actor: Actor,
    company_id: UUID,
    entry_id: UUID,
    *,
    lines: Sequence[Mapping[str, Any]],
    currency: str,
    memo: str,
    source: Mapping[str, Any] | None = None,
    run_id: UUID | None = None,
    task_id: UUID | None = None,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> NewFact:
    """``source`` names what the entry books (e.g. ``{"kind": "money.cost.recorded",
    "fact_id": ...}``); ``run_id`` / ``task_id`` are the attribution the budget
    queries roll up on."""
    if not memo.strip():
        raise MoneyError("memo is required")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=ENTRY_POSTED,
        payload={
            "entry_id": str(entry_id),
            "currency": _check_currency(currency),
            "lines": _check_lines(lines),
            "memo": memo.strip()[:500],
            "source": dict(source) if source else None,
            "run_id": str(run_id) if run_id else None,
            "task_id": str(task_id) if task_id else None,
        },
        idempotency_key=f"entry:{entry_id}:posted",
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def cost_recorded(
    actor: Actor,
    company_id: UUID,
    *,
    model_call_fact_id: UUID,
    provider: str,
    model: str,
    mode: str,
    source: Mapping[str, Any] | None,
    booked: Mapping[str, Any] | None,
    posted: bool,
    reason: str | None,
    run_id: UUID | None,
    task_id: UUID | None,
    correlation_id: UUID | None = None,
) -> NewFact:
    """``source`` is the provider-currency estimate from ``agent.model.called``
    (``amount_micro``, ``currency``, ``pricing_version``) or ``None`` for an unpriced
    model; ``booked`` is the same amount in the company's currency (``amount_micro``,
    ``currency``, ``fx_rate``, ``fx_version``) or ``None`` when it could not be
    converted. ``posted`` says whether a ledger entry follows; ``reason`` says why not."""
    if posted and booked is None:
        raise MoneyError("a posted cost needs a booked amount")
    if not posted and not reason:
        raise MoneyError("an unposted cost needs a reason")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=COST_RECORDED,
        payload={
            "model_call_fact_id": str(model_call_fact_id),
            "provider": provider,
            "model": model,
            "mode": mode,
            "source": dict(source) if source else None,
            "booked": dict(booked) if booked else None,
            "posted": posted,
            "reason": reason,
            "run_id": str(run_id) if run_id else None,
            "task_id": str(task_id) if task_id else None,
        },
        idempotency_key=f"cost:{model_call_fact_id}:recorded",
        correlation_id=correlation_id,
        causation_id=model_call_fact_id,
    )
