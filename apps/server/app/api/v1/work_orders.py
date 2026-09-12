"""/work-orders —— 官方工作市场（M1.3，设计 §17/§18）。

**读**：任何登录用户可读**公开市场**（官方订单本身不含公司私有数据：只列在招订单、
以及"我承接的"订单与自己的提交）。设计 §6 的"受控跨公司读取域"针对 T2 市场；
工作订单是公开的官方要约，不泄露任何公司资产。

**写**：领取 / 提交（公司作用域）。**发布 / 验收 / 结算不出玩家 router**（§32 三层边界：
系统/管理面走 CLI `scripts/work_orders.py`）—— 金额与验收判定不能被玩家自证。

奖励发放只经内部 `SettlementService → MonetaryAuthority → LedgerService.post()`（E1/E4）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.core.request_context import get_request_identity
from app.economy.contracts import EconomicActor
from app.models.enums import EvaluationMode, WorkOrderKind, WorkOrderStatus
from app.repositories import economy as economy_repo
from app.schemas.economy import (
    EscrowOut,
    WorkOrderBindIn,
    WorkOrderBindingOut,
    WorkOrderCreateIn,
    WorkOrderDeclineIn,
    WorkOrderDetailOut,
    WorkOrderPageOut,
    WorkOrderSubmitIn,
)
from app.services.economy.escrow import EscrowService
from app.services.economy.ledger import LedgerError
from app.services.economy.work_orders import WorkOrderError, WorkOrderService
from app.work import work_order_bridge as bridge

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

#: 默认市场视图：在招订单（DRAFT 不出现在市场里；终态由 `mine` 或详情查看）
MARKET_STATUSES = (WorkOrderStatus.open.value,)


def _company_or_404(company_id: int | None) -> int:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return int(company_id)


def _holder_company_id(order) -> int | None:
    """订单的**承接公司**（没有承接方 ⇒ None）。读面用它做隔离判断。"""
    if str(order.assignee_actor_kind or "") != "company":
        return None
    return int(order.assignee_actor_ref or 0) or None


def _order_out(order, *, company_id: int | None, service: WorkOrderService) -> dict:
    is_mine = (
        order.assignee_actor_kind == "company"
        and company_id is not None
        and int(order.assignee_actor_ref or 0) == company_id
    )
    is_issuer = (
        order.issuer_actor_kind == "company"
        and company_id is not None
        and int(order.issuer_actor_ref or 0) == company_id
    )
    escrow_view = EscrowService(service.db).view_for_order(int(order.id))
    return {
        "work_order_id": int(order.id),
        "code": order.code,
        "kind": order.kind,
        "title": order.title,
        "description": order.description,
        "requirements": dict(order.requirements_json or {}),
        "deliverables": dict(order.deliverables_json or {}),
        "reward_amount": int(order.reward_amount),
        "currency": order.currency,
        "funding_mode": order.funding_mode,
        "evaluation_mode": order.evaluation_mode,
        "status": order.status,
        "deadline_at": order.deadline_at,
        "issuer_actor_kind": order.issuer_actor_kind,
        "accepted_at": order.accepted_at,
        "submitted_at": order.submitted_at,
        "settled_at": order.settled_at,
        "assignee_company_id": int(order.assignee_actor_ref)
        if order.assignee_actor_ref is not None
        else None,
        "issuer_company_id": int(order.issuer_actor_ref)
        if order.issuer_actor_kind == "company" and order.issuer_actor_ref is not None
        else None,
        "is_mine": bool(is_mine),
        "is_issuer": bool(is_issuer),
        "escrow": (
            EscrowOut(
                escrow_id=escrow_view.escrow_id,
                status=escrow_view.status,
                amount=escrow_view.amount,
                currency=escrow_view.currency,
                account_balance=escrow_view.account_balance,
                payee_company_id=escrow_view.payee_actor_ref
                if escrow_view.payee_actor_kind == "company"
                else None,
            )
            if escrow_view is not None
            else None
        ),
        "submission_count": len(service.submissions(int(order.id))),
        "payable_amount": service.payable_amount(order),
        "policy_version": order.policy_version,
    }


@router.get("", response_model=WorkOrderPageOut)
def list_work_orders(
    status: str | None = Query(default=None, description="按状态过滤（默认只看在招 OPEN）"),
    kind: str | None = Query(default=None),
    mine: bool = Query(default=False, description="只看本公司承接的订单"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """官方工作市场：默认列在招订单；`mine=true` 列本公司承接的（含进行中与已结算）。"""
    service = WorkOrderService(db)
    assignee = None
    statuses: tuple[str, ...] | None = MARKET_STATUSES
    if mine:
        current = _company_or_404(company_id)
        assignee = ("company", current)
        statuses = (status.upper(),) if status else None
    elif status:
        statuses = (status.upper(),)
    orders = economy_repo.list_work_orders(
        db,
        statuses=statuses,
        kinds=(kind.upper(),) if kind else None,
        assignee=assignee,
        limit=limit,
        offset=offset,
    )
    total = economy_repo.count_work_orders(db, statuses=statuses, assignee=assignee)
    return {
        "items": [_order_out(order, company_id=company_id, service=service) for order in orders],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", response_model=WorkOrderDetailOut, status_code=201)
def create_work_order(
    payload: WorkOrderCreateIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """发布**玩家**订单（`player_bounty` / `player_contract`）。

    - **发布前必须锁资**（E11）：钱在同一事务里从本公司账户转入该订单自己的托管账户；
      余额不足 → 409 `insufficient_funds`，**不会留下"已发布但没锁资"的订单**；
    - 官方类 kind 一律拒绝（422 `kind_not_player_kind`）——想发行必须走官方渠道（CLI，M1.3）；
    - 金额上限 `player_order_max_reward`（政策护栏）。
    """
    current = _company_or_404(company_id)
    service = WorkOrderService(db)
    try:
        kind = WorkOrderKind(payload.kind.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown work order kind") from exc
    try:
        result = service.publish_player_order(
            issuer=EconomicActor.company(current),
            title=payload.title,
            reward_amount=payload.reward_amount,
            kind=kind,
            description=payload.description,
            requirements=payload.requirements,
            deliverables=payload.deliverables,
            evaluation_mode=EvaluationMode(payload.evaluation_mode),
            deadline_at=payload.deadline_at,
        )
    except WorkOrderError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    except LedgerError as exc:
        # 余额不足/账户不可用：整笔已回滚（不产生订单）
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return get_work_order(int(result.order.id), company_id=company_id, db=db)


@router.post("/{order_id}/cancel", response_model=WorkOrderDetailOut)
def cancel_work_order(
    order_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """取消订单（只有发布方本人；`OPEN`/`ACCEPTED` → `CANCELLED`）并**退款给发布方**。

    非发布方 → 404（不泄露存在性）；不可取消状态 → 409；重复取消幂等。
    """
    current = _company_or_404(company_id)
    service = WorkOrderService(db)
    try:
        service.cancel(order_id, company_id=current)
    except WorkOrderError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return get_work_order(order_id, company_id=company_id, db=db)


@router.get("/{order_id}", response_model=WorkOrderDetailOut)
def get_work_order(
    order_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """订单详情。提交/验收记录只对**承接方**返回（别人的交付物不属于你）。"""
    service = WorkOrderService(db)
    order = economy_repo.get_work_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    payload = _order_out(order, company_id=company_id, service=service)
    if payload["is_mine"]:
        payload["submissions"] = [
            {
                "submission_id": int(row.id),
                "attempt": int(row.attempt),
                "company_id": int(row.company_id),
                "summary": row.summary,
                "deliverables": dict(row.deliverables_json or {}),
                "artifact_refs": list(row.artifact_refs or []),
                "project_id": row.project_id,
                "created_at": row.created_at,
            }
            for row in service.submissions(order_id)
        ]
        payload["evaluations"] = [
            {
                "evaluation_id": int(row.id),
                "mode": row.mode,
                "verdict": row.verdict,
                "score": row.score,
                "bonuses": dict(row.bonuses_json or {}),
                "notes": row.notes,
                "created_at": row.created_at,
            }
            for row in service.evaluations(order_id)
        ]
    return payload


@router.post("/{order_id}/accept", response_model=WorkOrderDetailOut)
def accept_work_order(
    order_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """领取订单（OPEN → ACCEPTED）。并发下只有一个公司能领到；重复领取幂等。"""
    current = _company_or_404(company_id)
    service = WorkOrderService(db)
    try:
        service.accept(order_id, company_id=current)
    except WorkOrderError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return get_work_order(order_id, company_id=company_id, db=db)


@router.get("/{order_id}/binding", response_model=WorkOrderBindingOut)
def get_work_order_binding(
    order_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """执行绑定视图（M2.9）：投递事实 + 管理决定（绑定 / 明确拒绝）。"""
    current = _company_or_404(company_id)
    order = economy_repo.get_work_order(db, int(order_id))
    if order is None or _holder_company_id(order) != current:
        raise HTTPException(status_code=404, detail="work_order_not_found")
    return bridge.binding_view(db, order)


@router.post("/{order_id}/binding", response_model=WorkOrderBindingOut)
def bind_work_order_project(
    order_id: int,
    payload: WorkOrderBindIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """把一个 Project 绑到订单上（**管理决定**；系统不替谁选执行载体，W1/WO5）。"""
    current = _company_or_404(company_id)
    order = economy_repo.get_work_order(db, int(order_id))
    if order is None or _holder_company_id(order) != current:
        raise HTTPException(status_code=404, detail="work_order_not_found")
    try:
        bridge.bind_project(
            db,
            order,
            project_id=payload.project_id,
            actor_employee_id=payload.actor_employee_id,
            reason=payload.reason,
        )
    except bridge.WorkOrderBridgeError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return bridge.binding_view(db, order)


@router.post("/{order_id}/binding/decline", response_model=WorkOrderBindingOut)
def decline_work_order_binding(
    order_id: int,
    payload: WorkOrderDeclineIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """明确不接这份订单（**管理决定**，理由必填）—— J1 的另一条路径。"""
    current = _company_or_404(company_id)
    order = economy_repo.get_work_order(db, int(order_id))
    if order is None or _holder_company_id(order) != current:
        raise HTTPException(status_code=404, detail="work_order_not_found")
    try:
        bridge.decline_binding(
            db, order, actor_employee_id=payload.actor_employee_id, reason=payload.reason
        )
    except bridge.WorkOrderBridgeError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return bridge.binding_view(db, order)


@router.post("/{order_id}/submit", response_model=WorkOrderDetailOut)
def submit_work_order(
    order_id: int,
    payload: WorkOrderSubmitIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """提交交付物（ACCEPTED → IN_PROGRESS → SUBMITTED → REVIEWING）。

    `evaluation_mode=auto` 时在同一请求内完成确定性验收与结算（APPROVED → SETTLED）；
    `manual` 时停在 SUBMITTED/REVIEWING，等管理面（CLI）验收。
    """
    current = _company_or_404(company_id)
    service = WorkOrderService(db)
    identity = get_request_identity()
    try:
        service.submit(
            order_id,
            company_id=current,
            summary=payload.summary,
            deliverables=payload.deliverables,
            artifact_refs=payload.artifact_refs,
            project_id=payload.project_id,
            submitted_by_user_id=identity.user_id if identity else None,
        )
    except WorkOrderError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return get_work_order(order_id, company_id=company_id, db=db)
