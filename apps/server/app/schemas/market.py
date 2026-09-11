"""T2.3 市场 API schema —— **公开投影**（设计 §6）。

字段集即公开契约：schema 里没有的字段就是不对外暴露的（credential/runtime/provider/
memory/messages/drive/知识正文/审计 hash 等一律不在）。`owner_company_id` 与内部
`person_id` 同样不出现 —— 市场侧用 `listing_id` 与 `identity_id` 作为锚点。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.competency import EmployeeCapabilitiesOut
from app.schemas.person import PersonKnowledgeSummaryOut


class MarketListingOut(BaseModel):
    """索引级挂牌投影（列表与详情头部共用）。"""

    listing_id: int
    identity_id: str
    name: str
    avatar: str = ""
    origin: str
    cultivation_state: str
    status: str
    quality_tier: str | None = None
    listed_at: datetime
    closed_at: datetime | None = None
    listed_by: str


class MarketListingItemOut(MarketListingOut):
    """列表项：在挂牌投影上追加 Fit 摘要（T2.5；仅请求带 position 时非空）。

    与挂牌响应（`MarketListingOut`）分开：POST 的返回形状是 T2.3 冻结的公开契约，
    不因 T2.5 的新字段而变化。
    """

    fit: MarketFitSummaryOut | None = None


class MarketListingPageOut(BaseModel):
    items: list[MarketListingItemOut]
    total: int
    limit: int
    offset: int


class MarketListingCreateIn(BaseModel):
    person_id: int
    #: 发行方档位（T2.4 起使用）；玩家挂牌留空
    quality_tier: str | None = None


class MarketTimelineEventOut(BaseModel):
    """履历事件公开投影：outcome 只含公开键（无 session_ids 等内部引用）。"""

    id: int
    kind: str
    topic: str
    outcome: dict
    occurred_at: datetime


class MarketEvidenceOut(BaseModel):
    """证据公开投影：保留 source_ref/assessment_run_id 以支持下钻追溯（验收 A）。"""

    id: int
    competency_code: str
    competency_name: str
    source_kind: str
    source_ref: str
    assessment_run_id: int | None = None
    signal: int | None = None
    quality: float | None = None
    occurred_at: datetime


class MarketFitEvaluationOut(BaseModel):
    """Fit 逐项评估的**公开投影**（无 requirement_id / competency_definition_id / inputs_hash）。"""

    code: str
    name: str
    domain_code: str
    kind: str
    requirement_type: str
    critical: bool
    minimum_score: int | None = None
    target_score: int | None = None
    minimum_confidence: float | None = None
    #: 候选人侧（null = 未评估，**不是 0**）
    candidate_score: int | None = None
    candidate_confidence: float | None = None
    evaluation_status: str
    reason_code: str
    gap_type: str | None = None
    is_unknown: bool
    is_strength: bool
    is_development_opportunity: bool
    margin_to_minimum: int | None = None
    margin_to_target: int | None = None


class MarketFitSummaryOut(BaseModel):
    """列表页的 Fit 摘要（详情页用 MarketFitOut）。"""

    position_definition_id: int
    fit_status: str
    qualification_status: str
    known_fit_score: float | None = None
    fit_confidence: float | None = None
    requirement_coverage: float
    known_count: int
    total_count: int


class MarketFitOut(BaseModel):
    """列表详情页的市场 Fit（public 投影：score/confidence/coverage/missing，无 inputs_hash）。"""

    listing_id: int
    position_definition_id: int
    position_code: str
    configured: bool
    profile_version_id: int | None = None
    profile_version: int | None = None
    fit_status: str
    qualification_status: str
    known_fit_score: float | None = None
    overall_fit_score: float | None = None
    fit_confidence: float | None = None
    requirement_coverage: float
    required_coverage: float
    preferred_coverage: float
    known_count: int
    total_count: int
    general_fit: float | None = None
    professional_fit: float | None = None
    strengths: list[MarketFitEvaluationOut]
    gaps: list[MarketFitEvaluationOut]
    uncertainties: list[MarketFitEvaluationOut]
    development_opportunities: list[MarketFitEvaluationOut]
    requirement_evaluations: list[MarketFitEvaluationOut]
    engine_version: str
    policy_version: str
    serializer_version: str
    calculated_at: datetime | None = None


class MarketListingSummaryOut(BaseModel):
    listing_id: int
    status: str
    quality_tier: str | None = None
    listed_at: datetime
    listed_by: str


class MarketCandidateIdentityOut(BaseModel):
    identity_id: str | None
    name: str
    avatar: str = ""
    origin: str | None = None
    cultivation_state: str | None = None


class MarketCandidateOut(BaseModel):
    """候选人公开档案：履历即证据链（score/confidence 并列，未评估如实 null）。"""

    listing: MarketListingSummaryOut
    identity: MarketCandidateIdentityOut
    traits: list[dict]
    competencies: EmployeeCapabilitiesOut
    knowledge_summary: PersonKnowledgeSummaryOut
    timeline: list[MarketTimelineEventOut]
    evidence: list[MarketEvidenceOut]
    market_state: str
