"""Cultivation service（T1.0，docs/cultivation-system-design.md §2 D2/§3）。

T1.0 只有角色 CRUD：建角色（trained/blank；可选 template 顺带开 program）、
列表（本公司 owner）、详情（person + profile + program + 事件流）。
发行方生成器（issued）与上市/雇佣流转（listed/hired）属 T2，不在这里。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.cultivation import CharacterProfile
from app.models.person import Person
from app.repositories import cultivation as cultivation_repo
from app.repositories import persons as person_repo

#: T1.0 只开放玩家自训与空白养成；issued 是 T2 发行方生成器的事。
_CREATABLE_ORIGINS = {"trained", "blank"}
_TEMPLATES = {"academic", "vocational", "self_taught"}


def create_character(
    db: Session,
    *,
    name: str,
    origin: str,
    template: str | None,
    owner_company_id: int,
) -> tuple[Person, CharacterProfile]:
    if origin not in _CREATABLE_ORIGINS:
        raise HTTPException(
            status_code=422,
            detail=f"origin must be one of {sorted(_CREATABLE_ORIGINS)} (issued 属 T2)",
        )
    if template is not None and template not in _TEMPLATES:
        raise HTTPException(
            status_code=422, detail=f"unknown template: {template}（自由养成请留空）"
        )
    person, profile = cultivation_repo.create_character(
        db, name=name, origin=origin, owner_company_id=owner_company_id
    )
    if template is not None:
        cultivation_repo.create_program(db, person_id=person.id, template=template)
    db.commit()
    return person, profile


def list_characters(db: Session, owner_company_id: int, lifecycle: str | None = None):
    """本公司持有的角色（公司隔离：别家 owner 的角色结构上就进不来）。"""
    return cultivation_repo.list_characters(db, owner_company_id, lifecycle)


def get_character_detail(
    db: Session, profile_id: int, owner_company_id: int
) -> tuple[Person, CharacterProfile, list, list]:
    """详情 = person + profile + programs + 事件流。别公司的角色按 404 处理。"""
    profile = cultivation_repo.get_profile(db, profile_id)
    if profile is None or profile.owner_company_id != owner_company_id:
        raise HTTPException(status_code=404, detail="character not found")
    person = person_repo.get_person(db, profile.person_id)
    assert person is not None  # profile.person_id 由 create_character 一体落库
    programs = cultivation_repo.list_programs(db, person.id)
    events = cultivation_repo.list_education_events(db, person.id)
    return person, profile, programs, events
