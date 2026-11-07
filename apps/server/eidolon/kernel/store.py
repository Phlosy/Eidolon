"""Persistence for the fact log: three tables and one writer.

``facts``        the log itself; append-only, enforced by a trigger (K1)
``fact_chains``  per-company tail (last chain_seq + last hash); its row lock serializes
                 appends for one company so the chain never forks (K3)
``fact_cursors`` consumer progress (K6)

Every state change in Eidolon v2 is either a fact or a projection of facts.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.config import settings
from eidolon.db import Base
from eidolon.kernel import hashchain
from eidolon.kernel.facts import Actor, ActorKind, Fact, NewFact


class FactRow(Base):
    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_facts_company_idempotency"),
        UniqueConstraint("company_id", "chain_seq", name="uq_facts_company_chain_seq"),
        Index("ix_facts_kind_seq", "kind", "seq"),
        Index("ix_facts_correlation", "correlation_id"),
        Index("ix_facts_company_seq", "company_id", "seq"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, nullable=False)
    chain_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    correlation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_fact(self) -> Fact:
        return Fact(
            id=self.id,
            seq=self.seq,
            chain_seq=self.chain_seq,
            company_id=self.company_id,
            actor=Actor(ActorKind(self.actor_kind), self.actor_id),
            kind=self.kind,
            payload=self.payload,
            idempotency_key=self.idempotency_key,
            causation_id=self.causation_id,
            correlation_id=self.correlation_id,
            decision_id=self.decision_id,
            prev_hash=self.prev_hash,
            hash=self.hash,
            created_at=self.created_at,
        )


class FactChainRow(Base):
    __tablename__ = "fact_chains"

    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=hashchain.GENESIS_HASH
    )


class FactCursorRow(Base):
    __tablename__ = "fact_cursors"

    consumer: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Immutability at the database level. Installed by the kernel migration; kept here
# as the single source so tests and migrations use identical DDL (K1).
IMMUTABILITY_DDL = (
    """
    CREATE OR REPLACE FUNCTION facts_are_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'facts are append-only (%)', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END $$;
    """,
    """
    CREATE TRIGGER facts_no_update_delete
    BEFORE UPDATE OR DELETE ON facts
    FOR EACH ROW EXECUTE FUNCTION facts_are_immutable();
    """,
)
IMMUTABILITY_DROP_DDL = (
    "DROP TRIGGER IF EXISTS facts_no_update_delete ON facts;",
    "DROP FUNCTION IF EXISTS facts_are_immutable();",
)


class IdempotencyConflict(ValueError):
    """Same (company, idempotency_key) submitted again with different content."""


class PayloadTooLarge(ValueError):
    pass


class FactStore:
    """The only writer of ``facts``. One instance is enough; it holds no state."""

    def __init__(self, *, payload_max_bytes: int | None = None, notify_channel: str | None = None):
        self.payload_max_bytes = payload_max_bytes or settings.fact_payload_max_bytes
        self.notify_channel = notify_channel or settings.fact_notify_channel

    # -- writing -----------------------------------------------------------------

    def append(self, db: Session, new: NewFact, *, now: datetime | None = None) -> Fact:
        """Record ``new`` in the caller's transaction and return the fact.

        Idempotent: resubmitting the same key with the same content returns the
        original fact; the same key with different content raises (K2). The chain
        row is locked ``FOR UPDATE`` for the rest of the transaction, so concurrent
        appends for one company serialize and the chain cannot fork (K3).
        """
        encoded = hashchain.canonical_json(new.payload)
        if len(encoded) > self.payload_max_bytes:
            raise PayloadTooLarge(
                f"payload is {len(encoded)} bytes; limit {self.payload_max_bytes}. "
                "Store large content elsewhere and reference it."
            )

        chain = self._lock_chain(db, new.company_id)

        existing = db.scalar(
            select(FactRow).where(
                FactRow.company_id == new.company_id,
                FactRow.idempotency_key == new.idempotency_key,
            )
        )
        if existing is not None:
            if (existing.kind, existing.payload, existing.actor_kind, existing.actor_id) != (
                new.kind,
                new.payload,
                new.actor.kind.value,
                new.actor.id,
            ):
                raise IdempotencyConflict(
                    f"idempotency_key {new.idempotency_key!r} already used for a different fact"
                )
            return existing.to_fact()

        created_at = (now or datetime.now(UTC)).astimezone(UTC)
        fact_id = uuid4()
        correlation_id = new.correlation_id or fact_id
        chain_seq = chain.last_seq + 1
        fact_hash = hashchain.digest(
            id=fact_id,
            company_id=new.company_id,
            chain_seq=chain_seq,
            actor=new.actor,
            kind=new.kind,
            payload=new.payload,
            idempotency_key=new.idempotency_key,
            causation_id=new.causation_id,
            correlation_id=correlation_id,
            decision_id=new.decision_id,
            created_at=created_at,
            prev_hash=chain.last_hash,
        )
        row = FactRow(
            id=fact_id,
            chain_seq=chain_seq,
            company_id=new.company_id,
            actor_kind=new.actor.kind.value,
            actor_id=new.actor.id,
            kind=new.kind,
            payload=new.payload,
            idempotency_key=new.idempotency_key,
            causation_id=new.causation_id,
            correlation_id=correlation_id,
            decision_id=new.decision_id,
            prev_hash=chain.last_hash,
            hash=fact_hash,
            created_at=created_at,
        )
        db.add(row)
        chain.last_seq = chain_seq
        chain.last_hash = fact_hash
        db.flush()
        # Delivered by PostgreSQL only if this transaction commits.
        db.execute(text(f"NOTIFY {self.notify_channel}"))
        return row.to_fact()

    def _lock_chain(self, db: Session, company_id: UUID) -> FactChainRow:
        chain = db.scalar(
            select(FactChainRow).where(FactChainRow.company_id == company_id).with_for_update()
        )
        if chain is not None:
            return chain
        # First fact for this company. Two writers may race here; the loser's insert
        # is a no-op and it then blocks on the winner's row lock.
        db.execute(
            insert(FactChainRow)
            .values(company_id=company_id, last_seq=0, last_hash=hashchain.GENESIS_HASH)
            .on_conflict_do_nothing(index_elements=["company_id"])
        )
        chain = db.scalar(
            select(FactChainRow).where(FactChainRow.company_id == company_id).with_for_update()
        )
        assert chain is not None
        return chain

    # -- reading -----------------------------------------------------------------

    def get(self, db: Session, fact_id: UUID) -> Fact | None:
        row = db.get(FactRow, fact_id)
        return row.to_fact() if row else None

    def last_seq(self, db: Session) -> int:
        return db.scalar(select(FactRow.seq).order_by(FactRow.seq.desc()).limit(1)) or 0

    def stream(
        self,
        db: Session,
        *,
        after_seq: int = 0,
        limit: int = 500,
        kinds: Iterable[str] | None = None,
        company_id: UUID | None = None,
    ) -> list[Fact]:
        """Facts in global order after ``after_seq``."""
        query = select(FactRow).where(FactRow.seq > after_seq).order_by(FactRow.seq).limit(limit)
        if kinds is not None:
            query = query.where(FactRow.kind.in_(list(kinds)))
        if company_id is not None:
            query = query.where(FactRow.company_id == company_id)
        return [row.to_fact() for row in db.scalars(query)]

    def chain(
        self, db: Session, company_id: UUID, *, after_chain_seq: int = 0, limit: int = 500
    ) -> list[Fact]:
        """One company's facts in chain order."""
        query = (
            select(FactRow)
            .where(FactRow.company_id == company_id, FactRow.chain_seq > after_chain_seq)
            .order_by(FactRow.chain_seq)
            .limit(limit)
        )
        return [row.to_fact() for row in db.scalars(query)]

    def by_correlation(self, db: Session, correlation_id: UUID) -> list[Fact]:
        query = (
            select(FactRow).where(FactRow.correlation_id == correlation_id).order_by(FactRow.seq)
        )
        return [row.to_fact() for row in db.scalars(query)]

    def verify_chain(
        self, db: Session, company_id: UUID, *, batch: int = 1000
    ) -> hashchain.ChainReport:
        """Walk the whole chain and report the first break, if any (K3)."""
        return hashchain.verify(company_id, self._iter_chain(db, company_id, batch))

    def _iter_chain(self, db: Session, company_id: UUID, batch: int):
        after = 0
        while True:
            page: Sequence[Fact] = self.chain(db, company_id, after_chain_seq=after, limit=batch)
            if not page:
                return
            yield from page
            after = page[-1].chain_seq
            if len(page) < batch:
                return
