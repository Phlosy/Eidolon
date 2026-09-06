from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

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
    # §13.1：UI 必须能区分“失败驱动”与“行为策略延伸”，否则高分延伸项会伪装成失败。
    source: str = ""
    created_at: datetime
    updated_at: datetime


class SkillUsageOut(ORMModel):
    id: int
    employee_id: int
    skill_id: int
    task_id: int | None
    work_session_id: int | None
    skill_validation_status: str
    selection_reason: str
    policy_version: str
    profile_revision: int
    success: bool
    # 人的判断；None = 尚未评价。与 success 互不推导（§10.2）。
    outcome: str | None = None
    outcome_source: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillUsageOutcomeIn(BaseModel):
    """v1 只开放人工评价（§18.2 已定）；永不接受 success / confidence。

    `extra="forbid"` 是刻意的：让调用方**明确失败**比为它静默丢弃 `success`
    更诚实 —— 静默忽略会让人以为可以写结果判定（live 验证时发现 integration 会这么猜）。
    """

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["useful", "not_useful"]


class SkillUsageBenchmarksOut(BaseModel):
    total_usages: int
    candidate_usages: int
    candidate_skills_tried: int
    rated_usages: int
    pending_ratings: int
    trial_rate: float | None = None
    conversion_rate: float | None = None
    useful_rate: float | None = None


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
