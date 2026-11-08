"""Decisions: what waits for a human, and how a human signs. The envelope is the
institution's (request / sign / conclude / record); the API only carries it."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from eidolon.api.commands import (
    Commands,
    Conflict,
    DomainError,
    Forbidden,
    decision_dict,
    idem,
    person_in,
)
from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.api.routers.manage import guard
from eidolon.identity import EmploymentRow
from eidolon.institution import DecisionError, load

router = APIRouter(tags=["decisions"])
commands = Commands()


class OpenIn(BaseModel):
    gate: str = Field(min_length=1, max_length=64)
    subject: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] | None = None  # human gates: the exact action to bind
    amount: int | None = None
    author: str | None = None


class FollowIn(BaseModel):
    decision_ids: list[UUID] | None = None  # default: everything pending for a human


class SignIn(BaseModel):
    verdict: str = Field(pattern="^(approve|reject|rework)$")
    reason: str | None = Field(default=None, max_length=2000)
    action: dict[str, Any] | None = None  # restate the bound action (default: as bound)


@router.get("/companies/{company_id}/decisions")
def pending(
    company_id: UUID,
    decider: str | None = "human",
    status: str | None = "pending",
    db: Session = Depends(get_db),
    _: EmploymentRow = Depends(member_of),
):
    return [
        decision_dict(d)
        for d in commands.pending_decisions(db, company_id, decider=decider, status=status)
    ]


@router.get("/decisions/{decision_id}")
def decision(
    decision_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)
):
    try:
        d = load(db, decision_id)
    except DecisionError as exc:
        raise HTTPException(404, str(exc)) from exc
    if d.company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")
    return decision_dict(d)


@router.post("/companies/{company_id}/decisions", status_code=201)
def open_decision(
    company_id: UUID,
    body: OpenIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    employment: EmploymentRow = Depends(member_of),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        d = commands.open_decision(
            db,
            person_in(db, employment),
            gate=body.gate,
            subject=body.subject,
            action=body.action,
            amount=body.amount,
            author=body.author,
            decision_id=idem(me.user.user_id, idempotency_key),
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return decision_dict(d)


@router.post("/companies/{company_id}/decisions/follow")
def follow_recommendations(
    company_id: UUID,
    body: FollowIn,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    """R7-e: sign the pending decisions the way the company recommended, in one go."""
    return commands.follow_recommendations(
        db, person_in(db, employment), decision_ids=body.decision_ids
    )


@router.post("/decisions/{decision_id}/sign")
def sign_decision(
    decision_id: UUID,
    body: SignIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    try:
        d = load(db, decision_id)
    except DecisionError as exc:
        raise HTTPException(404, str(exc)) from exc
    employment = next((e for e in memberships(db, me) if e.company_id == d.company_id), None)
    if employment is None:
        raise HTTPException(403, "you are not employed by this company")
    try:
        state, applied = commands.sign_decision(
            db,
            person_in(db, employment),
            decision_id=decision_id,
            verdict=body.verdict,
            reason=body.reason,
            action=body.action,
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    out = decision_dict(state)
    out["applied"] = applied
    return out


@router.post("/decisions/{decision_id}/escalate")
def escalate_decision(
    decision_id: UUID,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    """Force an open decision to its next decider (the gate's escalate_to)."""
    try:
        d = load(db, decision_id)
    except DecisionError as exc:
        raise HTTPException(404, str(exc)) from exc
    employment = next((e for e in memberships(db, me) if e.company_id == d.company_id), None)
    if employment is None:
        raise HTTPException(403, "you are not employed by this company")
    try:
        state = commands.escalate_decision(db, person_in(db, employment), decision_id=decision_id)
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return decision_dict(state)
