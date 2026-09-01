"""Provider / model-binding repositories (v0.2).

Scope enforcement lives HERE (repository level), not just in the API layer:
company-scope providers are visible to everyone; employee-scope providers are
only ever returned to their owner.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import ProviderScope
from app.models.provider import ModelBinding, Provider


def _scope_filter(employee_id: int | None):
    if employee_id is None:
        return Provider.scope == ProviderScope.company.value
    return (Provider.scope == ProviderScope.company.value) | (
        (Provider.scope == ProviderScope.employee.value)
        & (Provider.owner_employee_id == employee_id)
    )


def list_providers(db: Session, employee_id: int | None = None) -> list[Provider]:
    return list(
        db.scalars(select(Provider).where(_scope_filter(employee_id)).order_by(Provider.id))
    )


def get_provider(db: Session, provider_id: int) -> Provider | None:
    return db.get(Provider, provider_id)


def get_provider_visible(
    db: Session, provider_id: int, employee_id: int | None = None
) -> Provider | None:
    """get_provider + scope check: employee-scope rows are owner-only."""
    provider = db.get(Provider, provider_id)
    if provider is None:
        return None
    if provider.scope == ProviderScope.employee.value and provider.owner_employee_id != employee_id:
        return None
    return provider


def create_provider(db: Session, **fields) -> Provider:
    provider = Provider(**fields)
    db.add(provider)
    db.flush()
    return provider


def delete_provider(db: Session, provider: Provider) -> None:
    db.delete(provider)
    db.flush()


# ---- model bindings ----


def list_bindings_for_employee(db: Session, employee_id: int) -> list[ModelBinding]:
    return list(
        db.scalars(
            select(ModelBinding)
            .where(ModelBinding.employee_id == employee_id)
            .order_by(ModelBinding.position)
        )
    )


def get_primary_binding(db: Session, employee_id: int) -> ModelBinding | None:
    return db.scalars(
        select(ModelBinding)
        .where(ModelBinding.employee_id == employee_id, ModelBinding.is_primary.is_(True))
        .order_by(ModelBinding.position)
        .limit(1)
    ).first()


def create_binding(db: Session, **fields) -> ModelBinding:
    binding = ModelBinding(**fields)
    db.add(binding)
    db.flush()
    return binding


def count_bindings_for_provider(db: Session, provider_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(ModelBinding.id)).where(ModelBinding.provider_id == provider_id)
        )
        or 0
    )


def clear_primary_flags(db: Session, employee_id: int) -> None:
    for binding in list_bindings_for_employee(db, employee_id):
        binding.is_primary = False
    db.flush()
