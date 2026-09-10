"""Provider / model-binding repositories (v0.2).

Scope enforcement lives HERE (repository level), not just in the API layer:
company-scope providers are visible within the active company; employee-scope
providers are only ever returned to their owner.

R1.4 切读（docs/person-core-migration.md D4 批次 4）：employee-scope 的属主口径与
model_bindings 的读口径从 employee_id（deprecated 镜像列）切到 person_id
（owner_person_id / person_id）；换算经 app/repositories/persons.py 单一入口，
解析不到回落旧口径 + warning。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.enums import ProviderScope
from app.models.provider import ModelBinding, Provider
from app.repositories import persons as person_repo


def _scope_filter(db: Session, employee_id: int | None):
    if employee_id is None:
        return Provider.scope == ProviderScope.company.value
    return (Provider.scope == ProviderScope.company.value) | (
        (Provider.scope == ProviderScope.employee.value)
        & person_repo.read_criterion(
            db, employee_id, Provider.owner_person_id, Provider.owner_employee_id
        )
    )


def list_providers(db: Session, employee_id: int | None = None) -> list[Provider]:
    stmt = select(Provider).where(_scope_filter(db, employee_id)).order_by(Provider.id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(Provider.company_id == identity.company_id)
    return list(db.scalars(stmt))


def get_provider(db: Session, provider_id: int) -> Provider | None:
    stmt = select(Provider).where(Provider.id == provider_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(Provider.company_id == identity.company_id)
    return db.scalar(stmt)


def get_provider_visible(
    db: Session, provider_id: int, employee_id: int | None = None
) -> Provider | None:
    """get_provider + scope check: employee-scope rows are owner-only."""
    provider = get_provider(db, provider_id)
    if provider is None:
        return None
    if provider.scope == ProviderScope.employee.value and (
        employee_id is None
        or not person_repo.matches_owner(
            db, employee_id, provider.owner_person_id, provider.owner_employee_id
        )
    ):
        return None
    return provider


def create_provider(db: Session, **fields) -> Provider:
    identity = get_request_identity()
    if identity is not None:
        fields.setdefault("company_id", identity.company_id)
    # 双写（R1.4）：owner_employee_id 为 NULL 的 company-scope 行没有人称可解析，跳过
    owner_employee_id = fields.get("owner_employee_id")
    if owner_employee_id is not None:
        fields.setdefault("owner_person_id", person_repo.write_person_id(db, owner_employee_id))
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
            .where(
                person_repo.read_criterion(
                    db, employee_id, ModelBinding.person_id, ModelBinding.employee_id
                )
            )
            .order_by(ModelBinding.position)
        )
    )


def get_primary_binding(db: Session, employee_id: int) -> ModelBinding | None:
    return db.scalars(
        select(ModelBinding)
        .where(
            person_repo.read_criterion(
                db, employee_id, ModelBinding.person_id, ModelBinding.employee_id
            ),
            ModelBinding.is_primary.is_(True),
        )
        .order_by(ModelBinding.position)
        .limit(1)
    ).first()


def create_binding(db: Session, **fields) -> ModelBinding:
    # 双写（R1.4）：employee_id（deprecated 镜像）+ person_id（权威口径）
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
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
