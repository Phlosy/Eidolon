"""Candidate Analysis —— 职位 × 候选人才（P9，docs/candidate-analysis.md）。

只分析、不任命。批量复用 P8 PositionFitEngine（calculate_many 共享 position profile），
分组（Band）与组内排序由 CandidateAnalysisPolicy 集中决定，确定性、版本化、可解释。

原则（有测试）：
- Candidate Ranking 是 **Employee × Position 上下文**，不是全公司统一 Talent Score；
- Unknown 候选按 NEEDS_EVIDENCE 分组，绝不当“最差”排在末尾；
- fit 分数只做已知项（Unknown≠Bad 沿用 P8）；
- 不提供自动任命/调岗端点（P10）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Department, Employee
from app.models.position import PositionDefinition
from app.repositories import position as position_repo
from app.services import position_profile as profile_service
from app.talent.fit import service as fit_service
from app.talent.fit.engine import calculate_many
from app.talent.fit.models import GapType, QualificationStatus
from app.talent.fit.policy import POSITION_FIT_ENGINE_VERSION, POSITION_FIT_POLICY_VERSION
from app.workforce.status import WorkforceStatusResolver

CANDIDATE_ANALYSIS_VERSION = "v1"

#: 默认候选范围：待分配（AVAILABLE）；include_assigned 时加入已任职（ASSIGNED）
DEFAULT_STATUSES = ("available",)
WITH_ASSIGNED_STATUSES = ("available", "assigned")


class CandidateBand:
    RECOMMENDED = "RECOMMENDED"
    VIABLE = "VIABLE"
    DEVELOPMENTAL = "DEVELOPMENTAL"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    CRITICAL_GAP = "CRITICAL_GAP"

    ORDER = (
        RECOMMENDED,
        VIABLE,
        DEVELOPMENTAL,
        NEEDS_EVIDENCE,
        CRITICAL_GAP,
    )


@dataclass
class CandidateAnalysisPolicy:
    """分组/排序阈值集中。v1：

    - RECOMMENDED：QUALIFIED 且 fit_confidence ≥ recommended_min_confidence；
    - VIABLE：QUALIFIED_WITH_GAPS 且无技术缺口（仅 target/required gap 之外）——
      即无 REQUIRED_GAP/TARGET_GAP/CRITICAL_GAP，且 fit_confidence ≥ min_confidence；
    - DEVELOPMENTAL：存在 REQUIRED_GAP 或 TARGET_GAP（evidence 足够，无关键短板）；
    - NEEDS_EVIDENCE：INSUFFICIENT_DATA（含关键 unknown）；
    - CRITICAL_GAP：存在 CRITICAL_GAP。
    组内排序：required_coverage desc → fit_confidence desc → known_fit desc → employee_id asc。
    """

    version: str = CANDIDATE_ANALYSIS_VERSION
    recommended_min_confidence: float = 0.5
    min_confidence: float = 0.3


POLICY = CandidateAnalysisPolicy()


def _band_for(result) -> str:
    if any(item.gap_type == GapType.CRITICAL_GAP for item in result.requirement_evaluations):
        return CandidateBand.CRITICAL_GAP
    if result.qualification_status == QualificationStatus.INSUFFICIENT_DATA:
        return CandidateBand.NEEDS_EVIDENCE
    if result.qualification_status == QualificationStatus.QUALIFIED:
        if (
            result.fit_confidence is not None
            and result.fit_confidence >= POLICY.recommended_min_confidence
        ):
            return CandidateBand.RECOMMENDED
        return (
            CandidateBand.VIABLE
            if (result.fit_confidence or 0) >= POLICY.min_confidence
            else CandidateBand.NEEDS_EVIDENCE
        )
    # QUALIFIED_WITH_GAPS
    has_technical_gap = any(
        item.gap_type in {GapType.REQUIRED_GAP, GapType.TARGET_GAP}
        for item in result.requirement_evaluations
    )
    if has_technical_gap:
        return CandidateBand.DEVELOPMENTAL
    return CandidateBand.VIABLE


def _band_order_key(result) -> tuple:
    return (
        -1 * result.required_coverage if result.required_coverage is not None else 0,
        -1 * (result.fit_confidence or 0),
        -1 * (result.known_fit_score or 0),
        result.employee_id,
    )


def analyze(
    db: Session,
    *,
    position_definition_id: int,
    include_assigned: bool = False,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """一个职位的候选分析（只读）。无 ACTIVE profile ⇒ evaluable=False + 空 bands。"""
    position = db.get(PositionDefinition, position_definition_id)
    if position is None:
        raise fit_service.FitDomainError("position not found")
    now = datetime.now(UTC)
    evaluable = profile_service.active_profile(db, position_definition_id) is not None
    if not evaluable:
        return {
            "position": {"id": position.id, "code": position.code, "name": position.name},
            "profile": None,
            "evaluable": False,
            "bands": [],
            "meta": {
                "engine_version": POSITION_FIT_ENGINE_VERSION,
                "policy_version": POSITION_FIT_POLICY_VERSION,
                "candidate_analysis_version": POLICY.version,
                "calculated_at": now,
            },
        }

    company_id = position.company_id
    people = (
        list(db.scalars(select(Employee).where(Employee.company_id == company_id)))
        if company_id is not None
        else []
    )
    resolver = WorkforceStatusResolver(db)
    views = resolver.views(people)
    wanted = set(WITH_ASSIGNED_STATUSES if include_assigned else DEFAULT_STATUSES)
    query = (search or "").strip().lower()
    candidates: list[tuple[Employee, dict]] = []
    for person in people:
        if person.lifecycle_status in {"offboarded", "pending"}:
            continue
        if views[int(person.id)].workforce_status.value not in wanted:
            continue
        if query and query not in f"{person.name} {person.slug}".lower():
            continue
        candidates.append((person, views[int(person.id)].workforce_status.value))

    candidate_ids = [int(person.id) for person, _status in candidates]
    results = calculate_many(db, position=position, employee_ids=candidate_ids)
    currents = position_repo.current_positions_by_employee(db, candidate_ids)
    department_ids = {
        person.department_id for person, _status in candidates if person.department_id
    }
    department_names = {
        department.id: department.name
        for department in db.scalars(
            select(Department).where(Department.id.in_(list(department_ids)))
        )
    }

    by_band: dict[str, list[dict]] = {band: [] for band in CandidateBand.ORDER}
    for person, status in candidates:
        result = results.get(int(person.id))
        if result is None:
            continue
        by_band[_band_for(result)].append(
            _candidate_payload(
                db,
                person,
                status,
                result,
                currents.get(int(person.id)),
                department_names.get(person.department_id) if person.department_id else None,
            )
        )
    for band in CandidateBand.ORDER:
        by_band[band].sort(key=_band_key)

    active = profile_service.active_profile(db, position_definition_id)
    bands = [
        {
            "band": band,
            "count": len(by_band[band]),
            "candidates": by_band[band],
        }
        for band in CandidateBand.ORDER
        if by_band[band]
    ]
    batch_hash = hashlib.sha256(
        json.dumps(
            {
                "position_definition_id": position.id,
                "profile_version": active.version if active else None,
                "candidate_ids": sorted(candidate_ids),
                "engine_version": POSITION_FIT_ENGINE_VERSION,
                "policy_version": POSITION_FIT_POLICY_VERSION,
                "candidate_analysis_version": POLICY.version,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "position": {
            "id": position.id,
            "code": position.code,
            "name": position.name,
        },
        "profile": (
            {"version_id": active.id, "version": active.version, "status": active.status}
            if active
            else None
        ),
        "evaluable": True,
        "bands": bands,
        "meta": {
            "engine_version": POSITION_FIT_ENGINE_VERSION,
            "policy_version": POSITION_FIT_POLICY_VERSION,
            "candidate_analysis_version": POLICY.version,
            "inputs_hash": batch_hash,
            "include_assigned": include_assigned,
            "calculated_at": now,
        },
    }


def _candidate_payload(
    db: Session,
    person: Employee,
    workforce_status: str,
    result,
    current,
    department_name,
) -> dict:
    critical_gaps = [
        item.code
        for item in result.requirement_evaluations
        if item.gap_type == GapType.CRITICAL_GAP
    ]
    required_gaps = [
        item.code
        for item in result.requirement_evaluations
        if item.gap_type in {GapType.REQUIRED_GAP, GapType.TARGET_GAP}
    ]
    uncertain = [item.code for item in result.uncertainties]
    return {
        "employee": {
            "employee_id": int(person.id),
            "name": person.name,
            "slug": person.slug,
            "avatar": person.avatar or "",
            "workforce_status": workforce_status,
            "department_id": person.department_id,
            "department_name": department_name,
            "current_position": _current_summary(current),
        },
        "fit": {
            "known_fit_score": result.known_fit_score,
            "fit_confidence": result.fit_confidence,
            "required_coverage": result.requirement_coverage,
            "required_required_coverage": result.required_coverage,
            "qualification_status": result.qualification_status,
            "fit_status": result.fit_status,
            "critical_gap_count": len(critical_gaps),
            "required_gap_count": len(required_gaps),
            "uncertainty_count": len(uncertain),
            "strengths": [item.code for item in result.strengths][:5],
            "gaps": [item.code for item in result.gaps][:5],
            "uncertainties": uncertain[:5],
            "development_opportunities": [item.code for item in result.development_opportunities][
                :5
            ],
        },
    }


def _current_summary(current) -> dict | None:
    if current is None:
        return None
    return {
        "definition_id": current.definition_id,
        "code": current.code,
        "name": current.name,
        "department_id": current.department_id,
        "department_name": current.department_name,
        "slot_code": current.slot_code,
    }


def _band_key(item: dict) -> tuple:
    fit = item["fit"]
    return (
        -1 * (fit["required_required_coverage"] or 0),
        -1 * (fit["fit_confidence"] or 0),
        -1 * (fit["known_fit_score"] or 0),
        item["employee"]["employee_id"],
    )
