"""Persistent, declarative tutorial engine driven by real domain state.

三条不可让步的规则（本次迭代的核心）：

1. **完成只由业务状态决定。** 步骤声明里的 ``requirement`` 一定要被
   :mod:`app.tutorials.requirements` 求值；前端点按钮、看没看到聚光灯，
   都不能让步骤前进。唯一的例外是 ``INFORMATION`` 步骤 —— 它本来就不主张
   任何业务事实，只是"我知道了"。

2. **核心教程与实战教程是两行独立进度。** 公司进入 OPERATING 只看核心教程；
   Classic Snake 实战可以整体跳过，跳过只写一个状态字段。

3. **跳过实战零成本。** ``skip_practice()`` 里不允许出现任何创建项目、
   调用 Agent、启动 Runtime Session、生成文档/Artifact 的动作。
   这条由测试用"事后数一遍表里的行数"来守住，而不是靠注释。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.request_context import get_request_identity
from app.events.bus import bus
from app.models.auth import CompanyMembership, User
from app.models.base import utcnow
from app.models.enums import TutorialStatus
from app.models.organization import Company
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project_delivery import TutorialProgressOut, TutorialTemplateOut
from app.services import position_compat
from app.tutorials import (
    CORE_TUTORIAL_ID,
    DEFINITIONS,
    PRACTICE_TUTORIAL_ID,
    TUTORIAL_CENTER,
    TUTORIAL_LIBRARY,
)
from app.tutorials.requirements import Facts, evaluate, known
from app.tutorials.schema import INTERACTION_COMPLETES, flatten, validate

# 每个教程的线性步骤表；步骤 id 全局唯一，所以前端只需要报 id，不用先说属于谁
_STEPS: dict[str, list[dict[str, Any]]] = {
    tutorial_id: flatten(definition) for tutorial_id, definition in DEFINITIONS.items()
}
_STEP_OWNER: dict[str, str] = {}
for _tid, _steps in _STEPS.items():
    for _step in _steps:
        if _step["id"] in _STEP_OWNER:
            raise ValueError(f"tutorial step id 冲突：{_step['id']}")
        _STEP_OWNER[_step["id"]] = _tid

# 声明与注册表必须一致：requirement 拼错会让某个门永远过不去，而它在运行时
# 只表现为"教程莫名卡住"，极难排查 —— 所以在导入阶段直接炸掉。
_PROBLEMS = [
    f"{tutorial_id}: {problem}"
    for tutorial_id, definition_row in DEFINITIONS.items()
    for problem in validate(definition_row, known())
]
if _PROBLEMS:
    raise RuntimeError("教程声明不合法：" + "；".join(_PROBLEMS))


# ------------------------------------------------------------------ 定义读取
def definition(tutorial_id: str = CORE_TUTORIAL_ID) -> dict:
    return _definition(tutorial_id)


def definitions() -> list[dict]:
    """教程库需要展示的全部教程（含各自的步骤）。"""
    return [DEFINITIONS[tutorial_id] for tutorial_id in DEFINITIONS]


def library() -> list[dict]:
    return TUTORIAL_LIBRARY


def center() -> list[dict]:
    return TUTORIAL_CENTER


def step_owner(step: str) -> str | None:
    return _STEP_OWNER.get(step)


def _definition(tutorial_id: str) -> dict:
    row = DEFINITIONS.get(tutorial_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tutorial not found")
    return row


def _steps(tutorial_id: str) -> list[dict[str, Any]]:
    return _STEPS[tutorial_id]


def _step(tutorial_id: str, step: str) -> dict[str, Any]:
    for item in _steps(tutorial_id):
        if item["id"] == step:
            return item
    raise HTTPException(status_code=404, detail="tutorial step not found")


# ------------------------------------------------------------------ 进度读写
def get_progress(db: Session, tutorial_id: str = CORE_TUTORIAL_ID) -> TutorialProgressOut:
    user, company = _principal(db)
    progress = delivery_repo.get_tutorial(db, user.id, tutorial_id)
    if progress is None:
        first = _steps(tutorial_id)[0]
        progress = delivery_repo.create_tutorial(
            db,
            user_id=user.id,
            company_id=company.id,
            tutorial_id=tutorial_id,
            status=TutorialStatus.not_started.value,
            current_stage=first["stage"],
            current_step=first["id"],
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


def start(db: Session, tutorial_id: str = CORE_TUTORIAL_ID) -> TutorialProgressOut:
    progress = _row(db, tutorial_id)
    progress.status = TutorialStatus.active.value
    progress.started_at = progress.started_at or utcnow()
    progress.completed_at = None
    progress.paused_at = None
    _reconcile(db, progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def pause(db: Session, tutorial_id: str = CORE_TUTORIAL_ID) -> TutorialProgressOut:
    progress = _row(db, tutorial_id)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    progress.status = TutorialStatus.paused.value
    progress.paused_at = utcnow()
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def resume(db: Session, tutorial_id: str = CORE_TUTORIAL_ID) -> TutorialProgressOut:
    return start(db, tutorial_id)


def skip(db: Session, tutorial_id: str = CORE_TUTORIAL_ID) -> TutorialProgressOut:
    """跳过整个教程。

    实战教程走到这里时必须保持零副作用：只改这一行状态，不碰项目、不碰 runtime。
    """
    if not _definition(tutorial_id).get("allow_skip"):
        raise HTTPException(
            status_code=409,
            detail=(
                "the core tutorial is completed by doing the real work; "
                "optional steps can be skipped"
            ),
        )
    progress = _row(db, tutorial_id)
    progress.status = TutorialStatus.skipped.value
    progress.completed_at = None
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def complete_step(db: Session, step: str) -> TutorialProgressOut:
    tutorial_id = _owner_or_404(step)
    config = _step(tutorial_id, step)
    progress = _row(db, tutorial_id)
    if progress.status != TutorialStatus.active.value:
        raise HTTPException(status_code=409, detail="tutorial is not active")
    _reconcile(db, progress)
    if config["kind"] not in INTERACTION_COMPLETES and not _satisfied(db, progress, config):
        raise HTTPException(status_code=409, detail="complete the real business action first")
    _mark_completed(progress, step)
    _advance(db, progress)
    db.commit()
    db.refresh(progress)
    return TutorialProgressOut.model_validate(progress)


def skip_step(db: Session, step: str) -> TutorialProgressOut:
    tutorial_id = _owner_or_404(step)
    config = _step(tutorial_id, step)
    if not config["allow_skip"]:
        raise HTTPException(
            status_code=409,
            detail=f"this tutorial step is {config['kind']} and cannot be skipped",
        )
    progress = _row(db, tutorial_id)
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
    """兼容旧端点：把 QA 留到以后。"""
    return skip_step(db, "hire_qa")


# ------------------------------------------------------------------ 实战教程
def practice_preview(db: Session) -> dict[str, Any]:
    """开始实战前必须让用户看到的成本说明（§42/§43）。

    只读现状、不推进进度：是否真的会消耗 token 由"非 mock runtime + 有效
    provider 绑定"推出来，而不是写死一个布尔量。
    """
    user, company = _principal(db)
    facts = Facts.load(db, company, None)
    team: list[dict[str, Any]] = []
    uses_llm = False
    mock_only = True
    for employee in facts.employees:
        runtime = facts.runtimes.get(employee.id)
        if runtime is None:
            continue  # 还没 runtime 的员工不参与实战，也不影响成本判断
        bindings = facts.bindings.get(employee.id, [])
        binding = bindings[0] if bindings else None
        provider_ready = bool(binding and binding.provider_id in facts.usable_providers)
        real_llm = runtime.runtime_type != "mock" and provider_ready
        uses_llm = uses_llm or real_llm
        if real_llm:
            mock_only = False
        provider = facts.provider_of(binding) if binding else None
        team.append(
            {
                "employee_id": employee.id,
                "name": employee.name,
                "role": facts.role_of.get(employee.id, "engineer"),
                "lifecycle_status": employee.lifecycle_status,
                "runtime": runtime.runtime_type,
                "provider": provider.name if provider else None,
                "model": binding.model if binding else None,
            }
        )
    row = delivery_repo.get_tutorial(db, user.id, PRACTICE_TUTORIAL_ID)
    return {
        "tutorial_id": PRACTICE_TUTORIAL_ID,
        "template_name": "Classic Snake",
        "tutorial_accelerated": True,
        "uses_llm": uses_llm,
        "mock_only": mock_only,
        "team": team,
        "practice_status": row.status if row else TutorialStatus.not_started.value,
    }


# ------------------------------------------------------------------ 内部
def _row(db: Session, tutorial_id: str):
    get_progress(db, tutorial_id)
    user, _ = _principal(db)
    return delivery_repo.get_tutorial(db, user.id, tutorial_id)


def _owner_or_404(step: str) -> str:
    tutorial_id = _STEP_OWNER.get(step)
    if tutorial_id is None:
        raise HTTPException(status_code=404, detail="tutorial step not found")
    return tutorial_id


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


def _facts(db: Session, progress) -> Facts:
    company = org_repo.get_default_company(db)
    return Facts.load(db, company, _project_id(db, progress, company))


def _project_id(db: Session, progress, company: Company | None) -> int | None:
    """教程盯哪个项目。

    一旦记下就固定住：用户后来建的项目不该把实战进度"意外推进"。
    """
    context = progress.context or {}
    recorded = context.get("project_id")
    if recorded:
        return int(recorded)
    if company is None or progress.tutorial_id != PRACTICE_TUTORIAL_ID:
        return None
    projects = project_repo.list_projects(db, company.id)
    return max(project.id for project in projects) if projects else None


def _satisfied(db: Session, progress, config: dict[str, Any]) -> bool:
    return evaluate(config["requirement"], _facts(db, progress))


def _reconcile(db: Session, progress) -> None:
    """把当前业务状态映射回进度。GET 每次都会跑，是事件丢失时的恢复路径。"""
    company = org_repo.get_default_company(db)
    if company is None:
        return
    context = dict(progress.context or {})
    employees = org_repo.list_employees(db, company.id)
    for role, key in (("ceo", "ceo_employee_id"), ("engineer", "engineer_employee_id")):
        # 教程上下文里"谁是 CEO"也走职位域优先的桥：占着编制的人 > 镜像列。
        # `employees` 这行原本只是为了一次线性扫描，现在两个角色各自最多查一次。
        match = position_compat.employee_by_legacy_role(db, company.id, role)
        if match is not None and match.id in {e.id for e in employees}:
            context[key] = match.id
    project_id = _project_id(db, progress, company)
    if project_id is not None and not context.get("project_id"):
        context["project_id"] = project_id
    progress.context = context

    facts = Facts.load(db, company, project_id)
    before = (progress.current_step, progress.status)
    for config in _steps(progress.tutorial_id):
        if config["kind"] in INTERACTION_COMPLETES:
            continue  # 信息步骤要用户亲手"知道了"，不能被业务状态代劳
        if config["id"] in (progress.completed_steps or []):
            continue
        if evaluate(config["requirement"], facts):
            _mark_completed(progress, config["id"])
    _advance(db, progress)
    if (progress.current_step, progress.status) != before:
        _publish_change(db, progress, before)


def _mark_completed(progress, step: str) -> None:
    completed = list(progress.completed_steps or [])
    if step not in completed:
        completed.append(step)
        progress.completed_steps = completed


def _advance(db: Session, progress) -> None:
    finished = set(progress.completed_steps or []) | set(progress.skipped_steps or [])
    for config in _steps(progress.tutorial_id):
        if config["id"] not in finished:
            progress.current_stage = config["stage"]
            progress.current_step = config["id"]
            return
    progress.current_stage = "completed"
    progress.current_step = "completed"
    progress.status = TutorialStatus.completed.value
    progress.completed_at = utcnow()
    user = db.get(User, progress.user_id)
    if DEFINITIONS[progress.tutorial_id].get("sets_operating_stage"):
        # 只有核心教程通关才把公司切到 OPERATING；实战教程不影响（§40）
        company = org_repo.get_default_company(db)
        if company is not None:
            company.stage = "OPERATING"
        if user is not None and user.onboarding_status != "completed":
            user.onboarding_status = "completed"


def _publish_change(db: Session, progress, before: tuple[str, str]) -> None:
    """推进后发事件，让前端立刻重取进度（§24）。

    GET /tutorial 自己也会触发这里；前端把 tutorial.* 事件映射成
    invalidate(['tutorial']) 时，第二次 GET 的状态已经稳定、不会再发事件，
    所以这条链自然收敛。
    """
    payload = {
        "tutorial_id": progress.tutorial_id,
        "current_step": progress.current_step,
        "current_stage": progress.current_stage,
        "status": progress.status,
        "previous_step": before[0],
    }
    event_type = (
        "tutorial.completed"
        if progress.status == TutorialStatus.completed.value
        else "tutorial.advanced"
    )
    bus.publish(event_type, payload, company_id=progress.company_id)


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
