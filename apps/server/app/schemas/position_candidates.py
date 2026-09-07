"""Position Candidate Analysis read schemas（P9）。派生字段全部由序列化出口计算（ADR-12）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CandidateEmployeeOut(BaseModel):
    employee_id: int
    name: str
    slug: str
    avatar: str
    workforce_status: str
    department_id: int | None = None
    department_name: str | None = None
    current_position: dict | None = None


class CandidateFitOut(BaseModel):
    known_fit_score: float | None = None
    fit_confidence: float | None = None
    required_coverage: float
    required_required_coverage: float
    qualification_status: str
    fit_status: str
    critical_gap_count: int
    required_gap_count: int
    uncertainty_count: int
    strengths: list[str]
    gaps: list[str]
    uncertainties: list[str]
    development_opportunities: list[str]


class CandidateItemOut(BaseModel):
    employee: CandidateEmployeeOut
    fit: CandidateFitOut


class CandidateBandOut(BaseModel):
    band: str
    count: int
    candidates: list[CandidateItemOut]


class CandidateAnalysisMetaOut(BaseModel):
    engine_version: str
    policy_version: str
    candidate_analysis_version: str
    inputs_hash: str
    include_assigned: bool
    calculated_at: datetime | None = None


class CandidateAnalysisOut(BaseModel):
    position: dict
    profile: dict | None = None
    evaluable: bool
    bands: list[CandidateBandOut]
    meta: CandidateAnalysisMetaOut
