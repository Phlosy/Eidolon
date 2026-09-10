"""/cultivation —— 角色（Character）CRUD（T1.0，docs/cultivation-system-design.md §3）。

角色 = Person + character_profiles 扩展表；只能看/建**本公司 owner** 的角色
（公司边界走 api/scope.py 的既有口径，不自造鉴权）。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.schemas.cultivation import (
    CharacterCreateIn,
    CharacterDetailOut,
    CharacterOut,
    EducationEventOut,
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
