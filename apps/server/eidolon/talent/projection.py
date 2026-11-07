"""Read model for talent: ``talent_evidence`` (one row per piece of evidence) and
``talent_competencies`` (one row per person × area). Both are rebuilt from the
facts the evidence source reads; nothing here is an input to any decision.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, Index, Integer, SmallInteger, String, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base
from eidolon.kernel.facts import Fact
from eidolon.talent.competency import Competency
from eidolon.talent.evidence import SOURCE_KINDS, Evidence, EvidenceSource


class EvidenceRow(Base):
    __tablename__ = "talent_evidence"
    __table_args__ = (
        Index("ix_talent_evidence_person", "person_id", "occurred_at"),
        Index("ix_talent_evidence_company_area", "company_id", "area"),
    )
    fact_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    person_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    area: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    signal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class CompetencyRow(Base):
    __tablename__ = "talent_competencies"
    person_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    area: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    raw: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positive: Mapped[int] = mapped_column(Integer, nullable=False)
    negative: Mapped[int] = mapped_column(Integer, nullable=False)
    last_evidence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    def as_competency(self) -> Competency:
        return Competency(self.raw, self.evidence_count, self.positive, self.negative)


TABLES = ("talent_evidence", "talent_competencies")


class TalentProjection:
    name = "talent"
    handles = SOURCE_KINDS

    def __init__(self, source: EvidenceSource | None = None):
        self.source = source or EvidenceSource()

    def handle(self, db: Session, fact: Fact) -> None:
        for piece in self.source.derive(db, fact):
            if db.get(EvidenceRow, (piece.fact_id, piece.person_id)) is not None:
                continue  # crash replay: this piece is already in
            db.add(_row(piece))
            comp = db.get(CompetencyRow, (piece.person_id, piece.area))
            if comp is None:
                comp = CompetencyRow(
                    person_id=piece.person_id,
                    area=piece.area,
                    score=50,
                    raw=0.0,
                    evidence_count=0,
                    positive=0,
                    negative=0,
                    last_evidence_at=piece.occurred_at,
                    last_seq=piece.seq,
                )
                db.add(comp)
            c = comp.as_competency().absorb(piece.signal, piece.weight)
            comp.raw, comp.score = c.raw, c.score
            comp.evidence_count, comp.positive, comp.negative = (
                c.evidence_count,
                c.positive,
                c.negative,
            )
            comp.last_evidence_at, comp.last_seq = piece.occurred_at, piece.seq
            db.flush()

    def reset(self, db: Session) -> None:
        db.execute(text(f"TRUNCATE {', '.join(TABLES)}"))


def _row(e: Evidence) -> EvidenceRow:
    return EvidenceRow(
        fact_id=e.fact_id,
        person_id=e.person_id,
        company_id=e.company_id,
        area=e.area,
        kind=e.kind,
        signal=e.signal,
        weight=e.weight,
        task_id=e.task_id,
        project_id=e.project_id,
        run_id=e.run_id,
        occurred_at=e.occurred_at,
        last_seq=e.seq,
    )


def evidence_of(db: Session, person_id: str, *, limit: int = 500) -> list[EvidenceRow]:
    return list(
        db.scalars(
            select(EvidenceRow)
            .where(EvidenceRow.person_id == person_id)
            .order_by(EvidenceRow.last_seq)
            .limit(limit)
        )
    )


def competencies_of(db: Session, person_id: str) -> list[CompetencyRow]:
    return list(
        db.scalars(
            select(CompetencyRow)
            .where(CompetencyRow.person_id == person_id)
            .order_by(CompetencyRow.area)
        )
    )
