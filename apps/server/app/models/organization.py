"""Organization domain: Company / Department / Employee. See docs/architecture.md §3.2."""

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import EmployeeRole, EmployeeStatus, LifecycleStatus, RuntimeType
from app.models.person import Person


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(2000), default="")
    industry: Mapped[str] = mapped_column(String(200), default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(30), default="FOUNDING")

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
    __table_args__ = (
        # R1.0（docs/person-core-migration.md D2/D3）：person_id 裸 nullable、刻意不加 FK
        # （项目不开 PRAGMA foreign_keys，给历史表加 FK 会逼 alembic 用 batch 重建表）。
        # 兼容期 Employee 1-1 代理 Person，靠这个部分唯一索引保证一 person 至多一 employee
        # —— 它必须写进模型，否则 `alembic check` 会把它当成库里多出来的东西要求删除。
        Index(
            "uq_employees_person_id",
            "person_id",
            unique=True,
            sqlite_where=text("person_id IS NOT NULL"),
            postgresql_where=text("person_id IS NOT NULL"),
        ),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    role: Mapped[str] = mapped_column(String(50), default=EmployeeRole.engineer.value)
    title: Mapped[str] = mapped_column(String(200), default="")
    avatar: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(50), default=EmployeeStatus.idle.value)
    # v0.4: lifecycle state (docs/design-v0.4-lifecycle.md §1) + NamingPolicy username
    lifecycle_status: Mapped[str] = mapped_column(String(50), default=LifecycleStatus.active.value)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    runtime_type: Mapped[str] = mapped_column(String(50), default=RuntimeType.mock.value)
    runtime_config: Mapped[dict] = mapped_column(JSON, default=dict)
    workspace_path: Mapped[str] = mapped_column(String(500), unique=True)
    memory_namespace: Mapped[str] = mapped_column(String(200), unique=True)
    current_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", use_alter=True, name="fk_employees_current_task"), nullable=True
    )

    company: Mapped[Company] = relationship(back_populates="employees")
    department: Mapped[Department | None] = relationship(back_populates="employees")
    # PersonCore 兼容层：person 是「人」（身份/命名权威），本行只剩公司成员身份。
    # D3 刻意不加 FK ⇒ join 条件必须显式写出（primaryjoin + foreign_keys）。
    person: Mapped[Person | None] = relationship(
        back_populates="employee",
        primaryjoin="Employee.person_id == Person.id",
        foreign_keys="Employee.person_id",
    )
