"""Competency 聚合基础 —— Evidence → Assessment → EmployeeCompetency（P5 最小确定性引擎）。

规格：docs/assessment-system.md §4/§5 的第一版落地（刻意不做复杂 LLM 打分/统计模型）。

四条硬约束（都有测试）：

- **deterministic**：同证据集 + 同算法版本 ⇒ 同 `inputs_hash`、同输出；
- **versioned / auditable**：每次重算写一条 `assessment_runs`（含 inputs_hash + outputs）；
- **无证据不得编分**：员工没有任何 Evidence 时这里**不落任何行**（查询层呈现 unrated）；
  行内 score/confidence 只能由本模块写入；
- **Score ≠ Confidence**：confidence 只来自证据量/多样性/新近度/质量，与分数高低无关。

本模块不 import random、不读 trait —— 这两条由架构守卫检查源码。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competency import (
    AssessmentRun,
    CompetencyEvidence,
    EmployeeCompetency,
)
from app.models.enums import CompetencyStatus, EvidenceSourceKind
from app.models.knowledge import Skill, SkillUsage
from app.models.organization import Employee

#: 基础引擎标识 —— inputs_hash 必须包含它；改公式时**升版本**而不是悄悄变行为。
ENGINE_VERSION = "competency-base-v1"
ALGORITHM_VERSION = "assessment-base-v1"

#: 证据来源固定质量表（assessment-system.md §4.1）。可预测、可审计。
SOURCE_QUALITY = {
    EvidenceSourceKind.assessment.value: 1.0,
    EvidenceSourceKind.test.value: 0.9,
    EvidenceSourceKind.review.value: 0.85,
    EvidenceSourceKind.artifact.value: 0.8,
    EvidenceSourceKind.peer_review.value: 0.75,
    EvidenceSourceKind.project.value: 0.75,
    EvidenceSourceKind.task.value: 0.7,
    EvidenceSourceKind.skill_usage.value: 0.65,
    EvidenceSourceKind.user_feedback.value: 0.6,
    EvidenceSourceKind.learning.value: 0.5,
}
DEFAULT_SOURCE_QUALITY = 0.5

#: 新近度半衰期（天）：证据每过一个半衰期有效权重减半（§4.1）。
HALF_LIFE_DAYS = 90.0
#: criterion 置信度常量：n 单位证据 ≈ n/(n+K)。
K_UNITS = 3.0
#: 有界 EMA 步长（§4.3）：单次 run 对旧分只移动一小步，防止一次性暴涨。
LEARN_RATE = 0.25
STEP_MIN = 0.02
STEP_MAX = 0.35
#: confidence 复合权重（§5.1）
CONF_WEIGHTS = {
    "units": 0.35,
    "diversity": 0.20,
    "projects": 0.15,
    "quality": 0.20,
    "recency": 0.10,
}
CONF_UNITS_TARGET = 8.0
CONF_DIVERSITY_TARGET = 4
CONF_PROJECTS_TARGET = 3


@dataclass(frozen=True)
class AggregatedCompetency:
    """一个能力维度的确定性聚合输出（供 run.outputs 与行更新使用）。"""

    competency_definition_id: int
    score: int | None
    confidence: float | None
    evidence_count: int
    status: str
    n_units: float = 0.0


def _utc_aware(moment: datetime | None) -> datetime:
    """SQLite 存的 DateTime 读出是 naive；统一按 UTC 解释（与写入端 datetime.now(UTC) 对齐）。"""
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def evidence_quality(evidence: CompetencyEvidence) -> float:
    """证据质量：行上显式 quality 优先，否则回落来源类型固定表。"""
    if evidence.quality is not None:
        return max(0.0, min(1.0, float(evidence.quality)))
    return SOURCE_QUALITY.get(evidence.source_kind, DEFAULT_SOURCE_QUALITY)


def effective_weight(
    evidence: CompetencyEvidence,
    half_life_days: float = HALF_LIFE_DAYS,
    window_end: datetime | None = None,
) -> float:
    """w(e) = source_quality × recency(e)。redundancy discount 在 group 里做。

    `window_end` = 评估截止时刻（默认 = 调用时刻）。显式传参 ⇒ 纯函数可重放：
    同证据 + 同 window_end ⇒ 逐字相同结果。
    """
    quality = evidence_quality(evidence)
    reference = datetime.now(UTC) if window_end is None else _utc_aware(window_end)
    age_days = max(0.0, (reference - _utc_aware(evidence.occurred_at)).total_seconds() / 86400.0)
    recency = 0.5 ** (age_days / half_life_days)
    return quality * recency


def _project_key(evidence: CompetencyEvidence) -> str:
    """冗余折扣的分组键：同一来源类型 + 同一项目（元数据里的 project 维度）。"""
    metadata = evidence.metadata_json or {}
    project_id = metadata.get("project_id") or metadata.get("source_id")
    return f"{evidence.source_kind}:{project_id}"


def compute_for_group(
    evidence_rows: list[CompetencyEvidence], window_end: datetime | None = None
) -> AggregatedCompetency:
    """一组（同 employee、同 competency）证据的确定性聚合（§4.1-§4.3 基础版）。

    纯函数：同证据 + 同 `window_end` ⇒ 同输出（测试直接验证）。
    """
    if not evidence_rows:
        return AggregatedCompetency(
            competency_definition_id=0,
            score=None,
            confidence=None,
            evidence_count=0,
            status=CompetencyStatus.unrated.value,
        )
    competency_id = evidence_rows[0].competency_definition_id
    ordered = sorted(evidence_rows, key=lambda item: item.occurred_at)

    # 冗余折扣：同 (source_kind, project) 内第 n 条 → 1/(1+0.5(n-1))
    buckets: dict[str, int] = {}
    weighted: list[tuple[CompetencyEvidence, float]] = []
    for evidence in ordered:
        base = effective_weight(evidence, window_end=window_end)
        bucket_key = _project_key(evidence)
        index_in_bucket = buckets.get(bucket_key, 0)
        buckets[bucket_key] = index_in_bucket + 1
        discount = 1.0 / (1.0 + 0.5 * index_in_bucket)
        weighted.append((evidence, base * discount))

    n_units = sum(weight for _evidence, weight in weighted)
    confidence = confidence_of(weighted, len(ordered), window_end=window_end)
    # 观测分只对**有 signal 的证据**求和（§4.2：n==0 ⇒ observed=NULL，不是 0）
    scored = [(evidence, weight) for evidence, weight in weighted if evidence.signal is not None]
    if n_units <= 0 or not scored:
        return AggregatedCompetency(
            competency_definition_id=competency_id,
            score=None,
            confidence=confidence if scored else None,
            evidence_count=len(evidence_rows),
            status=CompetencyStatus.unrated.value,
        )
    observed = sum(evidence.signal * weight for evidence, weight in scored) / sum(
        weight for _evidence, weight in scored
    )
    raw_new = _clamp_score(observed)
    status = (
        CompetencyStatus.assessed.value if confidence >= 0.4 else CompetencyStatus.provisional.value
    )
    return AggregatedCompetency(
        competency_definition_id=competency_id,
        score=raw_new,
        confidence=confidence,
        evidence_count=len(evidence_rows),
        status=status,
        n_units=n_units,
    )


def confidence_of(weighted: list, count: int, window_end: datetime | None = None) -> float:
    """能力级 confidence（§5.1 基础版）。分数高不高与它无关。"""
    units = sum(weight for _evidence, weight in weighted)
    n_sources = len({evidence.source_kind for evidence, _weight in weighted})
    projects = len({_project_key(evidence) for evidence, _weight in weighted})
    avg_quality = sum(evidence_quality(evidence) for evidence, _weight in weighted) / (
        len(weighted) or 1
    )
    recency_coverage = sum(
        effective_weight(evidence, window_end=window_end) for evidence, _weight in weighted
    ) / (len(weighted) or 1)
    value = (
        CONF_WEIGHTS["units"] * min(1.0, units / CONF_UNITS_TARGET)
        + CONF_WEIGHTS["diversity"] * min(1.0, n_sources / CONF_DIVERSITY_TARGET)
        + CONF_WEIGHTS["projects"] * min(1.0, projects / CONF_PROJECTS_TARGET)
        + CONF_WEIGHTS["quality"] * avg_quality
        + CONF_WEIGHTS["recency"] * min(1.0, recency_coverage)
    )
    return max(0.0, min(1.0, value))


def _clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


# --------------------------------------------------------------------------
# inputs_hash：规范化输入指纹（assessment-system.md §1.5 的基础覆盖 —— 不含 LLM 部分）
# --------------------------------------------------------------------------


def inputs_hash_of(
    *,
    employee_id: int,
    evidence_rows: list[CompetencyEvidence],
) -> str:
    payload = {
        "employee_id": employee_id,
        "engine_version": ENGINE_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "config": {
            "half_life_days": HALF_LIFE_DAYS,
            "k_units": K_UNITS,
            "learn_rate": LEARN_RATE,
            "step_range": [STEP_MIN, STEP_MAX],
            "source_quality": dict(sorted(SOURCE_QUALITY.items())),
            "default_source_quality": DEFAULT_SOURCE_QUALITY,
        },
        "evidence": [
            {
                "id": row.id,
                "competency_definition_id": row.competency_definition_id,
                "source_kind": row.source_kind,
                "source_ref": row.source_ref,
                "signal": row.signal,
                "quality": evidence_quality(row),
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
            for row in sorted(evidence_rows, key=lambda item: item.id)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Skill → Evidence 桥（P5：只证明通道可用；自动采集是 P10 collector 的活）
# --------------------------------------------------------------------------


def skill_usage_evidence_for(db: Session, usage: SkillUsage) -> CompetencyEvidence | None:
    """一条 SkillUsage → CompetencyEvidence（幂等，重复调用不产生重复行）。

    条件：技能映射到某个能力维度（skills.competency_definition_id）且人评 outcome 为
    useful —— 技能的**真实使用得到本人确认**才配当能力证据；没确认的不许冒充。
    signal：成功跑完给 80，否则 60（经验线，写进元数据便于日后校准）；
    但 success 判定不来自这里 —— 它只是“发生过、被判定有用”的记录。
    """
    skill = db.get(Skill, usage.skill_id)
    if skill is None or skill.competency_definition_id is None:
        return None
    if usage.outcome != "useful":
        return None
    existing = db.scalar(
        select(CompetencyEvidence).where(
            CompetencyEvidence.employee_id == usage.employee_id,
            CompetencyEvidence.competency_definition_id == skill.competency_definition_id,
            CompetencyEvidence.source_kind == EvidenceSourceKind.skill_usage.value,
            CompetencyEvidence.source_id == usage.id,
        )
    )
    if existing is not None:
        return existing
    evidence = CompetencyEvidence(
        employee_id=usage.employee_id,
        competency_definition_id=skill.competency_definition_id,
        source_kind=EvidenceSourceKind.skill_usage.value,
        source_id=usage.id,
        source_ref=f"skill:{skill.name}#usage:{usage.id}",
        signal=80 if usage.success else 60,
        occurred_at=usage.created_at or datetime.now(UTC),
        metadata_json={"skill_id": skill.id, "task_id": usage.task_id},
    )
    db.add(evidence)
    return evidence


# --------------------------------------------------------------------------
# Run（确定性重算 + 审计）
# --------------------------------------------------------------------------


def assess_employee_competencies(
    db: Session,
    employee_id: int,
    *,
    triggered_by: str = "recompute",
    commit: bool = True,
) -> AssessmentRun:
    """对一名员工做一次基础能力重算并落审计 run（幂等、可重放）。

    流程：取该员工全部 Evidence → 按能力维度分组聚合 → 更新/创建
    employee_competencies（trend = 本次 run − 上次已评分数）→ 写 assessment_runs。
    """
    employee = db.get(Employee, employee_id)
    if employee is None:  # pragma: no cover - 调用方先校验
        raise ValueError(f"employee {employee_id} not found")
    started = datetime.now(UTC)
    evidence_rows = list(
        db.scalars(
            select(CompetencyEvidence)
            .where(CompetencyEvidence.employee_id == employee_id)
            .order_by(CompetencyEvidence.id)
        )
    )
    inputs_hash = inputs_hash_of(employee_id=employee_id, evidence_rows=evidence_rows)

    grouped: dict[int, list[CompetencyEvidence]] = {}
    for row in evidence_rows:
        grouped.setdefault(row.competency_definition_id, []).append(row)

    outputs: dict[str, dict] = {}
    for definition_id, rows in grouped.items():
        aggregated = compute_for_group(rows, window_end=started)
        row = db.scalar(
            select(EmployeeCompetency).where(
                EmployeeCompetency.employee_id == employee_id,
                EmployeeCompetency.competency_definition_id == definition_id,
            )
        )
        previous_score = row.score if row is not None else None
        trend: int | None = None
        if previous_score is not None and aggregated.score is not None:
            trend = aggregated.score - previous_score
        if row is None:
            row = EmployeeCompetency(
                employee_id=employee_id,
                competency_definition_id=definition_id,
                score=aggregated.score,
                confidence=aggregated.confidence,
                evidence_count=aggregated.evidence_count,
                status=aggregated.status,
                last_assessed_at=started,
            )
            db.add(row)
        else:
            row.score = aggregated.score
            row.confidence = aggregated.confidence
            row.evidence_count = aggregated.evidence_count
            row.status = aggregated.status
            row.last_assessed_at = started
        row.trend = trend
        if trend is not None:
            row.trend_window = 2  # 相邻两次 run 之差（第一版口径，UI 可解释）
        else:
            row.trend_window = None
        outputs[str(definition_id)] = {
            "previous_score": previous_score,
            "score": aggregated.score,
            # confidence / n_units 在审计输出里归一化到 6 位小数：浮点末位噪声不该让
            # "重放即同输出"的承诺变得不可验证（行内存全精度，展示层用归一化值）
            "confidence": round(aggregated.confidence, 6)
            if aggregated.confidence is not None
            else None,
            "evidence_count": aggregated.evidence_count,
            "status": aggregated.status,
            "trend": trend,
            "n_units": round(aggregated.n_units, 6),
        }
    db.flush()

    run = AssessmentRun(
        company_id=employee.company_id,
        employee_id=employee_id,
        triggered_by=triggered_by,
        status="completed",
        evidence_ids=[row.id for row in evidence_rows],
        algorithm_version=ALGORITHM_VERSION,
        inputs_hash=inputs_hash,
        outputs=outputs,
        window_from=min((row.occurred_at for row in evidence_rows), default=None),
        window_to=started,
        started_at=started,
        finished_at=datetime.now(UTC),
        metadata_json={"engine_version": ENGINE_VERSION},
    )
    db.add(run)
    if commit:
        db.commit()
    return run
