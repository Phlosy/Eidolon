from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ProviderScope, ProviderType


class ModelEntryIn(BaseModel):
    """一条模型条目：model 是发给厂商的真实模型名，alias 是界面显示名。"""

    model: str
    alias: str = ""


class ProviderPresetOut(BaseModel):
    """内置厂商预设（GET /providers/presets）：前端表单据此自动填 base_url 与推荐模型。"""

    provider_type: str
    default_base_url: str | None
    requires_api_key: bool
    recommended_models: list[str]
    docs_url: str | None = None


class ProviderCreate(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: str | None = None
    # v0.3: omitted scope resolves to "employee" when owner_employee_id is
    # given, "company" otherwise.
    scope: ProviderScope | None = None
    owner_employee_id: int | None = None
    api_key: str | None = None  # write-only; stored in the secret store
    metadata: dict = {}
    # 公司级模型目录：绑定是员工级的，公司级只存目录（metadata.available_models）
    models: list[ModelEntryIn] = []
    primary_model: str | None = None


class EmployeeProviderCreate(BaseModel):
    """POST /employees/{id}/providers — always scope=employee, owned by the employee."""

    name: str
    provider_type: ProviderType
    base_url: str | None = None
    api_key: str | None = None  # write-only; stored in the secret store
    model: str | None = None  # legacy 单模型入口；等价于 models=[{"model": model}]
    # 多模型条目：每个建一条 ModelBinding，primary_model 为默认启动（缺省取第一个）
    models: list[ModelEntryIn] = []
    primary_model: str | None = None


class BindingCreate(BaseModel):
    """POST /employees/{id}/bindings —— 给员工加一条模型绑定。"""

    provider_id: int
    model: str
    alias: str = ""
    make_primary: bool = False


class BindingPatch(BaseModel):
    """PATCH /employees/{id}/bindings/{id} —— 目前只有显示名可改。"""

    alias: str | None = None


class ModelBindingOut(BaseModel):
    id: int
    employee_id: int
    provider_id: int
    provider_name: str
    model: str
    alias: str
    is_primary: bool
    position: int


class ProviderProbeRequest(BaseModel):
    """POST /providers/probe —— 不保存配置，直接试连并拉模型清单。"""

    provider_type: ProviderType
    base_url: str | None = None
    api_key: str | None = None  # 只用于本次探测，不落库


class ProviderProbeOut(BaseModel):
    ok: bool
    error: str | None
    models: list[str]


class ProviderPatch(BaseModel):
    name: str | None = None
    provider_type: ProviderType | None = None
    base_url: str | None = None
    scope: ProviderScope | None = None
    owner_employee_id: int | None = None
    api_key: str | None = None  # write-only: sending a value replaces the credential
    enabled: bool | None = None
    metadata: dict | None = None
    # 公司级模型目录更新（None = 不动）
    models: list[ModelEntryIn] | None = None
    primary_model: str | None = None


class ProviderOut(BaseModel):
    """Never carries plaintext key material — only a mask."""

    id: int
    name: str
    provider_type: str
    base_url: str | None
    scope: str
    owner_employee_id: int | None
    enabled: bool
    has_credential: bool
    credential_mask: str | None
    metadata: dict
    in_use_by: int = 0
    # 公司级模型目录（员工私有账号通过 bindings 管模型，这里为空）
    available_models: list[dict] = []
    default_model: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderTestResultOut(BaseModel):
    ok: bool
    latency_ms: float | None
    error: str | None
    models_count: int | None


class ProviderModelsOut(BaseModel):
    models: list[str]
    source: str = "api"  # api | manual
