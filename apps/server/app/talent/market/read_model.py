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

import dataclasses

from sqlalchemy.orm import Session

from app.models.market import MarketListing
from app.repositories import market as market_repo
from app.talent.fit import service as fit_service
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


def listing_page(
    db: Session,
    query: MarketSearchQuery,
    *,
    position=None,  # noqa: ANN001  （PositionDefinition；避免无谓 import 循环）
    company_id: int | None = None,
) -> dict:
    """市场列表：索引级投影（不含 traits/competency/evidence —— 那些在详情）。

    带 `position` 时（T2.5）：为每个在市候选人算 Fit 并附**摘要** + 按 Fit 排序
    （有分在前、未知在后，未知**不剔除** —— Unknown != Bad）。排序需要全量，
    因此该路径先不分页取回、排序后再切片（本地市场规模可接受，已文档化）。
    """
    from app.talent.market.local_adapter import LocalMarketAdapter

    adapter = LocalMarketAdapter()
    if position is None:
        views = adapter.search_listings(db, query)
        total = market_repo.count_listing_rows(
            db,
            active_only=True,
            text=query.text,
            origin=query.origin,
            quality_tier=query.quality_tier,
        )
    else:
        full = dataclasses.replace(query, limit=None)
        views = adapter.search_listings(db, full)
        total = len(views)

    names = market_repo.participant_display_names(
        db, [view.listed_by_participant_id for view in views]
    )
    items = [
        {
            **listing_out(db, view, display_name=names.get(view.listed_by_participant_id)),
            "avatar": "",
            "fit": None,
        }
        for view in views
    ]

    if position is not None:
        for view, item in zip(views, items, strict=True):
            result = fit_service.calculate_person_fit(
                db,
                person_id=view.person_id,
                position_definition_id=int(position.id),
                company_id=company_id,
            )
            item["fit"] = fit_summary_out(result)
        # 有分在前（score desc），未知在后；同分看 confidence，再看挂牌时间（新在前）
        ordered = sorted(
            zip(items, views, strict=True),
            key=lambda pair: (
                pair[0]["fit"]["known_fit_score"] is None,
                -(pair[0]["fit"]["known_fit_score"] or 0.0),
                -(pair[0]["fit"]["fit_confidence"] or 0.0),
                -pair[1].listed_at.timestamp(),
            ),
        )
        items = [item for item, _view in ordered]
        items = items[query.offset : query.offset + (query.limit or len(items))]

    return {"items": items, "total": total, "limit": query.limit or total, "offset": query.offset}


def fit_summary_out(result) -> dict:  # noqa: ANN001  （PositionFitResult）
    """列表页 Fit 摘要（公开投影的一部分）。"""
    return {
        "position_definition_id": result.position_definition_id,
        "fit_status": result.fit_status,
        "qualification_status": result.qualification_status,
        "known_fit_score": result.known_fit_score,
        "fit_confidence": result.fit_confidence,
        "requirement_coverage": result.requirement_coverage,
        "known_count": result.known_count,
        "total_count": result.total_count,
    }


_PUBLIC_FIT_EVALUATION_KEYS = (
    "code",
    "name",
    "domain_code",
    "kind",
    "requirement_type",
    "critical",
    "minimum_score",
    "target_score",
    "minimum_confidence",
    "evaluation_status",
    "reason_code",
    "gap_type",
    "is_unknown",
    "is_strength",
    "is_development_opportunity",
    "margin_to_minimum",
    "margin_to_target",
)


def _fit_evaluation_out(evaluation) -> dict:  # noqa: ANN001
    """逐项评估的公开投影：`employee_score` → `candidate_score`；去掉内部 id。"""
    payload = {key: getattr(evaluation, key) for key in _PUBLIC_FIT_EVALUATION_KEYS}
    payload["candidate_score"] = evaluation.employee_score
    payload["candidate_confidence"] = evaluation.employee_confidence
    return payload


def candidate_fit_out(listing_id: int, result) -> dict:  # noqa: ANN001
    """市场 Fit 详情（public）：score/confidence/coverage/missing。

    **不含** `inputs_hash` 与 owner id（设计 §6.2/§6.3 的公开投影纪律）。
    """
    from app.talent.fit.serializer import SERIALIZER_VERSION

    return {
        "listing_id": listing_id,
        "position_definition_id": result.position_definition_id,
        "position_code": result.position_code,
        "configured": result.configured,
        "profile_version_id": result.profile_version_id,
        "profile_version": result.profile_version,
        "fit_status": result.fit_status,
        "qualification_status": result.qualification_status,
        "known_fit_score": result.known_fit_score,
        "overall_fit_score": result.overall_fit_score,
        "fit_confidence": result.fit_confidence,
        "requirement_coverage": result.requirement_coverage,
        "required_coverage": result.required_coverage,
        "preferred_coverage": result.preferred_coverage,
        "known_count": result.known_count,
        "total_count": result.total_count,
        "general_fit": result.general_fit,
        "professional_fit": result.professional_fit,
        "strengths": [_fit_evaluation_out(item) for item in result.strengths],
        "gaps": [_fit_evaluation_out(item) for item in result.gaps],
        "uncertainties": [_fit_evaluation_out(item) for item in result.uncertainties],
        "development_opportunities": [
            _fit_evaluation_out(item) for item in result.development_opportunities
        ],
        "requirement_evaluations": [
            _fit_evaluation_out(item) for item in result.requirement_evaluations
        ],
        "engine_version": result.engine_version,
        "policy_version": result.policy_version,
        "serializer_version": SERIALIZER_VERSION,
        "calculated_at": result.calculated_at,
    }


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
