"""The ledger as a fold of ``money.*`` facts, plus guarded posting.

``LedgerState.from_facts`` replays a company's account and entry facts in order and
refuses anything the projection would also refuse (unknown account, wrong currency),
so the fold *is* the definition of a valid ledger; ``verify`` and the projection
both measure themselves against it.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from eidolon.kernel.facts import Actor, Fact
from eidolon.kernel.store import FactStore
from eidolon.money import facts as F


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    kind: str
    currency: str
    fact_id: UUID

    @property
    def normal_side(self) -> str:
        return F.normal_side(self.kind)


@dataclass(frozen=True, slots=True)
class Line:
    account: str
    side: str
    amount_micro: int


@dataclass(frozen=True, slots=True)
class Entry:
    entry_id: UUID
    currency: str
    lines: tuple[Line, ...]
    memo: str
    source: dict[str, Any] | None
    run_id: UUID | None
    task_id: UUID | None
    fact_id: UUID
    seq: int


@dataclass
class LedgerState:
    company_id: UUID
    accounts: dict[str, Account] = field(default_factory=dict)
    entries: list[Entry] = field(default_factory=list)
    debits: dict[str, int] = field(default_factory=dict)
    credits: dict[str, int] = field(default_factory=dict)
    last_fact_id: UUID | None = None

    def balance(self, code: str) -> int:
        """Signed balance on the account's normal side."""
        account = self.accounts[code]
        d, c = self.debits.get(code, 0), self.credits.get(code, 0)
        return d - c if account.normal_side == "debit" else c - d

    def trial_balance(self) -> tuple[int, int]:
        return sum(self.debits.values()), sum(self.credits.values())

    def apply(self, fact: Fact) -> None:
        if fact.kind == F.ACCOUNT_OPENED:
            p = fact.payload
            if p["code"] in self.accounts:
                raise F.MoneyError(f"account {p['code']!r} opened twice")
            self.accounts[p["code"]] = Account(
                p["code"], p["name"], p["kind"], p["currency"], fact.id
            )
        elif fact.kind == F.ENTRY_POSTED:
            p = fact.payload
            lines = tuple(
                Line(ln["account"], ln["side"], ln["amount_micro"])
                for ln in F._check_lines(p["lines"])
            )
            for line in lines:
                account = self.accounts.get(line.account)
                if account is None:
                    raise F.MoneyError(
                        f"entry {p['entry_id']} posts to unknown account {line.account!r}"
                    )
                if account.currency != p["currency"]:
                    raise F.MoneyError(
                        f"entry {p['entry_id']} in {p['currency']} posts to "
                        f"{line.account!r} kept in {account.currency}"
                    )
            for line in lines:
                book = self.debits if line.side == "debit" else self.credits
                book[line.account] = book.get(line.account, 0) + line.amount_micro
            self.entries.append(
                Entry(
                    UUID(p["entry_id"]),
                    p["currency"],
                    lines,
                    p["memo"],
                    p.get("source"),
                    UUID(p["run_id"]) if p.get("run_id") else None,
                    UUID(p["task_id"]) if p.get("task_id") else None,
                    fact.id,
                    fact.seq,
                )
            )
        else:
            return
        self.last_fact_id = fact.id

    @classmethod
    def from_facts(cls, company_id: UUID, facts: list[Fact]) -> "LedgerState":
        state = cls(company_id)
        for fact in sorted(facts, key=lambda f: f.seq):
            if fact.company_id == company_id and fact.kind in F.LEDGER_KINDS:
                state.apply(fact)
        return state


class Ledger:
    """Reads fold the facts; writes validate against the fold before appending."""

    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()

    def state(self, db: Session, company_id: UUID) -> LedgerState:
        facts = self.store.stream(db, kinds=F.LEDGER_KINDS, company_id=company_id, limit=1_000_000)
        return LedgerState.from_facts(company_id, facts)

    def open_account(
        self,
        db: Session,
        company_id: UUID,
        actor: Actor,
        *,
        code: str,
        name: str,
        kind: str,
        currency: str,
    ) -> Fact:
        return self.store.append(
            db,
            F.account_opened(actor, company_id, code=code, name=name, kind=kind, currency=currency),
        )

    def post(
        self,
        db: Session,
        company_id: UUID,
        actor: Actor,
        *,
        lines: list[dict[str, Any]],
        currency: str,
        memo: str,
        entry_id: UUID | None = None,
        source: dict[str, Any] | None = None,
        run_id: UUID | None = None,
        task_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        state: LedgerState | None = None,
    ) -> Fact:
        new = F.entry_posted(
            actor,
            company_id,
            entry_id or uuid4(),
            lines=lines,
            currency=currency,
            memo=memo,
            source=source,
            run_id=run_id,
            task_id=task_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        state = state or self.state(db, company_id)
        for line in new.payload["lines"]:
            account = state.accounts.get(line["account"])
            if account is None:
                raise F.MoneyError(f"no account {line['account']!r} in company {company_id}")
            if account.currency != currency:
                raise F.MoneyError(
                    f"account {line['account']!r} is kept in {account.currency}, not {currency}"
                )
        return self.store.append(db, new)
