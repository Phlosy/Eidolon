"""Organization domain: Company / Department / Employee. See docs/architecture.md §3.2."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import EmployeeRole, EmployeeStatus, RuntimeType


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(2000), default="")
    industry: Mapped[str] = mapped_column(String(200), default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    departments: Mapped[list["Department"]] = relationship(back_populates="company")
    employees: Mapped[list["Employee"]] = relationship(back_populates="company")


class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(2000), default="")

    company: Mapped[Company] = relationship(back_populates="departments")
    employees: Mapped[list["Employee"]] = relationship(back_populates="department")


class Employee(TimestampMixin, Base):
    __tablename__ = "employees"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    role: Mapped[str] = mapped_column(String(50), default=EmployeeRole.engineer.value)
    title: Mapped[str] = mapped_column(String(200), default="")
    avatar: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(50), default=EmployeeStatus.idle.value)
    runtime_type: Mapped[str] = mapped_column(String(50), default=RuntimeType.mock.value)
    runtime_config: Mapped[dict] = mapped_column(JSON, default=dict)
    workspace_path: Mapped[str] = mapped_column(String(500), unique=True)
    memory_namespace: Mapped[str] = mapped_column(String(200), unique=True)
    current_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", use_alter=True, name="fk_employees_current_task"), nullable=True
    )

    company: Mapped[Company] = relationship(back_populates="employees")
    department: Mapped[Department | None] = relationship(back_populates="employees")
