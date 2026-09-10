"""EvidenceCandidate —— 业务事实 →（collector 解释）→ 候选证据。

Candidate 只是“解释”，**不是**已落库的 CompetencyEvidence：下一步必须经过
normalizer 的校验/幂等 upsert（docs/evidence-pipeline.md §三/§四）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EvidenceCandidate:
    employee_id: int | None  # None = person-only（培养期教育证据，T1.1）
    source_type: str  # EvidenceSourceKind
    source_id: int | None
    source_ref: str  # 人类可读，能反查（如 "task:12 (Implement Authentication)"）
    observation: str  # 发生了什么（Who/What/Where/When 的简短叙述）
    competency_definition_id: int
    signal: int | None  # 0-100；None = 只记录发生过，不判分
    strength: float  # 对目标能力的证明力度（0..1）
    reliability: float  # 这条证据本身多可信（0..1）
    occurred_at: datetime
    environment: str = ""  # "" = 真实；mock = 模拟/教程；education = 培养期（T1.1）
    metadata: dict = field(default_factory=dict)
    person_id: int | None = None  # person-only 证据的属主（employee_id 为 None 时必填）
