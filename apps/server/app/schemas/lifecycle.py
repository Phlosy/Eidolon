"""Lifecycle schemas (v0.4, frozen API contract: docs/design-v0.4-lifecycle.md §10)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EmployeeRole, ProviderType, RuntimeType
from app.schemas.organization import EmployeeOut


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- requests ----


class OnboardRequest(BaseModel):
    name: str
    slug: str | None = None
    title: str = ""
    role: EmployeeRole = EmployeeRole.engineer
    department_id: int
    position_id: int | None = None
    manager_employee_id: int | None = None
    runtime_type: RuntimeType = RuntimeType.mock
    provider_id: int | None = None
    provider_name: str | None = None
    provider_type: ProviderType | None = None
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    model: str | None = None
    personality: str = ""
    goals: str = ""
    learning_enabled: bool = True
    curiosity: float = Field(default=0.5, ge=0.0, le=1.0)
    access_package_ids: list[int] = []


class TransferRequest(BaseModel):
    department_id: int
    position_id: int | None = None
    manager_employee_id: int | None = None
    access_package_ids: list[int] | None = None
    reason: str = ""


class SuspendRequest(BaseModel):
    reason: str = ""


class OffboardRequest(BaseModel):
    # "department" (default) | "company" | "archive" | an employee id as string
    transfer_to: str = "department"
    reason: str = ""


class PreviewRequest(BaseModel):
    department_id: int
    position_id: int | None = None
    access_package_ids: list[int] = []


class AccessPackageCreate(BaseModel):
    slug: str
    name: str
    description: str = ""
    entitlement_ids: list[int] = []


# ---- responses ----


class PositionOut(ORMModel):
    id: int
    department_id: int
    title: str
    level: str
    created_at: datetime
    updated_at: datetime


class EntitlementOut(ORMModel):
    id: int
    key: str
    name: str
    type: str
    resource_type: str
    description: str


class EntitlementSourceOut(BaseModel):
    package_id: int
    package_name: str


class EffectiveEntitlementOut(BaseModel):
    entitlement: EntitlementOut
    sources: list[EntitlementSourceOut]


class AccessPackageOut(ORMModel):
    id: int
    slug: str
    name: str
    description: str
    built_in: bool
    entitlements: list[EntitlementOut] = []


class ResourceAccountOut(ORMModel):
    id: int
    employee_id: int
    resource_type: str
    provider_id: int | None
    external_account_id: str | None
    username: str
    display_name: str
    status: str
    provisioning_state: str
    last_synced_at: datetime | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class EmploymentOut(ORMModel):
    id: int
    employee_id: int
    department_id: int | None
    position_id: int | None
    manager_employee_id: int | None
    employment_status: str
    joined_at: datetime
    effective_from: datetime
    effective_to: datetime | None
    metadata_json: dict


class EmploymentHistoryOut(BaseModel):
    current: EmploymentOut | None
    history: list[EmploymentOut]


class ResourceAssetOut(ORMModel):
    id: int
    resource_type: str
    external_id: str | None
    owner_employee_id: int | None
    project_id: int | None
    provider_key: str
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class ProvisioningStepOut(ORMModel):
    id: int
    job_id: int
    seq: int
    resource_type: str
    provider_key: str
    action: str
    description: str
    status: str
    attempts: int
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class JobOut(ORMModel):
    id: int
    employee_id: int
    kind: str
    status: str
    total_steps: int
    done_steps: int
    reason: str
    created_at: datetime
    completed_at: datetime | None
    steps: list[ProvisioningStepOut] = []


class OnboardResponse(BaseModel):
    employee: EmployeeOut
    job: JobOut


class PreviewStepOut(BaseModel):
    resource_type: str
    provider_key: str
    action: str
    description: str
    available: bool


class PreviewResponse(BaseModel):
    steps: list[PreviewStepOut]


class DriftOut(BaseModel):
    account_id: int
    resource_type: str
    kind: str
    detail: str


class ReconcileResponse(BaseModel):
    drifts: list[DriftOut]
