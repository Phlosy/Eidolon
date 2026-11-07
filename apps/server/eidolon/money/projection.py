"""Read model for the ledger: ``money_accounts`` (one row per account with running
totals) and ``money_journal`` (one row per entry line). Rebuildable from
``money.account.opened`` / ``money.entry.posted``.

Attribution to a project is *not* stored: a line carries ``task_id``, and the
project roll-up joins ``work_tasks`` at query time, so the ledger never depends on
which consumer ran first.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base
from eidolon.kernel.consumers import clear_tables
from eidolon.kernel.facts import Fact
from eidolon.money import facts as F
from eidolon.work.projection import TaskRow


class AccountRow(Base):
    __tablename__ = "money_accounts"
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    debit_total_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credit_total_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    @property
    def balance_micro(self) -> int:
        d, c = self.debit_total_micro, self.credit_total_micro
        return d - c if F.normal_side(self.kind) == "debit" else c - d


class JournalRow(Base):
    __tablename__ = "money_journal"
    __table_args__ = (
        Index("ix_money_journal_company_posted", "company_id", "posted_at"),
        Index("ix_money_journal_run", "run_id"),
        Index("ix_money_journal_task", "task_id"),
        Index("ix_money_journal_entry", "entry_id"),
    )
    entry_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    account_code: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(6), nullable=False)
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    memo: Mapped[str] = mapped_column(String(500), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_fact_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    cause_fact_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_kind: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[dict | None] = mapped_column(JSONB)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


TABLES = ("money_accounts", "money_journal")


class LedgerProjection:
    name = "money_ledger"
    handles = F.LEDGER_KINDS

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        if fact.kind == F.ACCOUNT_OPENED:
            db.execute(
                insert(AccountRow)
                .values(
                    company_id=fact.company_id,
                    code=p["code"],
                    name=p["name"],
                    kind=p["kind"],
                    currency=p["currency"],
                    debit_total_micro=0,
                    credit_total_micro=0,
                    opened_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["company_id", "code"])
            )
        elif fact.kind == F.ENTRY_POSTED:
            entry_id = UUID(p["entry_id"])
            if db.get(JournalRow, (entry_id, 0)) is not None:
                return  # already projected (crash replay)
            source = p.get("source") or None
            for no, line in enumerate(p["lines"]):
                db.add(
                    JournalRow(
                        entry_id=entry_id,
                        line_no=no,
                        company_id=fact.company_id,
                        account_code=line["account"],
                        side=line["side"],
                        amount_micro=line["amount_micro"],
                        currency=p["currency"],
                        memo=p["memo"],
                        posted_at=fact.created_at,
                        entry_fact_id=fact.id,
                        cause_fact_id=fact.causation_id,
                        source_kind=source.get("kind") if source else None,
                        source=source,
                        run_id=UUID(p["run_id"]) if p.get("run_id") else None,
                        task_id=UUID(p["task_id"]) if p.get("task_id") else None,
                        last_seq=fact.seq,
                    )
                )
                account = db.get(AccountRow, (fact.company_id, line["account"]))
                if account is None:
                    raise F.MoneyError(
                        f"entry {entry_id} posts to unknown account {line['account']!r}"
                    )
                if line["side"] == "debit":
                    account.debit_total_micro += line["amount_micro"]
                else:
                    account.credit_total_micro += line["amount_micro"]
                account.last_seq = fact.seq
            db.flush()

    def reset(self, db: Session) -> None:
        clear_tables(db, *TABLES)


# -- queries --------------------------------------------------------------------------


def accounts(db: Session, company_id: UUID) -> list[AccountRow]:
    return list(
        db.scalars(
            select(AccountRow).where(AccountRow.company_id == company_id).order_by(AccountRow.code)
        )
    )


def _expense_debits(db: Session, *where) -> int:
    """Σ debit lines on expense accounts — what was *spent*, in micro-units."""
    query = (
        select(func.coalesce(func.sum(JournalRow.amount_micro), 0))
        .join(
            AccountRow,
            (AccountRow.company_id == JournalRow.company_id)
            & (AccountRow.code == JournalRow.account_code),
        )
        .where(AccountRow.kind == "expense", JournalRow.side == "debit", *where)
    )
    return int(db.scalar(query) or 0)


def run_spend_micro(db: Session, run_id: UUID) -> int:
    return _expense_debits(db, JournalRow.run_id == run_id)


def task_spend_micro(db: Session, task_id: UUID) -> int:
    return _expense_debits(db, JournalRow.task_id == task_id)


def project_spend_micro(db: Session, project_id: UUID) -> int:
    task_ids = select(TaskRow.task_id).where(TaskRow.project_id == project_id)
    return _expense_debits(db, JournalRow.task_id.in_(task_ids))


def company_spend_micro(db: Session, company_id: UUID) -> int:
    return _expense_debits(db, JournalRow.company_id == company_id)
