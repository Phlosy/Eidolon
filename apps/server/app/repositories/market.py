"""Market repository（T2.3）—— 挂牌/参与者的持久化原语。

**幂等纪律**（概念架构 §4 规则 7）：重复挂牌与重复取参与者都靠唯一索引 +
`ON CONFLICT DO NOTHING` + 回查赢家，不靠"先查再插"（TOCTOU 教训，drive 先例）。

**关闭用条件更新**（plan §9）：`UPDATE … WHERE id = ? AND status = 'active'`，
rowcount 判定 —— 并发下只有一个调用者能关成功（T2.6 招募靠它防双招）。
"""

from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.cultivation import CharacterProfile
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.models.market import MarketListing, MarketParticipant
from app.models.person import Person


def ensure_participant(
    db: Session,
    *,
    kind: str,
    company_id: int | None,
    display_name: str = "",
    profile_json: dict | None = None,
) -> MarketParticipant:
    """幂等取用参与者（player_company 按 (kind, company_id) 唯一）。"""
    existing = get_participant_by_owner(db, kind=kind, company_id=company_id)
    if existing is not None:
        return existing

    values = {
        "kind": kind,
        "company_id": company_id,
        "display_name": display_name,
        "profile_json": profile_json or {},
        "active": True,
    }
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        db.execute(sqlite_insert(MarketParticipant).values(**values).on_conflict_do_nothing())
    else:  # pragma: no cover - 本仓库部署为 SQLite；保留可移植分支
        db.execute(
            MarketParticipant.__table__.insert()
            .values(**values)
            .prefix_with("ON CONFLICT DO NOTHING")
        )
    db.flush()
    participant = get_participant_by_owner(db, kind=kind, company_id=company_id)
    assert participant is not None  # 冲突后必然能回查到赢家
    return participant


def get_participant_by_owner(
    db: Session, *, kind: str, company_id: int | None
) -> MarketParticipant | None:
    stmt = select(MarketParticipant).where(MarketParticipant.kind == kind)
    if company_id is None:
        stmt = stmt.where(MarketParticipant.company_id.is_(None))
    else:
        stmt = stmt.where(MarketParticipant.company_id == company_id)
    return db.scalars(stmt.order_by(MarketParticipant.id)).first()


def get_participant(db: Session, participant_id: int) -> MarketParticipant | None:
    return db.get(MarketParticipant, participant_id)


def get_listing(db: Session, listing_id: int) -> MarketListing | None:
    return db.get(MarketListing, listing_id)


def get_active_listing_for_person(db: Session, person_id: int) -> MarketListing | None:
    return db.scalars(
        select(MarketListing).where(
            MarketListing.person_id == person_id,
            MarketListing.status == MarketListingStatus.active.value,
        )
    ).first()


def create_active_listing(
    db: Session,
    *,
    person_id: int,
    listed_by_participant_id: int,
    quality_tier: str | None = None,
    metadata_json: dict | None = None,
) -> tuple[MarketListing, bool]:
    """幂等挂牌 → (挂牌行, 是否本次新建)。

    已存在 active 挂牌 → 直接返回它（`created=False`）；并发首次挂牌由部分唯一索引
    收敛到同一行，两个调用者看到的都是赢家行（`created` 在极窄并发窗口可能都报 True，
    无害：HTTP 201/200 的差别，不产生第二行）。
    """
    existing = get_active_listing_for_person(db, person_id)
    if existing is not None:
        return existing, False

    values = {
        "person_id": person_id,
        "status": MarketListingStatus.active.value,
        "quality_tier": quality_tier,
        "listed_by_participant_id": listed_by_participant_id,
        "listed_at": utcnow(),
        "close_reason": "",
        "metadata_json": metadata_json or {},
    }
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        db.execute(sqlite_insert(MarketListing).values(**values).on_conflict_do_nothing())
    else:  # pragma: no cover - 本仓库部署为 SQLite；保留可移植分支
        db.execute(
            MarketListing.__table__.insert().values(**values).prefix_with("ON CONFLICT DO NOTHING")
        )
    db.flush()
    listing = get_active_listing_for_person(db, person_id)
    assert listing is not None  # 冲突后必然能回查到赢家
    return listing, True


def close_active_listing(
    db: Session,
    listing_id: int,
    *,
    reason: str,
    recruited_company_id: int | None = None,
    recruited_employee_id: int | None = None,
) -> bool:
    """条件关闭 active 挂牌：rowcount=1 才算真的关掉了（并发/重复调用 → False）。

    T2.6 招募在同一事务里调用它并把招募方回填到行上（listing 自身即"何时/被谁/为何
    结束"的事实来源）。
    """
    result = db.execute(
        update(MarketListing)
        .where(
            MarketListing.id == listing_id,
            MarketListing.status == MarketListingStatus.active.value,
        )
        .values(
            status=MarketListingStatus.closed.value,
            closed_at=utcnow(),
            close_reason=reason,
            recruited_company_id=recruited_company_id,
            recruited_employee_id=recruited_employee_id,
        )
    )
    db.flush()
    return result.rowcount == 1


def listing_rows(
    db: Session,
    *,
    listing_id: int | None = None,
    person_id: int | None = None,
    active_only: bool = True,
    text: str | None = None,
    origin: str | None = None,
    quality_tier: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[tuple[MarketListing, Person, CharacterProfile | None]]:
    """挂牌 + 人员（+ 角色档案）联合读面：搜索/详情共用一条 SQL（无 N+1）。"""
    stmt = (
        select(MarketListing, Person, CharacterProfile)
        .join(Person, Person.id == MarketListing.person_id)
        .outerjoin(CharacterProfile, CharacterProfile.person_id == MarketListing.person_id)
    )
    if listing_id is not None:
        stmt = stmt.where(MarketListing.id == listing_id)
    if person_id is not None:
        stmt = stmt.where(MarketListing.person_id == person_id)
    if active_only:
        stmt = stmt.where(MarketListing.status == MarketListingStatus.active.value)
    if quality_tier:
        stmt = stmt.where(MarketListing.quality_tier == quality_tier)
    if origin:
        stmt = stmt.where(CharacterProfile.origin == origin)
    if text:
        pattern = f"%{text.strip()}%"
        stmt = stmt.where(
            or_(Person.name.ilike(pattern), CharacterProfile.identity_id.ilike(pattern))
        )
    stmt = stmt.order_by(MarketListing.listed_at.desc(), MarketListing.id.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return [tuple(row) for row in db.execute(stmt).all()]


def count_listing_rows(
    db: Session,
    *,
    active_only: bool = True,
    text: str | None = None,
    origin: str | None = None,
    quality_tier: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(MarketListing)
        .join(Person, Person.id == MarketListing.person_id)
        .outerjoin(CharacterProfile, CharacterProfile.person_id == MarketListing.person_id)
    )
    if active_only:
        stmt = stmt.where(MarketListing.status == MarketListingStatus.active.value)
    if quality_tier:
        stmt = stmt.where(MarketListing.quality_tier == quality_tier)
    if origin:
        stmt = stmt.where(CharacterProfile.origin == origin)
    if text:
        pattern = f"%{text.strip()}%"
        stmt = stmt.where(
            or_(Person.name.ilike(pattern), CharacterProfile.identity_id.ilike(pattern))
        )
    return int(db.scalar(stmt) or 0)


def participant_display_names(db: Session, participant_ids: list[int]) -> dict[int, str]:
    if not participant_ids:
        return {}
    rows = db.scalars(
        select(MarketParticipant).where(MarketParticipant.id.in_(set(participant_ids)))
    )
    return {row.id: row.display_name for row in rows}


def is_player_participant_of(db: Session, participant_id: int, company_id: int | None) -> bool:
    """该参与者是否就是该公司（挂牌/下架的公司边界判定）。"""
    participant = get_participant(db, participant_id)
    return (
        participant is not None
        and participant.kind == MarketParticipantKind.player_company.value
        and company_id is not None
        and participant.company_id == company_id
    )
