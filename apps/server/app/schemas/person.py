"""T2.1 Person 读面 schema。

形状契约见 docs/t2-talent-market-design.md §9：identity / traits / competency summary /
knowledge summary（+ 可选 timeline / evidence）。未评估一律 `null`，**永不 0**。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.competency import EmployeeCapabilitiesOut
from app.schemas.cultivation import EducationEventOut


class PersonIdentityOut(BaseModel):
    """Person 身份投影。

    `identity_id` / `origin` / `cultivation_state` / `owner_company_id` 可为 null：
    没有 CharacterProfile 的 person（纯入职员工）是合法状态。
    """

    person_id: int
    name: str
    slug: str
    avatar: str = ""
    identity_id: str | None = None
    origin: str | None = None
    cultivation_state: str | None = None
    owner_company_id: int | None = None
    created_at: datetime


class PersonKnowledgeTopicOut(BaseModel):
    topic: str
    count: int


class PersonKnowledgeSummaryOut(BaseModel):
    """知识摘要：只有统计与主题，**没有正文**（同一形状供 T2.3 市场投影复用）。"""

    total: int
    by_scope: dict[str, int]
    top_topics: list[PersonKnowledgeTopicOut]


class PersonEvidenceOut(BaseModel):
    """证据（person 口径）：字段与员工读面同构，属主改为 person_id。"""

    id: int
    person_id: int | None
    competency_definition_id: int
    competency_code: str
    competency_name: str
    source_kind: str
    source_id: int | None
    source_ref: str
    assessment_run_id: int | None
    signal: int | None
    quality: float | None
    occurred_at: datetime


class PersonProfileOut(BaseModel):
    """统一人员投影（Person Read Model）。

    identity / traits / competencies / knowledge_summary 恒在；
    timeline / evidence 需 `include=timeline,evidence` 显式请求（可能很大）——
    未请求时为 null，与"请求了但为空"（[]）语义区分。
    """

    identity: PersonIdentityOut
    traits: list[dict]
    competencies: EmployeeCapabilitiesOut
    knowledge_summary: PersonKnowledgeSummaryOut
    timeline: list[EducationEventOut] | None = None
    evidence: list[PersonEvidenceOut] | None = None
