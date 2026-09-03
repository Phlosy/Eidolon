"""Human account, session and passkey API contracts."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.organization import CompanyOut, ORMModel

PASSWORD_REQUIREMENTS_DESCRIPTION = (
    "Minimum 8 characters; include at least two: English letters, numbers, special characters."
)
PASSWORD_WHITESPACE_CHARACTERS = frozenset(
    "\u0009\u000a\u000b\u000c\u000d"
    "\u001c\u001d\u001e\u001f"
    "\u0020\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=256,
        description=PASSWORD_REQUIREMENTS_DESCRIPTION,
    )
    display_name: str = Field(default="", max_length=200)
    locale: str = Field(default="zh-CN", max_length=20)
    timezone: str = Field(default="Asia/Shanghai", max_length=80)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password_character_types(cls, value: str) -> str:
        character_type_count = sum(
            (
                bool(re.search(r"[A-Za-z]", value)),
                bool(re.search(r"[0-9]", value)),
                any(
                    not re.fullmatch(r"[A-Za-z0-9]", character)
                    and character not in PASSWORD_WHITESPACE_CHARACTERS
                    for character in value
                ),
            )
        )
        if character_type_count < 2:
            raise ValueError(PASSWORD_REQUIREMENTS_DESCRIPTION)
        return value


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
