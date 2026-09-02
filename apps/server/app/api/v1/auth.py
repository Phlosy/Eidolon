"""Human account, session and WebAuthn endpoints."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentAuth, current_auth
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import PasskeyCredential, UserSession
from app.models.base import utcnow
from app.schemas.auth import (
    AuthStateOut,
    LoginRequest,
    PasskeyNameRequest,
    PasskeyOut,
    RegisterRequest,
    RegisterResponse,
    SessionOut,
    VerifyEmailRequest,
    WebAuthnOptionsOut,
    WebAuthnVerifyRequest,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    result, _ = auth_service.register(db, payload, request)
    return result


@router.post("/verify-email", response_model=AuthStateOut)
def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    return auth_service.verify_email(db, payload.token, request, response)


@router.post("/login", response_model=AuthStateOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    return auth_service.authenticate_password(db, payload, request, response)


@router.get("/me", response_model=AuthStateOut)
def me(auth: CurrentAuth = Depends(current_auth), db: Session = Depends(get_db)):
    return auth_service.auth_state(db, auth.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    resolved = auth_service.session_from_token(
        db, request.cookies.get(settings.session_cookie_name)
    )
    if resolved:
        session, _ = resolved
        session.revoked_at = utcnow()
        db.commit()
    auth_service.clear_session_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    response: Response,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    for session in db.scalars(
        select(UserSession).where(
            UserSession.user_id == auth.user.id, UserSession.revoked_at.is_(None)
        )
    ):
        session.revoked_at = utcnow()
    db.commit()
    auth_service.clear_session_cookies(response)


@router.get("/sessions", response_model=list[SessionOut])
def sessions(auth: CurrentAuth = Depends(current_auth), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(UserSession)
            .where(UserSession.user_id == auth.user.id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.last_seen_at.desc())
        )
    )
    return [
        SessionOut.model_validate(row).model_copy(update={"current": row.id == auth.session.id})
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: int,
    response: Response,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != auth.user.id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    row.revoked_at = utcnow()
    db.commit()
    if row.id == auth.session.id:
        auth_service.clear_session_cookies(response)


@router.get("/passkeys", response_model=list[PasskeyOut])
def list_passkeys(auth: CurrentAuth = Depends(current_auth), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(PasskeyCredential)
            .where(PasskeyCredential.user_id == auth.user.id)
            .order_by(PasskeyCredential.created_at)
        )
    )


@router.post("/passkeys/registration/options", response_model=WebAuthnOptionsOut)
def passkey_registration_options(
    payload: PasskeyNameRequest,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    return auth_service.registration_options(db, auth.user, payload.name)


@router.post(
    "/passkeys/registration/verify",
    response_model=PasskeyOut,
    status_code=status.HTTP_201_CREATED,
)
def passkey_registration_verify(
    payload: WebAuthnVerifyRequest,
    request: Request,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    return auth_service.verify_registration(db, auth.user, payload, request)


@router.post("/passkeys/authentication/options", response_model=WebAuthnOptionsOut)
def passkey_authentication_options(db: Session = Depends(get_db)):
    return auth_service.authentication_options(db)


@router.post("/passkeys/authentication/verify", response_model=AuthStateOut)
def passkey_authentication_verify(
    payload: WebAuthnVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    return auth_service.authenticate_passkey(db, payload, request, response)


@router.patch("/passkeys/{passkey_id}", response_model=PasskeyOut)
def rename_passkey(
    passkey_id: int,
    payload: PasskeyNameRequest,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    row = db.get(PasskeyCredential, passkey_id)
    if row is None or row.user_id != auth.user.id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="passkey not found")
    row.name = payload.name.strip()
    db.commit()
    db.refresh(row)
    return row


@router.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_passkey(
    passkey_id: int,
    request: Request,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    row = db.get(PasskeyCredential, passkey_id)
    if row is not None and row.user_id == auth.user.id:
        db.delete(row)
        auth_service.record_audit_event(
            db, "user.passkey_removed", request=request, user_id=auth.user.id
        )
        db.commit()
