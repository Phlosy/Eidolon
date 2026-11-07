"""Money, read-only: balances, project costs, cost traces."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.identity import EmploymentRow
from eidolon.money import TraceError, accounts, company_spend_micro, cost_trace, project_costs
from eidolon.money import verify as ledger_verify
from eidolon.work.projection import ProjectRow

router = APIRouter(tags=["ledger"])


@router.get("/companies/{company_id}/ledger")
def balances(
    company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)
):
    rows = accounts(db, company_id)
    return {
        "company_id": str(company_id),
        "spend_micro": company_spend_micro(db, company_id),
        "accounts": [
            {
                "code": r.code,
                "name": r.name,
                "kind": r.kind,
                "currency": r.currency,
                "debit_total_micro": r.debit_total_micro,
                "credit_total_micro": r.credit_total_micro,
                "balance_micro": r.balance_micro,
            }
            for r in rows
        ],
    }


@router.get("/companies/{company_id}/ledger/verify")
def verify(company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)):
    report = ledger_verify(db, company_id)
    return {
        "ok": report.ok,
        "accounts": report.accounts,
        "entries": report.entries,
        "costs": report.costs,
        "unposted": report.unposted,
        "projection_lag": report.projection_lag,
        "debit_total": report.debit_total,
        "credit_total": report.credit_total,
        "problems": report.problems,
    }


@router.get("/projects/{project_id}/costs")
def costs(project_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(404, "no such project")
    if row.company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")
    return project_costs(db, project_id).as_dict()


@router.get("/cost-trace/{ref}")
def trace(ref: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    try:
        t = cost_trace(db, ref)
    except TraceError as exc:
        raise HTTPException(404, str(exc)) from exc
    if t.cost.company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")
    out = t.as_dict()
    out["chain"] = [
        {"seq": f.seq, "fact_id": str(f.id), "kind": f.kind, "at": f.created_at.isoformat()}
        for f in t.chain
    ]
    return out
