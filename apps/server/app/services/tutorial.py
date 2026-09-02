"""Persistent, declarative tutorial engine driven by real domain state."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.request_context import get_request_identity
from app.models.auth import CompanyMembership, User
from app.models.base import utcnow
from app.models.drive import DriveNode
from app.models.enums import (
    DriveNodeKind,
    EmployeeRole,
    ProjectPhaseStatus,
    ReviewDecision,
    TutorialStatus,
)
from app.models.runtime import RuntimeInstance
from app.repositories import git as git_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project_delivery import TutorialProgressOut, TutorialTemplateOut
from app.tutorials import COMPANY_FOUNDING_TUTORIAL, TUTORIAL_CENTER


def _steps() -> list[dict]:
    return [step for stage in COMPANY_FOUNDING_TUTORIAL["stages"] for step in stage["steps"]]


STEP_ORDER = [step["id"] for step in _steps()]
STEP_BY_ID = {step["id"]: step for step in _steps()}


def definition() -> dict:
    return COMPANY_FOUNDING_TUTORIAL


def center() -> list[dict]:
    return TUTORIAL_CENTER


def get_progress(db: Session) -> TutorialProgressOut:
    user, company = _principal(db)
    progress = delivery_repo.get_tutorial(db, user.id)
    if progress is None:
        progress = delivery_repo.create_tutorial(
            db,
            user_id=user.id,
            company_id=company.id,
            tutorial_id=COMPANY_FOUNDING_TUTORIAL["id"],
            status=TutorialStatus.not_started.value,
            current_stage="welcome",
            current_step=STEP_ORDER[0],
            completed_steps=[],
            skipped_steps=[],
            context={},
        )
        db.commit()
        db.refresh(progress)
    if progress.status == TutorialStatus.active.value:
        _reconcile(db, progress)
        db.commit()
        db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def start(db: Session) -> TutorialProgressOut:
    progress = _row(db)
    progress.status = TutorialStatus.active.value
    progress.started_at = progress.started_at or utcnow()
    progress.completed_at = None
    progress.paused_at = None
    _reconcile(db, progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def pause(db: Session) -> TutorialProgressOut:
    progress = _row(db)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    progress.status = TutorialStatus.paused.value
    progress.paused_at = utcnow()
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def skip(db: Session) -> TutorialProgressOut:
    progress = _row(db)
    progress.status = TutorialStatus.skipped.value
    progress.completed_at = None
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def resume(db: Session) -> TutorialProgressOut:
    return start(db)


def complete_step(db: Session, step: str) -> TutorialProgressOut:
    if step not in STEP_BY_ID:
        raise HTTPException(status_code=404, detail="tutorial step not found")
    progress = _row(db)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    _reconcile(db, progress)
    if step == "company_setup":
        satisfied = True
    else:
        satisfied = _step_satisfied(db, progress, step)
    if not satisfied:
        raise HTTPException(status_code=409, detail="complete the real business action first")
    _mark_completed(progress, step)
    _advance(db, progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def skip_step(db: Session, step: str) -> TutorialProgressOut:
    config = STEP_BY_ID.get(step)
    if config is None:
        raise HTTPException(status_code=404, detail="tutorial step not found")
    if not config["optional"]:
        raise HTTPException(status_code=409, detail="this tutorial step is required")
    progress = _row(db)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    skipped = list(progress.skipped_steps or [])
    if step not in skipped:
        skipped.append(step)
    progress.skipped_steps = skipped
    _advance(db, progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def defer_qa(db: Session) -> TutorialProgressOut:
    return skip_step(db, "hire_qa")


def classic_snake_template() -> TutorialTemplateOut:
    rows = [
        ("游戏区域", "显示游戏区域", "进入页面可见清晰网格"),
        ("方向控制", "方向键控制蛇移动", "四个方向键响应正确"),
        ("随机食物", "随机生成可见食物", "每次食用后出现新食物"),
        ("长度与分数", "食用后增加长度和分数", "长度与分数同步增长"),
        ("碰撞结束", "碰撞边界或自身后结束", "显示游戏结束状态"),
        ("重新开始", "支持重新开始", "重置蛇、食物和分数"),
        ("暂停", "支持暂停和继续", "暂停时游戏状态不变化"),
    ]
    return TutorialTemplateOut(
        name="Classic Snake",
        intake={
            "name": "Classic Snake",
            "code": "SNAKE",
            "priority": "high",
            "customer": "Eidolon Tutorial",
            "background": "用于验证 Eidolon AI Software Studio 完整软件项目生命周期。",
            "objectives": ["交付一个可快速运行、可验收的经典 Web 贪吃蛇小游戏"],
            "requirements": [
                {
                    "code": f"REQ-{index:03d}",
                    "title": title,
                    "description": description,
                    "priority": "must",
                    "acceptance_criteria": acceptance,
                }
                for index, (title, description, acceptance) in enumerate(rows, start=1)
            ],
            "technical_requirements": ["React", "Vite", "TypeScript", "No Backend"],
            "constraints": ["支持离线部署"],
            "deliverables": [
                "Source Code",
                "Build Package",
                "Requirements Report",
                "Design Report",
                "Internal Test Report",
                "Acceptance Test Report",
                "User Manual",
                "Deployment Manual",
                "Review Presentations",
            ],
            "review_configuration": {
                "requirements_review": True,
                "design_review": True,
                "acceptance_review": True,
                "additional_reviews": [],
            },
            "tutorial_accelerated": True,
        },
    )


def _row(db: Session):
    get_progress(db)
    user, _ = _principal(db)
    return delivery_repo.get_tutorial(db, user.id)


def _principal(db: Session):
    identity = get_request_identity()
    if identity is not None:
        user = db.get(User, identity.user_id)
        company = org_repo.get_default_company(db)
        if user is not None and company is not None:
            return user, company
    if settings.auth_required:
        raise HTTPException(status_code=401, detail="authentication required")
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    user = db.scalar(select(User).where(User.email == "legacy@eidolon.local"))
    if user is None:
        user = User(
            email="legacy@eidolon.local",
            email_verified=True,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$legacy$legacy",
            display_name="Legacy Operator",
            onboarding_status="completed",
        )
        db.add(user)
        db.flush()
        db.add(CompanyMembership(user_id=user.id, company_id=company.id, role="OWNER"))
        db.commit()
        db.refresh(user)
    return user, company


def _reconcile(db: Session, progress) -> None:
    context = dict(progress.context or {})
    company = org_repo.get_default_company(db)
    if company is None:
        return
    employees = org_repo.list_employees(db, company.id)
    ceo = next(
        (employee for employee in employees if employee.role == EmployeeRole.ceo.value), None
    )
    engineer = next(
        (employee for employee in employees if employee.role == EmployeeRole.engineer.value), None
    )
    qa = next(
        (employee for employee in employees if employee.role == EmployeeRole.qa_engineer.value),
        None,
    )
    if ceo:
        context["ceo_employee_id"] = ceo.id
    if engineer:
        context["engineer_employee_id"] = engineer.id
    if qa:
        context["qa_employee_id"] = qa.id
    projects = project_repo.list_projects(db, company.id)
    if projects and not context.get("project_id"):
        context["project_id"] = projects[0].id
    progress.context = context
    # Explanatory company_setup stays explicit; every other completion is observed.
    for step in STEP_ORDER[1:]:
        if _step_satisfied(db, progress, step):
            _mark_completed(progress, step)
    _advance(db, progress)


def _step_satisfied(db: Session, progress, step: str) -> bool:
    company = org_repo.get_default_company(db)
    if company is None:
        return False
    employees = org_repo.list_employees(db, company.id)
    ceo = next(
        (employee for employee in employees if employee.role == EmployeeRole.ceo.value), None
    )
    if step == "hire_ceo":
        return ceo is not None
    if step == "configure_ceo":
        return (
            ceo is not None
            and db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == ceo.id))
            is not None
        )
    if step == "cloud_docs":
        return (
            db.scalar(
                select(DriveNode).where(
                    DriveNode.company_id == company.id,
                    DriveNode.kind == DriveNodeKind.document.value,
                )
            )
            is not None
        )
    if step == "git_setup":
        return any(connection.enabled for connection in git_repo.list_connections(db))
    if step == "hire_engineer":
        return any(employee.role == EmployeeRole.engineer.value for employee in employees)
    if step == "hire_qa":
        return any(employee.role == EmployeeRole.qa_engineer.value for employee in employees)
    project_id = (progress.context or {}).get("project_id")
    project = project_repo.get_project(db, project_id) if project_id else None
    if step == "create_project":
        return project is not None and project.company_id == company.id
    if project is None or project.company_id != company.id:
        return False
    reviews = {row.review_type: row.decision for row in delivery_repo.list_reviews(db, project.id)}
    approved = {ReviewDecision.approved.value, ReviewDecision.conditionally_approved.value}
    if step == "requirements_review":
        return reviews.get("requirements_review") in approved
    if step == "design_review":
        return reviews.get("design_review") in approved
    if step == "acceptance_review":
        return reviews.get("acceptance_review") in approved
    phases = {row.phase_type: row.status for row in delivery_repo.list_phases(db, project.id)}
    if step == "development":
        return phases.get("development") == ProjectPhaseStatus.completed.value
    if step == "testing":
        return all(
            phases.get(key) == ProjectPhaseStatus.completed.value
            for key in ("internal_testing", "user_acceptance_testing")
        )
    if step == "delivery":
        return project.status == "completed" and bool(
            delivery_repo.list_delivery_packages(db, project.id)
        )
    return False


def _mark_completed(progress, step: str) -> None:
    completed = list(progress.completed_steps or [])
    if step not in completed:
        completed.append(step)
        progress.completed_steps = completed


def _advance(db: Session, progress) -> None:
    finished = set(progress.completed_steps or []) | set(progress.skipped_steps or [])
    for stage in COMPANY_FOUNDING_TUTORIAL["stages"]:
        for step in stage["steps"]:
            if step["id"] not in finished:
                progress.current_stage = stage["id"]
                progress.current_step = step["id"]
                return
    progress.current_stage = "operating"
    progress.current_step = "completed"
    progress.status = TutorialStatus.completed.value
    progress.completed_at = utcnow()
    company = org_repo.get_default_company(db)
    user = db.get(User, progress.user_id)
    if company is not None:
        company.stage = "OPERATING"
    if user is not None:
        user.onboarding_status = "completed"
