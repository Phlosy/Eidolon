"""Person 读面访问策略（T2.1）。

**自有 person 判定**（`/api/v1/persons/*` 的公司作用域）：

1. 该 person 的角色档案 `character_profiles.owner_company_id == 请求公司`（培养期持有），或
2. 该 person 在某公司有 employee 行，且 `employees.company_id == 请求公司`（在职）。

两条都成立才可读；否则一律 404（**不泄露存在性** —— 与 `_employee_or_404` 同口径）。
培养期与招募后可读性都覆盖：招募后 B 公司经 employee 分支可读，原持有方 A 仍可读
（`owner_company_id` 是培养期持有语义，招募不改写历史 —— 设计 §5）。

注意：市场（跨公司公开投影）**不**走这里，走 T2.3 的 `MarketReadService`（设计 §6/D4）。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.person import Person
from app.repositories import cultivation as cultivation_repo
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo


def is_own_person(db: Session, person_id: int, company_id: int | None) -> bool:
    if company_id is None:
        return False
    profile = cultivation_repo.get_profile_by_person(db, person_id)
    if profile is not None and profile.owner_company_id == company_id:
        return True
    employee = org_repo.get_employee_by_person(db, person_id)
    return employee is not None and employee.company_id == company_id


def visible_person_or_404(db: Session, person_id: int, company_id: int | None) -> Person:
    person = person_repo.get_person(db, person_id)
    if person is None or not is_own_person(db, person.id, company_id):
        raise HTTPException(status_code=404, detail="person not found")
    return person
