"""/employees + private sub-resources (memory / knowledge / skills / learning)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import KnowledgeScope
from app.models.organization import Employee
from app.repositories import events as event_repo
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import runtimes as runtime_repo
from app.schemas.knowledge import (
    EventOut,
    KnowledgeItemOut,
    LearningPriorityOut,
    LearningRecordOut,
    MemoryEntryOut,
    SkillOut,
    SkillUsageBenchmarksOut,
    SkillUsageOut,
    SkillUsageOutcomeIn,
)
from app.schemas.organization import (
    EmployeeCreate,
    EmployeeOut,
    EmployeePatch,
    EmployeePerformance,
)
from app.schemas.provider import (
    BindingCreate,
    BindingPatch,
    EmployeeProviderCreate,
    ModelBindingOut,
    ProviderOut,
)
from app.schemas.runtime import (
    BehaviorProjectionOut,
    EmployeeBrainOut,
    EmployeeBrainPatch,
    EmployeeRuntimeCreate,
    EmployeeRuntimeProviderPatch,
    RuntimeInstanceOut,
)
from app.schemas.work import (
    AuthorityGrantOut,
    PositionExpectationRefOut,
    ReadinessOut,
    ReadinessProvisionOut,
    RoleContextOut,
    RoleContextPageOut,
    RoleProjectBriefOut,
    RoleResourceOut,
)
from app.services import employees as employee_service
from app.services import runtimes as runtime_service
from app.services.providers import provider_service
from app.work import readiness
from app.work import role_context as role_context_service

router = APIRouter(prefix="/employees", tags=["employees"])


def _get_employee_or_404(db: Session, employee_id: int) -> Employee:
    employee = org_repo.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


@router.get("", response_model=list[EmployeeOut])
def list_employees(db: Session = Depends(get_db)) -> list[EmployeeOut]:
    return [EmployeeOut.model_validate(e) for e in org_repo.list_employees(db)]


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)) -> EmployeeOut:
    # v0.4 compat: routes through the onboarding engine with defaults
    return EmployeeOut.model_validate(await employee_service.create_employee(db, payload))


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: int, db: Session = Depends(get_db)) -> EmployeeOut:
    return EmployeeOut.model_validate(_get_employee_or_404(db, employee_id))


@router.delete("/{employee_id}", status_code=204)
def delete_employee(employee_id: int, db: Session = Depends(get_db)) -> None:
    """v0.4: hard delete is 403 unless EIDOLON_ALLOW_HARD_DELETE=true; the
    business flow is POST /employees/{id}/offboard (history is kept)."""
    employee = _get_employee_or_404(db, employee_id)
    employee_service.delete_employee(db, employee)


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int, payload: EmployeePatch, db: Session = Depends(get_db)
) -> EmployeeOut:
    employee = _get_employee_or_404(db, employee_id)
    return EmployeeOut.model_validate(employee_service.update_employee(db, employee, payload))


@router.get("/{employee_id}/memory", response_model=list[MemoryEntryOut])
def get_memory(employee_id: int, db: Session = Depends(get_db)) -> list[MemoryEntryOut]:
    employee = _get_employee_or_404(db, employee_id)
    entries = knowledge_repo.list_memory_entries(db, employee.id)
    return [MemoryEntryOut.model_validate(e) for e in entries]


@router.get("/{employee_id}/knowledge", response_model=list[KnowledgeItemOut])
def get_private_knowledge(
    employee_id: int, db: Session = Depends(get_db)
) -> list[KnowledgeItemOut]:
    employee = _get_employee_or_404(db, employee_id)
    items = knowledge_repo.list_knowledge_items(
        db, scope=KnowledgeScope.private.value, employee_id=employee.id
    )
    return [KnowledgeItemOut.model_validate(i) for i in items]


@router.get("/{employee_id}/skills", response_model=list[SkillOut])
def get_skills(employee_id: int, db: Session = Depends(get_db)) -> list[SkillOut]:
    employee = _get_employee_or_404(db, employee_id)
    return [SkillOut.model_validate(s) for s in knowledge_repo.list_skills(db, employee.id)]


@router.get("/{employee_id}/learning-records", response_model=list[LearningRecordOut])
def get_learning_records(
    employee_id: int, db: Session = Depends(get_db)
) -> list[LearningRecordOut]:
    employee = _get_employee_or_404(db, employee_id)
    records = knowledge_repo.list_learning_records(db, employee.id)
    return [LearningRecordOut.model_validate(r) for r in records]


@router.get("/{employee_id}/learning-priorities", response_model=list[LearningPriorityOut])
def get_learning_priorities(
    employee_id: int, db: Session = Depends(get_db)
) -> list[LearningPriorityOut]:
    employee = _get_employee_or_404(db, employee_id)
    priorities = knowledge_repo.list_learning_priorities(db, employee.id)
    return [LearningPriorityOut.model_validate(p) for p in priorities]


@router.get("/{employee_id}/skill-usages", response_model=list[SkillUsageOut])
def get_skill_usages(employee_id: int, db: Session = Depends(get_db)) -> list[SkillUsageOut]:
    """候选/已验证技能的基准流水（§10.1）。"""
    employee = _get_employee_or_404(db, employee_id)
    return [
        SkillUsageOut.model_validate(u) for u in knowledge_repo.list_skill_usages(db, employee.id)
    ]


@router.get("/{employee_id}/skill-usages/benchmarks", response_model=SkillUsageBenchmarksOut)
def get_skill_usage_benchmarks(
    employee_id: int, db: Session = Depends(get_db)
) -> SkillUsageBenchmarksOut:
    """§10.2 的三个指标；分母为 0 时是 null，不是 0。"""
    employee = _get_employee_or_404(db, employee_id)
    return SkillUsageBenchmarksOut.model_validate(
        knowledge_repo.skill_usage_benchmarks(db, employee.id)
    )


@router.patch("/{employee_id}/skill-usages/{usage_id}/outcome", response_model=SkillUsageOut)
def rate_skill_usage(
    employee_id: int,
    usage_id: int,
    payload: SkillUsageOutcomeIn,
    db: Session = Depends(get_db),
) -> SkillUsageOut:
    """人的评价接口（§18.2：v1 只做这一条）。

    它**只能**写 outcome / outcome_source：success 是 _finalize 的客观事实，confidence 属于
    证据面，两者都不接受从人格或评价推导（§2）。
    """
    employee = _get_employee_or_404(db, employee_id)
    usage = knowledge_repo.get_skill_usage_for_employee(db, usage_id, employee.id)
    if usage is None:
        raise HTTPException(status_code=404, detail="skill usage not found")
    usage.outcome = payload.outcome
    usage.outcome_source = "manual_rating"
    db.commit()
    db.refresh(usage)
    return SkillUsageOut.model_validate(usage)


@router.get("/{employee_id}/activity", response_model=list[EventOut])
def get_activity(employee_id: int, db: Session = Depends(get_db)) -> list[EventOut]:
    employee = _get_employee_or_404(db, employee_id)
    events = event_repo.list_events(db, limit=50, actor_employee_id=employee.id)
    return [EventOut.model_validate(e) for e in events]


@router.get("/{employee_id}/performance", response_model=EmployeePerformance)
def get_performance(employee_id: int, db: Session = Depends(get_db)) -> EmployeePerformance:
    _get_employee_or_404(db, employee_id)
    return employee_service.get_performance(db, employee_id)


# ---- v0.3: employee-owned providers ----


@router.get("/{employee_id}/readiness", response_model=ReadinessOut)
def get_employee_readiness(employee_id: int, db: Session = Depends(get_db)) -> ReadinessOut:
    """逐项就绪事实（position / workspace / runtime / provider）+ 缺口（M2.8，W31）。

    `ready_to_work` 是**派生量**（不落库）：它就是"四项事实现在都成立"的缩写，
    所以未就绪时一定能回答"差哪一项"。
    """
    employee = org_repo.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return ReadinessOut(**readiness.readiness_report(db, employee).as_dict())


@router.post("/{employee_id}/provision", response_model=ReadinessProvisionOut)
def provision_employee_readiness(
    employee_id: int, db: Session = Depends(get_db)
) -> ReadinessProvisionOut:
    """重跑环境编排（工作区 / 运行时 / 供应商）—— 人类管理动作（M2.8）。

    典型场景：招募时环境没配好（job `partial`），公司把供应商配好后再跑一次。
    失败**不会**把员工变成"就绪"：结果里带 job 状态与失败步骤。
    """
    employee = org_repo.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    try:
        result = readiness.provision_employee(db, employee)
    except readiness.ReadinessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReadinessProvisionOut(**result.as_dict())


@router.get("/{employee_id}/providers", response_model=list[ProviderOut])
def list_employee_providers(employee_id: int, db: Session = Depends(get_db)) -> list[ProviderOut]:
    """The employee's own accounts + company-scope shared ones (marked by scope)."""
    _get_employee_or_404(db, employee_id)
    return provider_service.list(db, employee_id)


@router.post("/{employee_id}/providers", response_model=ProviderOut, status_code=201)
def create_employee_provider(
    employee_id: int, payload: EmployeeProviderCreate, db: Session = Depends(get_db)
) -> ProviderOut:
    _get_employee_or_404(db, employee_id)
    return provider_service.create_for_employee(db, employee_id, payload)


# ---- model bindings: 一个员工可绑多个模型，is_primary 是默认启动模型 ----


@router.get("/{employee_id}/bindings", response_model=list[ModelBindingOut])
def list_employee_bindings(
    employee_id: int, db: Session = Depends(get_db)
) -> list[ModelBindingOut]:
    _get_employee_or_404(db, employee_id)
    return provider_service.list_bindings(db, employee_id)


@router.post("/{employee_id}/bindings", response_model=ModelBindingOut, status_code=201)
def add_employee_binding(
    employee_id: int, payload: BindingCreate, db: Session = Depends(get_db)
) -> ModelBindingOut:
    _get_employee_or_404(db, employee_id)
    return provider_service.add_binding(db, employee_id, payload)


@router.post("/{employee_id}/bindings/{binding_id}/primary", response_model=list[ModelBindingOut])
def set_primary_binding(
    employee_id: int, binding_id: int, db: Session = Depends(get_db)
) -> list[ModelBindingOut]:
    _get_employee_or_404(db, employee_id)
    return provider_service.set_primary_binding(db, employee_id, binding_id)


@router.patch("/{employee_id}/bindings/{binding_id}", response_model=ModelBindingOut)
def update_employee_binding(
    employee_id: int, binding_id: int, payload: BindingPatch, db: Session = Depends(get_db)
) -> ModelBindingOut:
    _get_employee_or_404(db, employee_id)
    return provider_service.update_binding(db, employee_id, binding_id, payload)


@router.delete("/{employee_id}/bindings/{binding_id}", status_code=204)
def delete_employee_binding(
    employee_id: int, binding_id: int, db: Session = Depends(get_db)
) -> None:
    _get_employee_or_404(db, employee_id)
    provider_service.delete_binding(db, employee_id, binding_id)


# ---- v0.2: employee runtime + brain ----


@router.get("/{employee_id}/runtime", response_model=RuntimeInstanceOut)
def get_employee_runtime(employee_id: int, db: Session = Depends(get_db)) -> RuntimeInstanceOut:
    employee = _get_employee_or_404(db, employee_id)
    instance = runtime_service.get_employee_runtime(db, employee)
    if instance is None:
        raise HTTPException(status_code=404, detail="employee has no runtime instance")
    return instance


@router.post("/{employee_id}/runtime", response_model=RuntimeInstanceOut, status_code=201)
async def create_employee_runtime(
    employee_id: int, payload: EmployeeRuntimeCreate, db: Session = Depends(get_db)
) -> RuntimeInstanceOut:
    employee = _get_employee_or_404(db, employee_id)
    return await runtime_service.create_employee_runtime(db, employee, payload)


@router.patch("/{employee_id}/runtime", response_model=RuntimeInstanceOut)
async def change_employee_runtime(
    employee_id: int,
    payload: EmployeeRuntimeCreate,
    db: Session = Depends(get_db),
) -> RuntimeInstanceOut:
    """切换运行时类型（Mock ↔ Hermes/OpenClaw）或同类型下换 provider/model。"""
    employee = _get_employee_or_404(db, employee_id)
    return await runtime_service.change_employee_runtime(db, employee, payload)


@router.patch("/{employee_id}/runtime/provider", response_model=RuntimeInstanceOut)
async def update_employee_runtime_provider(
    employee_id: int, payload: EmployeeRuntimeProviderPatch, db: Session = Depends(get_db)
) -> RuntimeInstanceOut:
    employee = _get_employee_or_404(db, employee_id)
    return await runtime_service.change_runtime_provider(db, employee, payload)


@router.delete("/{employee_id}/runtime", status_code=204)
async def delete_employee_runtime(employee_id: int, db: Session = Depends(get_db)) -> None:
    employee = _get_employee_or_404(db, employee_id)
    await runtime_service.delete_employee_runtime(db, employee)


@router.get("/{employee_id}/brain", response_model=EmployeeBrainOut)
def get_employee_brain(employee_id: int, db: Session = Depends(get_db)) -> EmployeeBrainOut:
    employee = _get_employee_or_404(db, employee_id)
    return runtime_service.get_brain(db, employee)


@router.get("/{employee_id}/brain/projection", response_model=BehaviorProjectionOut)
def get_brain_projection(employee_id: int, db: Session = Depends(get_db)) -> BehaviorProjectionOut:
    """投影审计面：当前策略摘要 + 投影正文 + T3 是否已随实例生效（§8）。"""
    employee = _get_employee_or_404(db, employee_id)
    projection = runtime_service.project_brain(db, employee)
    instance = runtime_repo.get_instance_for_employee(db, employee.id)
    mirrored = (instance.metadata_json or {}).get("behavior_revision") if instance else None
    return BehaviorProjectionOut(
        employee_id=employee.id,
        policy_version=projection["policy_version"],
        revision=projection["revision"],
        band=projection["band"],
        projection_markdown=projection["projection_markdown"],
        paths=projection["paths"],
        mirrored_revision=mirrored,
        mirror_current=mirrored == projection["revision"],
    )


@router.patch("/{employee_id}/brain", response_model=EmployeeBrainOut)
def update_employee_brain(
    employee_id: int, payload: EmployeeBrainPatch, db: Session = Depends(get_db)
) -> EmployeeBrainOut:
    employee = _get_employee_or_404(db, employee_id)
    return runtime_service.patch_brain(db, employee, payload)


@router.get("/{employee_id}/role-context", response_model=RoleContextPageOut)
def get_employee_role_context(
    employee_id: int, db: Session = Depends(get_db)
) -> RoleContextPageOut:
    """履职上下文（M2.2，设计 §5/§6）—— **派生读模型，不落表**。

    回答「这个人现在以什么职位、被授权做什么、期望是什么、该关心哪些项目、
    公司给了他哪些建议阅读的资源」。

    三件它**不做**的事：

    * 不含任何**已获得的能力**数值（期望只给引用；分值请读岗位画像）；
    * 不含任何"你应该先做什么"的系统指令 —— 学什么、怎么组织工作由 Agent 自己判断
      （Adaptive Onboarding，W4）；
    * 不判定权限结果：它列出**持有**哪些授权；某次动作在不在授权内由
      `authority.authorizes()` 逐次校验（default-deny，W37/W39）。
    """
    employee = _get_employee_or_404(db, employee_id)
    context = role_context_service.build_role_context(db, employee)
    resources = role_context_service.resolve_role_resources(db, employee)
    return RoleContextPageOut(
        context=RoleContextOut(
            person_id=context.person_id,
            employee_id=context.employee_id,
            position_definition_id=context.position_definition_id,
            position_code=context.position_code,
            department_id=context.department_id,
            responsibilities=list(context.responsibilities),
            authority=[
                AuthorityGrantOut(
                    kind=grant.kind.value,
                    scope_kind=grant.scope_kind.value,
                    scope_ref=grant.scope_ref,
                    max_amount=grant.max_amount,
                    grant_id=grant.grant_id,
                )
                for grant in context.authority
            ],
            expectations=[
                PositionExpectationRefOut(
                    competency_code=item.competency_code,
                    requirement_type=item.requirement_type,
                    critical=item.critical,
                )
                for item in context.expectations
            ],
            advisory_scope=list(context.advisory_scope),
            # 用**已解析**的那一份（同一批数据）；`context.resource_index` 是契约里的
            # 纯引用视图，读面要的是"这个指针落到哪了"，所以两处保持同源同序。
            resource_index=[
                RoleResourceOut(
                    kind=item.kind.value,
                    ref=item.ref,
                    note=item.note,
                    required=item.required,
                    resolution=item.resolution.value,
                    pointer=item.pointer,
                )
                for item in resources
            ],
            direct_reports=list(context.direct_reports),
            company_policy_keys=list(context.company_policy_keys),
            current_project_ids=list(context.current_project_ids),
            knowledge_scopes=list(context.knowledge_scopes),
        ),
        resources=[
            RoleResourceOut(
                kind=item.kind.value,
                ref=item.ref,
                note=item.note,
                required=item.required,
                resolution=item.resolution.value,
                pointer=item.pointer,
            )
            for item in resources
        ],
        live_projects=[
            RoleProjectBriefOut(**brief)
            for brief in role_context_service.live_projects_for_role(db, employee)
        ],
        has_management_authority=bool(context.authority),
        authority_grant_count=len(context.authority),
    )
