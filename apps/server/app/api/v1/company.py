"""/company —— 公司读面 + **工作策略**（M2.1，D1/D2）。

工作策略只回答两件事，都是**配置**而不是决策：

- `work_mode_default`：新项目默认用哪种工作模式（冷启动 guided → 成熟 managed）；
- `work_intake_position_code`：哪个**职位**承担 Work Intake 责任（默认 CEO）。

改这里**不会**改写任何既有项目：`projects.work_mode` 与
`projects.work_intake_position_code` 都是创建时的快照（W35）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.config import settings
from app.core.database import get_db
from app.models.enums import ResponsibilityKind
from app.repositories import organization as org_repo
from app.schemas.organization import CompanyOut, WorkPolicyOut, WorkPolicyPatchIn
from app.work import contracts, readiness, work_defaults, work_intake

router = APIRouter(tags=["company"])


def _company_or_404(db: Session, company_id: int | None):
    company = org_repo.get_company(db, int(company_id)) if company_id else None
    if company is None:
        company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return company


def _work_policy_out(db: Session, company) -> dict:
    code, is_configured = work_intake.configured_position_code_and_source(
        company, ResponsibilityKind.work_intake
    )
    return {
        "work_mode_default": work_defaults.company_work_mode_default(db, company).value,
        # M2.8：运行时策略（只配环境，不配工作方式）
        "runtime_defaults": readiness.company_runtime_defaults(db, int(company.id)),
        "work_mode_explicit": work_defaults.is_work_mode_explicitly_configured(company),
        "work_mode_by_stage": {
            stage: mode.value for stage, mode in contracts.WORK_MODE_BY_COMPANY_STAGE.items()
        },
        "work_intake_position_code": code,
        "work_intake_default_position_code": contracts.RESPONSIBILITY_DEFAULTS[
            ResponsibilityKind.work_intake
        ],
        "work_intake_is_configured": is_configured,
        "allow_planning_fixtures": bool(settings.allow_planning_fixtures),
    }


@router.get("/company", response_model=CompanyOut)
def get_company(
    company_id: int | None = Depends(resolve_company_id), db: Session = Depends(get_db)
) -> CompanyOut:
    return CompanyOut.model_validate(_company_or_404(db, company_id))


@router.get("/company/work-policy", response_model=WorkPolicyOut)
def get_work_policy(
    company_id: int | None = Depends(resolve_company_id), db: Session = Depends(get_db)
) -> dict:
    """公司工作策略（只读）。"""
    return _work_policy_out(db, _company_or_404(db, company_id))


@router.patch("/company/work-policy", response_model=WorkPolicyOut)
def patch_work_policy(
    payload: WorkPolicyPatchIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """改公司工作策略（**默认值**，不改既有项目 —— W35）。

    - `work_mode` 一旦显式设定，系统就**不再**在学习期结束后自动推进它（尊重用户选择）；
    - `work_intake_position_code` 是职位 **code**（如 `ceo` / `coo`）；该职位必须已存在，
      否则 422（避免配出一个永远解析不到的责任目标）。
    """
    company = _company_or_404(db, company_id)
    if payload.work_mode is not None:
        work_defaults.set_company_work_mode_default(
            db, company, payload.work_mode, reason="user_configured", commit=False
        )
    if payload.work_intake_position_code is not None:
        code = payload.work_intake_position_code.strip()
        if code:
            from app.repositories import position as position_repo

            definition = position_repo.get_definition_by_code(db, code, company_id=int(company.id))
            if definition is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown position code for this company: {code!r}",
                )
        work_defaults.set_company_work_intake_code(db, company, code, commit=False)
    if payload.runtime_defaults is not None:
        # M2.8（RD4/I6）：只配环境。未知键与**禁止键**（人格/提示词/工作流…）一律 422 ——
        # 静默忽略会让"我配了但它没生效"变成查不出来的谜。
        try:
            readiness.set_company_runtime_defaults(
                db, int(company.id), payload.runtime_defaults, commit=False
            )
        except readiness.ReadinessError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return _work_policy_out(db, company)
