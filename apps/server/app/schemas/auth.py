"""Human account, session and passkey API contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.organization import CompanyOut, ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=200)
    locale: str = Field(default="zh-CN", max_length=20)
    timezone: str = Field(default="Asia/Shanghai", max_length=80)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RegisterResponse(BaseModel):
    email: str
    verification_required: bool = True
    expires_at: datetime
    development_verification_token: str | None = None


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: int
    email: str
    email_verified: bool
    display_name: str
    avatar: str
    status: str
    onboarding_status: str
    locale: str
    timezone: str
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MembershipOut(ORMModel):
    id: int
    user_id: int
    company_id: int
    role: str
    created_at: datetime
    updated_at: datetime


class AuthStateOut(BaseModel):
    user: UserOut
    company: CompanyOut
    membership: MembershipOut


class SessionOut(ORMModel):
    id: int
    current: bool = False
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    ip_address: str
    user_agent: str


class PasskeyNameRequest(BaseModel):
    name: str = Field(default="Passkey", min_length=1, max_length=120)


class PasskeyOut(ORMModel):
    id: int
    name: str
    transports: list[str]
    device_type: str
    backed_up: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


class WebAuthnOptionsOut(BaseModel):
    challenge_id: int
    public_key: dict[str, Any]


class WebAuthnVerifyRequest(BaseModel):
    challenge_id: int
    credential: dict[str, Any]
