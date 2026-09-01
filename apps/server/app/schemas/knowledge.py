from datetime import datetime

from pydantic import BaseModel

from app.models.enums import KnowledgeScope
from app.schemas.organization import ORMModel


class MemoryEntryOut(ORMModel):
    id: int
    employee_id: int
    kind: str
    content: str
    source_ref: str
    created_at: datetime
    updated_at: datetime


class KnowledgeItemOut(ORMModel):
    id: int
    scope: str
    owner_employee_id: int | None
    department_id: int | None
    title: str
    content: str
    topic: str
    status: str
    confidence: float
    sources: list
    proposed_scope: str | None
    created_at: datetime
    updated_at: datetime


class ProposalCreate(BaseModel):
    target_scope: KnowledgeScope


class ReviewCreate(BaseModel):
    approve: bool


class SkillOut(ORMModel):
    id: int
    employee_id: int
    name: str
    description: str
    version: int
    attempts: int
    success_count: int
    avg_duration_sec: float
    avg_cost: float
    validation_status: str
    created_at: datetime
    updated_at: datetime


class LearningRecordOut(ORMModel):
    id: int
    employee_id: int
    project_id: int | None
    task_id: int | None
    kind: str
    topic: str
    problem: str
    observation: str
    lesson: str
    solution: str
    confidence: float
    sources: list
    created_at: datetime
    updated_at: datetime


class LearningPriorityOut(ORMModel):
    id: int
    employee_id: int
    topic: str
    score: int
    reason: str
    created_at: datetime
    updated_at: datetime


class EventOut(ORMModel):
    id: int
    type: str
    company_id: int | None
    actor_employee_id: int | None
    project_id: int | None
    task_id: int | None
    payload: dict
    created_at: datetime
    updated_at: datetime
