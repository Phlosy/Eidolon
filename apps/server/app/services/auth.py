"""Human authentication using Argon2id, opaque sessions and WebAuthn."""

from __future__ import annotations

import hashlib
import json
import secrets
import smtplib
import time
from collections import deque
from datetime import timedelta
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.models.auth import (
    AccountActionToken,
    CompanyMembership,
    EmailVerificationToken,
    PasskeyCredential,
    PendingRegistration,
    User,
    UserAuditEvent,
    UserSession,
    WebAuthnChallenge,
)
from app.models.base import utcnow
from app.models.organization import Company, Department
from app.schemas.auth import AuthStateOut, MembershipOut, UserOut
from app.schemas.organization import CompanyOut
from app.services.email_delivery import send_account_action_email, send_verification_email

_password_hasher = PasswordHasher()
_attempts: dict[str, deque[float]] = {}
_attempt_lock = Lock()
_DEPARTMENTS = [
    ("Executive", "executive"),
    ("Product", "product"),
    ("Research", "research"),
    ("Engineering", "engineering"),
    ("QA", "qa"),
]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _is_expired(value) -> bool:
    """SQLite returns naive datetimes even when UTC values were persisted."""
    now = utcnow()
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return value <= now


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def enforce_rate_limit(key: str, *, limit: int = 8, window_seconds: int = 60) -> None:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _attempt_lock:
        if key not in _attempts and len(_attempts) >= 10_000:
            for candidate, timestamps in list(_attempts.items()):
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if not timestamps:
                    _attempts.pop(candidate, None)
            if len(_attempts) >= 10_000:
                raise HTTPException(status_code=429, detail="too many attempts; try again shortly")
        bucket = _attempts.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="too many attempts; try again shortly")
        bucket.append(now)


def record_audit_event(
    db: Session,
    action: str,
    *,
    request: Request,
    user_id: int | None = None,
    company_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(
        UserAuditEvent(
            action=action,
            user_id=user_id,
            company_id=company_id,
            ip_address=_client_ip(request),
            metadata_json=metadata or {},
        )
    )


def _derive_username(email: str) -> str:
    """注册时未指定 username → 由邮箱 local part 清洗派生（小写字母数字_-）。"""
    base = "".join(c if c.isalnum() or c in "-_" else "-" for c in email.split("@")[0].lower())
    return base.strip("-") or "user"


def register(
    db: Session, payload, request: Request, background: BackgroundTasks | None = None
) -> tuple[dict, int]:
    email = str(payload.email).strip().lower()
    enforce_rate_limit(f"register:{_client_ip(request)}:{email}", limit=5, window_seconds=300)
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        if existing.status == "active":
            raise HTTPException(status_code=409, detail="an account already exists for this email")
        # 已注销的账号不占邮箱（兼容匿名化之前的历史数据）：改写后释放给新注册
        _anonymize(existing)
        db.flush()
    pending = db.scalar(select(PendingRegistration).where(PendingRegistration.email == email))
    expires_at = utcnow() + timedelta(minutes=settings.email_verification_ttl_minutes)
    if pending is None:
        pending = PendingRegistration(email=email)
        db.add(pending)
    pending.password_hash = _password_hasher.hash(payload.password)
    pending.display_name = payload.display_name.strip()
    pending.username = (payload.username or "").strip().lower() or _derive_username(pending.email)
    pending.locale = payload.locale
    pending.timezone = payload.timezone
    pending.expires_at = expires_at
    db.flush()
    for old in db.scalars(
        select(EmailVerificationToken).where(
            EmailVerificationToken.pending_registration_id == pending.id,
            EmailVerificationToken.used_at.is_(None),
        )
    ):
        old.used_at = utcnow()
    token = secrets.token_urlsafe(32)
    db.add(
        EmailVerificationToken(
            pending_registration_id=pending.id,
            token_hash=_hash(token),
            expires_at=expires_at,
        )
    )
    record_audit_event(db, "user.registered", request=request)
    db.commit()
    _deliver(lambda: send_verification_email(email, token, locale=pending.locale), background)
    return {
        "email": email,
        "verification_required": True,
        "expires_at": expires_at,
        "development_verification_token": token
        if settings.email_delivery_mode == "console"
        else None,
    }, pending.id


def resend_verification(
    db: Session, email: str, request: Request, background: BackgroundTasks | None = None
) -> dict:
    """重发注册验证邮件：作废旧链接、签发新令牌、延长 pending 有效期。

    无论 pending 是否存在都返回同样的回执，避免探测"这个邮箱注册过没有"。
    60 秒内只允许重发一次（覆盖式重发，旧链接立即失效）。
    """
    email = email.strip().lower()
    enforce_rate_limit(
        f"resend-verify:{_client_ip(request)}:{email}", limit=1, window_seconds=60
    )
    expires_at = utcnow() + timedelta(minutes=settings.email_verification_ttl_minutes)
    token = ""
    pending = db.scalar(select(PendingRegistration).where(PendingRegistration.email == email))
    if pending is not None:
        pending.expires_at = expires_at
        for old in db.scalars(
            select(EmailVerificationToken).where(
                EmailVerificationToken.pending_registration_id == pending.id,
                EmailVerificationToken.used_at.is_(None),
            )
        ):
            old.used_at = utcnow()
        token = secrets.token_urlsafe(32)
        db.add(
            EmailVerificationToken(
                pending_registration_id=pending.id,
                token_hash=_hash(token),
                expires_at=expires_at,
            )
        )
        record_audit_event(db, "user.verification_resent", request=request)
        db.commit()
        _deliver(
            lambda: send_verification_email(email, token, locale=pending.locale), background
        )
    return {
        "email": email,
        "verification_required": True,
        "expires_at": expires_at,
        "development_verification_token": token
        if token and settings.email_delivery_mode == "console"
        else None,
    }


def verify_email(db: Session, token: str, request: Request, response: Response) -> AuthStateOut:
    row = db.scalar(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == _hash(token))
    )
    if row is None or row.used_at is not None:
        raise HTTPException(status_code=409, detail="verification link is invalid or already used")
    if _is_expired(row.expires_at):
        raise HTTPException(status_code=410, detail="verification link has expired")
    pending = db.get(PendingRegistration, row.pending_registration_id)
    if pending is None or _is_expired(pending.expires_at):
        raise HTTPException(status_code=410, detail="registration has expired")
    if db.scalar(select(User).where(User.email == pending.email)) is not None:
        raise HTTPException(status_code=409, detail="account already verified")

    username = (pending.username or "").strip().lower() or _derive_username(pending.email)
    username_base, counter = username, 2
    while db.scalar(select(User).where(User.username == username)) is not None:
        username = f"{username_base}-{counter}"
        counter += 1
    user = User(
        email=pending.email,
        username=username,
        email_verified=True,
        password_hash=pending.password_hash,
        display_name=pending.display_name,
        locale=pending.locale,
        timezone=pending.timezone,
        onboarding_status="not_started",
    )
    db.add(user)
    db.flush()
    slug_base = "".join(c if c.isalnum() else "-" for c in pending.email.split("@")[0].lower())
    slug = f"{slug_base.strip('-') or 'eidolon'}-{user.id}"
    company = Company(
        name=f"{pending.display_name or pending.email.split('@')[0]}'s Eidolon",
        slug=slug,
        description="A newly founded AI company.",
        industry="software",
        settings={},
        stage="FOUNDING",
    )
    db.add(company)
    db.flush()
    for name, department_slug in _DEPARTMENTS:
        db.add(
            Department(
                company_id=company.id,
                name=name,
                slug=department_slug,
                description=f"{name} department",
            )
        )
    membership = CompanyMembership(user_id=user.id, company_id=company.id, role="OWNER")
    db.add(membership)
    row.used_at = utcnow()
    record_audit_event(
        db, "user.email_verified", request=request, user_id=user.id, company_id=company.id
    )
    record_audit_event(
        db, "company.created", request=request, user_id=user.id, company_id=company.id
    )
    record_audit_event(
        db, "company.member_added", request=request, user_id=user.id, company_id=company.id
    )
    db.commit()
    db.refresh(user)
    db.refresh(company)
    db.refresh(membership)
    create_session(db, user, request, response)
    return _auth_state(user, company, membership)


def authenticate_password(
    db: Session, payload, request: Request, response: Response
) -> AuthStateOut:
    identifier = str(payload.identifier or payload.email or payload.username or "").strip().lower()
    enforce_rate_limit(
        f"login:{_client_ip(request)}:{identifier}", limit=8, window_seconds=300
    )
    if "@" in identifier:
        user = db.scalar(select(User).where(User.email == identifier))
    else:
        user = db.scalar(select(User).where(User.username == identifier))
    if user is None or not user.email_verified or user.status != "active":
        raise HTTPException(status_code=401, detail="invalid login credentials")
    try:
        _password_hasher.verify(user.password_hash, payload.password)
    except (VerifyMismatchError, InvalidHashError):
        raise HTTPException(status_code=401, detail="email or password is incorrect") from None
    if _password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _password_hasher.hash(payload.password)
    user.last_login_at = utcnow()
    membership, company = primary_company(db, user.id)
    record_audit_event(
        db, "user.logged_in", request=request, user_id=user.id, company_id=company.id
    )
    db.commit()
    create_session(db, user, request, response)
    return _auth_state(user, company, membership)


# ---- 账户安全操作（改邮箱 / 改密码 / 注销）：一律先邮件确认，确认时才生效 ----

ACCOUNT_ACTIONS = {"change_email", "change_password", "delete_account"}


def _issue_action_token(
    db: Session, user: User, action: str, payload: dict, request: Request
) -> tuple[str, object]:
    """作废旧令牌、签发新令牌。返回 (明文 token, expires_at)。"""
    expires_at = utcnow() + timedelta(minutes=settings.email_verification_ttl_minutes)
    for old in db.scalars(
        select(AccountActionToken).where(
            AccountActionToken.user_id == user.id,
            AccountActionToken.action == action,
            AccountActionToken.used_at.is_(None),
        )
    ):
        old.used_at = utcnow()
    token = secrets.token_urlsafe(32)
    db.add(
        AccountActionToken(
            user_id=user.id,
            action=action,
            payload=payload,
            token_hash=_hash(token),
            expires_at=expires_at,
        )
    )
    record_audit_event(db, f"user.{action}_requested", request=request, user_id=user.id)
    db.commit()
    return token, expires_at


def _action_request_response(email: str, token: str, expires_at) -> dict:
    return {
        "email": email,
        "verification_required": True,
        "expires_at": expires_at,
        # 本地开发没有真实邮箱（console 投递打到服务端日志），token 随响应返回
        # 供测试与开发联调；前端只用它判断"现在是开发模式"，不会自动确认。
        "development_verification_token": token
        if settings.email_delivery_mode == "console"
        else None,
    }


def _deliver(send, background: BackgroundTasks | None) -> None:
    """发信。SMTP 可能非常慢（实测 Gmail 一次握手+发送 ~37s），同步发会把注册/
    改密码这些请求一起卡住 —— 有 BackgroundTasks 就响应先回、邮件后台发。
    同步路径（测试、脚本）保留"发不出去就 503"的硬失败，便于立刻发现配置错。"""
    if background is not None:
        background.add_task(send)
        return
    try:
        send()
    except (OSError, smtplib.SMTPException, RuntimeError) as exc:
        raise HTTPException(
            status_code=503, detail="verification email could not be delivered"
        ) from exc


def request_email_change(
    db: Session, user: User, payload, request: Request, background: BackgroundTasks | None = None
) -> dict:
    """换绑邮箱：确认邮件发到**新**邮箱，点链接后才真正改。"""
    enforce_rate_limit(f"email-change:{user.id}", limit=5, window_seconds=300)
    new_email = str(payload.new_email).strip().lower()
    if new_email == user.email:
        raise HTTPException(status_code=409, detail="new email is the same as the current one")
    if db.scalar(select(User).where(User.email == new_email)) is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this email")
    token, expires_at = _issue_action_token(
        db, user, "change_email", {"new_email": new_email}, request
    )
    _deliver(
        lambda: send_account_action_email(new_email, "change_email", token, locale=user.locale),
        background,
    )
    return _action_request_response(new_email, token, expires_at)


def request_password_change(
    db: Session, user: User, payload, request: Request, background: BackgroundTasks | None = None
) -> dict:
    """改密码：先验当前密码，再发确认邮件；点链接后才换新哈希。"""
    try:
        _password_hasher.verify(user.password_hash, payload.current_password)
    except (VerifyMismatchError, InvalidHashError):
        raise HTTPException(status_code=400, detail="current password is incorrect") from None
    token, expires_at = _issue_action_token(
        db,
        user,
        "change_password",
        {"password_hash": _password_hasher.hash(payload.new_password)},
        request,
    )
    _deliver(
        lambda: send_account_action_email(user.email, "change_password", token, locale=user.locale),
        background,
    )
    return _action_request_response(user.email, token, expires_at)


def request_account_deletion(
    db: Session, user: User, request: Request, background: BackgroundTasks | None = None
) -> dict:
    """注销账号：确认邮件发到当前邮箱，点链接后才执行。"""
    enforce_rate_limit(f"delete-account:{user.id}", limit=5, window_seconds=300)
    token, expires_at = _issue_action_token(db, user, "delete_account", {}, request)
    _deliver(
        lambda: send_account_action_email(user.email, "delete_account", token, locale=user.locale),
        background,
    )
    return _action_request_response(user.email, token, expires_at)


def _anonymize(user: User) -> None:
    """注销后释放登录邮箱，让同一地址可以重新注册；审计记录只认 user_id。"""
    user.status = "deleted"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.display_name = ""
    user.avatar = ""
    user.password_hash = ""


def _revoke_all_sessions(db: Session, user_id: int) -> None:
    for session in db.scalars(
        select(UserSession).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
        )
    ):
        session.revoked_at = utcnow()


def confirm_account_action(
    db: Session, token: str, request: Request, response: Response | None = None
) -> dict:
    """执行邮件确认的动作。令牌是一次性的"邮箱所有权"证明，不要求会话。"""
    row = db.scalar(
        select(AccountActionToken).where(AccountActionToken.token_hash == _hash(token))
    )
    if row is None or row.used_at is not None:
        raise HTTPException(status_code=409, detail="verification link is invalid or already used")
    if _is_expired(row.expires_at):
        raise HTTPException(status_code=410, detail="verification link has expired")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="account no longer exists")

    if row.action == "change_email":
        new_email = str(row.payload.get("new_email", "")).strip().lower()
        taken = db.scalar(select(User).where(User.email == new_email, User.id != user.id))
        if not new_email or taken is not None:
            raise HTTPException(status_code=409, detail="an account already exists for this email")
        user.email = new_email
        # 换邮箱不影响会话：身份没变，只是登录标识换了
    elif row.action == "change_password":
        password_hash = str(row.payload.get("password_hash", ""))
        if not password_hash:
            raise HTTPException(status_code=409, detail="verification link is invalid")
        user.password_hash = password_hash
        # 改密码后所有设备都退出（包括发起设备），用新密码重新登录
        _revoke_all_sessions(db, user.id)
    elif row.action == "delete_account":
        record_audit_event(db, "user.deleted", request=request, user_id=user.id)
        _revoke_all_sessions(db, user.id)
        _anonymize(user)
    else:
        raise HTTPException(status_code=409, detail="verification link is invalid")

    row.used_at = utcnow()
    record_audit_event(db, f"user.{row.action}_confirmed", request=request, user_id=user.id)
    db.commit()
    if row.action in ("change_password", "delete_account") and response is not None:
        clear_session_cookies(response)
    return {"action": row.action, "email": user.email if row.action != "delete_account" else None}


def create_session(db: Session, user: User, request: Request, response: Response) -> UserSession:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(24)
    now = utcnow()
    session = UserSession(
        user_id=user.id,
        token_hash=_hash(token),
        csrf_token=csrf,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    response.set_cookie(
        "eidolon_csrf",
        csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return session


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie("eidolon_csrf", path="/")


def session_from_token(db: Session, token: str | None) -> tuple[UserSession, User] | None:
    if not token:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if session is None or session.revoked_at is not None or _is_expired(session.expires_at):
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status != "active":
        return None
    session.last_seen_at = utcnow()
    db.commit()
    return session, user


def primary_company(db: Session, user_id: int) -> tuple[CompanyMembership, Company]:
    membership = db.scalar(
        select(CompanyMembership)
        .where(CompanyMembership.user_id == user_id)
        .order_by(CompanyMembership.id)
    )
    if membership is None:
        raise HTTPException(status_code=409, detail="account has no company membership")
    company = db.get(Company, membership.company_id)
    if company is None:
        raise HTTPException(status_code=409, detail="company membership is invalid")
    return membership, company


def auth_state(db: Session, user: User) -> AuthStateOut:
    membership, company = primary_company(db, user.id)
    return _auth_state(user, company, membership)


def _auth_state(user: User, company: Company, membership: CompanyMembership) -> AuthStateOut:
    return AuthStateOut(
        user=UserOut.model_validate(user),
        company=CompanyOut.model_validate(company),
        membership=MembershipOut.model_validate(membership),
    )


def registration_options(db: Session, user: User, name: str) -> dict:
    existing = list(
        db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id))
    )
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_name=user.email,
        user_id=str(user.id).encode(),
        user_display_name=user.display_name or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
            for item in existing
        ],
    )
    raw = json.loads(options_to_json(options))
    challenge = WebAuthnChallenge(
        user_id=user.id,
        ceremony="registration",
        challenge=raw["challenge"],
        name=name.strip() or "Passkey",
        expires_at=utcnow() + timedelta(minutes=settings.webauthn_challenge_ttl_minutes),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return {"challenge_id": challenge.id, "public_key": raw}


def verify_registration(db: Session, user: User, payload, request: Request) -> PasskeyCredential:
    challenge = _challenge(db, payload.challenge_id, "registration", user.id)
    try:
        result = verify_registration_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="passkey registration could not be verified"
        ) from exc
    credential_id = bytes_to_base64url(result.credential_id)
    if db.scalar(select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id)):
        raise HTTPException(status_code=409, detail="passkey is already registered")
    transports = payload.credential.get("response", {}).get("transports", [])
    row = PasskeyCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(result.credential_public_key),
        sign_count=result.sign_count,
        transports=transports,
        device_type=getattr(
            result.credential_device_type,
            "value",
            str(result.credential_device_type),
        ),
        backed_up=result.credential_backed_up,
        name=challenge.name,
        metadata_json={},
    )
    db.add(row)
    challenge.used_at = utcnow()
    record_audit_event(db, "user.passkey_added", request=request, user_id=user.id)
    db.commit()
    db.refresh(row)
    return row


def authentication_options(db: Session) -> dict:
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    raw = json.loads(options_to_json(options))
    challenge = WebAuthnChallenge(
        ceremony="authentication",
        challenge=raw["challenge"],
        expires_at=utcnow() + timedelta(minutes=settings.webauthn_challenge_ttl_minutes),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return {"challenge_id": challenge.id, "public_key": raw}


def authenticate_passkey(
    db: Session, payload, request: Request, response: Response
) -> AuthStateOut:
    challenge = _challenge(db, payload.challenge_id, "authentication", None)
    credential_id = payload.credential.get("id")
    credential = db.scalar(
        select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id)
    )
    if credential is None:
        raise HTTPException(status_code=401, detail="passkey is not registered")
    try:
        result = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=base64url_to_bytes(credential.public_key),
            credential_current_sign_count=credential.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail="passkey assertion could not be verified"
        ) from exc
    user = db.get(User, credential.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="passkey account no longer exists")
    credential.sign_count = result.new_sign_count
    credential.last_used_at = utcnow()
    challenge.used_at = utcnow()
    user.last_login_at = utcnow()
    membership, company = primary_company(db, user.id)
    record_audit_event(
        db,
        "user.logged_in",
        request=request,
        user_id=user.id,
        company_id=company.id,
        metadata={"method": "passkey"},
    )
    db.commit()
    create_session(db, user, request, response)
    return _auth_state(user, company, membership)


def _challenge(
    db: Session, challenge_id: int, ceremony: str, user_id: int | None
) -> WebAuthnChallenge:
    row = db.get(WebAuthnChallenge, challenge_id)
    if row is None or row.ceremony != ceremony or (user_id is not None and row.user_id != user_id):
        raise HTTPException(status_code=404, detail="challenge not found")
    if row.used_at is not None:
        raise HTTPException(status_code=409, detail="challenge was already used")
    if _is_expired(row.expires_at):
        raise HTTPException(status_code=410, detail="challenge has expired")
    return row
