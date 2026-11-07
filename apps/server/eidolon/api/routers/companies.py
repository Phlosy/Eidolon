"""Companies, their institution, their people. Read-only."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.identity import CompanyRow, EmploymentRow, PersonRow, active_employments
from eidolon.institution import current as current_institution
from eidolon.talent import competencies_of, profile, resume

router = APIRouter(tags=["companies"])


def _company(row: CompanyRow) -> dict:
    return {
        "company_id": str(row.company_id),
        "name": row.name,
        "slug": row.slug,
        "currency": row.currency,
        "owner_user_id": str(row.owner_user_id),
        "created_at": row.created_at.isoformat(),
    }


def _employment(e: EmploymentRow) -> dict:
    return {
        "employment_id": str(e.employment_id),
        "person_id": e.person_id,
        "role": e.role,
        "title": e.title,
        "department_id": e.department_id,
        "status": e.status,
        "started_at": e.started_at.isoformat(),
        "ended_at": e.ended_at.isoformat() if e.ended_at else None,
    }


@router.get("/companies")
def my_companies(db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    ids = {e.company_id for e in memberships(db, me)}
    rows = db.scalars(select(CompanyRow).where(CompanyRow.company_id.in_(ids))) if ids else []
    return [_company(r) for r in rows]


@router.get("/companies/{company_id}")
def company(
    company_id: UUID,
    db: Session = Depends(get_db),
    _: EmploymentRow = Depends(member_of),
):
    row = db.get(CompanyRow, company_id)
    if row is None:
        raise HTTPException(404, "no such company")
    institution = current_institution(db, company_id)
    out = _company(row)
    out["institution"] = (
        {
            "template_id": institution.template.id,
            "version": institution.template.version,
            "name": institution.template.name,
            "fingerprint": institution.fingerprint,
            "changes": institution.changes,
        }
        if institution
        else None
    )
    out["headcount"] = len(active_employments(db, company_id))
    return out


@router.get("/companies/{company_id}/institution")
def institution(
    company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)
):
    state = current_institution(db, company_id)
    if state is None:
        raise HTTPException(404, "this company has adopted no institution")
    return {
        "fingerprint": state.fingerprint,
        "changes": state.changes,
        "adopted_fact_id": str(state.adopted_fact_id),
        "template": state.template.as_dict(),
    }


@router.get("/companies/{company_id}/employees")
def employees(
    company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)
):
    rows = active_employments(db, company_id)
    persons = {
        p.person_id: p
        for p in db.scalars(
            select(PersonRow).where(PersonRow.person_id.in_([r.person_id for r in rows]))
        )
    }
    out = []
    for e in rows:
        p = persons.get(e.person_id)
        out.append(
            {
                **_employment(e),
                "name": p.name if p else e.person_id,
                "kind": p.kind if p else None,
                "competencies": [
                    {"area": c.area, "score": c.score, "evidence_count": c.evidence_count}
                    for c in competencies_of(db, e.person_id)
                ],
            }
        )
    return out


@router.get("/persons/{person_id}")
def person(
    person_id: str,
    with_resume: bool = False,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    """A person's profile. Visible to anyone who shares a company with them (or to
    themselves)."""
    prof = profile(db, person_id)
    if prof is None:
        raise HTTPException(404, "no such person")
    mine = {e.company_id for e in memberships(db, me)}
    theirs = {e.company_id for e in prof.employments if e.status == "active"}
    if person_id not in me.person_ids and not (mine & theirs):
        raise HTTPException(403, "you share no company with this person")
    out = prof.as_dict()
    if with_resume:
        out["resume"] = [
            {
                "at": r.at.isoformat(),
                "kind": r.kind,
                "company_id": str(r.company_id),
                "area": r.area,
                **r.detail,
            }
            for r in resume(db, person_id)
        ]
    return out
