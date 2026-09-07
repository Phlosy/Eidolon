"""Deterministic Assessment Engine v2 —— 档案驱动的考核（docs/assessment-system.md §4~5 正式版）。

输入：Validated CompetencyEvidence + AssessmentProfile + Assessment Window。
输出：criterion 结果 + competency 贡献 + score/confidence/trend 更新 + 审计 run。

承诺（都有测试）：
- deterministic / auditable / versioned：同证据 + 同 window + 同档案版本 + 同引擎版本
  ⇒ 同 inputs_hash、同输出；
- Score ≠ Confidence；无证据不编分；证据不足（< min_evidence_count）保持 UNRATED；
- 失败证据通过低 signal 参与聚合（降低观测），绝不“失败一次 → score-10”；
- Trend 来自相邻两次 Assessment（历史），不是单条证据。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assessment.catalog import resolve_position_profile
from app.evidence.policy import POLICY
from app.models.assessment import (
    AssessmentCriterion,
    AssessmentCriterionCompetency,
    AssessmentProfile,
    AssessmentResult,
)
from app.models.competency import (
    AssessmentRun,
    CompetencyEvidence,
    EmployeeCompetency,
)
from app.models.enums import CompetencyStatus
from app.models.organization import Employee
from app.models.project import Project, Task
from app.models.project_delivery import ReviewMeeting

ENGINE_VERSION = "assessment-profile-v1"
ALGORITHM_VERSION = "assessment-profile-v1"

#: 聚合常量（确定性输入的一部分；改动必须升引擎版本）
K_UNITS = 3.0
LEARN_RATE = 0.25
STEP_MIN = 0.02
STEP_MAX = 0.35
CONF_ASSESSED = 0.4
HALF_LIFE_DAYS = 90.0
CONF_WEIGHTS = {
    "units": 0.35,
    "diversity": 0.20,
    "projects": 0.15,
    "quality": 0.20,
    "recency": 0.10,
}


def _now_day() -> datetime:
    """UTC 日粒度“现在”：同一自然日的重放可得到逐字一致的 hash 与结果（确定性）。"""
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _reliability_of(row: CompetencyEvidence) -> float:
    if row.reliability is not None:
        return max(0.0, min(1.0, float(row.reliability)))
    return POLICY.reliability(row.source_kind, row.environment or "")


def _strength_of(row: CompetencyEvidence) -> float:
    return float(row.strength) if row.strength is not None else 0.5


def _weight(row: CompetencyEvidence, window_to: datetime, buckets: dict, bucket_key: str) -> float:
    """strength × reliability × recency × 同桶冗余折扣。"""
    age_days = max(0.0, (window_to - _utc(row.occurred_at)).total_seconds() / 86400.0)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    index = buckets.get(bucket_key, 0)
    buckets[bucket_key] = index + 1
    discount = 1.0 / (1.0 + 0.5 * index)
    return _strength_of(row) * _reliability_of(row) * recency * discount


def _bucket_key(row: CompetencyEvidence) -> str:
    metadata = row.metadata_json or {}
    project = metadata.get("project_id") or metadata.get("source_id")
    return f"{row.source_kind}:{project}"


def _weighted(rows: list[CompetencyEvidence], window_to: datetime):
    buckets: dict[str, int] = {}
    return [(row, _weight(row, window_to, buckets, _bucket_key(row))) for row in rows]


def _observed_score(weighted: list) -> tuple[int | None, float]:
    scored = [(row, weight) for row, weight in weighted if row.signal is not None]
    if not scored:
        return None, 0.0
    weights_sum = sum(weight for _row, weight in scored)
    if weights_sum <= 0:
        return None, 0.0
    observed = sum(row.signal * weight for row, weight in scored) / weights_sum
    return max(0, min(100, int(round(observed)))), weights_sum


def _comp_confidence(weighted: list) -> float:
    units = sum(weight for _row, weight in weighted)
    n_sources = len({row.source_kind for row, _weight in weighted})
    projects = len({_bucket_key(row) for row, _weight in weighted})
    avg_quality = sum(_reliability_of(row) for row, _weight in weighted) / (len(weighted) or 1)
    recency_coverage = sum(_reliability_of(row) for row, _weight in weighted) / (len(weighted) or 1)
    value = (
        CONF_WEIGHTS["units"] * min(1.0, units / 8.0)
        + CONF_WEIGHTS["diversity"] * min(1.0, n_sources / 4)
        + CONF_WEIGHTS["projects"] * min(1.0, projects / 3)
        + CONF_WEIGHTS["quality"] * avg_quality
        + CONF_WEIGHTS["recency"] * min(1.0, recency_coverage)
    )
    return max(0.0, min(1.0, value))


def _criteria_of(db: Session, profile: AssessmentProfile) -> list[dict]:
    criteria = list(
        db.scalars(
            select(AssessmentCriterion)
            .where(AssessmentCriterion.profile_id == profile.id)
            .order_by(AssessmentCriterion.order_index, AssessmentCriterion.id)
        )
    )
    result = []
    for criterion in criteria:
        mappings = list(
            db.scalars(
                select(AssessmentCriterionCompetency).where(
                    AssessmentCriterionCompetency.criterion_id == criterion.id
                )
            )
        )
        result.append(
            {
                "criterion": criterion,
                "mappings": [
                    {
                        "competency_definition_id": int(m.competency_definition_id),
                        "contribution_weight": m.contribution_weight,
                        "evidence_type": m.evidence_type,
                    }
                    for m in mappings
                ],
            }
        )
    return result


def inputs_hash_for(
    *,
    employee_id: int,
    profile: AssessmentProfile,
    criteria: list[dict],
    evidence_rows: list[CompetencyEvidence],
    window_from: datetime | None,
    window_to: datetime,
) -> str:
    payload = {
        "employee_id": employee_id,
        "engine_version": ENGINE_VERSION,
        "profile": {
            "id": profile.id,
            "code": profile.code,
            "version": profile.version,
            "min_evidence_count": profile.min_evidence_count,
            "half_life_days": profile.half_life_days,
            "algorithm_version": profile.algorithm_version,
        },
        "criteria": sorted(
            [
                {
                    "code": item["criterion"].code,
                    "weight": item["criterion"].weight,
                    "evidence_kinds": list(item["criterion"].evidence_kinds or []),
                }
                for item in criteria
            ],
            key=lambda entry: entry["code"],
        ),
        "criterion_competencies": sorted(
            [
                {
                    "criterion": item["criterion"].code,
                    "competency_definition_id": m["competency_definition_id"],
                    "contribution_weight": m["contribution_weight"],
                    "evidence_type": m["evidence_type"],
                }
                for item in criteria
                for m in item["mappings"]
            ],
            key=lambda entry: (
                entry["criterion"],
                entry["competency_definition_id"],
            ),
        ),
        "window_from": window_from.isoformat() if window_from else None,
        "window_to": window_to.isoformat(),
        "config": {
            "k_units": K_UNITS,
            "learn_rate": LEARN_RATE,
            "step_range": [STEP_MIN, STEP_MAX],
            "conf_assessed": CONF_ASSESSED,
            "half_life_days": HALF_LIFE_DAYS,
            "confidence_weights": CONF_WEIGHTS,
            "source_reliability": dict(sorted(POLICY.reliability_of.items())),
        },
        "evidence": [
            {
                "id": row.id,
                "competency_definition_id": row.competency_definition_id,
                "source_kind": row.source_kind,
                "source_id": row.source_id,
                "signal": row.signal,
                "strength": _strength_of(row),
                "reliability": _reliability_of(row),
                "environment": row.environment or "",
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
            for row in sorted(evidence_rows, key=lambda item: item.id)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_assessment(
    db: Session,
    employee_id: int,
    *,
    profile: AssessmentProfile | None = None,
    window_from: datetime | None = None,
    assessment_type: str = "manual",
    trigger_note: str = "",
    commit: bool = True,
) -> AssessmentRun:
    """档案驱动的确定性考核。profile 缺省时按任职职位解析（解析不到则报错）。"""
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ValueError(f"employee {employee_id} not found")
    if profile is None:
        profile = resolve_position_profile(db, employee_id)
        if profile is None:
            raise ValueError("该员工没有匹配的 AssessmentProfile（需先任职内置职位）")

    window_to = _now_day()
    # 证据窗口：以“今天零点”为观察日，窗口上界为次日零点 —— 当天新产生的证据（occurred_at
    # 在白天任意时刻）都在窗口内；hash 仍用日粒度，保证同一天重放逐字一致。
    window_end = window_to + timedelta(days=1)
    window_from = _utc(window_from) if window_from else None
    evidence_scope = select(CompetencyEvidence).where(CompetencyEvidence.employee_id == employee_id)
    if window_from is not None:
        evidence_scope = evidence_scope.where(CompetencyEvidence.occurred_at >= _utc(window_from))
    evidence_scope = evidence_scope.where(CompetencyEvidence.occurred_at <= _utc(window_end))
    evidence_rows = list(db.scalars(evidence_scope.order_by(CompetencyEvidence.id)))
    criteria = _criteria_of(db, profile)
    inputs_hash = inputs_hash_for(
        employee_id=employee_id,
        profile=profile,
        criteria=criteria,
        evidence_rows=evidence_rows,
        window_from=window_from,
        window_to=window_to,
    )

    started = window_to
    # ---- criterion 结果 ----
    criterion_results: list[dict] = []
    for item in criteria:
        criterion = item["criterion"]
        comp_ids = {m["competency_definition_id"] for m in item["mappings"]}
        pool = [
            row
            for row in evidence_rows
            if row.competency_definition_id in comp_ids
            and (not criterion.evidence_kinds or row.source_kind in criterion.evidence_kinds)
        ]
        if not pool:
            continue
        weighted = _weighted(pool, window_to)
        observed, units = _observed_score(weighted)
        confidence = units / (units + K_UNITS)
        criterion_results.append(
            {
                "criterion": criterion,
                "observed": observed,
                "confidence": round(confidence, 6),
                "evidence_ids": [row.id for row in pool],
                "evidence_count": len(pool),
                "mappings": item["mappings"],
            }
        )

    # ---- competency 更新 ----
    outputs: dict[str, dict] = {}
    rows_by_def: dict[int, list[CompetencyEvidence]] = {}
    for row in evidence_rows:
        rows_by_def.setdefault(row.competency_definition_id, []).append(row)

    contribution_rows: list[dict] = []
    contributions_by_comp: dict[int, list[tuple[float, int | None]]] = {}
    for result in criterion_results:
        for mapping in result["mappings"]:
            comp_id = mapping["competency_definition_id"]
            contributions_by_comp.setdefault(comp_id, []).append(
                (mapping["contribution_weight"], result["observed"])
            )
            contribution_rows.append(
                {
                    "criterion_id": result["criterion"].id,
                    "competency_definition_id": comp_id,
                    "observed": result["observed"],
                    "confidence": result["confidence"],
                    "contribution": (
                        round(
                            mapping["contribution_weight"] * result["observed"],
                            4,
                        )
                        if result["observed"] is not None
                        else None
                    ),
                    "evidence_ids": result["evidence_ids"],
                    "evidence_count": result["evidence_count"],
                    "alpha": mapping["contribution_weight"],
                }
            )

    for definition_id, rows in sorted(rows_by_def.items()):
        if len(rows) < profile.min_evidence_count:
            continue  # 证据不足 ⇒ 保持 UNRATED，不编分
        weighted = _weighted(rows, window_to)
        confidence = _comp_confidence(weighted)
        direct_observed, _units = _observed_score(weighted)

        contributions = contributions_by_comp.get(definition_id, [])
        weighted_by_alpha = [
            (alpha, observed) for alpha, observed in contributions if observed is not None
        ]
        if weighted_by_alpha:
            raw_observed = sum(alpha * observed for alpha, observed in weighted_by_alpha) / sum(
                alpha for alpha, _observed in weighted_by_alpha
            )
            raw_score = max(0, min(100, int(round(raw_observed))))
        else:
            raw_score = direct_observed
            if raw_score is None:
                raw_score = 0
        if raw_score is None or raw_score <= 0:
            continue

        row = db.scalar(
            select(EmployeeCompetency).where(
                EmployeeCompetency.employee_id == employee_id,
                EmployeeCompetency.competency_definition_id == definition_id,
            )
        )
        previous_score = row.score if row is not None else None
        if previous_score is None:
            new_score = raw_score
        else:
            step = max(STEP_MIN, min(STEP_MAX, LEARN_RATE * confidence))
            new_score = int(round(previous_score + step * (raw_score - previous_score)))
            new_score = max(0, min(100, new_score))
        trend = (new_score - previous_score) if previous_score is not None else None
        status = (
            CompetencyStatus.assessed.value
            if confidence >= CONF_ASSESSED
            else CompetencyStatus.provisional.value
        )
        if row is None:
            row = EmployeeCompetency(
                employee_id=employee_id,
                competency_definition_id=definition_id,
                score=new_score,
                confidence=confidence,
                evidence_count=len(rows),
                status=status,
                last_assessed_at=started,
            )
            db.add(row)
        else:
            row.score = new_score
            row.confidence = confidence
            row.evidence_count = len(rows)
            row.status = status
            row.last_assessed_at = started
            row.trend = trend
            row.trend_window = 2 if trend is not None else None
        outputs[str(definition_id)] = {
            "previous_score": previous_score,
            "score": row.score,
            "confidence": round(float(row.confidence), 6),
            "evidence_count": row.evidence_count,
            "status": row.status,
            "trend": trend,
            "raw_score": raw_score,
        }
    db.flush()

    run = AssessmentRun(
        company_id=employee.company_id,
        employee_id=employee_id,
        profile_id=profile.id,
        profile_version=profile.version,
        assessment_type=assessment_type,
        triggered_by=trigger_note or assessment_type,
        status="completed",
        evidence_ids=[row.id for row in evidence_rows],
        algorithm_version=ALGORITHM_VERSION,
        inputs_hash=inputs_hash,
        outputs=outputs,
        window_from=_utc(window_from) if window_from else None,
        window_to=window_to,
        started_at=started,
        finished_at=window_to,
        metadata_json={"engine_version": ENGINE_VERSION},
    )
    db.add(run)
    db.flush()

    for result in criterion_results:
        db.add(
            AssessmentResult(
                run_id=run.id,
                kind="criterion",
                criterion_id=result["criterion"].id,
                observed_score=result["observed"],
                confidence=result["confidence"],
                evidence_count=result["evidence_count"],
                evidence_ids=result["evidence_ids"],
                rationale=(
                    f"criterion {result['criterion'].code}（weight="
                    f"{result['criterion'].weight}）：{result['evidence_count']} 条证据"
                ),
            )
        )
    for contribution_row in contribution_rows:
        db.add(
            AssessmentResult(
                run_id=run.id,
                kind="contribution",
                criterion_id=contribution_row["criterion_id"],
                competency_definition_id=contribution_row["competency_definition_id"],
                observed_score=contribution_row["observed"],
                confidence=contribution_row["confidence"],
                contribution=contribution_row["contribution"],
                evidence_count=contribution_row["evidence_count"],
                evidence_ids=contribution_row["evidence_ids"],
                rationale=(
                    f"contribution {contribution_row['alpha']} × observed "
                    f"{contribution_row['observed']}"
                ),
                metadata_json={"contribution_weight": contribution_row["alpha"]},
            )
        )
    if commit:
        db.commit()
    return run


def run_project_end_assessments(db: Session, project_id: int) -> int:
    """项目完成：每个参与员工按各自任职职位档案跑一次 project_end 考核。"""
    project = db.get(Project, project_id)
    if project is None:
        return 0
    employee_ids: set[int] = set(
        int(value)
        for value in db.scalars(
            select(Task.assignee_id).where(
                Task.project_id == project_id,
                Task.assignee_id.isnot(None),
            )
        ).all()
    )
    employee_ids.update(
        int(value)
        for value in db.scalars(
            select(ReviewMeeting.presenter_employee_id).where(
                ReviewMeeting.project_id == project_id,
                ReviewMeeting.presenter_employee_id.isnot(None),
            )
        ).all()
    )
    count = 0
    for employee_id in sorted(employee_ids):
        try:
            run_assessment(
                db,
                employee_id,
                window_from=project.created_at,
                assessment_type="project_end",
                commit=False,
            )
        except ValueError:
            continue  # 无匹配档案：跳过（不是每个人的工作都由这套模板考核）
        count += 1
    if count:
        db.commit()
    return count
