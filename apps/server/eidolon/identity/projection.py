"""Read model for identity: four tables, rebuildable from ``identity.*`` facts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base
from eidolon.identity import facts as F
from eidolon.kernel.consumers import clear_tables
from eidolon.kernel.facts import Fact


class UserRow(Base):
    __tablename__ = "identity_users"
    __table_args__ = (Index("ix_identity_users_email", "email", unique=True),)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class CompanyRow(Base):
    __tablename__ = "identity_companies"
    __table_args__ = (Index("ix_identity_companies_slug", "slug", unique=True),)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonRow(Base):
    __tablename__ = "identity_persons"
    __table_args__ = (Index("ix_identity_persons_user", "user_id"),)
    person_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    persona: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class EmploymentRow(Base):
    __tablename__ = "identity_employments"
    __table_args__ = (
        Index("ix_identity_employments_company", "company_id", "status"),
        Index("ix_identity_employments_person", "person_id"),
    )
    employment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    person_id: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # active | ended
    decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(500))
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


TABLES = ("identity_users", "identity_companies", "identity_persons", "identity_employments")


class IdentityProjection:
    name = "identity"
    handles = F.KINDS

    def handle(self, db: Session, fact: Fact) -> None:
        p, k = fact.payload, fact.kind
        if k == F.USER_REGISTERED:
            db.execute(
                insert(UserRow)
                .values(
                    user_id=UUID(p["user_id"]),
                    email=p["email"],
                    display_name=p["display_name"],
                    status="active",
                    created_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
        elif k == F.COMPANY_CREATED:
            db.execute(
                insert(CompanyRow)
                .values(
                    company_id=UUID(p["company_id"]),
                    name=p["name"],
                    slug=p["slug"],
                    currency=p["currency"],
                    owner_user_id=UUID(p["owner_user_id"]),
                    created_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["company_id"])
            )
        elif k == F.PERSON_CREATED:
            db.execute(
                insert(PersonRow)
                .values(
                    person_id=p["person_id"],
                    name=p["name"],
                    kind=p["kind"],
                    user_id=UUID(p["user_id"]) if p.get("user_id") else None,
                    persona=dict(p.get("persona") or {}),
                    created_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["person_id"])
            )
        elif k == F.EMPLOYMENT_STARTED:
            db.execute(
                insert(EmploymentRow)
                .values(
                    employment_id=UUID(p["employment_id"]),
                    company_id=fact.company_id,
                    person_id=p["person_id"],
                    role=p["role"],
                    department_id=p.get("department_id"),
                    title=p.get("title"),
                    status="active",
                    decision_id=UUID(p["decision_id"]) if p.get("decision_id") else None,
                    started_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["employment_id"])
            )
        elif k == F.EMPLOYMENT_ENDED:
            row = db.get(EmploymentRow, UUID(p["employment_id"]))
            if row is not None and row.last_seq < fact.seq:
                row.status, row.ended_at, row.end_reason, row.last_seq = (
                    "ended",
                    fact.created_at,
                    p.get("reason"),
                    fact.seq,
                )

    def reset(self, db: Session) -> None:
        clear_tables(db, *TABLES)


def active_employments(db: Session, company_id: UUID) -> list[EmploymentRow]:
    return list(
        db.scalars(
            select(EmploymentRow)
            .where(EmploymentRow.company_id == company_id, EmploymentRow.status == "active")
            .order_by(EmploymentRow.started_at)
        )
    )
