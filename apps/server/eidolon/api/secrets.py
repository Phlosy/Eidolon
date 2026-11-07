"""Per-company provider credentials (R6-d): the API layer's third secret table.

The key is Fernet-encrypted at rest with a key derived from ``EIDOLON_V2_SECRET_KEY``;
nothing ever returns the plaintext over the API (only the last four characters).
The worker decrypts it when it builds the provider for a company's runs
(``agent.providers.routing.RoutedProvider``); a company without a row falls back to
the process-wide provider from ``.env``.
"""

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base

KINDS = ("openai_compatible", "anthropic")


class SecretsError(ValueError):
    pass


class ProviderCredentialRow(Base):
    __tablename__ = "provider_credentials"
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str | None] = mapped_column(String(200))
    api_key_encrypted: Mapped[str] = mapped_column(String(2000), nullable=False)
    key_tail: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)


def fernet(secret_key: str) -> Fernet:
    """Any passphrase → a Fernet key (sha256, urlsafe base64)."""
    if not secret_key or secret_key == "change-me-in-production":
        raise SecretsError(
            "EIDOLON_V2_SECRET_KEY is unset or the placeholder; provider credentials need a "
            "real secret to be stored"
        )
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    kind: str
    base_url: str | None
    model: str | None
    api_key: str


class ProviderCredentials:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key

    def set(
        self,
        db: Session,
        company_id: UUID,
        *,
        kind: str,
        api_key: str,
        base_url: str | None,
        model: str | None,
        by: str,
    ) -> ProviderCredentialRow:
        if kind not in KINDS:
            raise SecretsError(f"kind must be one of {KINDS}")
        if not api_key or len(api_key.strip()) < 8:
            raise SecretsError("api_key is required")
        token = fernet(self.secret_key).encrypt(api_key.strip().encode("utf-8")).decode("ascii")
        row = db.get(ProviderCredentialRow, company_id)
        if row is None:
            row = ProviderCredentialRow(company_id=company_id)
            db.add(row)
        row.kind, row.base_url, row.model = kind, (base_url or None), (model or None)
        row.api_key_encrypted, row.key_tail = token, api_key.strip()[-4:]
        row.updated_at, row.updated_by = datetime.now(UTC), by
        db.flush()
        return row

    def get(self, db: Session, company_id: UUID) -> ProviderCredentialRow | None:
        return db.get(ProviderCredentialRow, company_id)

    def clear(self, db: Session, company_id: UUID) -> bool:
        row = db.get(ProviderCredentialRow, company_id)
        if row is None:
            return False
        db.delete(row)
        db.flush()
        return True

    def config(self, db: Session, company_id: UUID) -> ProviderConfig | None:
        row = self.get(db, company_id)
        if row is None:
            return None
        try:
            key = fernet(self.secret_key).decrypt(row.api_key_encrypted.encode("ascii"))
        except InvalidToken as exc:
            raise SecretsError(
                f"company {company_id}: credential cannot be decrypted with the current "
                "EIDOLON_V2_SECRET_KEY"
            ) from exc
        return ProviderConfig(row.kind, row.base_url, row.model, key.decode("utf-8"))

    @staticmethod
    def masked(row: ProviderCredentialRow) -> dict:
        return {
            "company_id": str(row.company_id),
            "kind": row.kind,
            "base_url": row.base_url,
            "model": row.model,
            "key_tail": row.key_tail,
            "updated_at": row.updated_at.isoformat(),
            "updated_by": row.updated_by,
        }


def companies_with_credentials(db: Session) -> list[UUID]:
    return list(db.scalars(select(ProviderCredentialRow.company_id)))
