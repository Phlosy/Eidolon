from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.enums import EmployeeRole, EmployeeStatus, ProjectWorkMode, RuntimeType
from app.schemas.position import CurrentPositionOut


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DepartmentOut(ORMModel):
    id: int
    company_id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    updated_at: datetime


class CompanyOut(ORMModel):
    id: int
    name: str
    slug: str
    description: str
    industry: str
    settings: dict
    stage: str = "FOUNDING"
    departments: list[DepartmentOut] = []
    created_at: datetime
    updated_at: datetime


class EmployeeCreate(BaseModel):
    name: str
    role: EmployeeRole = EmployeeRole.engineer
    department_id: int | None = None
    title: str = ""
    avatar: str = ""
    runtime_type: RuntimeType = RuntimeType.mock
    runtime_config: dict = {}
    slug: str | None = None


class EmployeePatch(BaseModel):
    name: str | None = None
    title: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    status: EmployeeStatus | None = None
    runtime_type: RuntimeType | None = None
    runtime_config: dict | None = None


class EmployeeOut(ORMModel):
    id: int
    company_id: int
    department_id: int | None
    name: str
    slug: str
    role: str
    title: str
    avatar: str
    status: str
    lifecycle_status: str = "active"
    username: str | None = None
    runtime_type: str
    runtime_config: dict
    workspace_path: str
    memory_namespace: str
    current_task_id: int | None
    created_at: datetime
    updated_at: datetime


class EmployeePerformance(BaseModel):
    employee_id: int
    attempts: int
    success_count: int
    success_rate: float
    artifacts_count: int
    learning_records_count: int


class AssignmentIntegrityOut(BaseModel):
    """任职完整性（只读诊断，不是状态；ADR-12：字段必须由 serializer 计算）。

    与 `/talent-roster/integrity`、名册每行的 `integrity` 同源（position_service.integrity），
    这里只是员工维度的折叠视图：`issues` 为空 = 真的没问题，不是“没算”。
    """

    status: Literal["valid", "invalid"]
    issues: list[str]
    read_only: bool = True


class EmployeeDetailOut(EmployeeOut):
    """**员工详情主接口契约**（/employees/{id} 的最终形态，P6 WIP 落地时接线）。

    派生三区（`workforce_status` / `current_position` / `assignment_integrity`）只读、
    无 PATCH 入口，统一来源于 `WorkforceStatusResolver` / `position_compat` / 名册完整性
    —— 本 schema 只是把它们锁成一个可校验的形态。当前暂挂在 `/talent-roster/{id}`
    （`position_compat.enrich_employee()` 直接产出本形状），等 employees.py WIP 合并后
    由同一个出口接入 `/employees/{id}`。

    分层职责：`/employees/{id}` = 人物详情 + 当前派生任职视图；
    `/talent-roster` = 名册查询/筛选/分页 —— 不要长期让后者当第二套详情 SoT。
    """

    workforce_status: str
    has_primary_assignment: bool
    occupies_establishment: bool
    current_position: CurrentPositionOut | None = None
    assignment_integrity: AssignmentIntegrityOut
    role: str


# ---------------------------------------------------------------------------
# M2.1 · 公司工作策略（D1/D2）—— 只改**默认值**与**责任目标**，不改任何既有项目
# ---------------------------------------------------------------------------


class WorkPolicyOut(BaseModel):
    """公司工作策略读面。

    - `work_mode_default`：新建项目默认用哪种工作模式（冷启动 guided → 成熟 managed）；
    - `work_mode_explicit`：用户是否显式改过（false 时系统会在学习期结束后自动推进）；
    - `work_intake_position_code`：承担 Work Intake 责任的职位 code；
    - `work_intake_is_configured`：是否显式覆盖了默认（默认 = CEO）；
    - `allow_planning_fixtures`：本部署是否允许**显式**请求确定性规划 fixture
      （测试/教程/CI 基础设施；生产应为 false）。
    """

    work_mode_default: str
    work_mode_explicit: bool
    work_mode_by_stage: dict[str, str]
    work_intake_position_code: str
    work_intake_default_position_code: str
    work_intake_is_configured: bool
    allow_planning_fixtures: bool
    #: M2.8：公司默认**运行时策略**（只配环境：运行时/部署/供应商/模型/环境参数）。
    #: 人格、提示词、工作流、技能一律不在这里（W26/I6）。
    runtime_defaults: dict = {}


class WorkPolicyPatchIn(BaseModel):
    work_mode: ProjectWorkMode | None = None
    work_intake_position_code: str | None = None
    #: M2.8：只允许 `contracts.RUNTIME_POLICY_KEYS`；禁止键与未知键都会被拒绝（422）
    runtime_defaults: dict | None = None
