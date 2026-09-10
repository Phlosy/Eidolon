"""Career & Talent Development 服务（P10，docs/career-development.md）。

只做决策支持：Career Readiness 复用 P8 Fit（不重写公式）；DevelopmentPlan 只给建议、
由 Reconciler 按真实 Evidence/Assessment/Competency 派生进度（不靠手点）；真正
Promote/Transfer 复用 PositionAssignment 工作流并记录 CareerEvent —— 任何人事决策
仍由 Human 显式确认。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.career import (
    CareerEvent,
    CareerPath,
    CareerPathStep,
    DevelopmentPlan,
    DevelopmentPlanItem,
)
from app.models.competency import (
    AssessmentRun,
    CompetencyDefinition,
    EmployeeCompetency,
)
from app.models.enums import (
    CareerEventType,
    DevelopmentItemStatus,
    DevelopmentNeedType,
    DevelopmentPlanStatus,
)
from app.models.knowledge import LearningPriority
from app.models.organization import Employee
from app.models.position import PositionDefinition
from app.models.project import Project, Task
from app.models.project_delivery import ReviewMeeting
from app.repositories import knowledge as knowledge_repo
from app.repositories import persons as person_repo
from app.repositories import position as position_repo
from app.schemas.position import AssignmentIn
from app.services import position_service
from app.talent.fit import service as fit_service
from app.talent.fit.models import EvaluationStatus, GapType

CAREER_ANALYSIS_VERSION = "v1"

_NEEDS_EVIDENCE_GAPS = {
    GapType.CRITICAL_UNCERTAINTY,
    GapType.REQUIRED_UNCERTAINTY,
    GapType.UNRATED,
}


class CareerError(ValueError):
    """职业域错误（API 转 4xx）。"""


def _employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise CareerError("employee not found")
    return employee


def _position(db: Session, position_definition_id: int, company_id: int) -> PositionDefinition:
    position = db.get(PositionDefinition, position_definition_id)
    if position is None:
        raise CareerError("position not found")
    if position.company_id is not None and position.company_id != company_id:
        raise CareerError("position belongs to another company")
    return position


# ---------------------------------------------------------------------------
# Career Path
# ---------------------------------------------------------------------------


def company_paths(db: Session, company_id: int) -> list[CareerPath]:
    return list(
        db.scalars(
            select(CareerPath)
            .where((CareerPath.company_id.is_(None)) | (CareerPath.company_id == company_id))
            .order_by(CareerPath.id)
        )
    )


def next_steps(
    db: Session, from_position_definition_id: int, company_id: int
) -> list[CareerPathStep]:
    path_ids = [path.id for path in company_paths(db, company_id)]
    return list(
        db.scalars(
            select(CareerPathStep)
            .where(
                CareerPathStep.career_path_id.in_(path_ids),
                CareerPathStep.from_position_definition_id == from_position_definition_id,
            )
            .order_by(CareerPathStep.priority, CareerPathStep.id)
        )
    )


def seed_career_paths(db: Session, company_id: int) -> int:
    """默认路径：只对当前已存在的 PositionDefinition 接线；公司可 Clone/Customize。"""
    from app.models.position import PositionDefinition as PD

    existing = {
        definition.code: definition.id
        for definition in db.scalars(select(PD).where(PD.company_id == company_id))
    }

    def _def_id(code: str) -> int | None:
        return existing.get(code)

    templates: list[tuple[str, str, list[tuple[str, str, str]]]] = [
        # code, name, steps: (from_code, to_code, transition_type)
        (
            "professional_growth",
            "专业成长",
            [
                ("engineer", "researcher", "specialization"),
                ("engineer", "product_manager", "cross_functional"),
                ("qa_engineer", "engineer", "lateral"),
                ("researcher", "product_manager", "cross_functional"),
                ("product_manager", "ceo", "promotion"),
            ],
        ),
    ]
    created = 0
    for code, name, steps in templates:
        path = db.scalar(
            select(CareerPath).where(
                (CareerPath.company_id.is_(None)) | (CareerPath.company_id == company_id),
                CareerPath.code == code,
            )
        )
        if path is None:
            path = CareerPath(company_id=None, code=code, name=name, description="", built_in=True)
            db.add(path)
            db.flush()
            created += 1
        for from_code, to_code, transition_type in steps:
            from_id = _def_id(from_code)
            to_id = _def_id(to_code)
            if from_id is None or to_id is None:
                continue
            exists = db.scalar(
                select(CareerPathStep.id).where(
                    CareerPathStep.career_path_id == path.id,
                    CareerPathStep.from_position_definition_id == from_id,
                    CareerPathStep.to_position_definition_id == to_id,
                )
            )
            if exists is None:
                db.add(
                    CareerPathStep(
                        career_path_id=path.id,
                        from_position_definition_id=from_id,
                        to_position_definition_id=to_id,
                        transition_type=transition_type,
                        priority=len(steps),
                        recommended=True,
                    )
                )
                created += 1
    return created


# ---------------------------------------------------------------------------
# CareerEvent
# ---------------------------------------------------------------------------


def record_event(
    db: Session,
    employee: Employee,
    event_type: str,
    *,
    position_definition_id: int | None = None,
    slot_id: int | None = None,
    from_position_id: int | None = None,
    to_position_id: int | None = None,
    actor_user_id: int | None = None,
    reason: str = "",
    source_id: int | None = None,
    metadata: dict | None = None,
) -> CareerEvent:
    event = CareerEvent(
        company_id=employee.company_id,
        employee_id=employee.id,
        event_type=event_type,
        position_definition_id=position_definition_id,
        position_slot_id=slot_id,
        from_position_id=from_position_id,
        to_position_id=to_position_id,
        actor_user_id=actor_user_id,
        reason=reason,
        source_id=source_id,
        metadata_json=metadata or {},
    )
    db.add(event)
    db.flush()
    return event


# ---------------------------------------------------------------------------
# Experience / Readiness
# ---------------------------------------------------------------------------


def experience_summary(db: Session, employee_id: int) -> dict:
    tasks_completed = (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.assignee_id == employee_id, Task.status == "done")
        )
        or 0
    )
    projects_completed = (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .join(Task, Task.project_id == Project.id)
            .where(Task.assignee_id == employee_id, Project.status == "completed")
        )
        or 0
    )
    reviews_presented = (
        db.scalar(
            select(func.count())
            .select_from(ReviewMeeting)
            .where(
                ReviewMeeting.presenter_employee_id == employee_id,
                ReviewMeeting.decision.isnot(None),
            )
        )
        or 0
    )
    assessments_count = (
        db.scalar(
            select(func.count())
            .select_from(AssessmentRun)
            # R1.3：读口径切 person_id（单一入口换算，带旧口径回落）
            .where(
                person_repo.read_criterion(
                    db, employee_id, AssessmentRun.person_id, AssessmentRun.employee_id
                )
            )
        )
        or 0
    )
    active_plans = (
        db.scalar(
            select(func.count())
            .select_from(DevelopmentPlan)
            .where(
                DevelopmentPlan.employee_id == employee_id,
                DevelopmentPlan.status == DevelopmentPlanStatus.active.value,
            )
        )
        or 0
    )
    return {
        "tasks_completed": int(tasks_completed),
        "projects_completed": int(projects_completed),
        "reviews_presented": int(reviews_presented),
        "assessments_count": int(assessments_count),
        "active_plans": int(active_plans),
    }


def _tenure_days(db: Session, employee_id: int) -> int | None:
    assignment = position_repo.active_primary_assignment(db, employee_id)
    if assignment is None or assignment.effective_from is None:
        return None
    effective_from = assignment.effective_from
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - effective_from).days)


def readiness_for(
    db: Session,
    employee_id: int,
    position_definition_id: int,
    *,
    step: CareerPathStep | None = None,
) -> dict:
    """Career Readiness：P8 Fit 之上叠加 Experience / Assessment / Development 上下文。

    结构化状态，不做单一百分比。复用 P8（不重写公式）。
    """
    employee = _employee(db, employee_id)
    position = _position(db, position_definition_id, employee.company_id)
    fit = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=position_definition_id
    )
    experience = experience_summary(db, employee_id)
    tenure_days = _tenure_days(db, employee_id)
    step_tenure = step.minimum_tenure_days if step else 0
    experience_ready = tenure_days is not None and tenure_days >= step_tenure

    reasons: list[str] = []
    critical_gaps = [
        item.code for item in fit.requirement_evaluations if item.gap_type == "critical_gap"
    ]
    uncertainties = [item.code for item in fit.uncertainties]
    required_gaps = [
        item.code
        for item in fit.requirement_evaluations
        if item.gap_type in {"required_gap", "target_gap"}
    ]

    if critical_gaps:
        status = "CRITICAL_GAPS"
        reasons.append("target_position_has_critical_gaps")
    elif uncertainties or fit.qualification_status == "INSUFFICIENT_DATA":
        status = "NEEDS_EVIDENCE"
        reasons.append("insufficient_evidence_uncertainty")
    elif fit.qualification_status == "QUALIFIED":
        if not experience_ready:
            status = "DEVELOPMENT_NEEDED"
            reasons.append(f"tenure_under_minimum_days:{step_tenure}")
        else:
            status = "READY"
            reasons.append("qualified_and_experience_met")
    elif required_gaps:
        status = "DEVELOPMENT_NEEDED"
        reasons.append("required_competency_gaps")
    else:
        status = "NEAR_READY"
        reasons.append("meets_minimum_with_target_gaps")

    if experience["assessments_count"] == 0:
        reasons.append("no_assessment_history")

    payload = {
        "employee_id": employee_id,
        "target_position": {
            "id": position.id,
            "code": position.code,
            "name": position.name,
        },
        "position_fit": {
            "known_fit_score": fit.known_fit_score,
            "fit_confidence": fit.fit_confidence,
            "required_coverage": fit.required_coverage,
            "qualification_status": fit.qualification_status,
            "fit_status": fit.fit_status,
        },
        "critical_gaps": critical_gaps,
        "uncertainties": uncertainties,
        "required_gaps": required_gaps,
        "experience": experience,
        "experience_readiness": {
            "tenure_days": tenure_days,
            "minimum_tenure_days": step_tenure,
            "met": experience_ready,
        },
        "readiness_status": status,
        "reasons": reasons,
    }
    payload["inputs_hash"] = hashlib.sha256(
        json.dumps(
            {
                "employee_id": employee_id,
                "target_position_definition_id": position_definition_id,
                "fit_inputs_hash": fit.inputs_hash,
                "experience": experience,
                "tenure_days": tenure_days,
                "career_analysis_version": CAREER_ANALYSIS_VERSION,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    payload["career_analysis_version"] = CAREER_ANALYSIS_VERSION
    return payload


# ---------------------------------------------------------------------------
# Development Needs
# ---------------------------------------------------------------------------


def needs_from_fit(fit) -> list[dict]:
    """把 Fit 的 Requirement Evaluations 转成 Development Need（Unknown≠Weak）。"""
    needs = []
    for evaluation in fit.requirement_evaluations:
        if evaluation.evaluation_status == EvaluationStatus.MEETS_TARGET:
            continue  # 已达标=强项，不是发展需要
        current = evaluation.employee_score
        minimum = evaluation.minimum_score
        target = evaluation.target_score
        confidence = evaluation.employee_confidence
        if evaluation.evaluation_status in {
            EvaluationStatus.UNRATED,
            EvaluationStatus.INSUFFICIENT_CONFIDENCE,
        }:
            need_type = DevelopmentNeedType.evidence_gap.value
            gap_to_min = None
            gap_to_target = None
        elif evaluation.evaluation_status == EvaluationStatus.BELOW_MINIMUM:
            need_type = DevelopmentNeedType.competency_gap.value
            gap_to_min = (current or 0) - (minimum or 0)
            gap_to_target = (current or 0) - (target or 0) if target is not None else None
        else:  # MEETS_MINIMUM（target gap）
            need_type = DevelopmentNeedType.target_gap.value
            gap_to_min = (current or 0) - (minimum or 0)
            gap_to_target = (current or 0) - (target or 0) if target is not None else None
        needs.append(
            {
                "competency_definition_id": evaluation.competency_definition_id,
                "code": evaluation.code,
                "name": evaluation.name,
                "need_type": need_type,
                "current_score": current,
                "current_confidence": confidence,
                "minimum_required": minimum,
                "target_required": target,
                "gap_to_minimum": gap_to_min,
                "gap_to_target": gap_to_target,
                "critical": evaluation.critical,
                "source": "position_fit",
            }
        )
    return needs


# ---------------------------------------------------------------------------
# Development Plan
# ---------------------------------------------------------------------------


def create_plan(
    db: Session,
    employee_id: int,
    target_position_definition_id: int | None,
    *,
    user_id: int | None = None,
    title: str | None = None,
) -> DevelopmentPlan:
    employee = _employee(db, employee_id)
    fit = None
    source_fit_hash = ""
    if target_position_definition_id is not None:
        _ = _position(db, target_position_definition_id, employee.company_id)
        fit = fit_service.calculate_fit(
            db, employee_id=employee_id, position_definition_id=target_position_definition_id
        )
        source_fit_hash = fit.inputs_hash
    plan = DevelopmentPlan(
        company_id=employee.company_id,
        employee_id=employee_id,
        target_position_definition_id=target_position_definition_id,
        status=DevelopmentPlanStatus.draft.value,
        title=title or _plan_title(db, target_position_definition_id),
        created_by_user_id=user_id,
        source_fit_hash=source_fit_hash,
    )
    db.add(plan)
    db.flush()
    if fit is not None:
        for index, need in enumerate(needs_from_fit(fit)):
            if need["need_type"] == DevelopmentNeedType.target_gap.value and not need["critical"]:
                continue  # preferred/target 缺口进计划（critical/evidence/competency 优先）
            db.add(_item_from_need(plan.id, need, index))
        db.flush()
    record_event(
        db,
        employee,
        CareerEventType.development_plan_created.value,
        position_definition_id=target_position_definition_id,
        actor_user_id=user_id,
        source_id=plan.id,
        metadata={"plan_id": plan.id},
    )
    db.flush()
    return plan


def _plan_title(db: Session, target_position_definition_id: int | None) -> str:
    if target_position_definition_id is None:
        return "发展计划：通用"
    target = db.get(PositionDefinition, target_position_definition_id)
    return f"发展计划：{target.name if target else '目标职位'}"


def _item_from_need(plan_id: int, need: dict, index: int) -> DevelopmentPlanItem:
    actions = {
        "competency_gap": ["learning", "project_assignment", "practice_task", "peer_review"],
        "target_gap": ["practice_task", "project_assignment"],
        "evidence_gap": ["assessment", "project_opportunity", "peer_review", "presentation"],
    }.get(need["need_type"], ["learning", "assessment"])
    return DevelopmentPlanItem(
        plan_id=plan_id,
        competency_definition_id=need["competency_definition_id"],
        need_type=need["need_type"],
        objective=f"提升 {need['name']}：目标 {need['target_required'] or '已定义'}",
        target_score=need["target_required"],
        target_confidence=0.5 if need["need_type"] == "evidence_gap" else None,
        priority=index,
        status=DevelopmentItemStatus.planned.value,
        recommended_actions=actions,
        evidence_requirements=(
            ["assessment_run", "project_evidence", "peer_review"]
            if need["need_type"] == "evidence_gap"
            else ["assessment_run"]
        ),
    )


def plan_items(db: Session, plan_id: int) -> list[DevelopmentPlanItem]:
    return list(
        db.scalars(
            select(DevelopmentPlanItem)
            .where(DevelopmentPlanItem.plan_id == plan_id)
            .order_by(DevelopmentPlanItem.priority, DevelopmentPlanItem.id)
        )
    )


def activate_plan(
    db: Session, plan: DevelopmentPlan, *, user_id: int | None = None
) -> DevelopmentPlan:
    if plan.status != DevelopmentPlanStatus.draft.value:
        raise CareerError("只有 DRAFT 计划可以激活")
    plan.status = DevelopmentPlanStatus.active.value
    plan.started_at = datetime.now(UTC)
    plan.created_by_user_id = plan.created_by_user_id or user_id
    db.flush()
    return plan


def update_plan(
    db: Session, plan: DevelopmentPlan, *, title: str | None = None, description: str | None = None
) -> DevelopmentPlan:
    if title is not None:
        plan.title = title
    if description is not None:
        plan.description = description
    db.flush()
    return plan


def reconcile_employee_plans(db: Session, employee_id: int) -> int:
    """按真实 Competency/Evidence 重判进度（Assessment 完成后调用，不靠手点）。"""
    updated = 0
    plans = list(
        db.scalars(
            select(DevelopmentPlan).where(
                DevelopmentPlan.employee_id == employee_id,
                DevelopmentPlan.status == DevelopmentPlanStatus.active.value,
            )
        )
    )
    for plan in plans:
        items = plan_items(db, plan.id)
        rows = {
            row.competency_definition_id: row
            for row in db.scalars(
                select(EmployeeCompetency).where(
                    # R1.3：能力行按 person 口径读（单一入口换算，带旧口径回落）
                    person_repo.read_criterion(
                        db,
                        employee_id,
                        EmployeeCompetency.person_id,
                        EmployeeCompetency.employee_id,
                    ),
                    EmployeeCompetency.competency_definition_id.in_(
                        [item.competency_definition_id for item in items]
                    ),
                )
            )
        }
        completed_all = True
        for item in items:
            row = rows.get(item.competency_definition_id)
            score = row.score if row else None
            confidence = row.confidence if row else None
            target_score = item.target_score
            target_confidence = item.target_confidence
            if row is not None:
                item.progress_metadata = {
                    **(item.progress_metadata or {}),
                    "last_score": score,
                    "last_confidence": confidence,
                    "evidence_count": row.evidence_count,
                    "last_assessed_at": row.last_assessed_at,
                }
            if item.status == DevelopmentItemStatus.cancelled.value:
                continue
            if item.need_type == DevelopmentNeedType.evidence_gap.value:
                completed = (
                    row is not None
                    and confidence is not None
                    and (target_confidence is None or confidence >= target_confidence)
                )
            else:
                completed = (
                    row is not None
                    and score is not None
                    and (target_score is None or score >= target_score)
                    and (
                        target_confidence is None
                        or (confidence is not None and confidence >= target_confidence)
                    )
                )
            if completed:
                item.status = DevelopmentItemStatus.completed.value
                updated += 1
            elif row is not None and score is not None:
                item.status = (
                    DevelopmentItemStatus.waiting_evidence.value
                    if (
                        item.need_type == DevelopmentNeedType.evidence_gap.value
                        or (confidence or 0) < (target_confidence or 0)
                    )
                    else DevelopmentItemStatus.in_progress.value
                )
            else:
                item.status = DevelopmentItemStatus.planned.value
            if item.status != DevelopmentItemStatus.completed.value:
                completed_all = False
        if completed_all and items:
            plan.status = DevelopmentPlanStatus.completed.value
            plan.completed_at = datetime.now(UTC)
            updated += 1
            record_event(
                db,
                _employee(db, employee_id),
                CareerEventType.development_plan_completed.value,
                position_definition_id=plan.target_position_definition_id,
                source_id=plan.id,
                metadata={"plan_id": plan.id},
            )
    db.flush()
    return updated


# ---------------------------------------------------------------------------
# Learning Priority（显式、幂等）
# ---------------------------------------------------------------------------


def create_learning_priority_for_item(db: Session, item: DevelopmentPlanItem) -> LearningPriority:
    plan = db.get(DevelopmentPlan, item.plan_id)
    if plan is None:
        raise CareerError("plan not found")
    definition = db.get(CompetencyDefinition, item.competency_definition_id)
    topic = definition.name if definition else item.objective
    # R1.1：读（幂等判定）与写都走 knowledge repo —— 口径切换/双写封在那层
    existing = knowledge_repo.get_priority_by_topic(db, plan.employee_id, topic)
    if existing is not None:
        return existing  # 幂等：重复点击不重复创建
    return knowledge_repo.create_learning_priority(
        db,
        employee_id=plan.employee_id,
        topic=topic,
        score=60,
        reason=item.objective,
        source="development_plan",
    )


# ---------------------------------------------------------------------------
# Promote / Transfer（复用任职工作流 + 审计 + 低匹配 Warning）
# ---------------------------------------------------------------------------


def _transition_kind(from_def: PositionDefinition | None, to_def: PositionDefinition) -> str:
    if from_def is None:
        return CareerEventType.position_assigned.value
    return (
        CareerEventType.promoted.value
        if to_def.level > (from_def.level or 0)
        else CareerEventType.transferred.value
    )


def change_position(
    db: Session,
    employee: Employee,
    target_definition_id: int,
    slot_id: int,
    *,
    reason: str,
    actor_user_id: int | None = None,
) -> dict:
    """Human 触发的职位变更（Promote/Transfer/调岗共用）：复用 assign_position →
    记录 CareerEvent → 返回 readiness/warnings。低匹配只 Warning，不拦。"""
    _ = _position(db, target_definition_id, employee.company_id)
    current = position_repo.employee_current_position(db, int(employee.id))
    readiness = readiness_for(db, int(employee.id), target_definition_id)
    target_def = db.get(PositionDefinition, target_definition_id)
    warnings: list[str] = []
    if readiness["readiness_status"] in {"CRITICAL_GAPS", "NEEDS_EVIDENCE"}:
        warnings.append(readiness["readiness_status"])
    assignment = position_service.assign_position(
        db,
        employee,
        AssignmentIn(
            slot_id=slot_id,
            reason=reason,
            kind="transfer",
            manager_employee_id=None,
        ),
    )
    event_type = _transition_kind(
        db.get(PositionDefinition, current.definition_id) if current else None,
        target_def,
    )
    record_event(
        db,
        employee,
        event_type,
        position_definition_id=target_definition_id,
        slot_id=slot_id,
        from_position_id=current.definition_id if current else None,
        to_position_id=target_definition_id,
        actor_user_id=actor_user_id,
        reason=reason,
        metadata={"readiness_hash": readiness["inputs_hash"]},
    )
    db.commit()
    return {
        "assignment_id": assignment.id,
        "event_type": event_type,
        "readiness": readiness,
        "warnings": warnings,
    }


def promote(
    db: Session,
    employee: Employee,
    target_definition_id: int,
    slot_id: int,
    *,
    reason: str,
    actor_user_id: int | None = None,
) -> dict:
    return change_position(
        db,
        employee,
        target_definition_id,
        slot_id,
        reason=reason,
        actor_user_id=actor_user_id,
    )


# ---------------------------------------------------------------------------
# Career Overview / Timeline
# ---------------------------------------------------------------------------


def career_overview(db: Session, employee_id: int) -> dict:
    employee = _employee(db, employee_id)
    current = position_repo.employee_current_position(db, employee_id)
    next_positions = []
    if current is not None:
        seen: set[int] = set()
        for step in next_steps(db, current.definition_id, employee.company_id):
            if step.to_position_definition_id in seen:
                continue
            seen.add(step.to_position_definition_id)
            readiness = readiness_for(db, employee_id, step.to_position_definition_id, step=step)
            next_positions.append(
                {
                    "target_position": readiness["target_position"],
                    "transition_type": step.transition_type,
                    "readiness_status": readiness["readiness_status"],
                    "position_fit": readiness["position_fit"],
                    "required_gaps": readiness["required_gaps"],
                    "uncertainties": readiness["uncertainties"],
                }
            )
    plans = list(
        db.scalars(
            select(DevelopmentPlan)
            .where(DevelopmentPlan.employee_id == employee_id)
            .order_by(DevelopmentPlan.id.desc())
        )
    )
    timelines = _timeline(db, employee_id)
    return {
        "employee_id": employee_id,
        "current_position": (
            {
                "definition_id": current.definition_id,
                "code": current.code,
                "name": current.name,
                "since": current.since,
            }
            if current
            else None
        ),
        "next_positions": next_positions,
        "plans": [
            {
                "id": plan.id,
                "title": plan.title,
                "status": plan.status,
                "target_position_definition_id": plan.target_position_definition_id,
                "item_count": len(
                    [item for item in plan_items(db, plan.id) if item.status != "cancelled"]
                ),
                "completed_count": len(
                    [item for item in plan_items(db, plan.id) if item.status == "completed"]
                ),
                "created_at": plan.created_at,
            }
            for plan in plans
        ],
        "timeline": timelines,
    }


def _timeline(db: Session, employee_id: int) -> list[dict]:
    events = []
    for event in db.scalars(
        select(CareerEvent)
        .where(CareerEvent.employee_id == employee_id)
        .order_by(CareerEvent.effective_at.desc())
    ):
        events.append(
            {
                "type": event.event_type,
                "at": event.effective_at,
                "title": event.event_type,
                "reason": event.reason,
                "source": "career_event",
            }
        )
    for row in position_repo.career_history(db, employee_id):
        definition = (
            db.get(PositionDefinition, row.position_definition_id)
            if row.position_definition_id
            else None
        )
        events.append(
            {
                "type": "position_assigned" if row.effective_to is None else "position_released",
                "at": row.effective_from,
                "title": definition.name if definition else "—",
                "reason": row.reason,
                "source": "assignment",
            }
        )
    for run in db.scalars(
        select(AssessmentRun)
        .where(
            person_repo.read_criterion(
                db, employee_id, AssessmentRun.person_id, AssessmentRun.employee_id
            )
        )
        .order_by(AssessmentRun.created_at.desc())
    ):
        events.append(
            {
                "type": "assessment_completed",
                "at": run.finished_at or run.created_at,
                "title": f"assessment #{run.id}",
                "reason": run.assessment_type,
                "source": "assessment_run",
            }
        )
    events.sort(key=lambda item: str(item["at"] or ""), reverse=True)
    return events[:100]
