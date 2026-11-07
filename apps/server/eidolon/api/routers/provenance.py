"""Provenance: a fact's causal ancestry, and sealed bundles. Payloads are served
as recorded (facts never hold chain-of-thought; see runs.sanitize for why blob
refs are still dropped from the trace view)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.api.routers.runs import sanitize
from eidolon.identity import EmploymentRow
from eidolon.kernel import provenance
from eidolon.kernel.store import FactStore
from eidolon.money import ledger_fact_ids

router = APIRouter(tags=["provenance"])
store = FactStore()


@router.get("/facts/{fact_id}")
def fact(fact_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    f = store.get(db, fact_id)
    if f is None:
        raise HTTPException(404, "no such fact")
    if f.company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")
    return {
        "fact_id": str(f.id),
        "seq": f.seq,
        "chain_seq": f.chain_seq,
        "company_id": str(f.company_id),
        "kind": f.kind,
        "actor": f.actor.as_dict(),
        "payload": sanitize(f.payload),
        "causation_id": str(f.causation_id) if f.causation_id else None,
        "correlation_id": str(f.correlation_id),
        "decision_id": str(f.decision_id) if f.decision_id else None,
        "created_at": f.created_at.isoformat(),
        "hash": f.hash,
    }


@router.get("/facts/{fact_id}/trace")
def trace(
    fact_id: UUID,
    depth: int = 10,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    try:
        result = provenance.trace(db, fact_id, depth=min(max(depth, 0), 50))
    except provenance.UnknownFact as exc:
        raise HTTPException(404, str(exc)) from exc
    companies = {e.company_id for e in memberships(db, me)}
    root = next(f for f in result.facts if f.id == result.root)
    if root.company_id not in companies:
        raise HTTPException(403, "you are not employed by this company")
    return {
        "root": str(result.root),
        "facts": [
            {
                "fact_id": str(f.id),
                "seq": f.seq,
                "kind": f.kind,
                "actor": f.actor.as_dict(),
                "causation_id": str(f.causation_id) if f.causation_id else None,
                "correlation_id": str(f.correlation_id),
                "created_at": f.created_at.isoformat(),
                "payload": sanitize(f.payload),
            }
            for f in result.facts
            if f.company_id in companies
        ],
        "edges": [{"cause": str(e.cause), "effect": str(e.effect)} for e in result.edges],
        "depth_reached": result.depth_reached,
        "missing": [str(m) for m in result.missing],
    }


@router.get("/companies/{company_id}/bundle")
def bundle(
    company_id: UUID,
    correlation: UUID | None = None,
    with_ledger: bool = False,
    db: Session = Depends(get_db),
    _: EmploymentRow = Depends(member_of),
):
    """A sealed provenance bundle (the same JSON ``export-bundle`` writes), verifiable
    offline with ``scripts/verify_bundle.py``."""
    fact_ids = ledger_fact_ids(db, correlation) if (with_ledger and correlation) else None
    try:
        return provenance.bundle(db, company_id, correlation_id=correlation, fact_ids=fact_ids)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
