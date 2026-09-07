"""Assessment read schemas (P6). 读契约；写面只有「受限的 run 触发」（引擎算分，不由用户提交）。

ADR-12 纪律：派生/状态字段不落假默认 —— score/confidence/trend 用 null 表达缺省；
`evidence_count`、`status`、`trend_direction` 等由序列化出口显式计算。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CriterionCompetencyOut(BaseModel):
    competency_definition_id: int
    code: str
    name: str
    domain_code: str
    domain_name: str
    contribution_weight: float
    evidence_type: str


class CriterionOut(BaseModel):
    id: int
    code: str
    name: str
    description: str
    weight: float
    order_index: int
    evidence_kinds: list[str]
    competencies: list[CriterionCompetencyOut]


class AssessmentProfileOut(BaseModel):
    id: int
    code: str
    version: int
    name: str
    description: str
    applies_to_kind: str
    position_definition_id: int | None
    min_evidence_count: int
    half_life_days: float
    algorithm_version: str
    built_in: bool


class AssessmentProfileDetailOut(AssessmentProfileOut):
    criteria: list[CriterionOut]


class AssessmentRunSummaryOut(BaseModel):
    id: int
    employee_id: int
    profile_id: int | None
    profile_code: str | None
    profile_version: int | None
    assessment_type: str
    triggered_by: str
    status: str
    window_from: datetime | None
    window_to: datetime | None
    evidence_count: int
    inputs_hash: str
    started_at: datetime | None
    finished_at: datetime | None


class AssessmentResultOut(BaseModel):
    kind: str
    criterion_id: int | None
    criterion_code: str | None
    competency_definition_id: int | None
    competency_code: str | None
    observed_score: int | None
    confidence: float | None
    evidence_count: int
    contribution: float | None
    rationale: str


class AssessmentRunDetailOut(AssessmentRunSummaryOut):
    outputs: dict
    results: list[AssessmentResultOut]


class CompetencyExplanationOut(BaseModel):
    """GET /employees/{id}/competencies/{comp}/explanation —— 为什么是这个分。

    全部字段要么来自数据库事实（evidence/run），要么由序列化出口计算（trend_direction）。
    score/confidence 为 null = 未评估（不是 0）。
    """

    competency_definition_id: int
    code: str
    name: str
    domain_code: str
    domain_name: str
    score: int | None = None
    confidence: float | None = None
    evidence_count: int
    status: str
    trend: int | None = None
    trend_direction: str
    last_assessed_at: datetime | None = None
    assessment_history: list[dict]
    recent_evidence: list[dict]
    source_distribution: list[dict]
    recent_criterion_results: list[dict]
    relevant_skills: list[dict]
