"""Access packages & entitlement algebra (v0.4 §5/§6/§9).

Desired entitlements = union over the employee's AccessPackages, deduped by
entitlement id. Transfer computes ADD/REMOVE/KEEP between the old and new
package sets; role-derived packages (source="role") are managed automatically,
manual ones are never removed implicitly.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.enums import PackageSource
from app.models.lifecycle import AccessPackage, Entitlement
from app.repositories import lifecycle as lifecycle_repo

BASE_PACKAGE_SLUG = "base-employee"

ROLE_TO_PACKAGE_SLUG = {
    "ceo": "ceo",
    "product_manager": "product-manager",
    "researcher": "researcher",
    "engineer": "engineer",
    "qa_engineer": "qa-engineer",
}

# department slug -> package slug (seed departments map 1:1 to roles)
DEPARTMENT_TO_PACKAGE_SLUG = {
    "executive": "ceo",
    "product": "product-manager",
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa-engineer",
}

DEPARTMENT_TO_ROLE = {
    "executive": "ceo",
    "product": "product_manager",
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa_engineer",
}


@dataclass
class EffectiveEntitlement:
    entitlement: Entitlement
    sources: list[dict] = field(default_factory=list)  # [{package_id, package_name}]


@dataclass
class AccessDiff:
    add: list[Entitlement] = field(default_factory=list)
    remove: list[Entitlement] = field(default_factory=list)
    keep: list[Entitlement] = field(default_factory=list)


def package_entitlements(db: Session, package: AccessPackage) -> list[Entitlement]:
    items = lifecycle_repo.list_package_items(db, package.id)
    return [
        entitlement
        for item in items
        if (entitlement := lifecycle_repo.get_entitlement(db, item.entitlement_id)) is not None
    ]


def union_entitlements(db: Session, packages: list[AccessPackage]) -> list[EffectiveEntitlement]:
    """Union over packages, deduped by entitlement id, tracking source packages."""
    merged: dict[int, EffectiveEntitlement] = {}
    for package in packages:
        for entitlement in package_entitlements(db, package):
            entry = merged.setdefault(entitlement.id, EffectiveEntitlement(entitlement=entitlement))
            entry.sources.append({"package_id": package.id, "package_name": package.name})
    return list(merged.values())


def employee_entitlements(db: Session, employee_id: int) -> list[EffectiveEntitlement]:
    rows = lifecycle_repo.list_employee_packages(db, employee_id)
    packages = [
        package
        for row in rows
        if (package := lifecycle_repo.get_package(db, row.package_id)) is not None
    ]
    return union_entitlements(db, packages)


def resolve_packages(
    db: Session,
    *,
    role: str | None = None,
    department_slug: str | None = None,
    extra_package_ids: list[int] | None = None,
) -> list[AccessPackage]:
    """base-employee + the role/department-mapped package + explicit extras."""
    packages: list[AccessPackage] = []
    seen: set[int] = set()

    def _add(package: AccessPackage | None) -> None:
        if package is not None and package.id not in seen:
            seen.add(package.id)
            packages.append(package)

    _add(lifecycle_repo.get_package_by_slug(db, BASE_PACKAGE_SLUG))
    slug = None
    if department_slug is not None:
        slug = DEPARTMENT_TO_PACKAGE_SLUG.get(department_slug)
    if slug is None and role is not None:
        slug = ROLE_TO_PACKAGE_SLUG.get(role)
    if slug is not None and slug != BASE_PACKAGE_SLUG:
        _add(lifecycle_repo.get_package_by_slug(db, slug))
    for package_id in extra_package_ids or []:
        _add(lifecycle_repo.get_package(db, package_id))
    return packages


def sync_role_packages(
    db: Session, employee_id: int, desired: list[AccessPackage]
) -> tuple[list[int], list[int]]:
    """Reconcile source="role" assignments with the desired set; manual rows are
    left untouched. Returns (added_package_ids, removed_package_ids)."""
    desired_ids = {p.id for p in desired}
    added: list[int] = []
    removed: list[int] = []
    existing = lifecycle_repo.list_employee_packages(db, employee_id)
    existing_ids = {row.package_id for row in existing}
    for row in existing:
        if row.source == PackageSource.role.value and row.package_id not in desired_ids:
            lifecycle_repo.delete_employee_package(db, row)
            removed.append(row.package_id)
    for package in desired:
        if package.id not in existing_ids:
            source = (
                PackageSource.manual.value
                if package.slug not in (BASE_PACKAGE_SLUG, *ROLE_TO_PACKAGE_SLUG.values())
                else PackageSource.role.value
            )
            lifecycle_repo.create_employee_package(
                db, employee_id=employee_id, package_id=package.id, source=source
            )
            added.append(package.id)
    db.flush()
    return added, removed


def diff_entitlements(
    current: list[EffectiveEntitlement], desired: list[EffectiveEntitlement]
) -> AccessDiff:
    """§6: ADD = desired - current; REMOVE = current - desired; KEEP = ∩."""
    current_by_id = {e.entitlement.id: e.entitlement for e in current}
    desired_by_id = {e.entitlement.id: e.entitlement for e in desired}
    return AccessDiff(
        add=[e for key, e in desired_by_id.items() if key not in current_by_id],
        remove=[e for key, e in current_by_id.items() if key not in desired_by_id],
        keep=[e for key, e in desired_by_id.items() if key in current_by_id],
    )
