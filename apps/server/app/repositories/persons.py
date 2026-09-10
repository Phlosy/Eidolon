"""PersonCore 仓储（docs/person-core-migration.md §3 D4.1）。

**employee_id ↔ person_id 的换算只准在这个文件里**：各域 repo 切读时调用
`resolve_person_id`，不各自写 join——兼容期 / 切读期 / 切完后的口径差异
被封印在这一处。

person 的创建收敛在服务层的招聘/种子入口（services/lifecycle.py:onboard、
services/seed.py），这里只提供 `create_person` 原语；person 本身没有业务行为，
没有 PersonService。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.person import Person


def create_person(
    db: Session,
    *,
    slug: str,
    name: str,
    avatar: str | None = None,
    username: str | None = None,
) -> Person:
    person = Person(slug=slug, name=name, avatar=avatar, username=username)
    db.add(person)
    db.flush()
    return person


def get_person(db: Session, person_id: int) -> Person | None:
    return db.get(Person, person_id)


def get_person_by_slug(db: Session, slug: str) -> Person | None:
    return db.scalars(select(Person).where(Person.slug == slug)).first()


def resolve_person_id(db: Session, employee_id: int) -> int | None:
    """employee_id → person_id 的单一解析入口（D4.1）。

    person_id 为 NULL（历史回填遗漏）或 employee 不存在时返回 None，
    回退口径由调用方决定，不在此处静默兜底。
    """
    return db.scalar(select(Employee.person_id).where(Employee.id == employee_id))
