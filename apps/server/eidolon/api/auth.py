"""Credentials and sessions: the API layer's own persistent state (v2-ADR-22).

Neither facts nor projections: a password hash must never enter the immutable
ledger, and a session is a secret with a lifetime. Losing these tables costs a
re-login or a password reset and changes nothing the ledger says. Registration
writes the ``identity.user.registered`` fact and the credential row in one
transaction; the hash lives only here.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import DateTime, Index, String, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base
from eidolon.identity import IdentityError, IdentityService
from eidolon.identity import facts as IF
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

TABLES = ("auth_credentials", "auth_sessions")
MIN_PASSWORD = 8
_hasher = PasswordHasher()


class AuthError(ValueError):
    pass


class CredentialRow(Base):
    __tablename__ = "auth_credentials"
    __table_args__ = (Index("ix_auth_credentials_email", "email", unique=True),)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRow(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_token", "token_hash", unique=True),
        Index("ix_auth_sessions_user", "user_id"),
    )
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    def alive(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


@dataclass(frozen=True, slots=True)
class Login:
    session: SessionRow
    token: str  # goes into the HttpOnly cookie, never stored
    csrf: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(self, store: FactStore | None = None, *, session_ttl_hours: int = 24 * 30):
        self.store = store or FactStore()
        self.identity = IdentityService(self.store)
        self.ttl = timedelta(hours=session_ttl_hours)

    # -- accounts ---------------------------------------------------------------------

    def register(
        self,
        db: Session,
        *,
        email: str,
        display_name: str,
        password: str,
        person_id: str | None = None,
    ) -> tuple[UUID, str]:
        """A user (fact), their human person (fact) and their credential (row), in one
        transaction. Returns (user_id, person_id)."""
        if len(password) < MIN_PASSWORD:
            raise AuthError(f"password must be at least {MIN_PASSWORD} characters")
        email = email.strip().lower()
        if db.scalar(select(CredentialRow).where(CredentialRow.email == email)) is not None:
            raise AuthError("an account with this email already exists")
        user_id = uuid4()
        person_id = person_id or f"u-{user_id.hex[:8]}"
        try:
            fact = self.identity.register_user(
                db, email=email, display_name=display_name, user_id=user_id
            )
            if fact.payload["user_id"] != str(user_id):
                raise AuthError("an account with this email already exists")  # K2 hit
            self.identity.create_person(
                db,
                Actor(ActorKind.human, person_id),
                person_id,
                name=display_name,
                kind="human",
                user_id=user_id,
            )
        except IdentityError as exc:
            raise AuthError(str(exc)) from exc
        db.add(
            CredentialRow(
                user_id=user_id,
                email=email,
                password_hash=_hasher.hash(password),
                updated_at=_now(),
            )
        )
        db.flush()
        return user_id, person_id

    def change_password(self, db: Session, user_id: UUID, *, old: str, new: str) -> None:
        cred = db.get(CredentialRow, user_id)
        if cred is None or not self._verify(cred, old):
            raise AuthError("wrong password")
        if len(new) < MIN_PASSWORD:
            raise AuthError(f"password must be at least {MIN_PASSWORD} characters")
        cred.password_hash, cred.updated_at = _hasher.hash(new), _now()
        db.flush()

    # -- sessions ---------------------------------------------------------------------

    def login(self, db: Session, *, email: str, password: str, user_agent: str = "") -> Login:
        cred = db.scalar(select(CredentialRow).where(CredentialRow.email == email.strip().lower()))
        if cred is None or not self._verify(cred, password):
            raise AuthError("invalid email or password")
        token, csrf, now = secrets.token_urlsafe(48), secrets.token_urlsafe(24), _now()
        row = SessionRow(
            session_id=uuid4(),
            user_id=cred.user_id,
            token_hash=_hash_token(token),
            csrf_token=csrf,
            created_at=now,
            expires_at=now + self.ttl,
            last_seen_at=now,
            user_agent=user_agent[:500],
        )
        db.add(row)
        db.flush()
        return Login(row, token, csrf)

    def authenticate(self, db: Session, token: str | None) -> SessionRow | None:
        if not token:
            return None
        row = db.scalar(select(SessionRow).where(SessionRow.token_hash == _hash_token(token)))
        now = _now()
        if row is None or not row.alive(now):
            return None
        if now - row.last_seen_at > timedelta(minutes=5):
            row.last_seen_at = now
            db.flush()
        return row

    def sessions_of(self, db: Session, user_id: UUID) -> list[SessionRow]:
        now = _now()
        return [
            s
            for s in db.scalars(
                select(SessionRow)
                .where(SessionRow.user_id == user_id)
                .order_by(SessionRow.created_at)
            )
            if s.alive(now)
        ]

    def revoke(self, db: Session, session: SessionRow) -> None:
        if session.revoked_at is None:
            session.revoked_at = _now()
            db.flush()

    def revoke_all(self, db: Session, user_id: UUID, *, except_session: UUID | None = None) -> int:
        n = 0
        for s in self.sessions_of(db, user_id):
            if s.session_id != except_session:
                self.revoke(db, s)
                n += 1
        return n

    # -- helpers ----------------------------------------------------------------------

    @staticmethod
    def _verify(cred: CredentialRow, password: str) -> bool:
        try:
            return _hasher.verify(cred.password_hash, password)
        except VerifyMismatchError:
            return False


__all__ = [
    "MIN_PASSWORD",
    "TABLES",
    "AuthError",
    "AuthService",
    "CredentialRow",
    "IF",
    "Login",
    "SessionRow",
]
