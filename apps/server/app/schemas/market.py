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


class MarketListingPageOut(BaseModel):
    items: list[MarketListingOut]
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
