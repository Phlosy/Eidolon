"""Career API（P10 §50）—— 决策支持；promote/transfer 由 Human 显式触发并复用任职工作流。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.career import DevelopmentPlan
from app.models.competency import CompetencyDefinition
from app.models.organization import Employee
from app.schemas.career import (
    CareerOverviewOut,
    CareerReadinessOut,
    CreatePlanIn,
    DevelopmentNeedOut,
    DevelopmentPlanItemOut,
    DevelopmentPlanOut,
    PatchPlanIn,
    PositionChangeIn,
    PositionChangeOut,
)
from app.services import career as career_service
from app.services.career import CareerError

router = APIRouter(tags=["career"])


@router.get("/employees/{employee_id}/behavior-policy")
def employee_behavior_policy(
    employee_id: int,
    task_type: str | None = None,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """P11：行为策略解释（Trait Snapshot + advisory 数值 + reasons；缺省中性上下文）。"""
    from app.brain.resolver import explain_behavior
    from app.brain.trait_policies import BehaviorContext
    from app.models.runtime import EmployeeBrain

    _employee_or_404(db, employee_id, company_id)
    brain = db.scalar(select(EmployeeBrain).where(EmployeeBrain.employee_id == employee_id))
    context = BehaviorContext(task_type=task_type or "general")
    return explain_behavior(brain, context)


def _employee_or_404(db: Session, employee_id: int, company_id: int | None) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if company_id is not None and employee.company_id != company_id:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


def _career_error(exc: CareerError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _plan_out(db: Session, plan: DevelopmentPlan) -> dict:
    items = career_service.plan_items(db, plan.id)
    definition_ids = {item.competency_definition_id for item in items}
    definitions = {
        definition.id: definition
        for definition in db.query(CompetencyDefinition)
        .filter(CompetencyDefinition.id.in_(definition_ids))
        .all()
    }
    target = (
        db.get(
            __import__("app.models.position", fromlist=["PositionDefinition"]).PositionDefinition,
            plan.target_position_definition_id,
        )
        if plan.target_position_definition_id
        else None
    )
    return {
        "id": plan.id,
        "employee_id": plan.employee_id,
        "target_position_definition_id": plan.target_position_definition_id,
        "target_position": (
            {"id": target.id, "code": target.code, "name": target.name} if target else None
        ),
        "status": plan.status,
        "title": plan.title,
        "description": plan.description,
        "source_fit_hash": plan.source_fit_hash,
        "started_at": plan.started_at,
        "completed_at": plan.completed_at,
        "created_at": plan.created_at,
        "items": [
            {
                "id": item.id,
                "plan_id": item.plan_id,
                "competency_definition_id": item.competency_definition_id,
                "code": definitions[item.competency_definition_id].code
                if item.competency_definition_id in definitions
                else "",
                "name": definitions[item.competency_definition_id].name
                if item.competency_definition_id in definitions
                else "",
                "need_type": item.need_type,
                "objective": item.objective,
                "target_score": item.target_score,
                "target_confidence": item.target_confidence,
                "priority": item.priority,
                "status": item.status,
                "recommended_actions": list(item.recommended_actions or []),
                "progress_metadata": item.progress_metadata or {},
            }
            for item in items
        ],
    }


@router.get("/employees/{employee_id}/career", response_model=CareerOverviewOut)
def employee_career(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    _employee_or_404(db, employee_id, company_id)
    return career_service.career_overview(db, employee_id)


@router.get(
    "/employees/{employee_id}/career-readiness/{position_definition_id}",
    response_model=CareerReadinessOut,
)
def employee_readiness(
    employee_id: int,
    position_definition_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    _employee_or_404(db, employee_id, company_id)
    try:
        return career_service.readiness_for(db, employee_id, position_definition_id)
    except CareerError as exc:
        raise _career_error(exc) from exc


@router.get(
    "/employees/{employee_id}/career-needs/{position_definition_id}",
    response_model=list[DevelopmentNeedOut],
)
def employee_needs(
    employee_id: int,
    position_definition_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    from app.talent.fit import service as fit_service

    _employee_or_404(db, employee_id, company_id)
    try:
        fit = fit_service.calculate_fit(
            db,
            employee_id=employee_id,
            position_definition_id=position_definition_id,
        )
    except fit_service.FitDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return career_service.needs_from_fit(fit)


@router.get("/employees/{employee_id}/development-plans", response_model=list[DevelopmentPlanOut])
def employee_plans(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    _employee_or_404(db, employee_id, company_id)
    plans = list(
        db.query(DevelopmentPlan)
        .filter(DevelopmentPlan.employee_id == employee_id)
        .order_by(DevelopmentPlan.id.desc())
        .all()
    )
    return [_plan_out(db, plan) for plan in plans]


@router.post(
    "/employees/{employee_id}/development-plans", response_model=DevelopmentPlanOut, status_code=201
)
def create_plan(
    employee_id: int,
    payload: CreatePlanIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    _employee_or_404(db, employee_id, company_id)
    try:
        plan = career_service.create_plan(
            db, employee_id, payload.target_position_definition_id, title=payload.title
        )
    except CareerError as exc:
        raise _career_error(exc) from exc
    db.commit()
    return _plan_out(db, plan)


@router.patch("/development-plans/{plan_id}", response_model=DevelopmentPlanOut)
def patch_plan(
    plan_id: int,
    payload: PatchPlanIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    plan = db.get(DevelopmentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    _employee_or_404(db, plan.employee_id, company_id)
    career_service.update_plan(db, plan, title=payload.title, description=payload.description)
    db.commit()
    return _plan_out(db, plan)


@router.post("/development-plans/{plan_id}/activate", response_model=DevelopmentPlanOut)
def activate_plan(
    plan_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    plan = db.get(DevelopmentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    _employee_or_404(db, plan.employee_id, company_id)
    try:
        career_service.activate_plan(db, plan)
    except CareerError as exc:
        raise _career_error(exc) from exc
    db.commit()
    return _plan_out(db, plan)


@router.post(
    "/development-plan-items/{item_id}/create-learning-priority",
    response_model=DevelopmentPlanItemOut,
)
def create_learning_priority(
    item_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(
        __import__("app.models.career", fromlist=["DevelopmentPlanItem"]).DevelopmentPlanItem,
        item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="plan item not found")
    plan = db.get(DevelopmentPlan, item.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    _employee_or_404(db, plan.employee_id, company_id)
    career_service.create_learning_priority_for_item(db, item)
    db.commit()
    definition = db.get(CompetencyDefinition, item.competency_definition_id)
    return {
        "id": item.id,
        "plan_id": item.plan_id,
        "competency_definition_id": item.competency_definition_id,
        "code": definition.code if definition else "",
        "name": definition.name if definition else "",
        "need_type": item.need_type,
        "objective": item.objective,
        "target_score": item.target_score,
        "target_confidence": item.target_confidence,
        "priority": item.priority,
        "status": item.status,
        "recommended_actions": list(item.recommended_actions or []),
        "progress_metadata": item.progress_metadata or {},
    }


@router.post("/employees/{employee_id}/career/promote", response_model=PositionChangeOut)
def promote_employee(
    employee_id: int,
    payload: PositionChangeIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """Human 触发的晋升：复用任职工作流 + CareerEvent；低匹配只 Warning。"""
    employee = _employee_or_404(db, employee_id, company_id)
    try:
        return career_service.change_position(
            db,
            employee,
            payload.target_definition_id,
            payload.slot_id,
            reason=payload.reason,
        )
    except CareerError as exc:
        raise _career_error(exc) from exc


@router.post("/employees/{employee_id}/career/transfer", response_model=PositionChangeOut)
def transfer_employee_career(
    employee_id: int,
    payload: PositionChangeIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    employee = _employee_or_404(db, employee_id, company_id)
    try:
        return career_service.change_position(
            db,
            employee,
            payload.target_definition_id,
            payload.slot_id,
            reason=payload.reason,
        )
    except CareerError as exc:
        raise _career_error(exc) from exc
