"""Human account, session and WebAuthn endpoints."""

import mimetypes
import secrets
from pathlib import Path, PurePath

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentAuth, current_auth
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import PasskeyCredential, UserSession
from app.models.base import utcnow
from app.schemas.auth import (
    AccountActionConfirmRequest,
    AccountActionRequestOut,
    AccountActionResultOut,
    AuthStateOut,
    EmailChangeRequest,
    LoginRequest,
    PasskeyNameRequest,
    PasskeyOut,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    SessionOut,
    VerifyEmailRequest,
    WebAuthnOptionsOut,
    WebAuthnVerifyRequest,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    result, _ = auth_service.register(db, payload, request, background)
    return result


@router.post("/verify-email/resend", response_model=AccountActionRequestOut)
def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    return auth_service.resend_verification(db, str(payload.email), request, background)


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


@router.patch("/me", response_model=AuthStateOut)
def update_profile(
    payload: ProfileUpdateRequest,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    auth.user.display_name = payload.display_name.strip()
    db.commit()
    return auth_service.auth_state(db, auth.user)


@router.post("/me/delete", response_model=AccountActionRequestOut)
def request_account_deletion(
    request: Request,
    background: BackgroundTasks,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    """注销账号第一步：发确认邮件。真正删除发生在邮件链接确认时。"""
    return auth_service.request_account_deletion(db, auth.user, request, background)


@router.post("/me/password", response_model=AccountActionRequestOut)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    background: BackgroundTasks,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    """改密码第一步：验当前密码后发确认邮件，点链接后才生效。"""
    return auth_service.request_password_change(db, auth.user, payload, request, background)


@router.post("/me/email", response_model=AccountActionRequestOut)
def request_email_change(
    payload: EmailChangeRequest,
    request: Request,
    background: BackgroundTasks,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    return auth_service.request_email_change(db, auth.user, payload, request, background)


@router.post("/account-actions/confirm", response_model=AccountActionResultOut)
def confirm_account_action(
    payload: AccountActionConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    # 公开端点：token 本身就是"拥有该邮箱"的证明，点击邮件链接时不一定有会话
    return auth_service.confirm_account_action(db, payload.token, request, response)


_AVATAR_MEDIA_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_AVATAR_MAX_BYTES = 2 * 1024 * 1024


def _avatar_directory() -> Path:
    directory = Path(settings.data_root) / "avatars"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@router.post("/me/avatar", response_model=AuthStateOut)
async def upload_avatar(
    request: Request,
    auth: CurrentAuth = Depends(current_auth),
    db: Session = Depends(get_db),
):
    # 二进制直传（Content-Type 即图片类型），不走 multipart
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    extension = _AVATAR_MEDIA_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail="avatar must be a PNG, JPEG, WebP or GIF image")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="avatar file is empty")
    if len(body) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="avatar must be smaller than 2 MB")
    filename = f"{auth.user.id}-{secrets.token_hex(6)}{extension}"
    _avatar_directory().joinpath(filename).write_bytes(body)
    old = auth.user.avatar
    auth.user.avatar = f"/api/v1/auth/avatars/{filename}"
    auth_service.record_audit_event(
        db, "user.avatar_updated", request=request, user_id=auth.user.id
    )
    db.commit()
    if old.startswith("/api/v1/auth/avatars/"):
        stale = _avatar_directory() / old.rsplit("/", 1)[-1]
        if stale.name != filename:
            stale.unlink(missing_ok=True)
    return auth_service.auth_state(db, auth.user)


@router.get("/avatars/{filename}")
def get_avatar(filename: str):
    # 文件名是服务端生成的随机名；这里再挡一次路径穿越
    if PurePath(filename).name != filename:
        raise HTTPException(status_code=404, detail="avatar not found")
    path = _avatar_directory() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="avatar not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


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
