"""Profile and resume: pure queries over identity, talent and money read models.
Nothing is stored; change the shape freely."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.identity import EmploymentRow, IdentityService, PersonState
from eidolon.kernel.store import FactStore
from eidolon.money.projection import run_spend_micro
from eidolon.talent.projection import CompetencyRow, EvidenceRow, competencies_of, evidence_of


@dataclass
class ResumeEntry:
    at: datetime
    kind: str  # employment_started | employment_ended | <evidence kind>
    company_id: UUID
    area: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Profile:
    person: PersonState
    employments: list[EmploymentRow]
    competencies: list[CompetencyRow]
    evidence: list[EvidenceRow]
    spend_micro: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person.person_id,
            "name": self.person.name,
            "kind": self.person.kind,
            "persona": self.person.persona,
            "employments": [
                {
                    "company_id": str(e.company_id),
                    "role": e.role,
                    "title": e.title,
                    "status": e.status,
                    "started_at": e.started_at.isoformat(),
                    "ended_at": e.ended_at.isoformat() if e.ended_at else None,
                }
                for e in self.employments
            ],
            "competencies": [
                {
                    "area": c.area,
                    "score": c.score,
                    "rated": c.positive + c.negative > 0,
                    "evidence_count": c.evidence_count,
                    "positive": c.positive,
                    "negative": c.negative,
                    "last_evidence_at": c.last_evidence_at.isoformat(),
                }
                for c in self.competencies
            ],
            "evidence_count": len(self.evidence),
            "spend_micro": self.spend_micro,
        }


def profile(db: Session, person_id: str, *, store: FactStore | None = None) -> Profile | None:
    store = store or FactStore()
    person = IdentityService(store).person(db, person_id)
    if person is None:
        return None
    employments = list(
        db.scalars(
            select(EmploymentRow)
            .where(EmploymentRow.person_id == person_id)
            .order_by(EmploymentRow.started_at)
        )
    )
    evidence = evidence_of(db, person_id)
    run_ids = {e.run_id for e in evidence if e.run_id is not None}
    spend = sum(run_spend_micro(db, rid) for rid in run_ids)
    return Profile(person, employments, competencies_of(db, person_id), evidence, spend)


def resume(db: Session, person_id: str) -> list[ResumeEntry]:
    """The person's history as one timeline: employments and evidence, oldest first."""
    entries: list[ResumeEntry] = []
    for e in db.scalars(select(EmploymentRow).where(EmploymentRow.person_id == person_id)):
        entries.append(
            ResumeEntry(
                e.started_at,
                "employment_started",
                e.company_id,
                e.role,
                {"title": e.title, "employment_id": str(e.employment_id)},
            )
        )
        if e.ended_at is not None:
            entries.append(
                ResumeEntry(
                    e.ended_at,
                    "employment_ended",
                    e.company_id,
                    e.role,
                    {"reason": e.end_reason, "employment_id": str(e.employment_id)},
                )
            )
    for ev in evidence_of(db, person_id, limit=10_000):
        entries.append(
            ResumeEntry(
                ev.occurred_at,
                ev.kind,
                ev.company_id,
                ev.area,
                {
                    "signal": ev.signal,
                    "fact_id": str(ev.fact_id),
                    "task_id": str(ev.task_id) if ev.task_id else None,
                    "project_id": str(ev.project_id) if ev.project_id else None,
                    "run_id": str(ev.run_id) if ev.run_id else None,
                },
            )
        )
    entries.sort(key=lambda r: (r.at, r.kind))
    return entries


__all__ = ["Profile", "ResumeEntry", "profile", "resume"]
