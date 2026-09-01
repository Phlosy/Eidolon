from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ProviderScope, ProviderType


class ProviderCreate(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: str | None = None
    scope: ProviderScope = ProviderScope.company
    owner_employee_id: int | None = None
    api_key: str | None = None  # write-only; stored in the secret store
    metadata: dict = {}


class ProviderPatch(BaseModel):
    name: str | None = None
    provider_type: ProviderType | None = None
    base_url: str | None = None
    scope: ProviderScope | None = None
    owner_employee_id: int | None = None
    api_key: str | None = None  # write-only: sending a value replaces the credential
    enabled: bool | None = None
    metadata: dict | None = None


class ProviderOut(BaseModel):
    """Never carries plaintext key material — only a mask."""

    id: int
    name: str
    provider_type: str
    base_url: str | None
    scope: str
    owner_employee_id: int | None
    enabled: bool
    has_credential: bool
    credential_mask: str | None
    metadata: dict
    in_use_by: int = 0
    created_at: datetime
    updated_at: datetime


class ProviderTestResultOut(BaseModel):
    ok: bool
    latency_ms: float | None
    error: str | None
    models_count: int | None


class ProviderModelsOut(BaseModel):
    models: list[str]
    source: str = "api"  # api | manual
