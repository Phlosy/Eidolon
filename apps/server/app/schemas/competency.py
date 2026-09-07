"""Competency read schemas (P5). 读契约；写面只在聚合服务（无 PATCH score 入口）。

ADR-12 纪律：派生/状态字段不允许带 `0/[]/false` 默认值 —— score/confidence 用
`null` 表达"未评估"（缺得明白），evidence_count/status/trend_direction 由序列化出口
显式计算（unrated 的 evidence_count=0 是"真的没有证据"，不是"没算"）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.organization import ORMModel


class CompetencyDomainOut(ORMModel):
    id: int
    company_id: int | None
    code: str
    name: str
    kind: str
    description: str
    order_index: int
    built_in: bool


class CompetencyDefinitionOut(ORMModel):
    id: int
    domain_id: int
    code: str
    name: str
    description: str
    order_index: int
    built_in: bool


class TraitOut(BaseModel):
    """一个人格维度的读出（0..1 值域；值来自 BrainTraits，缺失键=注册表默认）。

    只描述"倾向怎样工作"；`affects_execution` 区分已接/未接行为的维度（UI 据此显示
    "当前不影响执行"），禁止把 trait 渲染成能力加成。
    """

    code: str
    label: str
    description: str
    value: float
    # 派生：round(value*100)，由序列化出口算（ADR-12：不落 schema 默认值）
    display: int
    affects_execution: bool


class EmployeeCompetencyOut(BaseModel):
    """员工在一个能力维度上的当前状态。score/confidence 为 null = 未评估。"""

    competency_definition_id: int
    domain_id: int
    domain_code: str
    domain_name: str
    code: str
    name: str
    description: str
    kind: str
    #: 0-100；NULL = 未评（不用 0 表示未评 —— 0 是"有证据表明很差"）
    score: int | None = None
    #: 0..1；NULL = 未知置信度（绝不显示成 0% 已确认）
    confidence: float | None = None
    evidence_count: int
    status: str
    #: 相邻两次 run 的能力分差；NULL = 无历史可比（UI 显示 →，不要显示 ↑+0）
    trend: int | None = None
    #: 由 trend 派生（up/stable/down/unknown）—— 序列化出口计算
    trend_direction: str
    last_assessed_at: datetime | None = None


class EmployeeCapabilitiesOut(BaseModel):
    """人物能力读面（docs/competency-system.md §8 的 P5 子集）。

    general = 10 维全量（未评估维度如实给 unrated/null）；professional = 动态目录
    （只有被评估过的维度出现）。两者都**不含** position fit（P8+）。
    """

    general: list[EmployeeCompetencyOut]
    professional: list[EmployeeCompetencyOut]


class CompetencyEvidenceOut(BaseModel):
    id: int
    employee_id: int
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
