"""PersonCore 聚合根（docs/person-core-migration.md §3 D1/D4.1）。

「人」先于「任职」存在：人格、知识、技能、记忆都挂在 person 上（各域自己的表 +
person_id 列），persons 表本身只承载身份与命名，保持最小——扩展走新表加列，
不放 JSON 万能字段。

兼容期（R1.0）口径：persons.slug 是唯一权威，employees.slug 是同源镜像列；
employee_id ↔ person_id 的换算只准走 app/repositories/persons.py（D4.1）。
"""

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.organization import Employee


class Person(TimestampMixin, Base):
    __tablename__ = "persons"

    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 兼容期 1-1：部分唯一索引 uq_employees_person_id（models/organization.py）保证
    # 一个 person 至多挂一个 employee；拆分完成后候选人 = 此处为 None 的 person。
    # D3 刻意不加 FK ⇒ join 条件必须显式写出（primaryjoin + foreign_keys）。
    employee: Mapped["Employee | None"] = relationship(
        back_populates="person",
        primaryjoin="Employee.person_id == Person.id",
        foreign_keys="Employee.person_id",
        uselist=False,
    )
