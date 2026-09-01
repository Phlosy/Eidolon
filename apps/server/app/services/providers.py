"""Provider business logic (v0.2).

Scope rules are enforced at repository level (repositories/providers.py);
this service adds credential handling (secret store, never plaintext out),
delete-while-in-use protection, test/list-models, and domain events.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.redaction import mask_secret
from app.events.bus import bus
from app.models.enums import ProviderScope
from app.models.provider import Provider
from app.providers import probe
from app.providers.secrets.store import LocalEncryptedSecretStore, get_secret_store
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.schemas.provider import (
    ProviderCreate,
    ProviderModelsOut,
    ProviderOut,
    ProviderPatch,
    ProviderTestResultOut,
)


def provider_out(db: Session, provider: Provider) -> ProviderOut:
    metadata = dict(provider.metadata_json or {})
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        scope=provider.scope,
        owner_employee_id=provider.owner_employee_id,
        enabled=provider.enabled,
        has_credential=provider.credential_ref is not None,
        credential_mask=metadata.pop("credential_mask", None),
        metadata=metadata,
        in_use_by=provider_repo.count_bindings_for_provider(db, provider.id),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


class ProviderService:
    def __init__(self, secrets: LocalEncryptedSecretStore | None = None) -> None:
        self._secrets = secrets or get_secret_store()

    def list(self, db: Session, employee_id: int | None = None) -> list[ProviderOut]:
        return [provider_out(db, p) for p in provider_repo.list_providers(db, employee_id)]

    def get(self, db: Session, provider_id: int, employee_id: int | None = None) -> ProviderOut:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        return provider_out(db, provider)

    def create(self, db: Session, payload: ProviderCreate) -> ProviderOut:
        if payload.scope == ProviderScope.employee.value:
            if payload.owner_employee_id is None:
                raise HTTPException(
                    status_code=422, detail="employee-scope providers require owner_employee_id"
                )
            if org_repo.get_employee(db, payload.owner_employee_id) is None:
                raise HTTPException(status_code=404, detail="owner employee not found")
        credential_ref = None
        mask = None
        if payload.api_key:
            credential_ref = self._secrets.store(db, payload.api_key)
            mask = mask_secret(payload.api_key)
        provider = provider_repo.create_provider(
            db,
            name=payload.name,
            provider_type=payload.provider_type.value,
            base_url=payload.base_url,
            scope=payload.scope.value,
            owner_employee_id=payload.owner_employee_id,
            enabled=True,
            credential_ref=credential_ref,
            metadata_json={**payload.metadata, **({"credential_mask": mask} if mask else {})},
        )
        db.commit()
        bus.publish(
            "provider.created",
            {"id": provider.id, "name": provider.name, "provider_type": provider.provider_type},
            actor_employee_id=provider.owner_employee_id,
        )
        return provider_out(db, provider)

    def update(
        self, db: Session, provider_id: int, payload: ProviderPatch, employee_id: int | None = None
    ) -> ProviderOut:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        data = payload.model_dump(exclude_unset=True)
        api_key = data.pop("api_key", None)
        metadata = data.pop("metadata", None)
        for field, value in data.items():
            setattr(provider, field, value.value if hasattr(value, "value") else value)
        if provider.scope == ProviderScope.employee.value and provider.owner_employee_id is None:
            raise HTTPException(
                status_code=422, detail="employee-scope providers require owner_employee_id"
            )
        metadata_json = dict(provider.metadata_json or {})
        if metadata is not None:
            metadata_json.update(metadata)
        if api_key:
            # rotate: replace the stored credential, drop the old ciphertext
            self._secrets.delete(db, provider.credential_ref)
            provider.credential_ref = self._secrets.store(db, api_key)
            metadata_json["credential_mask"] = mask_secret(api_key)
        provider.metadata_json = metadata_json
        db.commit()
        bus.publish(
            "provider.updated",
            {"id": provider.id, "name": provider.name},
            actor_employee_id=provider.owner_employee_id,
        )
        return provider_out(db, provider)

    def delete(self, db: Session, provider_id: int, employee_id: int | None = None) -> None:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        in_use = provider_repo.count_bindings_for_provider(db, provider_id)
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"provider is referenced by {in_use} model binding(s)",
            )
        self._secrets.delete(db, provider.credential_ref)
        provider_repo.delete_provider(db, provider)
        db.commit()

    async def test(
        self, db: Session, provider_id: int, employee_id: int | None = None
    ) -> ProviderTestResultOut:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        credential = self._secrets.retrieve(db, provider.credential_ref)
        result = await probe.test_provider(provider, credential)
        bus.publish(
            "provider.tested" if result["ok"] else "provider.failed",
            {"id": provider.id, "name": provider.name, "error": result["error"]},
            actor_employee_id=provider.owner_employee_id,
        )
        return ProviderTestResultOut(**result)

    async def list_models(
        self, db: Session, provider_id: int, employee_id: int | None = None
    ) -> ProviderModelsOut:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        credential = self._secrets.retrieve(db, provider.credential_ref)
        models = await probe.list_models(provider, credential)
        return ProviderModelsOut(models=models, source="api")


provider_service = ProviderService()
