"""Shared helpers for provisioners: ResourceAccount bookkeeping."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.lifecycle.naming import naming
from app.models.enums import ResourceAccountStatus
from app.models.lifecycle import ResourceAccount
from app.models.organization import Employee
from app.repositories import lifecycle as lifecycle_repo


def get_or_create_account(
    db: Session, employee: Employee, *, provider_key: str, resource_type: str
) -> tuple[ResourceAccount, bool]:
    """Idempotent account row lookup/creation. Returns (account, created)."""
    account = lifecycle_repo.get_account_by_provider_key(db, employee.id, provider_key)
    if account is not None:
        return account, False
    provider = lifecycle_repo.get_provider_by_key(db, provider_key)
    account = lifecycle_repo.create_account(
        db,
        employee_id=employee.id,
        resource_type=resource_type,
        provider_id=provider.id if provider else None,
        username=naming.username(employee.slug),
        display_name=employee.name,
        status=ResourceAccountStatus.provisioning.value,
        provisioning_state="pending",
        metadata_json={"provider_key": provider_key, "granted": []},
    )
    return account, True


def set_account_status(account: ResourceAccount, status: str, provisioning_state: str) -> None:
    account.status = status
    account.provisioning_state = provisioning_state
    account.last_synced_at = datetime.now(UTC)


def granted_keys(account: ResourceAccount) -> list[str]:
    return list((account.metadata_json or {}).get("granted") or [])


def mark_granted(account: ResourceAccount, entitlement_key: str) -> bool:
    """Append to the granted list; returns False if already there (dedupe)."""
    granted = granted_keys(account)
    if entitlement_key in granted:
        return False
    granted.append(entitlement_key)
    account.metadata_json = {**(account.metadata_json or {}), "granted": granted}
    return True


def mark_revoked(account: ResourceAccount, entitlement_key: str) -> bool:
    granted = granted_keys(account)
    if entitlement_key not in granted:
        return False
    granted.remove(entitlement_key)
    account.metadata_json = {**(account.metadata_json or {}), "granted": granted}
    return True


def ensure_asset(
    db: Session,
    employee: Employee,
    *,
    provider_key: str,
    resource_type: str,
    external_id: str | None,
    metadata: dict | None = None,
) -> None:
    """Create a ResourceAsset row if none exists for this provider/type/owner."""
    for asset in lifecycle_repo.list_assets_by_provider(db, employee.id, provider_key):
        if asset.resource_type == resource_type:
            return
    lifecycle_repo.create_asset(
        db,
        resource_type=resource_type,
        external_id=external_id,
        owner_employee_id=employee.id,
        provider_key=provider_key,
        metadata_json=metadata or {},
    )


def apply_asset_target(db: Session, employee: Employee, provider_key: str, target: dict) -> int:
    """Rewire ownership of the employee's assets for one provider.

    v1 default: employee → department (owner_employee_id NULL + department
    recorded in metadata). Supports employee / company / archive targets.
    """
    assets = lifecycle_repo.list_assets_by_provider(db, employee.id, provider_key)
    kind = target.get("kind", "department")
    for asset in assets:
        metadata = dict(asset.metadata_json or {})
        metadata.pop("department_id", None)
        metadata.pop("department_slug", None)
        metadata.pop("company_id", None)
        metadata.pop("archived", None)
        if kind == "employee":
            asset.owner_employee_id = target["employee_id"]
        else:
            asset.owner_employee_id = None
            if kind == "department":
                metadata["department_id"] = target.get("department_id")
                if target.get("department_slug"):
                    metadata["department_slug"] = target["department_slug"]
            elif kind == "company":
                metadata["company_id"] = target.get("company_id")
            elif kind == "archive":
                metadata["archived"] = True
        metadata["transferred_from"] = employee.id
        asset.metadata_json = metadata
    db.flush()
    return len(assets)
