"""``memory_entries``: the read model of memory facts. Rebuildable from zero
(``rebuild``), idempotent per fact (upserts keyed by memory_id; a replayed
``forgotten`` only lowers weight, so re-applying it is a no-op)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Computed, DateTime, Float, Index, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.agent.memory import facts as M
from eidolon.db import Base
from eidolon.kernel.consumers import clear_tables
from eidolon.kernel.facts import Fact

SEARCH_CONFIG = "english"  # Chinese needs zhparser/pg_jieba or vectors (pgvector, later)
SEARCH_EXPR = f"to_tsvector('{SEARCH_CONFIG}', coalesce(title, '') || ' ' || coalesce(body, ''))"


class MemoryEntryRow(Base):
    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("ix_memory_entries_company_person", "company_id", "person_id"),
        Index("ix_memory_entries_company_scope", "company_id", "scope"),
        Index("ix_memory_entries_search", "search", postgresql_using="gin"),
    )

    memory_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    person_id: Mapped[str] = mapped_column(String(200), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(nullable=False)
    search: Mapped[str] = mapped_column(TSVECTOR, Computed(SEARCH_EXPR, persisted=True))


TABLES = ("memory_entries",)


class MemoryProjection:
    name = "memory_entries"
    handles = M.KINDS

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        memory_id = UUID(p["memory_id"])
        if fact.kind == M.RECORDED:
            stmt = insert(MemoryEntryRow).values(
                memory_id=memory_id,
                company_id=fact.company_id,
                person_id=p["person_id"],
                layer=p["layer"],
                scope=p["scope"],
                department_id=p.get("department_id"),
                kind=p["kind"],
                title=p["title"],
                body=p["body"],
                tags=list(p.get("tags") or []),
                source=dict(p.get("source") or {}),
                importance=float(p.get("importance", 0.5)),
                weight=1.0,
                created_at=fact.created_at,
                updated_at=fact.created_at,
                last_seq=fact.seq,
            )
            db.execute(stmt.on_conflict_do_nothing(index_elements=["memory_id"]))
            return
        row = db.get(MemoryEntryRow, memory_id)
        if row is None or row.last_seq >= fact.seq:
            return  # unknown entry (foreign/missing) or already applied
        if fact.kind == M.PROMOTED:
            if M.SCOPE_RANK[p["to_scope"]] > M.SCOPE_RANK[row.scope]:
                row.scope = p["to_scope"]
                row.department_id = p.get("department_id")
        elif fact.kind == M.FORGOTTEN:
            row.weight = min(row.weight, float(p["weight"]))
        row.updated_at = fact.created_at
        row.last_seq = fact.seq

    def reset(self, db: Session) -> None:
        clear_tables(db, "memory_entries")


def count(db: Session) -> int:
    return db.scalar(select(text("count(*)")).select_from(MemoryEntryRow)) or 0
