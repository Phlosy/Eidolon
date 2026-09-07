"""Position Competency Profile read schemas (P7). 读契约 + 写请求。

ADR-12：派生/状态字段（coverage/integrity/requirement_count/configured）一律由
序列化出口计算，不落 schema 默认值；`configured=False` 表示"未配置"，不是空画像。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProfileRequirementOut(BaseModel):
    id: int
    competency_definition_id: int
    code: str
    name: str
    domain_id: int | None
    domain_code: str
    domain_name: str
    kind: str
    requirement_type: str  # required | preferred
    minimum_score: int | None = None
    target_score: int | None = None
    minimum_confidence: float | None = None
    critical: bool
    priority: int
    weight: float
    notes: str


class AssessmentCoverageOut(BaseModel):
    required_count: int
    covered_count: int
    uncovered_competency_ids: list[int]


class ProfileIntegrityOut(BaseModel):
    status: str
    codes: list[str]
    coverage: AssessmentCoverageOut
    read_only: bool


class ProfileAssessmentOut(BaseModel):
    id: int
    code: str
    version: int
    name: str
    algorithm_version: str


class ProfileVersionOut(BaseModel):
    id: int
    version: int
    status: str  # draft | active | retired
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    published_at: datetime | None = None
    published_note: str
    requirement_count: int


class PositionCompetencyProfileOut(BaseModel):
    position_definition_id: int
    position_code: str
    configured: bool
    profile_version: int | None = None
    profile_status: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    assessment_profile: ProfileAssessmentOut | None = None
    general: list[ProfileRequirementOut]
    professional: list[ProfileRequirementOut]
    integrity: ProfileIntegrityOut
    versions: list[ProfileVersionOut]


class PositionProfileSummaryOut(BaseModel):
    position_definition_id: int
    code: str
    name: str
    active_version: int | None = None
    profile_status: str | None = None
    requirement_count: int
    assessment_profile_code: str | None = None


# -------------------------------------------------------------------- 写请求


class RequirementIn(BaseModel):
    competency_definition_id: int
    requirement_type: str = "required"
    minimum_score: int | None = Field(default=None, ge=0, le=100)
    target_score: int | None = Field(default=None, ge=0, le=100)
    minimum_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    critical: bool = False
    priority: int = 0
    weight: float = Field(default=1.0, ge=0.0)
    notes: str = ""


class RequirementPatch(BaseModel):
    requirement_type: str | None = None
    minimum_score: int | None = Field(default=None, ge=0, le=100)
    target_score: int | None = Field(default=None, ge=0, le=100)
    minimum_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    critical: bool | None = None
    priority: int | None = None
    weight: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


class PublishIn(BaseModel):
    note: str = ""


class RetireIn(BaseModel):
    note: str = ""


class CloneIn(BaseModel):
    template_version_id: int
