"""Person Read Model（T2.1）—— 培养 / 市场 / 员工三处共用的统一人员投影。

**不做第二套聚合**：人格与能力直接复用既有出口
（`services/traits.person_traits_out`、`services/competency.person_capabilities_out`），
履历用 `repositories/cultivation.list_education_events`，知识只给统计摘要
（`repositories/knowledge.knowledge_summary_by_person`）。本模块只负责**组装**。

语义铁律（设计 §9 / 概念架构 §4）：
- 无证据 → `null`（unrated），**禁止 0**；
- `score` 与 `confidence` 并列，永不混算；
- trait 不参与任何能力换算；
- 知识只给 `topic/scope/count`，**不给正文**（同一形状将被 T2.3 市场投影复用）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.person import Person
from app.repositories import cultivation as cultivation_repo
from app.repositories import knowledge as knowledge_repo
from app.services import competency as competency_service
from app.services import traits as traits_service

#: 聚合端点的默认区块（小而稳定）；timeline / evidence 需显式 include（可能很大）。
DEFAULT_INCLUDES: frozenset[str] = frozenset()
OPTIONAL_INCLUDES: frozenset[str] = frozenset({"timeline", "evidence"})


def identity_out(db: Session, person: Person) -> dict:
    """身份投影：Person 本体 + （可选）培养档案字段。

    没有 CharacterProfile 的 person（纯入职员工）是合法状态：身份字段如实给 null。
    """
    profile = cultivation_repo.get_profile_by_person(db, person.id)
    return {
        "person_id": person.id,
        "name": person.name,
        "slug": person.slug,
        "avatar": person.avatar or "",
        "identity_id": profile.identity_id if profile else None,
        "origin": profile.origin if profile else None,
        "cultivation_state": profile.lifecycle if profile else None,
        "owner_company_id": profile.owner_company_id if profile else None,
        "created_at": person.created_at,
    }


def timeline_out(db: Session, person_id: int, *, limit: int, offset: int = 0) -> list[dict]:
    """履历事件（倒序 = 最新在前，与培养 UI 的时间线一致）。"""
    from app.schemas.cultivation import EducationEventOut

    events = cultivation_repo.list_education_events(
        db, person_id, newest_first=True, limit=limit, offset=offset
    )
    return [EducationEventOut.model_validate(event).model_dump() for event in events]


def evidence_out(
    db: Session,
    person_id: int,
    *,
    limit: int,
    offset: int = 0,
    source_type: str | None = None,
    competency: str | None = None,
) -> list[dict]:
    """证据（倒序）。payload 与员工证据读面同一份构造（`competency_service.evidence_payload`），
    只是把属主从 employee_id 换成 person_id。"""
    rows = competency_service.person_evidence_rows(
        db,
        person_id,
        limit=limit,
        offset=offset,
        source_type=source_type,
        competency=competency,
    )
    return [
        {"person_id": row.person_id, **item}
        for row, item in zip(rows, competency_service.evidence_payload(db, rows), strict=True)
    ]


def person_profile(
    db: Session,
    person: Person,
    *,
    includes: frozenset[str] = DEFAULT_INCLUDES,
    timeline_limit: int = 50,
    evidence_limit: int = 20,
) -> dict:
    """统一人员投影：identity / traits / competencies / knowledge_summary
    （+ 可选 timeline / evidence）。"""
    unknown = includes - OPTIONAL_INCLUDES
    if unknown:
        raise ValueError(f"unknown include: {sorted(unknown)}")
    profile = {
        "identity": identity_out(db, person),
        "traits": traits_service.person_traits_out(db, person.id),
        "competencies": competency_service.person_capabilities_out(db, person.id),
        "knowledge_summary": knowledge_repo.knowledge_summary_by_person(db, person.id),
        "timeline": None,
        "evidence": None,
    }
    if "timeline" in includes and timeline_limit > 0:
        profile["timeline"] = timeline_out(db, person.id, limit=timeline_limit)
    if "evidence" in includes and evidence_limit > 0:
        profile["evidence"] = evidence_out(db, person.id, limit=evidence_limit)
    return profile
