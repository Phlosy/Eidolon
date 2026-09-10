"""PersonCore 仓储（docs/person-core-migration.md §3 D4.1）。

**employee_id ↔ person_id 的换算只准在这个文件里**：各域 repo 切读时调用
`resolve_person_id`，不各自写 join——兼容期 / 切读期 / 切完后的口径差异
被封印在这一处。

person 的创建收敛在服务层的招聘/种子入口（services/lifecycle.py:onboard、
services/seed.py），这里只提供 `create_person` 原语；person 本身没有业务行为，
没有 PersonService。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.person import Person

logger = logging.getLogger(__name__)


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


def resolve_person_ids(db: Session, employee_ids: list[int]) -> dict[int, int]:
    """批量版 resolve_person_id（名册这类 N+1 敏感路径用）。

    解析不到的 employee_id 不进返回值，并记一次 warning（方案 §5 风险对策）。
    """
    if not employee_ids:
        return {}
    rows = db.execute(
        select(Employee.id, Employee.person_id).where(Employee.id.in_(employee_ids))
    ).all()
    resolved = {emp_id: person_id for emp_id, person_id in rows if person_id is not None}
    missing = [emp_id for emp_id in employee_ids if emp_id not in resolved]
    if missing:
        logger.warning("person_id 批量解析遗漏（employee_ids=%s），按旧口径回落", missing)
    return resolved


def write_person_id(db: Session, employee_id: int) -> int | None:
    """双写期的写入侧解析：解析不到时返回 None + warning —— person_id 留空，
    绝不编造或静默兜底（方案 §5）。各域写入点统一从这里拿 person_id。"""
    person_id = resolve_person_id(db, employee_id)
    if person_id is None:
        logger.warning(
            "双写 person_id 解析失败（employee_id=%s），该行 person_id 留空", employee_id
        )
    return person_id


def read_criterion(db: Session, employee_id: int, person_column, employee_column):
    """切读期的读口径选择器：能解析出 person_id 就按 person 过滤（新口径）；
    解析不到（legacy 行/悬空）回落 employee_id 旧口径 + warning（方案 §5）。

    employee_id ↔ person_id 的换算只准发生在本文件 —— 各域 repo 切读时调用这里，
    不各自写 join（D4.1）。
    """
    person_id = resolve_person_id(db, employee_id)
    if person_id is not None:
        return person_column == person_id
    logger.warning(
        "person_id 解析失败（employee_id=%s），读口径回落 employee_id 旧口径", employee_id
    )
    return employee_column == employee_id


def matches_owner(
    db: Session,
    employee_id: int,
    person_value: int | None,
    employee_value: int | None,
) -> bool:
    """内存态归属判定（read_criterion 的 ORM 对象版）：person 口径优先 ——
    解析得到 person_id 且行上 person 镜像列非空时按 person 比较；否则回落
    employee 镜像列。用于权限检查这类拿到整行后的判定（drive 写权限、provider
    属主可见性），行为与切读前的 employee 口径完全一致。"""
    person_id = resolve_person_id(db, employee_id)
    if person_id is not None and person_value is not None:
        return person_value == person_id
    return employee_value is not None and employee_value == employee_id
