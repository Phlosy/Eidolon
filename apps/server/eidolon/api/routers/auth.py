"""Accounts and sessions. Cookies: ``<session_cookie_name>`` (HttpOnly, the token)
and ``eidolon_csrf`` (readable; echoed back in ``X-CSRF-Token``)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from eidolon.api.auth import MIN_PASSWORD, AuthError, AuthService, Login
from eidolon.api.deps import CurrentUser, auth_service, current_user, get_db, memberships
from eidolon.api.passkeys import PasskeyError, PasskeyService

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD, max_length=200)
    person_id: str | None = Field(default=None, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=200)


def _set_cookies(request: Request, response: Response, login: Login) -> None:
    cfg = request.app.state.settings
    max_age = cfg.session_ttl_hours * 3600
    response.set_cookie(
        cfg.session_cookie_name,
        login.token,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        "eidolon_csrf",
        login.csrf,
        httponly=False,
        secure=cfg.cookie_secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_cookies(request: Request, response: Response) -> None:
    response.delete_cookie(request.app.state.settings.session_cookie_name, path="/")
    response.delete_cookie("eidolon_csrf", path="/")


def _limit_keys(request: Request, email: str | None = None) -> tuple[str, ...]:
    ip = request.client.host if request.client else "unknown"
    keys = (f"ip:{ip}",)
    return keys + (f"account:{email.strip().lower()}",) if email else keys


def _check_limit(request: Request, *keys: str) -> None:
    wait = request.app.state.limiter.blocked(*keys)
    if wait is not None:
        raise HTTPException(
            429,
            f"too many failed sign-in attempts; try again in {int(wait) + 1}s",
            headers={"Retry-After": str(int(wait) + 1)},
        )


def _me(db: Session, me: CurrentUser) -> dict:
    return {
        "user_id": str(me.user.user_id),
        "email": me.user.email,
        "display_name": me.user.display_name,
        "person_ids": list(me.person_ids),
        "companies": [
            {
                "company_id": str(e.company_id),
                "person_id": e.person_id,
                "role": e.role,
                "title": e.title,
            }
            for e in memberships(db, me)
        ],
        "session_id": str(me.session.session_id),
    }


@router.post("/register", status_code=201)
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(auth_service),
):
    try:
        user_id, person_id = auth.register(
            db,
            email=body.email,
            display_name=body.display_name,
            password=body.password,
            person_id=body.person_id,
        )
        login = auth.login(
            db,
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent", ""),
        )
    except AuthError as exc:
        raise HTTPException(409 if "exists" in str(exc) else 422, str(exc)) from exc
    _set_cookies(request, response, login)
    return {"user_id": str(user_id), "person_id": person_id}


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(auth_service),
):
    keys = _limit_keys(request, body.email)
    _check_limit(request, *keys)
    try:
        login = auth.login(
            db,
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent", ""),
        )
    except AuthError as exc:
        request.app.state.limiter.failed(*keys)
        raise HTTPException(401, str(exc)) from exc
    request.app.state.limiter.succeeded(*keys)
    _set_cookies(request, response, login)
    return {"user_id": str(login.session.user_id), "session_id": str(login.session.session_id)}


@router.get("/me")
def me(db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    return _me(db, me)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(auth_service),
):
    auth.revoke(db, me.session)
    _clear_cookies(request, response)
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
def logout_all(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(auth_service),
):
    auth.revoke_all(db, me.user.user_id)
    _clear_cookies(request, response)
    return Response(status_code=204)


@router.get("/sessions")
def sessions(
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(auth_service),
):
    return [
        {
            "session_id": str(s.session_id),
            "current": s.session_id == me.session.session_id,
            "created_at": s.created_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "user_agent": s.user_agent,
        }
        for s in auth.sessions_of(db, me.user.user_id)
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(auth_service),
):
    target = next(
        (s for s in auth.sessions_of(db, me.user.user_id) if s.session_id == session_id), None
    )
    if target is None:
        raise HTTPException(404, "no such session")
    auth.revoke(db, target)
    return Response(status_code=204)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    auth: AuthService = Depends(auth_service),
):
    try:
        auth.change_password(db, me.user.user_id, old=body.old_password, new=body.new_password)
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    auth.revoke_all(db, me.user.user_id, except_session=me.session.session_id)
    return Response(status_code=204)


# -- passkeys (WebAuthn) ----------------------------------------------------------------


class PasskeyNameIn(BaseModel):
    name: str = Field(default="Passkey", min_length=1, max_length=120)


class WebAuthnVerifyIn(BaseModel):
    challenge: str = Field(min_length=8, max_length=256)
    credential: dict


def passkeys(request: Request) -> PasskeyService:
    return request.app.state.passkeys


@router.get("/passkeys")
def list_passkeys(
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    svc: PasskeyService = Depends(passkeys),
):
    return [p.as_dict() for p in svc.list(db, me.user.user_id)]


@router.post("/passkeys/registration/options")
def passkey_registration_options(
    body: PasskeyNameIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    svc: PasskeyService = Depends(passkeys),
):
    return svc.registration_options(
        db,
        user_id=me.user.user_id,
        email=me.user.email,
        display_name=me.user.display_name,
        name=body.name,
    )


@router.post("/passkeys/registration/verify", status_code=201)
def passkey_registration_verify(
    body: WebAuthnVerifyIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    svc: PasskeyService = Depends(passkeys),
):
    try:
        return svc.verify_registration(
            db, user_id=me.user.user_id, challenge=body.challenge, credential=body.credential
        ).as_dict()
    except PasskeyError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.post("/passkeys/authentication/options")
def passkey_authentication_options(
    request: Request, db: Session = Depends(get_db), svc: PasskeyService = Depends(passkeys)
):
    _check_limit(request, *_limit_keys(request))
    return svc.authentication_options(db)


@router.post("/passkeys/authentication/verify")
def passkey_authentication_verify(
    body: WebAuthnVerifyIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    svc: PasskeyService = Depends(passkeys),
    auth: AuthService = Depends(auth_service),
):
    keys = _limit_keys(request)
    _check_limit(request, *keys)
    try:
        passkey = svc.verify_authentication(
            db, challenge=body.challenge, credential=body.credential
        )
    except PasskeyError as exc:
        request.app.state.limiter.failed(*keys)
        raise HTTPException(exc.status, exc.detail) from exc
    request.app.state.limiter.succeeded(*keys)
    login = auth.open_session(db, passkey.user_id, user_agent=request.headers.get("user-agent", ""))
    _set_cookies(request, response, login)
    return {"user_id": str(login.session.user_id), "session_id": str(login.session.session_id)}


@router.patch("/passkeys/{credential_id}")
def rename_passkey(
    credential_id: str,
    body: PasskeyNameIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    svc: PasskeyService = Depends(passkeys),
):
    try:
        return svc.rename(db, me.user.user_id, credential_id, body.name).as_dict()
    except PasskeyError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.delete("/passkeys/{credential_id}", status_code=204)
def remove_passkey(
    credential_id: str,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    svc: PasskeyService = Depends(passkeys),
):
    try:
        svc.remove(db, me.user.user_id, credential_id)
    except PasskeyError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return Response(status_code=204)
