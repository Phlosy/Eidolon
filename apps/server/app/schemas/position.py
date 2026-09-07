"""Position schemas —— 职位域的读出契约（写入契约在 P4b 的分配工作流里）。

`CurrentPositionOut` 是**派生视图**，不是可写对象：它没有对应的 PATCH 入口，
改变它只能通过"创建/关闭 PositionAssignment"。这条边界是 ADR-1/ADR-2 的落点。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PositionORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CurrentPositionOut(PositionORMModel):
    """`employees.role` 的正式替代物。为 null ⇒ 这个人处于 `AVAILABLE`。"""

    definition_id: int
    code: str = Field(description="公司内稳定唯一，机器可引用（如里程碑负责人按 code 找）")
    name: str
    level: int
    job_family: str = Field(description="office 分区与统计口径")
    legacy_role: str | None = Field(
        default=None, description="仅用于兼容镜像；新业务禁止读（ADR-5）"
    )
    department_id: int | None = None
    department_name: str | None = None
    slot_id: int
    slot_code: str
    since: datetime
    assignment_type: str
    position_is_custom: bool = Field(
        default=False, description="True ⇒ 该职位没有 legacy role 对应，旧字段是兜底值而非真值"
    )
    # `fit` 不在这里：Fit 依赖能力域（P9）。在没有证据分数之前返回一个数字就是编造。


# ------------------------------------------------------------------ 写入契约
# 只有这几类 In 模型能改变职位域；`CurrentPositionOut` 一类是派生视图，没有 PATCH 入口。


class PositionDefinitionIn(BaseModel):
    """开设一个职位模板（"公司需要什么职位"），不代表任何具体人。"""

    code: str = Field(min_length=1, max_length=100, description="公司内稳定唯一")
    name: str = Field(min_length=1, max_length=200)
    job_family: str = ""
    level: int = Field(default=1, ge=1, le=20)
    description: str = ""
    responsibilities: list[dict] = Field(default_factory=list)
    legacy_role: str | None = Field(
        default=None, description="仅在确实存在旧 role 对应时填写；不为了兼容而谎报"
    )


class PositionDefinitionOut(PositionORMModel):
    id: int
    company_id: int | None
    template_scope: str
    code: str
    name: str
    job_family: str
    level: int
    description: str
    legacy_role: str | None
    built_in: bool
    # 三个派生字段**不得带默认值**（ADR-12）：默认 0/[] 会让“忘了算”与“真的为 0”
    # 在响应体里长得一模一样。只能由 definitions_out() 显式填入后交给 schema 校验。
    slot_count: int
    vacant_count: int
    package_slugs: list[str]


class RosterIncumbent(BaseModel):
    id: int
    name: str
    slug: str


class PositionSlotOut(PositionORMModel):
    """坑的读出。**行政态与占用态是两个字段**（ADR-2 拍板），后者由派生填入。"""

    id: int
    company_id: int
    department_id: int
    department_name: str | None = None
    position_definition_id: int
    position_code: str | None = None
    position_name: str | None = None
    slot_code: str
    headcount_index: int
    administrative_status: str = Field(
        description="PLANNED / ACTIVE / FROZEN / CLOSED（唯一可写态）"
    )
    occupancy_status: str = Field(
        description="VACANT / OCCUPIED / FROZEN / CLOSED（派生，无写入口）"
    )
    manager_slot_id: int | None = None
    closed_at: datetime | None = None
    incumbents: list[RosterIncumbent] = Field(default_factory=list)


class SlotIn(BaseModel):
    department_id: int
    manager_slot_id: int | None = None
    count: int = Field(default=1, ge=1, le=20, description="一次开设几个同定义编制")
    note: str = ""


class SlotAdminIn(BaseModel):
    administrative_status: str = Field(description="只接受行政态；VACANT/OCCUPIED 会被 422 拒绝")
    reason: str = ""


class SlotActionIn(BaseModel):
    """坑的行政态动作为什么用 POST 而不是 PATCH：`position-system.md §8` 定的就是
    `/slots/{id}/freeze|close` 这种动作端点。动作端点更好审计（日志里是"冻结"，不是"改了个字段"），
    并且天然不允许调用方把 `administrative_status` 写成任意值。
    """

    reason: str = Field(default="", max_length=500)


class AssignmentIn(BaseModel):
    """显式分配。`slot_id` 与兼容口 `position_id` 二选一；都给则 `slot_id` 优先。"""

    slot_id: int | None = None
    position_id: int | None = Field(
        default=None, description="v0.4 兼容：按 v13 的 __position_id 标记找坑，不新建坑"
    )
    reason: str = ""
    effective_from: datetime | None = None
    assigned_by: int | None = None
    manager_employee_id: int | None = Field(
        default=None, description="显式指定的汇报对象；不给则从坑的 manager_slot_id 推导"
    )
    kind: str = Field(default="assign", description="assign | transfer —— 决定被关窗那一行的状态字")


class AssignmentOut(PositionORMModel):
    id: int
    employee_id: int
    department_id: int | None
    position_slot_id: int | None
    position_id: int | None
    assignment_type: str
    is_primary: bool
    employment_status: str
    joined_at: datetime
    effective_from: datetime
    effective_to: datetime | None
    position_title_snapshot: str
    reason: str
    # 派生字段（ADR-12）：无默认值，只能由 assignment_out() 填入
    occupied_slot: bool


class RosterEntryOut(BaseModel):
    employee_id: int
    name: str
    slug: str
    avatar: str = ""
    department_id: int | None = None
    department_name: str | None = None
    lifecycle_status: str
    workforce_status: str
    has_primary_assignment: bool
    occupies_establishment: bool
    current_position: CurrentPositionOut | None = None
    # 只读诊断（不是状态）：区分"正常待分配"与"历史悬空引用"（ADR-12：无默认值）
    integrity: list[str]
    # ---- P9 名册富化字段（均由序列化出口批量计算；低置信/未评估 = 空列表而非 0） ----
    runtime: dict | None = None
    provider: dict | None = None
    traits_summary: list[dict] = Field(default_factory=list)
    top_general_competencies: list[dict] = Field(default_factory=list)
    top_professional_competencies: list[dict] = Field(default_factory=list)
    assessment_summary: dict | None = None
    recent_activity: dict | None = None


class RosterStatsOut(BaseModel):
    total: int
    on_roster: int
    by_status: dict[str, int]
    slots_total: int
    slots_vacant: int


class IntegrityItemOut(BaseModel):
    assignment_id: int
    employee_id: int
    employee_slug: str | None = None
    kind: str
    detail: str


class IntegrityReportOut(BaseModel):
    """只读诊断报告。刻意不带任何可写字段 —— 它不是第二个状态真相。"""

    read_only: bool = True
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[IntegrityItemOut] = Field(default_factory=list)


class DepartmentOrganizationOut(BaseModel):
    """组织树的一层：部门 → 编制（含两个并列状态字段，ADR-2）。"""

    id: int
    name: str
    slug: str
    slots: list[PositionSlotOut] = Field(default_factory=list)


class OrganizationOut(BaseModel):
    departments: list[DepartmentOrganizationOut] = Field(default_factory=list)
