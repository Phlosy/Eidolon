"""Cultivation service（T1.0，docs/cultivation-system-design.md §2 D2/§3）。

T1.0 只有角色 CRUD：建角色（trained/blank；可选 template 顺带开 program）、
列表（本公司 owner）、详情（person + profile + program + 事件流）。
发行方生成器（issued）与上市/雇佣流转（listed/hired）属 T2，不在这里。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.cultivation import CharacterProfile, TrainingProgram
from app.models.enums import TalentOrigin
from app.models.person import Person
from app.repositories import cultivation as cultivation_repo
from app.repositories import persons as person_repo
from app.talent.cultivation import engine as engine_module

#: T1.0 只开放玩家自训与空白养成；issued 是 T2.4 发行方生成器的事（\u89c1 T2 \u8bbe\u8ba1 §7 D11）。
_CREATABLE_ORIGINS = {TalentOrigin.trained.value, TalentOrigin.blank.value}
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
            detail=f"origin must be one of {sorted(_CREATABLE_ORIGINS)} (issued 属 T2.4 发行方)",
        )
    if template is not None and template not in _TEMPLATES:
        raise HTTPException(
            status_code=422, detail=f"unknown template: {template}（自由养成请留空）"
        )
    person, profile = cultivation_repo.create_character(
        db, name=name, origin=origin, owner_company_id=owner_company_id
    )
    rng_seed = None
    if template is not None:
        program = cultivation_repo.create_program(db, person_id=person.id, template=template)
        rng_seed = program.rng_seed
    # T1.2 人格成型基线：模板倾向 + 噪声（blank = 中性 + 噪声）；幂等
    engine_module.initialize_character_brain(
        db, person.id, template_id=template, seed=rng_seed or profile.identity_id
    )
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


# ---- T1.1：培养推进 / 自由养成（引擎在 app/talent/cultivation/engine.py）----


def _owned_profile(db: Session, profile_id: int, owner_company_id: int) -> CharacterProfile:
    profile = cultivation_repo.get_profile(db, profile_id)
    if profile is None or profile.owner_company_id != owner_company_id:
        raise HTTPException(status_code=404, detail="character not found")
    return profile


def advance_program(db: Session, program_id: int, owner_company_id: int):
    """推进培养实例一个阶段（本公司持有校验 → 引擎）。"""
    program = db.get(TrainingProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="program not found")
    profile = cultivation_repo.get_profile_by_person(db, program.person_id)
    if profile is None or profile.owner_company_id != owner_company_id:
        raise HTTPException(status_code=404, detail="program not found")
    try:
        return engine_module.advance_program(db, program_id)
    except engine_module.CultivationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def run_free_session(
    db: Session,
    profile_id: int,
    owner_company_id: int,
    *,
    topic: str,
    mode: str,
    kind: str,
    signal: int,
):
    """自由养成会话（blank/无进行中模板实例的角色）。"""
    profile = _owned_profile(db, profile_id, owner_company_id)
    try:
        return engine_module.run_free_session(
            db, profile.person_id, topic=topic, mode=mode, kind=kind, signal=signal
        )
    except engine_module.CultivationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
