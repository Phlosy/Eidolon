"""Human identity and authentication models.

These records are intentionally separate from ``Employee``. A User is a
person operating Eidolon; an Employee is a persistent AI worker in a company.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    avatar: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    onboarding_status: Mapped[str] = mapped_column(String(30), default="not_started")
    locale: Mapped[str] = mapped_column(String(20), default="zh-CN")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")


class PendingRegistration(TimestampMixin, Base):
    __tablename__ = "pending_registrations"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    locale: Mapped[str] = mapped_column(String(20), default="zh-CN")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    expires_at: Mapped[datetime] = mapped_column(index=True)


class EmailVerificationToken(TimestampMixin, Base):
    __tablename__ = "email_verification_tokens"

    pending_registration_id: Mapped[int] = mapped_column(
        ForeignKey("pending_registrations.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class CompanyMembership(TimestampMixin, Base):
    __tablename__ = "company_memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40), default="OWNER")


class AccountActionToken(TimestampMixin, Base):
    """账户安全操作的邮件确认令牌：改邮箱 / 改密码 / 注销账号共用。

    ``action`` 决定确认时执行什么；``payload`` 带动作参数（如新邮箱、新密码哈希）。
    令牌一次性、限时，本身就是"拥有该邮箱"的证明，所以确认端点不要求会话。
    """

    __tablename__ = "account_action_tokens"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_seen_at: Mapped[datetime] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ip_address: Mapped[str] = mapped_column(String(100), default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")


class PasskeyCredential(TimestampMixin, Base):
    __tablename__ = "passkey_credentials"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(Text, unique=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(default=0)
    transports: Mapped[list] = mapped_column(JSON, default=list)
    device_type: Mapped[str] = mapped_column(String(50), default="unknown")
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(120), default="Passkey")
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class WebAuthnChallenge(TimestampMixin, Base):
    __tablename__ = "webauthn_challenges"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    ceremony: Mapped[str] = mapped_column(String(30), index=True)
    challenge: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(120), default="Passkey")
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UserAuditEvent(TimestampMixin, Base):
    __tablename__ = "user_audit_events"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    ip_address: Mapped[str] = mapped_column(String(100), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


__all__ = [
    "AccountActionToken",
    "CompanyMembership",
    "EmailVerificationToken",
    "PasskeyCredential",
    "PendingRegistration",
    "User",
    "UserAuditEvent",
    "UserSession",
    "WebAuthnChallenge",
]
