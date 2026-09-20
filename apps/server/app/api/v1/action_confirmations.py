"""Authenticated Owner decisions over fixed proposals, never arbitrary tool writes."""

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentAuth, current_auth
from app.core.database import get_db
from app.schemas.action_confirmation import (
    ActionConfirmationOut,
    ConfirmationCommand,
    ConfirmationStatus,
)
from app.work import action_confirmations as service

router = APIRouter(prefix="/action-confirmations", tags=["action-confirmations"])


def owner_command(request: Request, auth: CurrentAuth = Depends(current_auth)):
    cookie = request.cookies.get("eidolon_csrf", "")
    header = request.headers.get("x-csrf-token", "")
    if (
        not cookie
        or not header
        or not secrets.compare_digest(cookie, header)
        or not secrets.compare_digest(header, auth.session.csrf_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF token invalid")
    return auth


@router.get("", response_model=list[ActionConfirmationOut])
def list_confirmations(
    status: ConfirmationStatus | None = None,
    project_id: int | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    return service.list_for_company(
        db,
        auth.company.id,
        status=status,
        project_id=project_id,
        after_id=after_id,
        limit=limit,
    )


@router.get("/{confirmation_id}", response_model=ActionConfirmationOut)
def get_confirmation(
    confirmation_id: int,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    try:
        return service.view(db, service.get_for_company(db, auth.company.id, confirmation_id))
    except service.ConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


def _command(db, auth, confirmation_id, action):
    identity = dict(company_id=auth.company.id, user_id=auth.user.id, session_id=auth.session.id)
    try:
        if action in {"approve", "reject"}:
            row = service.review(db, confirmation_id, **identity, approve=action == "approve")
        if action in {"approve", "execute"}:
            row = service.execute(db, confirmation_id, **identity)
        if action == "expire":
            row = service.expire(db, confirmation_id, **identity)
        return service.view(db, row)
    except service.ConfirmationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


@router.post("/{confirmation_id}/approve", response_model=ActionConfirmationOut)
def approve_confirmation(
    confirmation_id: int,
    command: ConfirmationCommand = Body(default_factory=ConfirmationCommand),
    auth: CurrentAuth = Depends(owner_command),
    db: Session = Depends(get_db),
):
    return _command(db, auth, confirmation_id, "approve")


@router.post("/{confirmation_id}/reject", response_model=ActionConfirmationOut)
def reject_confirmation(
    confirmation_id: int,
    command: ConfirmationCommand = Body(default_factory=ConfirmationCommand),
    auth: CurrentAuth = Depends(owner_command),
    db: Session = Depends(get_db),
):
    return _command(db, auth, confirmation_id, "reject")


@router.post("/{confirmation_id}/execute", response_model=ActionConfirmationOut)
def execute_confirmation(
    confirmation_id: int,
    command: ConfirmationCommand = Body(default_factory=ConfirmationCommand),
    auth: CurrentAuth = Depends(owner_command),
    db: Session = Depends(get_db),
):
    return _command(db, auth, confirmation_id, "execute")


@router.post("/{confirmation_id}/expire", response_model=ActionConfirmationOut)
def expire_confirmation(
    confirmation_id: int,
    command: ConfirmationCommand = Body(default_factory=ConfirmationCommand),
    auth: CurrentAuth = Depends(owner_command),
    db: Session = Depends(get_db),
):
    return _command(db, auth, confirmation_id, "expire")
