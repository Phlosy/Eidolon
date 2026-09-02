from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ArtifactStatus, ArtifactType, TaskStatus
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

    @property
    def is_structured(self) -> bool:
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
