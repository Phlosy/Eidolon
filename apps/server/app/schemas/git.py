from datetime import datetime

from pydantic import BaseModel

from app.models.enums import GitPlatformType


class GitConnectionCreate(BaseModel):
    name: str
    platform_type: GitPlatformType
    base_url: str
    token: str | None = None  # write-only; stored in the secret store
    enabled: bool = True


class GitConnectionPatch(BaseModel):
    name: str | None = None
    platform_type: GitPlatformType | None = None
    base_url: str | None = None
    # write-only: sending a non-empty value replaces the credential;
    # absent/empty keeps the existing one.
    token: str | None = None
    enabled: bool | None = None


class GitConnectionOut(BaseModel):
    """Never carries plaintext token material — only a mask."""

    id: int
    name: str
    platform_type: str
    base_url: str
    has_credential: bool
    credential_mask: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class GitConnectionTestOut(BaseModel):
    ok: bool
    latency_ms: int | None
    version: str | None
    error: str | None


class GitBuiltinOut(BaseModel):
    docker_available: bool
    status: str  # not_installed | installing | stopped | running | error
    url: str | None
    version: str | None


class GitOverviewOut(BaseModel):
    builtin: GitBuiltinOut
    connections: list[GitConnectionOut]


class GitBuiltinActionOut(BaseModel):
    status: str
