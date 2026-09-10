"""Cultivation schemas（T1.0）。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import TalentOrigin
from app.schemas.organization import ORMModel


class CharacterCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # trained（玩家自训）/ blank（空白自由养成）；issued 属 T2.4 发行方生成器，不开放
    origin: str = TalentOrigin.trained.value
    # academic / vocational / self_taught；缺省 = 自由养成（不开 program）
    template: str | None = None


class ProgramOut(ORMModel):
    id: int
    template: str
    current_stage: int
    #: 模板阶段总数（由后端模板注册表透出，前端进度条不再猜）
    stages_total: int = 0
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


class CharacterProgramSummary(BaseModel):
    """列表卡片用的培养进度快照（详情页用完整 ProgramOut）。"""

    template: str
    current_stage: int
    stages_total: int
    status: str


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
    #: 活跃培养实例摘要（自由养成 / 已结束无实例时为 None）
    program: CharacterProgramSummary | None = None


class CharacterDetailOut(CharacterOut):
    programs: list[ProgramOut]
    events: list[EducationEventOut]
    # T1.3 成品档案：人格（8 维，只读）+ 证据聚合能力画像（未评估 = score/confidence null）。
    # 与 /employees/{id}/traits、/employees/{id}/competencies 同构，前端复用同一套展示契约。
    traits: list[dict] = Field(default_factory=list)
    competencies: dict[str, list[dict]] = Field(default_factory=dict)


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
