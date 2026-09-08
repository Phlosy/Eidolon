"""Learning schemas（P11）。派生/状态字段由序列化出口计算（ADR-12）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LearningPolicyOut(BaseModel):
    enabled: bool
    daily_token_budget: int
    daily_cost_budget: float
    max_session_minutes: int
    max_sessions_per_day: int
    allow_web_research: bool
    allow_practice: bool
    idle_delay_minutes: int
    cooldown_minutes: int


class EmployeeLearningPolicyOut(BaseModel):
    inherit_company_policy: bool
    enabled: bool
    personal_daily_budget_override: int | None = None
    idle_delay_override: int | None = None
    autonomous_learning_warning: bool


class CompanyLearningUsageOut(BaseModel):
    today_tokens: int
    today_cost: float
    today_sessions: int
    budget_remaining_tokens: int
    budget_remaining_cost: float


class LearningSessionOut(BaseModel):
    id: int
    employee_id: int
    company_id: int
    topic: str
    reason: str
    source_type: str
    source_id: int | None = None
    status: str
    learning_mode: str
    priority: int
    budget_tokens: int
    budget_cost: float
    budget_minutes: int
    tokens_used: int
    cost_used: float
    duration_minutes: int | None = None
    runtime_type: str
    provider_name: str
    model_name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    error: str
    summary: str
    outputs: dict


class LearningSessionStartIn(BaseModel):
    topic: str
    learning_mode: str = "web_research"
    source_type: str = "manual"
    minutes: int | None = None
    reason: str = ""


class CompanyLearningPolicyPatchIn(BaseModel):
    autonomous_learning_enabled: bool | None = None
    daily_token_budget: int | None = None
    daily_cost_budget: float | None = None
    max_session_minutes: int | None = None
    max_sessions_per_day: int | None = None
    allow_web_research: bool | None = None
    allow_practice: bool | None = None


class EmployeeLearningPolicyPatchIn(BaseModel):
    enabled: bool | None = None
    daily_token_budget: int | None = None
    idle_delay_minutes: int | None = None
