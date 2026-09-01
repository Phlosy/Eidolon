"""Local encrypted secret store (v0.2).

Plaintext keys never touch the providers table — only a ``credential_ref``
("local:<uuid4>"). Ciphertext lives in the ``secrets`` table, encrypted with a
Fernet key derived from ``EIDOLON_SECRET_KEY`` (sha256 → urlsafe-b64).
Every stored/retrieved value is registered with the redaction registry so it
can never leak through logs, events, or API responses.
"""

import base64
import hashlib
import uuid

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import redaction
from app.core.config import settings
from app.models.provider import Secret

_REF_PREFIX = "local:"


def _derive_fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class LocalEncryptedSecretStore:
    def __init__(self, secret_key: str | None = None) -> None:
        self._fernet = _derive_fernet(secret_key or settings.secret_key)

    def store(self, db: Session, value: str) -> str:
        """Encrypt + persist a secret; returns its credential_ref."""
        ref = f"{_REF_PREFIX}{uuid.uuid4()}"
        ciphertext = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        db.add(Secret(ref=ref, ciphertext=ciphertext))
        db.flush()
        redaction.register_secret(value)
        return ref

    def retrieve(self, db: Session, ref: str | None) -> str | None:
        if not ref:
            return None
        row = db.scalars(select(Secret).where(Secret.ref == ref)).first()
        if row is None:
            return None
        value = self._fernet.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
        redaction.register_secret(value)
        return value

    def delete(self, db: Session, ref: str | None) -> None:
        if not ref:
            return
        row = db.scalars(select(Secret).where(Secret.ref == ref)).first()
        if row is not None:
            db.delete(row)
            db.flush()

    def register_existing(self, db: Session) -> None:
        """Register all stored secrets with the redaction registry (startup)."""
        for row in db.scalars(select(Secret)):
            try:
                redaction.register_secret(
                    self._fernet.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
                )
            except Exception:  # pragma: no cover - corrupt/foreign key material
                continue


_store: LocalEncryptedSecretStore | None = None


def get_secret_store() -> LocalEncryptedSecretStore:
    global _store
    if _store is None:
        _store = LocalEncryptedSecretStore()
    return _store
