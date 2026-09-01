from datetime import datetime

from pydantic import BaseModel

from app.models.enums import RuntimeType
from app.schemas.organization import ORMModel


class RuntimeCapabilitiesOut(BaseModel):
    chat: bool
    task: bool
    filesystem: bool
    terminal: bool
    web: bool
    memory: bool
    skills: bool
    scheduler: bool
    streaming: bool
    artifacts: bool


class RuntimeTypeInfoOut(BaseModel):
    type: str
    implemented: bool
    docker_available: bool
    deployment_modes: list[str]
    capabilities: RuntimeCapabilitiesOut
    supported_providers: list[str]


class RuntimeInstanceOut(BaseModel):
    id: int
    employee_id: int
    runtime_type: str
    deployment_mode: str
    container_name: str | None
    image: str | None
    image_tag: str | None
    runtime_version: str | None
    status: str
    health_status: str
    internal_host: str | None
    internal_port: int | None
    workspace_path: str
    data_path: str
    model_binding_id: int | None
    cpu_limit: float
    memory_limit_mb: int
    last_healthcheck_at: datetime | None
    started_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # joined conveniences
    employee_name: str | None = None
    employee_slug: str | None = None
    provider_name: str | None = None
    model: str | None = None


class RuntimeLogsOut(BaseModel):
    lines: list[str]


class RuntimeImageOut(BaseModel):
    runtime_type: str
    repository: str
    tag: str
    installed_version: str | None
    latest_version: str | None
    update_available: bool
    compatibility_status: str
    update_status: str
    last_checked_at: datetime | None
    used_by: int = 0


class EmployeeRuntimeCreate(BaseModel):
    runtime_type: RuntimeType
    deployment_mode: str = "docker"
    provider_id: int | None = None
    model: str | None = None
    cpu_limit: float = 2.0
    memory_limit_mb: int = 4096


class EmployeeRuntimeProviderPatch(BaseModel):
    provider_id: int
    model: str


class EmployeeBrainOut(ORMModel):
    employee_id: int
    personality: str
    goals: str
    interests: list
    learning_policy: dict
    memory_policy: dict
    curiosity: float


class EmployeeBrainPatch(BaseModel):
    personality: str | None = None
    goals: str | None = None
    interests: list[str] | None = None
    learning_policy: dict | None = None
    memory_policy: dict | None = None
    curiosity: float | None = None
