"""/providers (v0.2).

Scope context is passed as ?employee_id=: company-scope providers are visible
to everyone, employee-scope providers only to their owner (enforced in the
repository layer). Responses never include plaintext keys.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.providers.base import PROVIDER_PRESETS
from app.schemas.provider import (
    ProviderCreate,
    ProviderModelsOut,
    ProviderOut,
    ProviderPatch,
    ProviderPresetOut,
    ProviderProbeOut,
    ProviderProbeRequest,
    ProviderTestResultOut,
)
from app.services.providers import provider_service

router = APIRouter(prefix="/providers", tags=["providers"])


# 注意路由顺序：/presets 必须排在 /{provider_id} 之前，否则被当成 id 匹配
@router.get("/presets", response_model=list[ProviderPresetOut])
def list_provider_presets() -> list[ProviderPresetOut]:
    return [
        ProviderPresetOut(
            provider_type=p.provider_type,
            default_base_url=p.default_base_url,
            requires_api_key=p.requires_api_key,
            recommended_models=list(p.recommended_models),
            docs_url=p.docs_url,
        )
        for p in PROVIDER_PRESETS
    ]


@router.get("", response_model=list[ProviderOut])
def list_providers(
    employee_id: int | None = Query(default=None), db: Session = Depends(get_db)
) -> list[ProviderOut]:
    return provider_service.list(db, employee_id)


@router.post("", response_model=ProviderOut, status_code=201)
def create_provider(payload: ProviderCreate, db: Session = Depends(get_db)) -> ProviderOut:
    return provider_service.create(db, payload)


@router.post("/probe", response_model=ProviderProbeOut)
async def probe_provider_config(payload: ProviderProbeRequest) -> ProviderProbeOut:
    """不保存配置的试连：创建对话框里"探测可用模型"用，api_key 不落库。"""
    return await provider_service.probe_config(payload)


@router.get("/{provider_id}", response_model=ProviderOut)
def get_provider(
    provider_id: int,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProviderOut:
    return provider_service.get(db, provider_id, employee_id)


@router.patch("/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int,
    payload: ProviderPatch,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProviderOut:
    return provider_service.update(db, provider_id, payload, employee_id)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    provider_service.delete(db, provider_id, employee_id)


@router.post("/{provider_id}/test", response_model=ProviderTestResultOut)
async def test_provider(
    provider_id: int,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProviderTestResultOut:
    return await provider_service.test(db, provider_id, employee_id)


@router.get("/{provider_id}/models", response_model=ProviderModelsOut)
async def list_provider_models(
    provider_id: int,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProviderModelsOut:
    return await provider_service.list_models(db, provider_id, employee_id)
