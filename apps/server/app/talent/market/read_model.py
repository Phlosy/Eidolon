"""MarketReadService（T2.3）—— 市场**公开投影**：只暴露白名单字段（设计 §6）。

与 T2.1 人员读面（app/talent/person/read_model.py）的关系：
**复用其取数，不复制聚合**；本模块只做两件事：
1. 把索引级挂牌记录转成公开形状（identity 白名单：
   `identity_id/name/avatar/origin/cultivation_state`，
   **不含** `person_id` / `owner_company_id` —— 设计 §6.1 未列即不公开）；
2. 为候选人档案做**显式字段投影**（设计 §6.3）：履历 outcome 只留公开键（去掉 `session_ids`
   等内部引用），证据只留公开列（去掉 `source_id`/`competency_definition_id`）。
   保留 `assessment_run_id` 与 `source_ref` 是为了兑现验收 A 的"证据可追溯"。

绝不返回：credential/provider/runtime/memory/messages/drive/artifacts/知识正文/审计 hash
（设计 §6.2；测试以"禁止字段不出现"兜住）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.market import MarketListing
from app.repositories import market as market_repo
from app.talent.market import eligibility
from app.talent.market.contracts import MarketListingView, MarketSearchQuery
from app.talent.person import read_model as person_read_model

#: 履历事件 `outcome` 的公开键（白名单；`session_ids` 等内部引用不外泄）
PUBLIC_OUTCOME_KEYS = frozenset(
    {
        "stage_id",
        "topics",
        "signals",
        "knowledge_produced",
        "duration_weeks",
        "fortunes",
        "assessment_run_id",
        "fortune",
        "narrative",
        "signal_delta",
        "extra_topics",
        "trait_shift",
        "signal",
    }
)

#: 证据读面的公开列（`source_ref` 保留：验收 A 的下钻锚点）
PUBLIC_EVIDENCE_KEYS = (
    "id",
    "competency_code",
    "competency_name",
    "source_kind",
    "source_ref",
    "assessment_run_id",
    "signal",
    "quality",
    "occurred_at",
)


def public_identity(db: Session, person_id: int) -> dict:
    """身份白名单投影（设计 §6.1）。"""
    from app.repositories import persons as person_repo

    person = person_repo.get_person(db, person_id)
    assert person is not None, "挂牌行指向的 person 必须存在"
    identity = person_read_model.identity_out(db, person)
    return {
        "identity_id": identity["identity_id"],
        "name": identity["name"],
        "avatar": identity["avatar"],
        "origin": identity["origin"],
        "cultivation_state": identity["cultivation_state"],
    }


def listing_out(db: Session, view: MarketListingView, *, display_name: str | None = None) -> dict:
    """索引级挂牌投影（列表/详情头部共用）。"""
    return {
        "listing_id": view.listing_id,
        "identity_id": view.identity_id,
        "name": view.name,
        "avatar": "",  # 由调用方（需要时）补；列表投影不单独取 person
        "origin": view.origin,
        "cultivation_state": view.cultivation_state,
        "status": view.status.value,
        "quality_tier": view.quality_tier,
        "listed_at": view.listed_at,
        "closed_at": view.closed_at,
        "listed_by": (
            display_name if display_name is not None else str(view.listed_by_participant_id)
        ),
    }


def listing_page(db: Session, query: MarketSearchQuery) -> dict:
    """市场列表：索引级投影（不含 traits/competency/evidence —— 那些在详情）。"""
    from app.talent.market.local_adapter import LocalMarketAdapter

    adapter = LocalMarketAdapter()
    views = adapter.search_listings(db, query)
    names = market_repo.participant_display_names(
        db, [view.listed_by_participant_id for view in views]
    )
    total = market_repo.count_listing_rows(
        db, active_only=True, text=query.text, origin=query.origin, quality_tier=query.quality_tier
    )
    items = [
        {
            **listing_out(db, view, display_name=names.get(view.listed_by_participant_id)),
            "avatar": "",
        }
        for view in views
    ]
    return {"items": items, "total": total, "limit": query.limit, "offset": query.offset}


def _public_timeline(db: Session, person_id: int, *, limit: int) -> list[dict]:
    events = person_read_model.timeline_out(db, person_id, limit=limit)
    out: list[dict] = []
    for event in events:
        outcome = dict(event.get("outcome") or {})
        out.append(
            {
                "id": event["id"],
                "kind": event["kind"],
                "topic": event["topic"],
                "outcome": {k: v for k, v in outcome.items() if k in PUBLIC_OUTCOME_KEYS},
                "occurred_at": event["occurred_at"],
            }
        )
    return out


def _public_evidence(db: Session, person_id: int, *, limit: int) -> list[dict]:
    rows = person_read_model.evidence_out(db, person_id, limit=limit)
    return [{key: row.get(key) for key in PUBLIC_EVIDENCE_KEYS} for row in rows]


def candidate_profile(
    db: Session,
    listing: MarketListing,
    *,
    timeline_limit: int = 50,
    evidence_limit: int = 20,
) -> dict:
    """候选人公开档案：身份 + 挂牌 + 人格 + 能力画像 + 知识摘要 + 履历 + 证据。"""
    person_id = int(listing.person_id)
    participant_name = market_repo.participant_display_names(
        db, [int(listing.listed_by_participant_id)]
    ).get(int(listing.listed_by_participant_id))
    return {
        "listing": {
            "listing_id": int(listing.id),
            "status": listing.status,
            "quality_tier": listing.quality_tier,
            "listed_at": listing.listed_at,
            "listed_by": participant_name or str(listing.listed_by_participant_id),
        },
        "identity": public_identity(db, person_id),
        "traits": person_read_model.traits_out(db, person_id),
        "competencies": person_read_model.competencies_out(db, person_id),
        "knowledge_summary": person_read_model.knowledge_summary_out(db, person_id),
        "timeline": _public_timeline(db, person_id, limit=timeline_limit),
        "evidence": _public_evidence(db, person_id, limit=evidence_limit),
        # 三轴快照供 UI 显示"可招募/已关闭"（派生，不落库）
        "market_state": eligibility.market_state(db, person_id).value,
    }
