"""Passkeys / WebAuthn (R6-d), ported from the legacy service (232ceff): two API
secret tables — registered credentials and short-lived ceremony challenges.

Server-side: the ``webauthn`` library generates options and verifies responses; the
browser does the rest. Challenges are single-use and expire; a discoverable login
(no user named up front) is supported because the challenge may carry no user.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from eidolon.db import Base


class PasskeyError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class PasskeyRow(Base):
    __tablename__ = "auth_passkeys"
    credential_id: Mapped[str] = mapped_column(String(1024), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    public_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    transports: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def as_dict(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "name": self.name,
            "transports": list(self.transports or []),
            "device_type": self.device_type,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class ChallengeRow(Base):
    __tablename__ = "auth_webauthn_challenges"
    challenge: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # register | login
    name: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _now() -> datetime:
    return datetime.now(UTC)


class PasskeyService:
    def __init__(self, *, rp_id: str, rp_name: str, origin: str, ttl_minutes: int = 5):
        self.rp_id, self.rp_name, self.origin = rp_id, rp_name, origin
        self.ttl = timedelta(minutes=ttl_minutes)

    # -- registration --------------------------------------------------------------

    def registration_options(
        self, db: Session, *, user_id: UUID, email: str, display_name: str, name: str
    ) -> dict[str, Any]:
        existing = self.list(db, user_id)
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_name=email,
            user_id=str(user_id).encode(),
            user_display_name=display_name or email,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
                for p in existing
            ],
        )
        raw = json.loads(options_to_json(options))
        self._sweep(db)
        db.add(
            ChallengeRow(
                challenge=raw["challenge"],
                user_id=user_id,
                purpose="register",
                name=(name or "Passkey").strip()[:120],
                expires_at=_now() + self.ttl,
            )
        )
        db.flush()
        return {"challenge": raw["challenge"], "public_key": raw}

    def verify_registration(
        self, db: Session, *, user_id: UUID, challenge: str, credential: dict[str, Any]
    ) -> PasskeyRow:
        row = self._take(db, challenge, "register", user_id)
        try:
            result = verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(row.challenge),
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001 - the library raises many kinds
            raise PasskeyError(400, "passkey registration could not be verified") from exc
        credential_id = bytes_to_base64url(result.credential_id)
        if db.get(PasskeyRow, credential_id) is not None:
            raise PasskeyError(409, "passkey is already registered")
        passkey = PasskeyRow(
            credential_id=credential_id,
            user_id=user_id,
            public_key=bytes_to_base64url(result.credential_public_key),
            sign_count=result.sign_count,
            name=row.name or "Passkey",
            transports=list((credential.get("response") or {}).get("transports") or []),
            device_type=getattr(result.credential_device_type, "value", ""),
            created_at=_now(),
        )
        db.add(passkey)
        db.flush()
        return passkey

    # -- authentication -----------------------------------------------------------

    def authentication_options(self, db: Session) -> dict[str, Any]:
        options = generate_authentication_options(
            rp_id=self.rp_id, user_verification=UserVerificationRequirement.REQUIRED
        )
        raw = json.loads(options_to_json(options))
        self._sweep(db)
        db.add(
            ChallengeRow(
                challenge=raw["challenge"],
                user_id=None,
                purpose="login",
                expires_at=_now() + self.ttl,
            )
        )
        db.flush()
        return {"challenge": raw["challenge"], "public_key": raw}

    def verify_authentication(
        self, db: Session, *, challenge: str, credential: dict[str, Any]
    ) -> PasskeyRow:
        """Returns the passkey that signed; the caller opens the session for its user."""
        row = self._take(db, challenge, "login", None)
        passkey = db.get(PasskeyRow, str(credential.get("id") or ""))
        if passkey is None:
            raise PasskeyError(401, "passkey is not registered")
        try:
            result = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(row.challenge),
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=base64url_to_bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise PasskeyError(401, "passkey assertion could not be verified") from exc
        passkey.sign_count = result.new_sign_count
        passkey.last_used_at = _now()
        db.flush()
        return passkey

    # -- management ---------------------------------------------------------------

    def list(self, db: Session, user_id: UUID) -> list[PasskeyRow]:
        return list(
            db.scalars(
                select(PasskeyRow)
                .where(PasskeyRow.user_id == user_id)
                .order_by(PasskeyRow.created_at)
            )
        )

    def rename(self, db: Session, user_id: UUID, credential_id: str, name: str) -> PasskeyRow:
        row = db.get(PasskeyRow, credential_id)
        if row is None or row.user_id != user_id:
            raise PasskeyError(404, "passkey not found")
        row.name = name.strip()[:120] or row.name
        db.flush()
        return row

    def remove(self, db: Session, user_id: UUID, credential_id: str) -> None:
        row = db.get(PasskeyRow, credential_id)
        if row is None or row.user_id != user_id:
            raise PasskeyError(404, "passkey not found")
        db.delete(row)
        db.flush()

    # -- challenges ----------------------------------------------------------------

    def _take(
        self, db: Session, challenge: str, purpose: str, user_id: UUID | None
    ) -> ChallengeRow:
        row = db.get(ChallengeRow, challenge)
        if (
            row is None
            or row.purpose != purpose
            or (user_id is not None and row.user_id != user_id)
        ):
            raise PasskeyError(404, "challenge not found")
        db.delete(row)  # single use, whatever happens next
        db.flush()
        if row.expires_at <= _now():
            raise PasskeyError(410, "challenge has expired")
        return row

    def _sweep(self, db: Session) -> None:
        for row in db.scalars(select(ChallengeRow).where(ChallengeRow.expires_at <= _now())):
            db.delete(row)
