from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ArtifactStatus, ArtifactType, TaskStatus
from app.schemas.organization import ORMModel


class ProjectCreate(BaseModel):
    name: str
    description: str  # 原始需求 → source_order_text
    goal: str = ""


class ProjectOut(ORMModel):
    id: int
    company_id: int
    name: str
    description: str
    status: str
    goal: str
    owner_id: int | None
    source_order_text: str
    created_at: datetime
    updated_at: datetime


class MilestoneOut(ORMModel):
    id: int
    project_id: int
    name: str
    description: str
    status: str
    order: int
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
