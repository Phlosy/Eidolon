"""Cultivation repositories（T1.0，docs/cultivation-system-design.md §1/§2）。

角色 = Person + 扩展表（D4.1）：person 的创建用 person_repo.create_person 原语，
本模块只管培养域三张表（character_profiles / training_programs / education_events）。

identity_id 生成规则：`CH-` + 12 位 Crockford base32（去掉 I/L/O/U 防肉眼混淆），
由 uuid4 的 60 bit 编码而来；DB 唯一约束兜底，撞了重试。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lifecycle.naming import naming
from app.models.cultivation import CharacterProfile, EducationEvent, TrainingProgram
from app.models.person import Person
from app.repositories import persons as person_repo

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: person-only 行（employee_id IS NULL）必须能回挂到角色档案 —— 覆盖 R1.1-R1.3
#: 切读过、且 v27 放开了 employee_id 的所有表（表名, 属主列）。
PERSON_ONLY_TABLES: tuple[tuple[str, str], ...] = (
    ("employee_brains", "employee_id"),
    ("memory_entries", "employee_id"),
    ("skills", "employee_id"),
    ("learning_records", "employee_id"),
    ("learning_sessions", "employee_id"),
    ("skill_usages", "employee_id"),
    ("learning_priorities", "employee_id"),
    ("employee_competencies", "employee_id"),
    ("competency_evidence", "employee_id"),
    ("assessment_runs", "employee_id"),
    ("runtime_instances", "employee_id"),
)


def _new_identity_id(db: Session) -> str:
    while True:
        value = uuid.uuid4().int & ((1 << 60) - 1)
        identity = "CH-" + "".join(
            _CROCKFORD[(value >> (5 * shift)) & 31] for shift in reversed(range(12))
        )
        if get_profile_by_identity(db, identity) is None:
            return identity


def _unique_person_slug(db: Session, name: str) -> str:
    base = naming.username(name) or "character"
    slug, n = base, 2
    while person_repo.get_person_by_slug(db, slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_character(
    db: Session,
    *,
    name: str,
    origin: str,
    owner_company_id: int | None,
) -> tuple[Person, CharacterProfile]:
    """建角色 = 建 person（聚合根）+ 建 profile（培养/市场域），一体完成。"""
    person = person_repo.create_person(db, slug=_unique_person_slug(db, name), name=name)
    profile = CharacterProfile(
        person_id=person.id,
        identity_id=_new_identity_id(db),
        origin=origin,
        owner_company_id=owner_company_id,
        lifecycle="cultivating",
    )
    db.add(profile)
    db.flush()
    return person, profile


def get_profile(db: Session, profile_id: int) -> CharacterProfile | None:
    return db.get(CharacterProfile, profile_id)


def get_profile_by_person(db: Session, person_id: int) -> CharacterProfile | None:
    return db.scalars(
        select(CharacterProfile).where(CharacterProfile.person_id == person_id)
    ).first()


def get_profile_by_identity(db: Session, identity_id: str) -> CharacterProfile | None:
    return db.scalars(
        select(CharacterProfile).where(CharacterProfile.identity_id == identity_id)
    ).first()


def list_characters(
    db: Session, owner_company_id: int, lifecycle: str | None = None
) -> list[CharacterProfile]:
    stmt = (
        select(CharacterProfile)
        .where(CharacterProfile.owner_company_id == owner_company_id)
        .order_by(CharacterProfile.id)
    )
    if lifecycle is not None:
        stmt = stmt.where(CharacterProfile.lifecycle == lifecycle)
    return list(db.scalars(stmt))


def create_program(
    db: Session,
    *,
    person_id: int,
    template: str = "",
    rng_seed: str | None = None,
) -> TrainingProgram:
    """开一次培养实例。rng_seed 创建时落库（确定性来源，测试可注入固定 seed）。"""
    program = TrainingProgram(
        person_id=person_id,
        template=template,
        rng_seed=rng_seed or uuid.uuid4().hex,
    )
    db.add(program)
    db.flush()
    return program


def list_programs(db: Session, person_id: int) -> list[TrainingProgram]:
    return list(
        db.scalars(
            select(TrainingProgram)
            .where(TrainingProgram.person_id == person_id)
            .order_by(TrainingProgram.id)
        )
    )


def create_education_event(db: Session, *, person_id: int, **fields) -> EducationEvent:
    event = EducationEvent(person_id=person_id, **fields)
    db.add(event)
    db.flush()
    return event


def list_education_events(db: Session, person_id: int) -> list[EducationEvent]:
    return list(
        db.scalars(
            select(EducationEvent)
            .where(EducationEvent.person_id == person_id)
            .order_by(EducationEvent.occurred_at, EducationEvent.id)
        )
    )


def find_orphan_person_only_rows(db: Session) -> list[dict]:
    """D1 守卫校验：person-only 行（employee_id IS NULL）必须属于有
    character_profile 的 person。返回脏行清单（空 = 干净）。

    服务层/守卫测试共用这一个实现，别在别处重写这条 SQL。
    """
    from sqlalchemy import text

    orphans: list[dict] = []
    for table, column in PERSON_ONLY_TABLES:
        rows = db.execute(
            text(
                f"SELECT t.id, t.person_id FROM {table} t"
                f" LEFT JOIN character_profiles cp ON cp.person_id = t.person_id"
                f" WHERE t.{column} IS NULL AND cp.person_id IS NULL"
            )
        ).all()
        orphans.extend({"table": table, "row_id": row[0], "person_id": row[1]} for row in rows)
    return orphans
