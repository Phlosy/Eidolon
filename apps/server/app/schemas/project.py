from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    ArtifactStatus,
    ArtifactType,
    PlanningFixture,
    ProjectWorkMode,
    TaskStatus,
)
from app.schemas.organization import ORMModel


class RequirementCreate(BaseModel):
    code: str | None = None
    title: str
    description: str = ""
    priority: str = "should"
    acceptance_criteria: str


class ReviewConfiguration(BaseModel):
    requirements_review: bool = True
    design_review: bool = True
    acceptance_review: bool = True
    additional_reviews: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def mandatory_gates_cannot_be_disabled(self):
        if not (self.requirements_review and self.design_review and self.acceptance_review):
            raise ValueError("requirements, design and acceptance reviews are mandatory")
        return self


class ProjectParticipants(BaseModel):
    customer_contact: str = ""
    project_owner_employee_id: int | None = None
    presenter_employee_id: int | None = None
    reviewer_names: list[str] = Field(default_factory=list)
    approver_names: list[str] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    name: str
    # Legacy one-prompt intake remains accepted for API compatibility.
    description: str = ""
    goal: str = ""
    code: str | None = None
    priority: str = "medium"
    customer: str = ""
    owner_id: int | None = None
    background: str = ""
    objectives: list[str] = Field(default_factory=list)
    requirements: list[RequirementCreate] = Field(default_factory=list)
    technical_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    deadline: datetime | None = None
    milestones: list[dict] = Field(default_factory=list)
    review_configuration: ReviewConfiguration = Field(default_factory=ReviewConfiguration)
    participants: ProjectParticipants = Field(default_factory=ProjectParticipants)
    tutorial_accelerated: bool = False
    # M2.1（W35）：不传就用公司默认；传了就**快照**到项目行，之后公司默认变化不影响它。
    work_mode: ProjectWorkMode | None = None
    # M2.1（D3/W33）：**基础设施轴**，只能显式请求且需部署开启 `allow_planning_fixtures`。
    # 生产项目默认 `none` —— 不存在"Manager 没反应就用模板顶上"这条路径。
    planning_fixture: PlanningFixture = PlanningFixture.none

    @property
    def is_structured(self) -> bool:
        """**已退役的路由开关**（M2.1，B2）。

        M2.0 之前它决定"走结构化交付还是走 legacy 订单流"；M2.1 起路由由
        `work_mode`（产品）与 `planning_fixture`（基础设施）两个显式维度决定，
        本属性**不再影响任何服务端分支**，只在读面上保留（老客户端/测试的兼容谓词）。
        """
        return bool(
            self.code
            or self.background
            or self.objectives
            or self.requirements
            or self.technical_requirements
            or self.constraints
            or self.deliverables
        )


class ProjectOut(ORMModel):
    id: int
    company_id: int
    name: str
    description: str
    status: str
    goal: str
    owner_id: int | None
    source_order_text: str
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    code: str | None = None
    priority: str = "medium"
    customer: str = ""
    background: str = ""
    objectives: list = []
    technical_requirements: list = []
    constraints: list = []
    deliverables: list = []
    review_configuration: dict = {}
    participants: dict = {}
    tutorial_accelerated: bool = False
    # M2.1（设计 §11.5）
    work_mode: str | None = None
    planning_fixture: str | None = None
    spec_version: int = 1
    work_intake_position_code: str | None = None
    management_employee_id: int | None = None
    management_person_id: int | None = None
    management_assigned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MilestoneOut(ORMModel):
    id: int
    project_id: int
    name: str
    description: str
    status: str
    order: int
    owner_id: int | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskOut(ORMModel):
    id: int
    project_id: int
    milestone_id: int | None
    title: str
    description: str
    kind: str
    status: str
    priority: int
    assignee_id: int | None
    acceptance_criteria: str
    sequence: int
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    phase_id: int | None = None
    # Not an ORM column (n-n via task_dependencies); populated explicitly by services.
    dependencies: list[int] = []
    # M2.6（H4）：Manager 声明的**预期**交付物类型（实际产出在 /tasks/{id}/artifacts）
    produces: list[str] = []
    created_at: datetime
    updated_at: datetime


class TaskPatch(BaseModel):
    status: TaskStatus | None = None
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    assignee_id: int | None = None
    acceptance_criteria: str | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class ProjectPatch(BaseModel):
    owner_id: int | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class MilestonePatch(BaseModel):
    owner_id: int | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class ArtifactCreate(BaseModel):
    project_id: int
    type: ArtifactType = ArtifactType.other
    title: str
    content: str = ""
    task_id: int | None = None
    author_id: int | None = None
    status: ArtifactStatus = ArtifactStatus.draft


class ArtifactOut(ORMModel):
    id: int
    company_id: int
    project_id: int
    task_id: int | None
    type: str
    title: str
    content: str
    path: str | None
    sha256: str | None = None
    work_session_id: int | None = None
    version: int
    status: str
    author_id: int | None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    milestones: list[MilestoneOut] = []
    tasks: list[TaskOut] = []
    artifacts: list[ArtifactOut] = []


class ProjectTimeline(ProjectOut):
    milestones: list[MilestoneOut] = []
    tasks: list[TaskOut] = []


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    status: str


class GraphEdge(BaseModel):
    source: str
    target: str


class ProjectGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class WorkSessionOut(ORMModel):
    id: int
    task_id: int
    employee_id: int
    runtime_type: str
    runtime_session_ref: str
    status: str
    summary: str
    cost: dict
    runtime_instance_id: int | None = None
    provider_id: int | None = None
    model: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    sender_id: int
    content: str
    project_id: int | None = None
    recipient_id: int | None = None
    channel: str = "general"


class MessageOut(ORMModel):
    id: int
    company_id: int
    project_id: int | None
    sender_id: int
    recipient_id: int | None
    channel: str
    content: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# M2.1 · Canonical Executable Project read model（设计 §11.4/§11.5）
#
# `GET /projects/{id}/spec` 是「这个项目能不能回答那 8 个问题」的唯一出口：
# 1. canonical spec 是什么  2. work_mode 是什么  3. Work Intake 责任是什么
# 4. 哪个 Position Assignment 承担它  5. 管理的 actor 是谁
# 6. requirements/deliverables/acceptance criteria 是什么  7. spec 版本
# 8. 是否已进入执行
# ---------------------------------------------------------------------------


class SpecRequirementOut(BaseModel):
    code: str
    title: str
    priority: str = "should"
    acceptance_criteria: str = ""


class ProjectSpecFieldsOut(BaseModel):
    """Canonical Spec —— **Facts / Requirements**，不是 Execution Plan。"""

    background: str = ""
    goal: str = ""
    requirements: list[SpecRequirementOut] = []
    constraints: list[str] = []
    deliverables: list[str] = []
    acceptance_criteria: list[str] = []
    priority: str = "medium"
    deadline: datetime | None = None
    context: str = ""


class SpecCompletenessOut(BaseModel):
    is_complete: bool
    missing: list[str] = []
    #: 契约里允许为空的字段（不参与 missing 判定）
    optional_fields: list[str] = []


class WorkIntakeAssignmentOut(BaseModel):
    employee_id: int
    person_id: int | None = None
    slot_id: int | None = None
    since: datetime | None = None


class WorkIntakeOut(BaseModel):
    responsibility: str
    status: str
    position_code: str
    default_position_code: str
    is_configured: bool = False
    position_definition_id: int | None = None
    assignment: WorkIntakeAssignmentOut | None = None
    candidate_employee_ids: list[int] = []
    owner_user_id: int | None = None
    reason: str = ""


class ProjectManagementOut(BaseModel):
    """当前管理 actor 的快照指针。

    `stale` 表示"快照里的人已经不是当前责任持有者了"（例如 CEO 换人）——
    读面**如实报告漂移**，而不是默默改项目行：历史归 DecisionRecord（M2.4）。
    """

    employee_id: int | None = None
    person_id: int | None = None
    assigned_at: datetime | None = None
    position_code: str | None = None
    position_definition_id: int | None = None
    current_responsible_employee_id: int | None = None
    stale: bool = False


class ProjectExecutionOut(BaseModel):
    entered: bool
    task_count: int = 0
    task_status_counts: dict[str, int] = {}
    phase_count: int = 0
    artifact_count: int = 0
    #: 本项目的规划 fixture（`none` = 由 Manager Agent / Human 负责规划）
    planning_fixture: str | None = None


class ProjectSpecOut(BaseModel):
    project_id: int
    spec_version: int
    spec: ProjectSpecFieldsOut
    completeness: SpecCompletenessOut
    work_mode: str | None = None
    planning_fixture: str | None = None
    work_intake: WorkIntakeOut
    management: ProjectManagementOut
    execution: ProjectExecutionOut
    #: 契约问题 → 响应里回答它的字段路径（机器可核对，见 contracts.PROJECT_SPEC_QUESTIONS）
    questions: dict[str, str] = {}
