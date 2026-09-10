"""/cultivation —— 角色（Character）CRUD（T1.0，docs/cultivation-system-design.md §3）。

角色 = Person + character_profiles 扩展表；只能看/建**本公司 owner** 的角色
（公司边界走 api/scope.py 的既有口径，不自造鉴权）。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.schemas.cultivation import (
    AdvanceResultOut,
    CharacterCreateIn,
    CharacterDetailOut,
    CharacterOut,
    EducationEventOut,
    FreeSessionIn,
    FreeSessionResultOut,
    ProgramOut,
)
from app.services import cultivation as cultivation_service

router = APIRouter(prefix="/cultivation", tags=["cultivation"])


def _character_out(person, profile) -> CharacterOut:
    return CharacterOut(
        id=profile.id,
        person_id=profile.person_id,
        identity_id=profile.identity_id,
        name=person.name,
        slug=person.slug,
        origin=profile.origin,
        owner_company_id=profile.owner_company_id,
        lifecycle=profile.lifecycle,
        created_at=profile.created_at,
    )


@router.post("/characters", response_model=CharacterOut, status_code=201)
def create_character(
    payload: CharacterCreateIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> CharacterOut:
    """建角色（trained/blank；带 template 则一并开培养 program）。"""
    person, profile = cultivation_service.create_character(
        db,
        name=payload.name,
        origin=payload.origin,
        template=payload.template,
        owner_company_id=company_id,
    )
    return _character_out(person, profile)


@router.get("/characters", response_model=list[CharacterOut])
def list_characters(
    lifecycle: str | None = Query(None),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[CharacterOut]:
    from app.repositories import persons as person_repo

    profiles = cultivation_service.list_characters(db, company_id, lifecycle)
    return [
        _character_out(person_repo.get_person(db, profile.person_id), profile)
        for profile in profiles
    ]


@router.get("/characters/{profile_id}", response_model=CharacterDetailOut)
def get_character(
    profile_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> CharacterDetailOut:
    person, profile, programs, events = cultivation_service.get_character_detail(
        db, profile_id, company_id
    )
    return CharacterDetailOut(
        **_character_out(person, profile).model_dump(),
        programs=[ProgramOut.model_validate(p) for p in programs],
        events=[EducationEventOut.model_validate(e) for e in events],
    )


# ---- T1.1：培养推进 / 自由养成 ----


@router.post("/programs/{program_id}/advance", response_model=AdvanceResultOut)
def advance_program(
    program_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> AdvanceResultOut:
    """推进培养实例一个阶段（采样 → 学习产出 → 教育证据 → 履历事件）。"""
    from app.repositories import cultivation as cultivation_repo

    event = cultivation_service.advance_program(db, program_id, company_id)
    program = cultivation_repo.list_programs(db, event.person_id)
    current = next(p for p in program if p.id == program_id)
    profile = cultivation_repo.get_profile_by_person(db, event.person_id)
    return AdvanceResultOut(
        program=ProgramOut.model_validate(current),
        event=EducationEventOut.model_validate(event),
        lifecycle=profile.lifecycle if profile else "cultivating",
    )


@router.post(
    "/characters/{profile_id}/sessions", response_model=FreeSessionResultOut, status_code=201
)
def create_free_session(
    profile_id: int,
    payload: FreeSessionIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> FreeSessionResultOut:
    """自由养成：无模板的角色逐次指定主题/途径/强度（同一产出路径）。"""
    event = cultivation_service.run_free_session(
        db,
        profile_id,
        company_id,
        topic=payload.topic,
        mode=payload.mode,
        kind=payload.kind,
        signal=payload.signal,
    )
    return FreeSessionResultOut(event=EducationEventOut.model_validate(event))
