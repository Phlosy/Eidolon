"""Cultivation schemas（T1.0）。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.organization import ORMModel


class CharacterCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # trained（玩家自训）/ blank（空白自由养成）；issued 属 T2 发行方，不开放
    origin: str = "trained"
    # academic / vocational / self_taught；缺省 = 自由养成（不开 program）
    template: str | None = None


class ProgramOut(ORMModel):
    id: int
    template: str
    current_stage: int
    resource_used: dict
    status: str
    created_at: datetime


class EducationEventOut(ORMModel):
    id: int
    program_id: int | None
    kind: str
    topic: str
    outcome: dict
    evidence_id: int | None
    occurred_at: datetime


class CharacterOut(BaseModel):
    id: int
    person_id: int
    identity_id: str
    name: str
    slug: str
    origin: str
    owner_company_id: int | None
    lifecycle: str
    created_at: datetime


class CharacterDetailOut(CharacterOut):
    programs: list[ProgramOut]
    events: list[EducationEventOut]


class FreeSessionIn(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    mode: str = "web_research"  # LearningMode 值
    kind: str = "course"  # EducationEvent.kind：course/exam/project/internship/competition
    signal: int = Field(default=60, ge=0, le=100)  # 强度 → 证据 signal


class AdvanceResultOut(BaseModel):
    program: ProgramOut
    event: EducationEventOut
    lifecycle: str  # 推进后的角色 lifecycle（走完模板 → ready）


class FreeSessionResultOut(BaseModel):
    event: EducationEventOut
