"""Request-local human user and company scope for legacy service seams."""

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestIdentity:
    user_id: int
    company_id: int
    membership_role: str
    session_id: int


_identity: ContextVar[RequestIdentity | None] = ContextVar("eidolon_identity", default=None)


def get_request_identity() -> RequestIdentity | None:
    return _identity.get()


def set_request_identity(value: RequestIdentity) -> Token:
    return _identity.set(value)


def reset_request_identity(token: Token) -> None:
    _identity.reset(token)
