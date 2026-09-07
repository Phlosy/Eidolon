"""Evidence Normalizer —— Candidate →（校验 + 幂等）→ CompetencyEvidence。

职责（docs/evidence-pipeline.md §四）：
1. 校验 Employee/Company Scope 与 Source 真实存在；
2. 校验 competency 有效（必须在员工可见目录里）；
3. 幂等：同一业务事件重复消费绝不产生两份 Evidence。

稳定身份（dedup key）= (employee_id, source_kind, source_id, competency_definition_id)。
同一 (source, competency) 只允许一条证据行：复审改判（如 review decision 变化）是
**更新同一行**（保留 created_at），历史保留在 AssessmentRun/AssessmentResult 里。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence.candidate import EvidenceCandidate
from app.models.competency import CompetencyDefinition, CompetencyDomain, CompetencyEvidence
from app.models.enums import EvidenceSourceKind
from app.models.knowledge import LearningRecord, SkillUsage
from app.models.organization import Employee
from app.models.project import Artifact, Task
from app.models.project_delivery import ReviewMeeting


def dedup_key_of(
    *,
    employee_id: int,
    source_type: str,
    source_id: int | None,
    competency_definition_id: int,
) -> tuple:
    return (employee_id, source_type, source_id, competency_definition_id)


def _source_exists(db: Session, candidate: EvidenceCandidate) -> bool:
    """按来源类型校验业务事实真实存在（防伪造/悬空引用）。"""
    if candidate.source_id is None:
        return True  # 无源 id 的证据（如纯人工反馈）不做存在性校验
    kind = candidate.source_type
    if kind == EvidenceSourceKind.review.value:
        return db.get(ReviewMeeting, candidate.source_id) is not None
    if kind == EvidenceSourceKind.skill_usage.value:
        return db.get(SkillUsage, candidate.source_id) is not None
    if kind == EvidenceSourceKind.learning.value:
        return db.get(LearningRecord, candidate.source_id) is not None
    if kind == EvidenceSourceKind.artifact.value:
        return db.get(Artifact, candidate.source_id) is not None
    if kind in {EvidenceSourceKind.task.value, EvidenceSourceKind.test.value}:
        task = db.get(Task, candidate.source_id)
        return task is not None
    return True


def upsert_evidence(db: Session, candidate: EvidenceCandidate) -> tuple[CompetencyEvidence, bool]:
    """校验 + 幂等写入。返回 (row, created)。不 commit（调用方决定事务边界）。"""
    employee = db.get(Employee, candidate.employee_id)
    if employee is None:
        raise ValueError(f"employee {candidate.employee_id} not found")
    definition = db.get(CompetencyDefinition, candidate.competency_definition_id)
    if definition is None:
        raise ValueError(f"competency_definition {candidate.competency_definition_id} not found")
    domain = db.get(CompetencyDomain, definition.domain_id)
    if domain is None or (
        domain.company_id is not None and domain.company_id != employee.company_id
    ):
        raise ValueError(f"competency {definition.code} 不在员工可见目录（公司隔离被绕开）")
    if not _source_exists(db, candidate):
        raise ValueError(f"source {candidate.source_type}#{candidate.source_id} 不存在")

    key = dedup_key_of(
        employee_id=candidate.employee_id,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
        competency_definition_id=candidate.competency_definition_id,
    )
    db.flush()  # 同事务内重复消费也要去重（SessionLocal autoflush=False）
    existing = db.scalar(
        select(CompetencyEvidence).where(
            CompetencyEvidence.employee_id == key[0],
            CompetencyEvidence.source_kind == key[1],
            CompetencyEvidence.source_id == key[2],
            CompetencyEvidence.competency_definition_id == key[3],
        )
    )
    if existing is not None:
        existing.signal = candidate.signal
        existing.strength = candidate.strength
        existing.reliability = candidate.reliability
        existing.source_ref = candidate.source_ref
        existing.environment = candidate.environment
        existing.occurred_at = candidate.occurred_at
        existing.metadata_json = {**(existing.metadata_json or {}), **candidate.metadata}
        # quality = reliability 的兼容别名（P5 读路径仍读它）
        existing.quality = candidate.reliability
        return existing, False

    row = CompetencyEvidence(
        employee_id=candidate.employee_id,
        competency_definition_id=candidate.competency_definition_id,
        source_kind=candidate.source_type,
        source_id=candidate.source_id,
        source_ref=candidate.source_ref,
        signal=candidate.signal,
        quality=candidate.reliability,
        strength=candidate.strength,
        reliability=candidate.reliability,
        environment=candidate.environment,
        occurred_at=candidate.occurred_at,
        metadata_json=candidate.metadata,
    )
    db.add(row)
    return row, True


def environment_for_runtime(runtime_types: list[str]) -> str:
    """按会话/运行环境判定证据环境：任何 mock 会话 ⇒ mock。"""
    if any(runtime_type == "mock" for runtime_type in runtime_types):
        return "mock"
    return ""
