"""Persistent guided workflow over real company, employee and project state."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.enums import EmployeeRole, ReviewDecision, TutorialStatus
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project_delivery import TutorialProgressOut, TutorialTemplateOut

STEP_ORDER = [
    "company_setup",
    "hire_ceo",
    "configure_ceo",
    "hire_engineer",
    "hire_qa",
    "create_project",
    "requirements_review",
    "design_review",
    "delivery",
]


def get_progress(db: Session) -> TutorialProgressOut:
    company = _company(db)
    progress = delivery_repo.get_tutorial(db, company.id)
    if progress is None:
        progress = delivery_repo.create_tutorial(
            db,
            company_id=company.id,
            status=TutorialStatus.not_started.value,
            current_step="company_setup",
            completed_steps=[],
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
    _reconcile(db, progress)
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
    if step not in STEP_ORDER:
        raise HTTPException(status_code=404, detail="tutorial step not found")
    progress = _row(db)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    _reconcile(db, progress)
    if not _step_satisfied(db, progress, step):
        raise HTTPException(status_code=409, detail="complete the real business action first")
    completed = list(progress.completed_steps or [])
    if step not in completed:
        completed.append(step)
    progress.completed_steps = completed
    _advance(progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def defer_qa(db: Session) -> TutorialProgressOut:
    progress = _row(db)
    context = dict(progress.context or {})
    context["qa_deferred"] = True
    progress.context = context
    completed = list(progress.completed_steps or [])
    if "hire_qa" not in completed:
        completed.append("hire_qa")
    progress.completed_steps = completed
    _advance(progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def classic_snake_template() -> TutorialTemplateOut:
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
                for index, (title, description, acceptance) in enumerate(
                    [
                        ("游戏区域", "显示游戏区域", "进入页面可见清晰网格"),
                        ("方向控制", "方向键控制蛇移动", "四个方向键响应正确"),
                        ("随机食物", "随机生成可见食物", "每次食用后出现新食物"),
                        ("长度与分数", "食用后增加长度和分数", "长度与分数同步增长"),
                        ("碰撞结束", "碰撞边界或自身后结束", "显示游戏结束状态"),
                        ("重新开始", "支持重新开始", "重置蛇、食物和分数"),
                        ("暂停", "支持暂停和继续", "暂停时游戏状态不变化"),
                    ],
                    start=1,
                )
            ],
            "technical_requirements": [
                "React",
                "Vite",
                "TypeScript",
                "Chrome / Edge / Safari",
                "启动时间 < 3 seconds",
            ],
            "constraints": ["无需 Backend", "支持离线部署"],
            "deliverables": [
                "Source Code",
                "Production Build",
                "Requirements Report",
                "Design Document",
                "Test Report",
                "Acceptance Report",
                "User Manual",
                "Deployment Manual",
                "Review PPTs",
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
    return delivery_repo.get_tutorial(db, _company(db).id)


def _company(db: Session):
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    return company


def _reconcile(db: Session, progress) -> None:
    completed = list(progress.completed_steps or [])
    context = dict(progress.context or {})
    company = _company(db)
    employees = org_repo.list_employees(db, company.id)
    ceo = next((e for e in employees if e.role == EmployeeRole.ceo.value), None)
    engineer = next((e for e in employees if e.role == EmployeeRole.engineer.value), None)
    qa = next((e for e in employees if e.role == EmployeeRole.qa_engineer.value), None)
    if "company_setup" not in completed:
        completed.append("company_setup")
    if ceo:
        context["ceo_employee_id"] = ceo.id
        if "hire_ceo" not in completed:
            completed.append("hire_ceo")
    if engineer:
        context["engineer_employee_id"] = engineer.id
        if "hire_engineer" not in completed:
            completed.append("hire_engineer")
    if qa:
        context["qa_employee_id"] = qa.id
        if "hire_qa" not in completed:
            completed.append("hire_qa")
    project_id = context.get("project_id")
    project = project_repo.get_project(db, project_id) if project_id else None
    if project:
        if "create_project" not in completed:
            completed.append("create_project")
        decisions = {
            review.review_type: review.decision
            for review in delivery_repo.list_reviews(db, project.id)
        }
        approved = {ReviewDecision.approved.value, ReviewDecision.conditionally_approved.value}
        if (
            decisions.get("requirements_review") in approved
            and "requirements_review" not in completed
        ):
            completed.append("requirements_review")
        if decisions.get("design_review") in approved and "design_review" not in completed:
            completed.append("design_review")
        if project.status == "completed" and delivery_repo.list_delivery_packages(db, project.id):
            if "delivery" not in completed:
                completed.append("delivery")
    progress.completed_steps = completed
    progress.context = context
    _advance(progress)


def _step_satisfied(db: Session, progress, step: str) -> bool:
    if step in progress.completed_steps:
        return True
    if step == "configure_ceo":
        return bool((progress.context or {}).get("ceo_employee_id"))
    return False


def _advance(progress) -> None:
    completed = set(progress.completed_steps or [])
    for step in STEP_ORDER:
        if step not in completed:
            progress.current_step = step
            return
    progress.current_step = "completed"
    progress.status = TutorialStatus.completed.value
    progress.completed_at = utcnow()
