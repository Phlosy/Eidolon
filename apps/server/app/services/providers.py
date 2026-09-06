"""Provider business logic (v0.2).

Scope rules are enforced at repository level (repositories/providers.py);
this service adds credential handling (secret store, never plaintext out),
delete-while-in-use protection, test/list-models, and domain events.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.redaction import mask_secret
from app.events.bus import bus
from app.models.enums import ProviderScope
from app.models.provider import ModelBinding, Provider
from app.providers import probe
from app.providers.secrets.store import LocalEncryptedSecretStore, get_secret_store
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.schemas.provider import (
    BindingCreate,
    BindingPatch,
    EmployeeProviderCreate,
    ModelBindingOut,
    ModelEntryIn,
    ProviderCreate,
    ProviderModelsOut,
    ProviderOut,
    ProviderPatch,
    ProviderProbeOut,
    ProviderProbeRequest,
    ProviderTestResultOut,
)

# 模型名：字母数字开头，可含 . - _ / :（覆盖 ollama 的 tag 与 openrouter 的 org/model）。
# 手填模型名是允许的，但必须符合这个形状，否则运行时配置渲染会悄悄坏掉。
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:\-]{0,199}$")


def validate_model_name(model: str) -> str:
    model = model.strip()
    if not _MODEL_NAME.match(model):
        raise HTTPException(
            status_code=422,
            detail="invalid model name: letters, digits and '.', '-', '_', '/', ':' only",
        )
    return model


def provider_out(db: Session, provider: Provider) -> ProviderOut:
    metadata = dict(provider.metadata_json or {})
    available = metadata.pop("available_models", []) or []
    default_model = metadata.pop("default_model", None)
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
        available_models=available,
        default_model=default_model,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def catalog_metadata(entries: list[ModelEntryIn], primary_model: str | None) -> dict:
    """公司级模型目录 → metadata 片段。空目录返回空 dict（不留下脏 key）。"""
    seen: set[str] = set()
    catalog: list[dict] = []
    for entry in entries:
        name = validate_model_name(entry.model)
        if name in seen:
            continue
        seen.add(name)
        catalog.append({"model": name, "alias": entry.alias.strip()})
    if not catalog:
        return {}
    names = [item["model"] for item in catalog]
    return {
        "available_models": catalog,
        "default_model": primary_model if primary_model in names else names[0],
    }


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
        # v0.3: default scope is employee when an owner is given, else company
        scope = payload.scope or (
            ProviderScope.employee
            if payload.owner_employee_id is not None
            else ProviderScope.company
        )
        if scope == ProviderScope.employee:
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
            scope=scope.value,
            owner_employee_id=payload.owner_employee_id,
            enabled=True,
            credential_ref=credential_ref,
            metadata_json={
                **payload.metadata,
                **({"credential_mask": mask} if mask else {}),
                **catalog_metadata(payload.models, payload.primary_model),
            },
        )
        db.commit()
        bus.publish(
            "provider.created",
            {"id": provider.id, "name": provider.name, "provider_type": provider.provider_type},
            actor_employee_id=provider.owner_employee_id,
        )
        return provider_out(db, provider)

    def create_for_employee(
        self, db: Session, employee_id: int, payload: EmployeeProviderCreate
    ) -> ProviderOut:
        """Employee-owned account (v0.3): scope=employee, key → SecretStore.

        模型可以一次配多个：每个建一条 ModelBinding，``primary_model``（缺省取
        第一个）成为默认启动模型，之后可通过 bindings 端点切换。
        """
        if org_repo.get_employee(db, employee_id) is None:
            raise HTTPException(status_code=404, detail="employee not found")
        # 先校验再建账号：模型名不合法时不能留下一个没绑定的孤儿 provider。
        # 条目按真实模型名去重且保序；legacy 的 model 字段等价于单条目。
        entries = [*payload.models]
        if payload.model:
            entries.append(ModelEntryIn(model=payload.model))
        seen: set[str] = set()
        unique: list[ModelEntryIn] = []
        for entry in entries:
            name = validate_model_name(entry.model)
            if name in seen:
                continue
            seen.add(name)
            unique.append(ModelEntryIn(model=name, alias=entry.alias.strip()))
        out = self.create(
            db,
            ProviderCreate(
                name=payload.name,
                provider_type=payload.provider_type,
                base_url=payload.base_url,
                scope=ProviderScope.employee,
                owner_employee_id=employee_id,
                api_key=payload.api_key,
            ),
        )
        if unique:
            provider_repo.clear_primary_flags(db, employee_id)
            names = [entry.model for entry in unique]
            primary = payload.primary_model if payload.primary_model in names else names[0]
            for position, entry in enumerate(unique):
                provider_repo.create_binding(
                    db,
                    employee_id=employee_id,
                    provider_id=out.id,
                    model=entry.model,
                    alias=entry.alias,
                    is_primary=entry.model == primary,
                    position=position,
                )
            db.commit()
        return self.get(db, out.id, employee_id)

    def list_bindings(self, db: Session, employee_id: int) -> list[ModelBindingOut]:
        bindings = provider_repo.list_bindings_for_employee(db, employee_id)
        names = {p.id: p.name for p in provider_repo.list_providers(db, employee_id)}
        return [
            ModelBindingOut(
                id=b.id,
                employee_id=b.employee_id,
                provider_id=b.provider_id,
                provider_name=names.get(b.provider_id, ""),
                model=b.model,
                alias=b.alias,
                is_primary=b.is_primary,
                position=b.position,
            )
            for b in bindings
        ]

    def _own_binding(self, db: Session, employee_id: int, binding_id: int) -> ModelBinding:
        binding = db.get(ModelBinding, binding_id)
        if binding is None or binding.employee_id != employee_id:
            raise HTTPException(status_code=404, detail="model binding not found")
        return binding

    def add_binding(
        self, db: Session, employee_id: int, payload: BindingCreate
    ) -> ModelBindingOut:
        if org_repo.get_employee(db, employee_id) is None:
            raise HTTPException(status_code=404, detail="employee not found")
        # 员工只能绑自己看得见的 provider（自有或公司共享）
        provider = provider_repo.get_provider_visible(db, payload.provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        model = validate_model_name(payload.model)
        for existing in provider_repo.list_bindings_for_employee(db, employee_id):
            if existing.provider_id == payload.provider_id and existing.model == model:
                raise HTTPException(status_code=409, detail="this model binding already exists")
        if payload.make_primary:
            provider_repo.clear_primary_flags(db, employee_id)
        position = max(
            (b.position for b in provider_repo.list_bindings_for_employee(db, employee_id)),
            default=-1,
        ) + 1
        provider_repo.create_binding(
            db,
            employee_id=employee_id,
            provider_id=payload.provider_id,
            model=model,
            alias=payload.alias.strip(),
            is_primary=payload.make_primary,
            position=position,
        )
        db.commit()
        bus.publish(
            "provider.binding_added",
            {"employee_id": employee_id, "provider_id": payload.provider_id, "model": model},
            actor_employee_id=employee_id,
        )
        return [b for b in self.list_bindings(db, employee_id) if b.model == model][-1]

    def update_binding(
        self, db: Session, employee_id: int, binding_id: int, payload: BindingPatch
    ) -> ModelBindingOut:
        binding = self._own_binding(db, employee_id, binding_id)
        if payload.alias is not None:
            binding.alias = payload.alias.strip()
        db.commit()
        return [b for b in self.list_bindings(db, employee_id) if b.id == binding.id][0]

    def set_primary_binding(
        self, db: Session, employee_id: int, binding_id: int
    ) -> list[ModelBindingOut]:
        binding = self._own_binding(db, employee_id, binding_id)
        provider_repo.clear_primary_flags(db, employee_id)
        binding.is_primary = True
        db.commit()
        bus.publish(
            "provider.binding_primary",
            {"employee_id": employee_id, "binding_id": binding_id, "model": binding.model},
            actor_employee_id=employee_id,
        )
        return self.list_bindings(db, employee_id)

    def delete_binding(self, db: Session, employee_id: int, binding_id: int) -> None:
        binding = self._own_binding(db, employee_id, binding_id)
        was_primary = binding.is_primary
        db.delete(binding)
        db.flush()
        # 删掉默认模型时，把剩下的第一个顶上来，避免员工突然没有默认模型
        if was_primary:
            remaining = provider_repo.list_bindings_for_employee(db, employee_id)
            if remaining:
                remaining[0].is_primary = True
        db.commit()

    async def probe_config(self, payload: ProviderProbeRequest) -> ProviderProbeOut:
        """不保存的试连：用临时 Provider 对象走同一套探测逻辑。"""
        transient = Provider(
            name="probe",
            provider_type=payload.provider_type.value,
            base_url=payload.base_url or None,
        )
        result = await probe.probe_models(transient, payload.api_key)
        return ProviderProbeOut(**result)

    def update(
        self, db: Session, provider_id: int, payload: ProviderPatch, employee_id: int | None = None
    ) -> ProviderOut:
        provider = provider_repo.get_provider_visible(db, provider_id, employee_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        data = payload.model_dump(exclude_unset=True)
        api_key = data.pop("api_key", None)
        metadata = data.pop("metadata", None)
        # 模型目录不是 Provider 的列：转成 metadata 片段（传 [] 表示清空目录）
        models = data.pop("models", None)
        primary_model = data.pop("primary_model", None)
        for field, value in data.items():
            setattr(provider, field, value.value if hasattr(value, "value") else value)
        if provider.scope == ProviderScope.employee.value and provider.owner_employee_id is None:
            raise HTTPException(
                status_code=422, detail="employee-scope providers require owner_employee_id"
            )
        metadata_json = dict(provider.metadata_json or {})
        if metadata is not None:
            metadata_json.update(metadata)
        if models is not None:
            # 清空目录要把旧 key 一起摘掉，而不是留一个空列表
            metadata_json.pop("available_models", None)
            metadata_json.pop("default_model", None)
            metadata_json.update(
                catalog_metadata(
                    [ModelEntryIn(**item) if isinstance(item, dict) else item for item in models],
                    primary_model,
                )
            )
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
