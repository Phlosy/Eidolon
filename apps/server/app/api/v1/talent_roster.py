"""人才名册 API —— 公司里所有"人"的单一视图（`talent-roster.md §3`）。

读侧的硬要求：**不许 N+1**。名册是整页渲染，任职与岗位一次批量解析
（`current_positions_by_employee`），诊断也从已解析的岗位里算，不再回查。

写侧只有一件事发生在名册上：分配与卸任（`{id}/assignments`、`{id}/unassign`）。
`recruit / suspend / offboard` 属于招聘与生命周期流程，刻意不放进来 ——
招聘与任命是两个流程（`workforce-domain-refactor.md §52 #2`），
端点分开就是让它们无法被一次调用混做。

P4b 未实现的名册字段（`position_fit` / `actions` / `traits_summary` / `runtime` /
`provider` / `top_*_competencies`）属于 P4d / P6 / P9 / P12。这里不预先放值为 `null`
的占位键：一旦给出，前端就会开始依赖它的形状，而 `actions` 尤其危险 ——
服务端按状态给可用动作，是"避免第二个状态机"的前提（§3.2 结尾），得和 fit 一起做。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.organization import Employee
from app.models.position import PositionAssignment
from app.repositories import position as position_repo
from app.schemas.organization import EmployeeDetailOut, EmployeeOut
from app.schemas.position import (
    AssignmentIn,
    AssignmentOut,
    IntegrityReportOut,
    RosterEntryOut,
    RosterStatsOut,
)
from app.services import position_compat, position_service

router = APIRouter(prefix="/talent-roster", tags=["talent-roster"])


def _employee_or_404(db: Session, employee_id: int, company_id: int | None) -> Employee:
    """按 id 取人，并确认他属于调用者的公司。

    路由级的 `require_user` 只管"登录没有"，不管"这个人是不是你公司的"。多公司部署下
    少了这一层，A 公司就能给 B 公司的人分配职位 —— 而且坑是公司内资源，症状会是诡异的
    404/409，而不是明确的拒绝。找不到与不属于你都返回 404：不泄露其它公司的存在性。
    """
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if company_id is not None and employee.company_id != company_id:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


def _department_of(entry: dict) -> int | None:
    """过滤用的部门：已分配看**任职所在部门**，未分配看团队字段。

    `talent-roster.md §3.1` 要求按"当前任职的部门"过滤，而不是 `employees.department_id`
    历史列；但 AVAILABLE 的人根本没有任职，此时团队字段是唯一线索。P5 把团队字段正式
    降级为可选之后，这个 fallback 仍然成立 —— 顺序不能反，否则"调岗前按原部门筛"会筛错人。
    """
    position = entry.get("current_position")
    if position and position.get("department_id") is not None:
        return position["department_id"]
    return entry.get("department_id")


@router.get("", response_model=list[RosterEntryOut])
def list_roster(
    workforce_status: list[str] | None = Query(None, alias="status"),
    department_id: int | None = Query(None, description="按员工团队/任职部门过滤"),
    position_code: str | None = Query(
        None, description="按当前任职的定义 code 过滤；`none` = 未分配"
    ),
    runtime_type: str | None = Query(None, description="runtime 类型（mock/hermes/…）"),
    provider_id: int | None = Query(None, description="绑定模型供应商 id"),
    competency_code: str | None = Query(
        None, description="能力筛选 code（与 min_score/min_confidence 同用）"
    ),
    min_competency_score: float | None = Query(None, ge=0, le=100),
    min_competency_confidence: float | None = Query(None, ge=0, le=1),
    trait_code: str | None = Query(None, description="人格倾向筛选（Behavioral Preference）"),
    min_trait_value: float | None = Query(None, ge=0, le=1),
    q: str | None = Query(None, alias="search", description="按姓名/简称搜索"),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_offboarded: bool = Query(default=False, description="默认排除历史离职"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """人才名册（P9）：company-scoped 分页/过滤/搜索 + 富化（batch 派生，无 N+1）。

    默认只显示当前在册人才（排除 offboarded/pending）；`available` 是 WorkforceStatus
    （待分配），与员工 Runtime 活动态（idle/working…）是两个维度。
    """
    from app.services import talent_roster as roster_service

    result = roster_service.roster_query(
        db,
        company_id,
        statuses=(
            [item.strip().lower() for item in workforce_status if item.strip()]
            if workforce_status
            else None
        ),
        department_id=department_id,
        position_code=position_code,
        runtime_type=runtime_type,
        provider_id=provider_id,
        competency_code=competency_code,
        min_competency_score=min_competency_score,
        min_competency_confidence=min_competency_confidence,
        trait_code=trait_code,
        min_trait_value=min_trait_value,
        search=q,
        limit=limit,
        offset=offset,
        include_offboarded=include_offboarded,
    )
    return result["items"]


@router.get("/stats", response_model=RosterStatsOut)
def roster_stats(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """办公室人数/状态条的数据源 —— 数字与名册同源，避免页面各算一遍。"""
    return position_service.roster_stats(db, company_id)


@router.get("/integrity", response_model=IntegrityReportOut)
def integrity(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """**只读诊断**。修数据是用户动作（开编制 / 分配 / 卸任），不是状态。"""
    return position_service.integrity(db, company_id)


@router.get("/{employee_id}", response_model=EmployeeDetailOut)
def roster_detail(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """单人详情：v0.4 员工形状 + 派生三区（`workforce_status` / `current_position` /
    `assignment_integrity`）。输出 = `EmployeeDetailOut` —— **/employees/{id} 的最终契约**。

    现在先挂在这里，因为 `app/api/v1/employees.py` 是未提交 WIP（约定不混改别人在写的
    文件）；等它落地后，`/employees/{id}` 调同一个 `position_compat.enrich_employee()`
    出口即可（行为不变，端点收敛），名册回归查询/筛选/分页职责。
    """
    employee = _employee_or_404(db, employee_id, company_id)
    payload = EmployeeOut.model_validate(employee).model_dump()
    return position_compat.enrich_employee(db, employee, payload)


@router.get("/{employee_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    employee_id: int,
    include_closed: bool = True,
    db: Session = Depends(get_db),
    company_id: int | None = Depends(resolve_company_id),
) -> list:
    """一个人的任职时间轴（履历）。默认含历史行 —— 履历的价值就在闭环行上。"""
    _employee_or_404(db, employee_id, company_id)
    rows = (
        position_repo.career_history(db, employee_id)
        if include_closed
        else position_repo.active_assignments(db, employee_id)
    )
    return [position_service.assignment_out(db, row) for row in rows]


@router.post(
    "/{employee_id}/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def assign(
    employee_id: int,
    payload: AssignmentIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """分配 / 调动 —— 全系统**唯一**能创建生效 PRIMARY 任职的入口（`§4` 的三跳）。"""
    employee = _employee_or_404(db, employee_id, company_id)
    assignment = position_service.assign_position(db, employee, payload)
    return position_service.assignment_out(db, assignment)


# 卸任可能"本来就没有主职"（ AVAILABLE 的人再点一次），所以返回体可空。
@router.post("/{employee_id}/unassign", response_model=AssignmentOut | None)
def unassign(
    employee_id: int,
    reason: str = "",
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> PositionAssignment | None:
    """卸任：关时间轴、把编制还给组织，人级资源一律不动（回到 AVAILABLE）。

    返回**被关掉的那条**任职（已带 effective_to），前端据此把当前职位清空 ——
    只回一个计数会逼调用方再查一次名册才知道现在是什么状态。
    """
    employee = _employee_or_404(db, employee_id, company_id)
    before = position_repo.active_primary_assignment(db, employee_id)
    closed = position_service.release_position(db, employee, reason=reason)
    if not closed:
        # AVAILABLE 的人再点一次卸任：不是错误，也不需要造一个对象回来
        return None
    db.refresh(before) if before is not None else None
    # 数据异常时可能一次关掉多条（历史上重叠的 PRIMARY）；回第一条关掉的行，
    # 剩下的由 `integrity` 诊断说明 —— 响应形状不该为了异常而变。
    return position_service.assignment_out(db, before if before is not None else closed[0])
