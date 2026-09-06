"""Employee lifecycle orchestrator (v0.4): API-facing service.

EmployeeService never talks to Gitea / Drive / the filesystem directly for
provisioning — everything goes through the ProvisioningEngine + provisioners.
This module owns state transitions, employment history (append-only), package
assignment, audit entries and lifecycle events; the engine owns job execution.

Legacy migration (§12) is ``seed_lifecycle`` — idempotent, runs at startup.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brain import BrainTraits, write_traits_to_brain
from app.brain.projection import project_brain
from app.core.config import settings
from app.events.bus import bus
from app.lifecycle import access, audit
from app.lifecycle.engine import ProvisioningEngine
from app.lifecycle.naming import naming
from app.lifecycle.provisioners.base import Drift, ProvisionContext
from app.lifecycle.provisioners.registry import get_registry
from app.models.base import utcnow
from app.models.enums import (
    EmployeeStatus,
    LifecycleStatus,
    PackageSource,
    ProviderScope,
    ProvisioningJobKind,
    ResourceAccountStatus,
    ResourceType,
)
from app.models.lifecycle import AccessPackage, ProvisioningJob
from app.models.organization import Department, Employee
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo
from app.schemas.lifecycle import (
    AccessPackageCreate,
    OffboardRequest,
    OnboardRequest,
    PreviewRequest,
    TransferRequest,
)
from app.schemas.provider import EmployeeProviderCreate, ModelEntryIn
from app.services import seed
from app.services.providers import provider_service, validate_model_name

engine = ProvisioningEngine()

DEPARTMENT_POSITION_TITLES = {
    "executive": "CEO",
    "product": "Product Manager",
    "research": "Researcher",
    "engineering": "Engineer",
    "qa": "QA Engineer",
}

ROLE_TO_DEPARTMENT_SLUG = {role: slug for slug, role in access.DEPARTMENT_TO_ROLE.items()}


# entitlement catalog seeded for the builtin packages (§9)
def _entitlement_catalog(company_slug: str) -> list[dict]:
    catalog = [
        {
            "key": "workspace:private",
            "name": "Private workspace",
            "type": "resource_access",
            "resource_type": ResourceType.workspace.value,
            "description": "Employee-private workspace directory",
            "config": {"profile": "private"},
        },
        {
            "key": "workspace:dev",
            "name": "Dev workspace",
            "type": "resource_access",
            "resource_type": ResourceType.workspace.value,
            "description": "Workspace with development tooling profile",
            "config": {"profile": "dev"},
        },
        {
            "key": "docs:company-read",
            "name": "Company docs (read)",
            "type": "permission",
            "resource_type": ResourceType.docs.value,
            "description": "Viewer on the company handbook root",
            "config": {"path": "drive/handbook", "role": "viewer"},
        },
        {
            "key": "git:company-org-member",
            "name": "Company git org member",
            "type": "group",
            "resource_type": ResourceType.git.value,
            "description": "Membership in the company git organization",
            "config": {"org": company_slug, "team": "members"},
        },
    ]
    for dept_name, dept_slug in seed.DEPARTMENTS:
        catalog.append(
            {
                "key": f"git:{dept_slug}-team",
                "name": f"{dept_name} git team",
                "type": "group",
                "resource_type": ResourceType.git.value,
                "description": f"Membership in the {dept_name} git team",
                "config": {"org": company_slug, "team": dept_slug},
            }
        )
        catalog.append(
            {
                "key": f"docs:{dept_slug}",
                "name": f"{dept_name} docs",
                "type": "permission",
                "resource_type": ResourceType.docs.value,
                "description": f"Editor on the {dept_name} knowledge folder",
                "config": {
                    "path": f"drive/knowledge/departments/{dept_slug}",
                    "role": "editor",
                    "create": True,
                },
            }
        )
    return catalog


def _package_catalog() -> list[dict]:
    return [
        {
            "slug": access.BASE_PACKAGE_SLUG,
            "name": "Base Employee",
            "description": "Every employee: private workspace, company docs read, git org member",
            "role": None,
            "entitlements": ["workspace:private", "docs:company-read", "git:company-org-member"],
        },
        {
            "slug": "ceo",
            "name": "CEO",
            "description": "Executive department access",
            "role": "ceo",
            "entitlements": ["git:executive-team", "docs:executive"],
        },
        {
            "slug": "product-manager",
            "name": "Product Manager",
            "description": "Product department access",
            "role": "product_manager",
            "entitlements": ["git:product-team", "docs:product"],
        },
        {
            "slug": "researcher",
            "name": "Researcher",
            "description": "Research department access",
            "role": "researcher",
            "entitlements": ["git:research-team", "docs:research"],
        },
        {
            "slug": "engineer",
            "name": "Engineer",
            "description": "Engineering department access + dev workspace",
            "role": "engineer",
            "entitlements": ["git:engineering-team", "docs:engineering", "workspace:dev"],
        },
        {
            "slug": "qa-engineer",
            "name": "QA Engineer",
            "description": "QA department access",
            "role": "qa_engineer",
            "entitlements": ["git:qa-team", "docs:qa"],
        },
    ]


# --------------------------------------------------------------------- helpers


def _get_department_or_404(db: Session, department_id: int) -> Department:
    department = db.get(Department, department_id)
    company = org_repo.get_default_company(db)
    if department is None or company is None or department.company_id != company.id:
        raise HTTPException(status_code=404, detail="department not found")
    return department


def _validate_packages(db: Session, package_ids: list[int]) -> None:
    for package_id in package_ids:
        if lifecycle_repo.get_package(db, package_id) is None:
            raise HTTPException(status_code=404, detail=f"access package not found: {package_id}")


def _validate_position(db: Session, position_id: int | None, department_id: int) -> None:
    if position_id is None:
        return
    position = lifecycle_repo.get_position(db, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="position not found")
    if position.department_id != department_id:
        raise HTTPException(status_code=422, detail="position belongs to a different department")


def _guard_lifecycle(employee: Employee, allowed: tuple[str, ...], action: str) -> None:
    if employee.lifecycle_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"cannot {action} employee in lifecycle state {employee.lifecycle_status}",
        )


# --------------------------------------------------------------------- onboard


async def onboard(db: Session, payload: OnboardRequest) -> tuple[Employee, ProvisioningJob]:
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    department = _get_department_or_404(db, payload.department_id)
    _validate_position(db, payload.position_id, department.id)
    _validate_packages(db, payload.access_package_ids)
    if payload.provider_id is not None:
        provider = provider_repo.get_provider_visible(db, payload.provider_id)
        if provider is None or provider.scope != ProviderScope.company.value:
            raise HTTPException(status_code=404, detail="company provider not found")
        if not payload.model:
            raise HTTPException(status_code=422, detail="model is required with a provider")
    elif payload.provider_type is not None and (not payload.provider_name or not payload.model):
        raise HTTPException(
            status_code=422,
            detail="provider_name and model are required for a new provider",
        )
    slug = naming.username(payload.slug or payload.name)
    # 唯一性按全局口径判：slug / username / workspace_path / memory_namespace 都是全局唯一列，
    # 而公司视角的存在性检查看不见别的公司，会把冲突推到 INSERT 上变成 500。
    if org_repo.slug_taken_anywhere(db, slug):
        raise HTTPException(status_code=409, detail=f"employee slug already exists: {slug}")

    employee = org_repo.create_employee(
        db,
        company_id=company.id,
        department_id=department.id,
        name=payload.name,
        slug=slug,
        role=payload.role.value,
        title=payload.title,
        avatar="",
        status=EmployeeStatus.idle.value,
        lifecycle_status=LifecycleStatus.onboarding.value,
        username=slug,
        runtime_type=payload.runtime_type.value,
        runtime_config={},
        workspace_path=f"{settings.workspace_root}/{slug}",
        memory_namespace=f"emp_{slug}",
    )
    lifecycle_repo.create_employment(
        db,
        employee_id=employee.id,
        department_id=department.id,
        position_id=payload.position_id,
        manager_employee_id=payload.manager_employee_id,
        employment_status="active",
        metadata_json={"kind": "hire"},
    )
    packages = access.resolve_packages(
        db,
        role=payload.role.value,
        department_slug=department.slug,
        extra_package_ids=payload.access_package_ids,
    )
    for package in packages:
        source = (
            PackageSource.role.value
            if package.slug in (access.BASE_PACKAGE_SLUG, *access.ROLE_TO_PACKAGE_SLUG.values())
            else PackageSource.manual.value
        )
        lifecycle_repo.create_employee_package(
            db, employee_id=employee.id, package_id=package.id, source=source
        )
    seed.ensure_employee_runtime_state(db)
    brain = runtime_repo.ensure_brain(db, employee.id)
    brain.personality = payload.personality
    brain.goals = payload.goals
    brain.learning_policy = {
        **dict(brain.learning_policy or {}),
        "enabled": payload.learning_enabled,
        "source": "onboarding",
    }
    # traits 是唯一权威，curiosity 只是兼容镜像；两者只能由 BrainTraits 同步写（§4.1）。
    write_traits_to_brain(brain, BrainTraits.build({"curiosity": payload.curiosity}))
    project_brain(db, employee, brain)

    binding = None
    # 条目化模型：payload.model 是默认启动模型，payload.models 是完整条目列表；
    # 两者合并去重，默认模型不在列表里时补进去
    entries = list(payload.models)
    if payload.model and not any(entry.model == payload.model for entry in entries):
        entries.append(ModelEntryIn(model=payload.model))
    primary_model = payload.model or (entries[0].model if entries else None)
    if payload.provider_id is not None:
        provider = provider_repo.get_provider_visible(db, payload.provider_id, employee.id)
        assert provider is not None
        provider_repo.clear_primary_flags(db, employee.id)
        for position, entry in enumerate(entries):
            created = provider_repo.create_binding(
                db,
                employee_id=employee.id,
                provider_id=provider.id,
                model=validate_model_name(entry.model),
                alias=entry.alias.strip(),
                is_primary=entry.model == primary_model,
                position=position,
            )
            if entry.model == primary_model:
                binding = created
    elif payload.provider_type is not None:
        if not payload.provider_name or not entries:
            raise HTTPException(
                status_code=422,
                detail="provider_name and at least one model are required for a new provider",
            )
        provider_service.create_for_employee(
            db,
            employee.id,
            EmployeeProviderCreate(
                name=payload.provider_name,
                provider_type=payload.provider_type,
                base_url=payload.provider_base_url,
                api_key=payload.provider_api_key,
                models=entries,
                primary_model=primary_model,
            ),
        )
        binding = provider_repo.get_primary_binding(db, employee.id)

    if binding is not None:
        instance = runtime_repo.get_instance_for_employee(db, employee.id)
        if instance is not None:
            instance.model_binding_id = binding.id
    db.commit()
    db.refresh(employee)

    audit.record(
        db,
        action="employee.hired",
        employee_id=employee.id,
        before=None,
        after=audit.employee_snapshot(employee),
        reason="onboarding",
    )
    db.commit()
    bus.publish(
        "employee.hired",
        {"id": employee.id, "name": employee.name, "role": employee.role},
        company_id=company.id,
        actor_employee_id=employee.id,
    )
    # compat: pre-v0.4 consumers listen for employee.created
    bus.publish(
        "employee.created",
        {"id": employee.id, "name": employee.name, "role": employee.role},
        company_id=company.id,
        actor_employee_id=employee.id,
    )
    bus.publish(
        "employee.onboarding_started",
        {"id": employee.id, "name": employee.name},
        company_id=company.id,
        actor_employee_id=employee.id,
    )

    entitlements = access.employee_entitlements(db, employee.id)
    plan = engine.plan_onboarding(entitlements, slug=employee.slug)
    job = engine.create_job(
        db,
        employee_id=employee.id,
        kind=ProvisioningJobKind.onboarding.value,
        plan=plan,
        reason="onboarding",
    )
    db.commit()
    job = await engine.run(db, job)
    db.refresh(employee)
    return employee, job


# -------------------------------------------------------------------- transfer


async def transfer(db: Session, employee: Employee, payload: TransferRequest) -> ProvisioningJob:
    _guard_lifecycle(employee, (LifecycleStatus.active.value,), "transfer")
    department = _get_department_or_404(db, payload.department_id)
    _validate_position(db, payload.position_id, department.id)
    _validate_packages(db, payload.access_package_ids or [])

    current = access.employee_entitlements(db, employee.id)
    desired_packages = access.resolve_packages(
        db,
        role=employee.role,
        department_slug=department.slug,
        extra_package_ids=payload.access_package_ids or [],
    )
    # manual assignments are never implicitly removed
    desired_ids = {p.id for p in desired_packages}
    for row in lifecycle_repo.list_employee_packages(db, employee.id):
        if row.source == PackageSource.manual.value and row.package_id not in desired_ids:
            package = lifecycle_repo.get_package(db, row.package_id)
            if package is not None:
                desired_packages.append(package)
                desired_ids.add(package.id)
    diff = access.diff_entitlements(current, access.union_entitlements(db, desired_packages))

    before = audit.employee_snapshot(employee)
    current_employment = lifecycle_repo.get_current_employment(db, employee.id)
    joined_at = current_employment.joined_at if current_employment else utcnow()
    if current_employment is not None:
        current_employment.effective_to = utcnow()
        current_employment.employment_status = "transferred"
    lifecycle_repo.create_employment(
        db,
        employee_id=employee.id,
        department_id=department.id,
        position_id=payload.position_id,
        manager_employee_id=payload.manager_employee_id,
        employment_status="active",
        joined_at=joined_at,
        metadata_json={"kind": "transfer", "reason": payload.reason},
    )
    previous_department_id = employee.department_id
    employee.department_id = department.id
    access.sync_role_packages(db, employee.id, desired_packages)
    employee.lifecycle_status = LifecycleStatus.transferring.value
    db.commit()

    audit.record(
        db,
        action="employee.transfer",
        employee_id=employee.id,
        before=before,
        after=audit.employee_snapshot(employee),
        reason=payload.reason,
    )
    db.commit()
    bus.publish(
        "employee.transfer_started",
        {
            "id": employee.id,
            "from_department_id": previous_department_id,
            "to_department_id": department.id,
        },
        company_id=employee.company_id,
        actor_employee_id=employee.id,
    )

    plan = engine.plan_transfer(diff)
    job = engine.create_job(
        db,
        employee_id=employee.id,
        kind=ProvisioningJobKind.transfer.value,
        plan=plan,
        reason=payload.reason,
        metadata={
            "add": [e.key for e in diff.add],
            "remove": [e.key for e in diff.remove],
            "keep": [e.key for e in diff.keep],
        },
    )
    db.commit()
    return await engine.run(db, job)


# ------------------------------------------------------------ suspend / resume


async def suspend(db: Session, employee: Employee, reason: str = "") -> ProvisioningJob:
    _guard_lifecycle(
        employee, (LifecycleStatus.active.value, LifecycleStatus.onboarding.value), "suspend"
    )
    before = audit.employee_snapshot(employee)
    plan = engine.plan_suspension(db, employee)
    job = engine.create_job(
        db,
        employee_id=employee.id,
        kind=ProvisioningJobKind.suspension.value,
        plan=plan,
        reason=reason,
    )
    db.commit()
    job = await engine.run(db, job)
    db.refresh(employee)
    audit.record(
        db,
        action="employee.suspend",
        employee_id=employee.id,
        before=before,
        after=audit.employee_snapshot(employee),
        reason=reason,
    )
    db.commit()
    return job


async def resume(db: Session, employee: Employee) -> ProvisioningJob:
    _guard_lifecycle(employee, (LifecycleStatus.suspended.value,), "resume")
    before = audit.employee_snapshot(employee)
    plan = engine.plan_resumption(db, employee)
    job = engine.create_job(
        db,
        employee_id=employee.id,
        kind=ProvisioningJobKind.resumption.value,
        plan=plan,
        reason="resume",
    )
    db.commit()
    job = await engine.run(db, job)
    db.refresh(employee)
    audit.record(
        db,
        action="employee.resume",
        employee_id=employee.id,
        before=before,
        after=audit.employee_snapshot(employee),
        reason="resume",
    )
    db.commit()
    return job


# ------------------------------------------------------------------- offboard


def _resolve_transfer_target(db: Session, employee: Employee, transfer_to: str) -> dict:
    if transfer_to == "department":
        department = db.get(Department, employee.department_id) if employee.department_id else None
        return {
            "kind": "department",
            "department_id": employee.department_id,
            "department_slug": department.slug if department else None,
        }
    if transfer_to == "company":
        return {"kind": "company", "company_id": employee.company_id}
    if transfer_to == "archive":
        return {"kind": "archive"}
    try:
        target_id = int(transfer_to)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail='transfer_to must be "department"|"company"|"archive"|an employee id',
        ) from None
    target = org_repo.get_employee(db, target_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"transfer target employee not found: {target_id}"
        )
    if target.id == employee.id:
        raise HTTPException(status_code=422, detail="cannot transfer assets to the same employee")
    return {"kind": "employee", "employee_id": target.id}


async def offboard(db: Session, employee: Employee, payload: OffboardRequest) -> ProvisioningJob:
    # transferring is allowed: a partial transfer (e.g. git provider down) must
    # never trap an employee who needs to be offboarded.
    _guard_lifecycle(
        employee,
        (
            LifecycleStatus.active.value,
            LifecycleStatus.suspended.value,
            LifecycleStatus.onboarding.value,
            LifecycleStatus.transferring.value,
        ),
        "offboard",
    )
    target = _resolve_transfer_target(db, employee, payload.transfer_to)
    before = audit.employee_snapshot(employee)
    employee.lifecycle_status = LifecycleStatus.offboarding.value
    db.commit()
    audit.record(
        db,
        action="employee.offboard",
        employee_id=employee.id,
        before=before,
        after=audit.employee_snapshot(employee),
        reason=payload.reason,
    )
    db.commit()
    bus.publish(
        "employee.offboarding_started",
        {"id": employee.id, "name": employee.name, "transfer_to": payload.transfer_to},
        company_id=employee.company_id,
        actor_employee_id=employee.id,
    )
    plan = engine.plan_offboarding(db, employee)
    job = engine.create_job(
        db,
        employee_id=employee.id,
        kind=ProvisioningJobKind.offboarding.value,
        plan=plan,
        reason=payload.reason,
        metadata={"transfer_target": target},
    )
    db.commit()
    return await engine.run(db, job, extras={"transfer_target": target})


# ------------------------------------------------------------------ reconcile


async def reconcile(db: Session, employee: Employee) -> list[Drift]:
    registry = get_registry()
    drifts: list[Drift] = []
    for account in lifecycle_repo.list_accounts(db, employee.id):
        provider_key = engine.account_provider_key(db, account)
        provisioner = registry.provisioner_for(provider_key)
        drifts.extend(await provisioner.reconcile(account, ProvisionContext(db=db)))
    return drifts


# -------------------------------------------------------------------- preview


def preview(db: Session, payload: PreviewRequest) -> list[dict]:
    """Pure plan computation — never writes (wizard availability warnings)."""
    department = _get_department_or_404(db, payload.department_id)
    _validate_position(db, payload.position_id, department.id)
    _validate_packages(db, payload.access_package_ids)
    packages = access.resolve_packages(
        db, department_slug=department.slug, extra_package_ids=payload.access_package_ids
    )
    entitlements = access.union_entitlements(db, packages)
    plan = engine.plan_onboarding(entitlements)
    registry = get_registry()
    return [
        {
            "resource_type": step.resource_type,
            "provider_key": step.provider_key,
            "action": step.action,
            "description": step.description,
            "available": registry.provisioner_for(step.provider_key).available(),
        }
        for step in plan
    ]


# ----------------------------------------------------------- access packages


def create_access_package(db: Session, payload: AccessPackageCreate) -> AccessPackage:
    if lifecycle_repo.get_package_by_slug(db, payload.slug) is not None:
        raise HTTPException(status_code=409, detail=f"access package slug exists: {payload.slug}")
    _validate_packages(db, payload.entitlement_ids)
    package = lifecycle_repo.create_package(
        db,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        role=None,
        built_in=False,
    )
    for entitlement_id in payload.entitlement_ids:
        lifecycle_repo.create_package_item(db, package_id=package.id, entitlement_id=entitlement_id)
    db.commit()
    db.refresh(package)
    return package


# ---------------------------------------------------------- seed / legacy §12


def seed_lifecycle(db: Session) -> None:
    """Idempotent v0.4 seed + legacy migration (docs/design-v0.4-lifecycle.md §12).

    Always runs at startup: providers, entitlement catalog, the 6 default
    access packages, per-department positions; then backfills the 5 seed
    employees (lifecycle_status=active, username, employment record, role
    packages, workspace/docs resource accounts — resources already exist).
    """
    changed = False

    registry = get_registry()
    for provisioner in registry.all():
        if lifecycle_repo.get_provider_by_key(db, provisioner.key) is None:
            lifecycle_repo.create_provider(
                db,
                key=provisioner.key,
                type=provisioner.resource_type,
                name=provisioner.key,
                capabilities=provisioner.capabilities,
                connection={},
                status="active",
            )
            changed = True

    company = org_repo.get_default_company(db)
    company_slug = company.slug if company else "eidolon-studio"
    entitlements: dict[str, int] = {}
    for spec in _entitlement_catalog(company_slug):
        existing = lifecycle_repo.get_entitlement_by_key(db, spec["key"])
        if existing is None:
            existing = lifecycle_repo.create_entitlement(db, **spec)
            changed = True
        entitlements[spec["key"]] = existing.id

    for spec in _package_catalog():
        package = lifecycle_repo.get_package_by_slug(db, spec["slug"])
        if package is None:
            package = lifecycle_repo.create_package(
                db,
                slug=spec["slug"],
                name=spec["name"],
                description=spec["description"],
                role=spec["role"],
                built_in=True,
            )
            changed = True
        for key in spec["entitlements"]:
            entitlement_id = entitlements.get(key)
            if entitlement_id is None:
                continue
            if lifecycle_repo.get_package_item(db, package.id, entitlement_id) is None:
                lifecycle_repo.create_package_item(
                    db, package_id=package.id, entitlement_id=entitlement_id
                )
                changed = True

    if company is None:
        if changed:
            db.commit()
        return

    departments = list(db.scalars(select(Department).where(Department.company_id == company.id)))
    position_by_dept: dict[int, int] = {}
    for department in departments:
        title = DEPARTMENT_POSITION_TITLES.get(department.slug)
        if title is None:
            continue
        position = lifecycle_repo.get_position_by_title(db, department.id, title)
        if position is None:
            position = lifecycle_repo.create_position(
                db, department_id=department.id, title=title, level=""
            )
            changed = True
        position_by_dept[department.id] = position.id

    for employee in org_repo.list_employees(db, company.id):
        if not employee.username:
            employee.username = naming.username(employee.slug)
            changed = True
        if (
            not employee.lifecycle_status
            or employee.lifecycle_status == LifecycleStatus.pending.value
        ):
            employee.lifecycle_status = LifecycleStatus.active.value
            changed = True
        if not lifecycle_repo.list_employments(db, employee.id):
            lifecycle_repo.create_employment(
                db,
                employee_id=employee.id,
                department_id=employee.department_id,
                position_id=position_by_dept.get(employee.department_id),
                employment_status="active",
                joined_at=employee.created_at,
                effective_from=employee.created_at,
                metadata_json={"kind": "legacy_backfill"},
            )
            changed = True
        packages = access.resolve_packages(db, role=employee.role)
        for package in packages:
            if lifecycle_repo.get_employee_package(db, employee.id, package.id) is None:
                lifecycle_repo.create_employee_package(
                    db,
                    employee_id=employee.id,
                    package_id=package.id,
                    source=PackageSource.role.value,
                )
                changed = True
        # resources already exist for legacy employees → accounts backfilled active
        for provider_key, resource_type in (
            ("workspace:local", ResourceType.workspace.value),
            ("docs:builtin", ResourceType.docs.value),
        ):
            if lifecycle_repo.get_account_by_provider_key(db, employee.id, provider_key) is None:
                provider = lifecycle_repo.get_provider_by_key(db, provider_key)
                lifecycle_repo.create_account(
                    db,
                    employee_id=employee.id,
                    resource_type=resource_type,
                    provider_id=provider.id if provider else None,
                    username=naming.username(employee.slug),
                    display_name=employee.name,
                    status=ResourceAccountStatus.active.value,
                    provisioning_state="done",
                    metadata_json={"provider_key": provider_key, "granted": [], "legacy": True},
                )
                changed = True

    if changed:
        db.commit()
