"""API (R5-b): FastAPI over the read models and domain services. Depends on
everything below; nothing imports it."""

from eidolon.api.auth import AuthError, AuthService, CredentialRow, SessionRow

__all__ = ["AuthError", "AuthService", "CredentialRow", "SessionRow"]
