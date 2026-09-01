from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import EmployeeRole, EmployeeStatus, RuntimeType


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
