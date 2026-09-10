"""测试工厂（docs/person-core-migration.md §3 D6）。

兼容期 Employee 必须与 Person 成双出现（「先建 person 再建 employee」双写），
所以新测试不要裸构造 `Employee(...)` —— 用 `make_employee`，它内部自动建 person
并回填 person_id。工厂只 flush 不 commit，事务边界归调用方。
"""

from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.person import Person
from app.repositories import persons as person_repo


def make_person(
    db: Session,
    *,
    slug: str,
    name: str | None = None,
    avatar: str | None = "",
    username: str | None = None,
) -> Person:
    return person_repo.create_person(
        db,
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        avatar=avatar,
        username=username,
    )


def make_employee(
    db: Session,
    *,
    company_id: int,
    slug: str,
    name: str | None = None,
    person: Person | None = None,
    **fields,
) -> Employee:
    """建 employee 并自动配对 person（可传既有 person 复用），person_id 自动回填。"""
    if person is None:
        person = make_person(db, slug=slug, name=name)
    fields.setdefault("workspace_path", f"/tmp/{slug}-ws")
    fields.setdefault("memory_namespace", f"mem-{slug}")
    employee = Employee(
        company_id=company_id,
        person_id=person.id,
        name=name or person.name,
        slug=slug,
        **fields,
    )
    db.add(employee)
    db.flush()
    return employee
