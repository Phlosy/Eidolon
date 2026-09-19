from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.brain.traits import BrainTraits, TraitOutOfRange, UnknownTrait
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
    # 行为投影是否真的进了 agent 上下文（诚实能力位，§8）
    brain_projection: bool = False


class RuntimeTypeInfoOut(BaseModel):
    type: str
    implemented: bool
    docker_available: bool
    deployment_modes: list[str]
    capabilities: RuntimeCapabilitiesOut
    supported_providers: list[str]


class RuntimeProbeFactOut(BaseModel):
    state: Literal["passed", "failed", "unknown"]
    reason: str
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    docker_state: str | None = None
    docker_health: str | None = None
    http_status: int | None = None
    present: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class RuntimeModelProbeRequest(BaseModel):
    """Explicit paid opt-in; one request, no retry/fallback or arbitrary prompts."""

    model_config = {"extra": "forbid"}
    allow_paid: Literal[True]
    idempotency_key: str = Field(min_length=1, max_length=120)
    max_requests: Literal[1] = 1
    max_cost_usd: Decimal = Field(gt=0, le=1)
    timeout_seconds: float = Field(default=20, ge=1, le=30)
    max_output_tokens: int = Field(default=32, ge=1, le=128)


class RuntimeModelProbeOut(BaseModel):
    id: int
    state: str
    result: dict


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
    probes: dict[str, RuntimeProbeFactOut] = Field(default_factory=dict)


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


class BehaviorProjectionOut(BaseModel):
    """§8 投影审计面： revision 可比对，mirror_current 直接回答“容器里那份是不是最新的”。"""

    employee_id: int
    policy_version: str
    revision: int
    band: str
    projection_markdown: str
    paths: list[str]
    mirrored_revision: int | None = None
    mirror_current: bool = False


class EmployeeBrainOut(ORMModel):
    employee_id: int
    personality: str
    goals: str
    interests: list
    learning_policy: dict
    memory_policy: dict
    curiosity: float
    traits: dict | None = None
    # BehaviorPolicy 摘要：只含工作方式（额度/深度/风格），永不含 confidence / 结果判定。
    behavior: dict | None = None


class EmployeeBrainPatch(BaseModel):
    personality: str | None = None
    goals: str | None = None
    interests: list[str] | None = None
    learning_policy: dict | None = None
    memory_policy: dict | None = None
    curiosity: float | None = Field(default=None, ge=0.0, le=1.0)
    traits: dict[str, float] | None = None

    @field_validator("traits")
    @classmethod
    def _traits_must_be_registered_and_in_range(cls, value: dict[str, float] | None):
        """未注册 trait 与越界值都是 422（§17.1）；schema_version 由后端控制，传入被忽略。"""
        if value is None:
            return value
        try:
            BrainTraits.build(value)
        except (UnknownTrait, TraitOutOfRange, TypeError) as exc:
            raise ValueError(str(exc)) from exc
        return value
