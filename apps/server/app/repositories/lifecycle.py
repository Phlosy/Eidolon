"""Lifecycle repositories (v0.4): pure data access for the lifecycle tables."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lifecycle import (
    AccessPackage,
    AccessPackageItem,
    AuditLog,
    EmployeePackage,
    Employment,
    Entitlement,
    Position,
    ProvisioningJob,
    ProvisioningStep,
    ResourceAccount,
    ResourceAsset,
    ResourceProvider,
)
from app.repositories import position as position_repo

# ---- positions ----


def list_positions(db: Session, department_id: int | None = None) -> list[Position]:
    stmt = select(Position).order_by(Position.id)
    if department_id is not None:
        stmt = stmt.where(Position.department_id == department_id)
    return list(db.scalars(stmt))


def get_position(db: Session, position_id: int) -> Position | None:
    return db.get(Position, position_id)


def get_position_by_title(db: Session, department_id: int, title: str) -> Position | None:
    return db.scalars(
        select(Position).where(Position.department_id == department_id, Position.title == title)
    ).first()


def create_position(db: Session, **fields) -> Position:
    position = Position(**fields)
    db.add(position)
    db.flush()
    return position


# ---- employments ----


def list_employments(db: Session, employee_id: int) -> list[Employment]:
    return list(
        db.scalars(
            select(Employment).where(Employment.employee_id == employee_id).order_by(Employment.id)
        )
    )


def get_current_employment(db: Session, employee_id: int) -> Employment | None:
    """当前**主职**任职。

    实现已收口到 `repositories.position`（P4a）：旧写法是
    `order_by(id.desc()).limit(1)`，它不区分 primary / secondary / acting，
    一旦允许兼任就会“随手取到一条不是主职的”。保留函数名是为了 26 个调用点
    不一次改完，但**读法只能有一份**。
    """
    return position_repo.active_primary_assignment(db, employee_id)


def create_employment(db: Session, **fields) -> Employment:
    employment = Employment(**fields)
    db.add(employment)
    db.flush()
    return employment


# ---- resource providers / accounts ----


def get_provider_by_key(db: Session, key: str) -> ResourceProvider | None:
    return db.scalars(select(ResourceProvider).where(ResourceProvider.key == key)).first()


def list_providers(db: Session) -> list[ResourceProvider]:
    return list(db.scalars(select(ResourceProvider).order_by(ResourceProvider.id)))


def create_provider(db: Session, **fields) -> ResourceProvider:
    provider = ResourceProvider(**fields)
    db.add(provider)
    db.flush()
    return provider


def list_accounts(db: Session, employee_id: int) -> list[ResourceAccount]:
    return list(
        db.scalars(
            select(ResourceAccount)
            .where(ResourceAccount.employee_id == employee_id)
            .order_by(ResourceAccount.id)
        )
    )


def get_account_by_provider_key(
    db: Session, employee_id: int, provider_key: str
) -> ResourceAccount | None:
    return db.scalars(
        select(ResourceAccount)
        .join(ResourceProvider, ResourceAccount.provider_id == ResourceProvider.id)
        .where(ResourceAccount.employee_id == employee_id, ResourceProvider.key == provider_key)
    ).first()


def create_account(db: Session, **fields) -> ResourceAccount:
    account = ResourceAccount(**fields)
    db.add(account)
    db.flush()
    return account


# ---- entitlements / access packages ----


def get_entitlement_by_key(db: Session, key: str) -> Entitlement | None:
    return db.scalars(select(Entitlement).where(Entitlement.key == key)).first()


def get_entitlement(db: Session, entitlement_id: int) -> Entitlement | None:
    return db.get(Entitlement, entitlement_id)


def list_entitlements(db: Session) -> list[Entitlement]:
    return list(db.scalars(select(Entitlement).order_by(Entitlement.id)))


def create_entitlement(db: Session, **fields) -> Entitlement:
    entitlement = Entitlement(**fields)
    db.add(entitlement)
    db.flush()
    return entitlement


def list_packages(db: Session) -> list[AccessPackage]:
    return list(db.scalars(select(AccessPackage).order_by(AccessPackage.id)))


def get_package(db: Session, package_id: int) -> AccessPackage | None:
    return db.get(AccessPackage, package_id)


def get_package_by_slug(db: Session, slug: str) -> AccessPackage | None:
    return db.scalars(select(AccessPackage).where(AccessPackage.slug == slug)).first()


def create_package(db: Session, **fields) -> AccessPackage:
    package = AccessPackage(**fields)
    db.add(package)
    db.flush()
    return package


def list_package_items(db: Session, package_id: int) -> list[AccessPackageItem]:
    return list(
        db.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))
    )


def get_package_item(db: Session, package_id: int, entitlement_id: int) -> AccessPackageItem | None:
    return db.scalars(
        select(AccessPackageItem).where(
            AccessPackageItem.package_id == package_id,
            AccessPackageItem.entitlement_id == entitlement_id,
        )
    ).first()


def create_package_item(db: Session, **fields) -> AccessPackageItem:
    item = AccessPackageItem(**fields)
    db.add(item)
    db.flush()
    return item


def list_employee_packages(db: Session, employee_id: int) -> list[EmployeePackage]:
    return list(
        db.scalars(select(EmployeePackage).where(EmployeePackage.employee_id == employee_id))
    )


def get_employee_package(db: Session, employee_id: int, package_id: int) -> EmployeePackage | None:
    return db.scalars(
        select(EmployeePackage).where(
            EmployeePackage.employee_id == employee_id,
            EmployeePackage.package_id == package_id,
        )
    ).first()


def create_employee_package(db: Session, **fields) -> EmployeePackage:
    row = EmployeePackage(**fields)
    db.add(row)
    db.flush()
    return row


def delete_employee_package(db: Session, row: EmployeePackage) -> None:
    db.delete(row)
    db.flush()


# ---- provisioning jobs / steps ----


def create_job(db: Session, **fields) -> ProvisioningJob:
    job = ProvisioningJob(**fields)
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, job_id: int) -> ProvisioningJob | None:
    return db.get(ProvisioningJob, job_id)


def list_jobs(db: Session, employee_id: int | None = None) -> list[ProvisioningJob]:
    stmt = select(ProvisioningJob).order_by(ProvisioningJob.id.desc())
    if employee_id is not None:
        stmt = stmt.where(ProvisioningJob.employee_id == employee_id)
    return list(db.scalars(stmt))


def create_step(db: Session, **fields) -> ProvisioningStep:
    step = ProvisioningStep(**fields)
    db.add(step)
    db.flush()
    return step


def list_steps(db: Session, job_id: int) -> list[ProvisioningStep]:
    return list(
        db.scalars(
            select(ProvisioningStep)
            .where(ProvisioningStep.job_id == job_id)
            .order_by(ProvisioningStep.seq)
        )
    )


# ---- resource assets ----


def list_assets(db: Session, owner_employee_id: int | None = None) -> list[ResourceAsset]:
    stmt = select(ResourceAsset).order_by(ResourceAsset.id)
    if owner_employee_id is not None:
        stmt = stmt.where(ResourceAsset.owner_employee_id == owner_employee_id)
    return list(db.scalars(stmt))


def list_assets_by_provider(
    db: Session, owner_employee_id: int, provider_key: str
) -> list[ResourceAsset]:
    return list(
        db.scalars(
            select(ResourceAsset).where(
                ResourceAsset.owner_employee_id == owner_employee_id,
                ResourceAsset.provider_key == provider_key,
            )
        )
    )


def create_asset(db: Session, **fields) -> ResourceAsset:
    asset = ResourceAsset(**fields)
    db.add(asset)
    db.flush()
    return asset


# ---- audit ----


def list_audit_logs(db: Session, employee_id: int | None = None) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id)
    if employee_id is not None:
        stmt = stmt.where(AuditLog.employee_id == employee_id)
    return list(db.scalars(stmt))
