"""Lifecycle API (v0.4, frozen contract: docs/design-v0.4-lifecycle.md §10).

Routes under /employees/{id}/... live here (not in employees.py) so the
lifecycle surface stays in one module; path prefixes do not collide with the
existing employee routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.organization import Employee
from app.repositories import events as event_repo
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import organization as org_repo
from app.schemas.knowledge import EventOut
from app.schemas.lifecycle import (
    AccessPackageCreate,
    AccessPackageOut,
    EffectiveAccessOut,
    EffectiveEntitlementOut,
    EmploymentHistoryOut,
    EmploymentOut,
    EntitlementOut,
    JobOut,
    OffboardRequest,
    OnboardRequest,
    OnboardResponse,
    PositionOut,
    PreviewRequest,
    PreviewResponse,
    ProvisioningStepOut,
    ReconcileResponse,
    ResourceAccountOut,
    ResourceAssetOut,
    SuspendRequest,
    TransferRequest,
)
from app.schemas.organization import EmployeeOut
from app.services import lifecycle as lifecycle_service

router = APIRouter(tags=["lifecycle"])

LIFELINE_PREFIXES = ("employee.", "resource.", "asset.")


def _get_employee_or_404(db: Session, employee_id: int) -> Employee:
    employee = org_repo.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


def _steps(db: Session, job_id: int) -> list[ProvisioningStepOut]:
    return [ProvisioningStepOut.model_validate(s) for s in lifecycle_repo.list_steps(db, job_id)]


def _job_detail(db: Session, job) -> JobOut:
    out = JobOut.model_validate(job)
    out.steps = _steps(db, job.id)
    return out


# ---- onboarding / lifecycle actions ----


@router.post("/employees/onboard", response_model=OnboardResponse, status_code=201)
async def onboard_employee(
    payload: OnboardRequest, db: Session = Depends(get_db)
) -> OnboardResponse:
    employee, job = await lifecycle_service.onboard(db, payload)
    return OnboardResponse(employee=EmployeeOut.model_validate(employee), job=_job_detail(db, job))


@router.post("/employees/{employee_id}/transfer", response_model=JobOut)
async def transfer_employee(
    employee_id: int, payload: TransferRequest, db: Session = Depends(get_db)
) -> JobOut:
    employee = _get_employee_or_404(db, employee_id)
    job = await lifecycle_service.transfer(db, employee, payload)
    return _job_detail(db, job)


@router.post("/employees/{employee_id}/suspend", response_model=JobOut)
async def suspend_employee(
    employee_id: int, payload: SuspendRequest | None = None, db: Session = Depends(get_db)
) -> JobOut:
    employee = _get_employee_or_404(db, employee_id)
    job = await lifecycle_service.suspend(db, employee, (payload or SuspendRequest()).reason)
    return _job_detail(db, job)


@router.post("/employees/{employee_id}/resume", response_model=JobOut)
async def resume_employee(employee_id: int, db: Session = Depends(get_db)) -> JobOut:
    employee = _get_employee_or_404(db, employee_id)
    job = await lifecycle_service.resume(db, employee)
    return _job_detail(db, job)


@router.post("/employees/{employee_id}/offboard", response_model=JobOut)
async def offboard_employee(
    employee_id: int, payload: OffboardRequest | None = None, db: Session = Depends(get_db)
) -> JobOut:
    employee = _get_employee_or_404(db, employee_id)
    job = await lifecycle_service.offboard(db, employee, payload or OffboardRequest())
    return _job_detail(db, job)


# ---- read endpoints ----


@router.get("/employees/{employee_id}/accounts", response_model=list[ResourceAccountOut])
def list_accounts(employee_id: int, db: Session = Depends(get_db)) -> list[ResourceAccountOut]:
    _get_employee_or_404(db, employee_id)
    return [
        ResourceAccountOut.model_validate(a) for a in lifecycle_repo.list_accounts(db, employee_id)
    ]


def _entitlements_out(entries) -> list[EffectiveEntitlementOut]:
    """`EffectiveEntitlement` → API 形状。**唯一出口**，两个端点共用。

    留两份转换代码迟早漂移（层级字段就是先加在一份里、另一份忘了的那种）。
    """
    return [
        EffectiveEntitlementOut(
            entitlement=EntitlementOut.model_validate(entry.entitlement),
            sources=entry.sources,
        )
        for entry in entries
    ]


@router.get("/employees/{employee_id}/entitlements", response_model=list[EffectiveEntitlementOut])
def list_entitlements(
    employee_id: int, db: Session = Depends(get_db)
) -> list[EffectiveEntitlementOut]:
    from app.lifecycle import access

    _get_employee_or_404(db, employee_id)
    return _entitlements_out(access.employee_entitlements(db, employee_id))


@router.get("/employees/{employee_id}/access", response_model=EffectiveAccessOut)
def read_effective_access(employee_id: int, db: Session = Depends(get_db)) -> EffectiveAccessOut:
    """两层权限视图：人级 / 职位级 / 并集（P4d，docs/position-system.md §4）。

    职位声明与人级已有的重叠是常态（种子定义声明的就是 `engineer` 这类角色包），
    所以差集被拆成 `already_held_by_person` 与 `pending_from_position` 两种语义。
    """
    from app.lifecycle import access

    _get_employee_or_404(db, employee_id)
    view = access.effective_access(db, employee_id)
    declared = [package.slug for package in access.position_packages_for(db, employee_id)]
    person_slugs = {
        package.slug
        for row, package in access.employee_packages(db, employee_id)
        if access.layer_of_source(row.source) == "person"
    }
    position_slugs = {
        package.slug
        for row, package in access.employee_packages(db, employee_id)
        if access.layer_of_source(row.source) == "position"
    }
    # 差集要分两种语义：人级已有 ≠ 待开通（见 schema 里的说明）
    already = [slug for slug in declared if slug in person_slugs and slug not in position_slugs]
    pending = [slug for slug in declared if slug not in person_slugs and slug not in position_slugs]
    return EffectiveAccessOut(
        person=_entitlements_out(view["person"]),
        position=_entitlements_out(view["position"]),
        effective=_entitlements_out(view["effective"]),
        declared_by_position=declared,
        already_held_by_person=already,
        pending_from_position=pending,
    )


@router.get("/employees/{employee_id}/employment", response_model=EmploymentHistoryOut)
def get_employment(employee_id: int, db: Session = Depends(get_db)) -> EmploymentHistoryOut:
    _get_employee_or_404(db, employee_id)
    rows = lifecycle_repo.list_employments(db, employee_id)
    current = lifecycle_repo.get_current_employment(db, employee_id)
    return EmploymentHistoryOut(
        current=EmploymentOut.model_validate(current) if current else None,
        history=[EmploymentOut.model_validate(r) for r in rows],
    )


@router.get("/employees/{employee_id}/assets", response_model=list[ResourceAssetOut])
def list_assets(employee_id: int, db: Session = Depends(get_db)) -> list[ResourceAssetOut]:
    _get_employee_or_404(db, employee_id)
    return [
        ResourceAssetOut.model_validate(a)
        for a in lifecycle_repo.list_assets(db, owner_employee_id=employee_id)
    ]


@router.get("/employees/{employee_id}/timeline", response_model=list[EventOut])
def get_timeline(employee_id: int, db: Session = Depends(get_db)) -> list[EventOut]:
    employee = _get_employee_or_404(db, employee_id)
    events = event_repo.list_events(db, limit=200, actor_employee_id=employee.id)
    return [EventOut.model_validate(e) for e in events if e.type.startswith(LIFELINE_PREFIXES)]


@router.post("/employees/{employee_id}/reconcile", response_model=ReconcileResponse)
async def reconcile_employee(employee_id: int, db: Session = Depends(get_db)) -> ReconcileResponse:
    employee = _get_employee_or_404(db, employee_id)
    drifts = await lifecycle_service.reconcile(db, employee)
    return ReconcileResponse(drifts=[vars(d) for d in drifts])


# ---- positions / access packages ----


@router.get("/positions", response_model=list[PositionOut])
def list_positions(
    department_id: int | None = None, db: Session = Depends(get_db)
) -> list[PositionOut]:
    return [
        PositionOut.model_validate(p)
        for p in lifecycle_repo.list_positions(db, department_id=department_id)
    ]


@router.get("/access-packages", response_model=list[AccessPackageOut])
def list_access_packages(db: Session = Depends(get_db)) -> list[AccessPackageOut]:
    from app.lifecycle import access

    result = []
    for package in lifecycle_repo.list_packages(db):
        out = AccessPackageOut.model_validate(package)
        out.entitlements = [
            EntitlementOut.model_validate(e) for e in access.package_entitlements(db, package)
        ]
        result.append(out)
    return result


@router.post("/access-packages", response_model=AccessPackageOut, status_code=201)
def create_access_package(
    payload: AccessPackageCreate, db: Session = Depends(get_db)
) -> AccessPackageOut:
    from app.lifecycle import access

    package = lifecycle_service.create_access_package(db, payload)
    out = AccessPackageOut.model_validate(package)
    out.entitlements = [
        EntitlementOut.model_validate(e) for e in access.package_entitlements(db, package)
    ]
    return out


# ---- provisioning jobs ----


@router.get("/provisioning-jobs", response_model=list[JobOut])
def list_jobs(employee_id: int | None = None, db: Session = Depends(get_db)) -> list[JobOut]:
    return [JobOut.model_validate(j) for j in lifecycle_repo.list_jobs(db, employee_id=employee_id)]


@router.get("/provisioning-jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = lifecycle_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="provisioning job not found")
    return _job_detail(db, job)


@router.post("/provisioning-jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = lifecycle_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="provisioning job not found")
    job = await lifecycle_service.engine.retry(db, job)
    return _job_detail(db, job)


# ---- preview ----


@router.post("/provisioning/preview", response_model=PreviewResponse)
def provisioning_preview(payload: PreviewRequest, db: Session = Depends(get_db)) -> PreviewResponse:
    return PreviewResponse(steps=lifecycle_service.preview(db, payload))
