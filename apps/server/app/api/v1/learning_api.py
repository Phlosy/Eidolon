"""Learning API（P11 §54）—— 手工触发 + 政策 + 预算 + 调度触发。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.learning import LearningSession
from app.models.organization import Company, Employee
from app.repositories import persons as person_repo
from app.repositories import runtimes as runtime_repo
from app.schemas.learning import (
    CompanyLearningPolicyPatchIn,
    CompanyLearningUsageOut,
    EmployeeLearningPolicyOut,
    EmployeeLearningPolicyPatchIn,
    LearningPolicyOut,
    LearningSessionOut,
    LearningSessionStartIn,
)
from app.services import learning as learning_service
from app.services.learning import LearningError

router = APIRouter(tags=["learning"])


def _employee_or_404(db: Session, employee_id: int, company_id: int | None) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if company_id is not None and employee.company_id != company_id:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


def _company(db: Session, employee: Employee) -> Company:
    company = db.get(Company, employee.company_id)
    if company is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail="company not found")
    return company


def _session_out(session: LearningSession) -> dict:
    return {
        "id": session.id,
        "employee_id": session.employee_id,
        "company_id": session.company_id,
        "topic": session.topic,
        "reason": session.reason,
        "source_type": session.source_type,
        "source_id": session.source_id,
        "status": session.status,
        "learning_mode": session.learning_mode,
        "priority": session.priority,
        "budget_tokens": session.budget_tokens,
        "budget_cost": session.budget_cost,
        "budget_minutes": session.budget_minutes,
        "tokens_used": session.tokens_used,
        "cost_used": session.cost_used,
        "duration_minutes": session.duration_minutes,
        "runtime_type": session.runtime_type,
        "provider_name": session.provider_name,
        "model_name": session.model_name,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "cancelled_at": session.cancelled_at,
        "error": session.error,
        "summary": session.summary,
        "outputs": (session.metadata_json or {}).get("outputs", {}),
    }


@router.get("/company/learning-policy", response_model=LearningPolicyOut)
def get_company_learning_policy(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        company = (
            session.get(Company, company_id)
            if company_id
            else session.scalars(__import__("sqlalchemy").select(Company)).first()
        )
        policy = learning_service.company_learning_policy(session, company)
    return dict(vars(policy))


@router.patch("/company/learning-policy", response_model=LearningPolicyOut)
def patch_company_learning_policy(
    payload: CompanyLearningPolicyPatchIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    company = (
        db.get(Company, company_id)
        if company_id
        else (db.scalars(__import__("sqlalchemy").select(Company)).first())
    )
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    settings_data = dict(company.settings or {})
    policy = dict(learning_service.company_learning_policy(db, company).__dict__)
    policy.update({k: v for k, v in payload.model_dump().items() if v is not None})
    settings_data["learning_policy"] = {
        k: policy[k]
        for k in policy
        if k in dict(learning_service.DEFAULT_COMPANY_LEARNING_POLICY.__dict__)
    }
    company.settings = settings_data
    db.commit()
    return policy


@router.get("/employees/{employee_id}/learning-policy", response_model=EmployeeLearningPolicyOut)
def get_employee_learning_policy(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    employee = _employee_or_404(db, employee_id, company_id)
    return learning_service.employee_learning_policy(db, employee, _company(db, employee))


@router.patch("/employees/{employee_id}/learning-policy", response_model=EmployeeLearningPolicyOut)
def patch_employee_learning_policy(
    employee_id: int,
    payload: EmployeeLearningPolicyPatchIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    employee = _employee_or_404(db, employee_id, company_id)
    # R1.1：ensure_brain 是 get-or-create 唯一入口（读口径 person_id + 双写 + traits 初始化）
    brain = runtime_repo.ensure_brain(db, employee_id)
    override = dict(brain.learning_policy or {})
    if payload.enabled is not None:
        override["enabled"] = payload.enabled
    if payload.daily_token_budget is not None:
        override["daily_token_budget"] = payload.daily_token_budget
    if payload.idle_delay_minutes is not None:
        override["idle_delay_minutes"] = payload.idle_delay_minutes
    brain.learning_policy = override
    db.commit()
    return learning_service.employee_learning_policy(db, employee, _company(db, employee))


@router.get("/employees/{employee_id}/learning-usage", response_model=CompanyLearningUsageOut)
def get_learning_usage(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    from datetime import date

    employee = _employee_or_404(db, employee_id, company_id)
    company = _company(db, employee)
    policy = learning_service.company_learning_policy(db, company)
    usage = learning_service._usage(db, company, date.today())
    return {
        "today_tokens": usage["tokens"],
        "today_cost": usage["cost"],
        "today_sessions": usage["sessions"],
        "budget_remaining_tokens": max(0, policy.daily_token_budget - usage["tokens"]),
        "budget_remaining_cost": max(0.0, policy.daily_cost_budget - usage["cost"]),
    }


@router.get("/employees/{employee_id}/learning-sessions", response_model=list[LearningSessionOut])
def employee_learning_sessions(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    _employee_or_404(db, employee_id, company_id)
    sessions = list(
        db.query(LearningSession)
        .filter(
            person_repo.read_criterion(
                db, employee_id, LearningSession.person_id, LearningSession.employee_id
            )
        )
        .order_by(LearningSession.id.desc())
        .limit(50)
        .all()
    )
    return [_session_out(session) for session in sessions]


@router.get("/learning-sessions/{session_id}", response_model=LearningSessionOut)
def get_learning_session(
    session_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    session = db.get(LearningSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="learning session not found")
    if company_id is not None and session.company_id != company_id:
        raise HTTPException(status_code=404, detail="learning session not found")
    return _session_out(session)


@router.post(
    "/employees/{employee_id}/learning-sessions", response_model=LearningSessionOut, status_code=201
)
def start_learning(
    employee_id: int,
    payload: LearningSessionStartIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    _employee_or_404(db, employee_id, company_id)
    try:
        session = learning_service.create_session(
            db,
            employee_id,
            payload.topic,
            source_type=payload.source_type,
            learning_mode=payload.learning_mode,
            reason=payload.reason,
            minutes=payload.minutes,
        )
        session = learning_service.run_session(db, session)
    except LearningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_out(session)


@router.post("/learning-sessions/{session_id}/cancel", response_model=LearningSessionOut)
def cancel_learning(
    session_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    from datetime import UTC, datetime

    from app.models.enums import LearningSessionStatus

    session = db.get(LearningSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="learning session not found")
    if company_id is not None and session.company_id != company_id:
        raise HTTPException(status_code=404, detail="learning session not found")
    if session.status in {LearningSessionStatus.running.value, LearningSessionStatus.planned.value}:
        session.status = LearningSessionStatus.cancelled.value
        session.cancelled_at = datetime.now(UTC)
        db.commit()
    return _session_out(session)


class ScheduleRunIn(BaseModel):
    pass


@router.post("/company/learning/run")
def trigger_autonomous_learning(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """手动触发一轮自主学习调度（同 Scheduler 逻辑；默认全局开关 off 时返回 0）。"""
    created = learning_service.scheduler_trigger(db)
    return {"created_sessions": created}
