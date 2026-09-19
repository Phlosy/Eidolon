"""Bounded dashboard read model; project/task detail stays on its own endpoints."""

from pydantic import BaseModel, Field


class DashboardProjectSummary(BaseModel):
    project_id: int
    tasks_total: int
    tasks_completed: int
    blockers: int


class DashboardSummary(BaseModel):
    company_id: int
    employees_online: int
    employees_total: int
    active_projects: int
    completed_projects: int
    tasks_in_progress: int
    tasks_total: int
    tasks_completed: int
    documents_count: int
    total_attempts: int
    project_summaries: list[DashboardProjectSummary] = Field(max_length=4)
