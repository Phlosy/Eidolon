"""/contracts —— 通用商业合同（M1.6，设计 §21/§24）。

**读**：只返回**本公司参与**的合同（我是发布方或承接方）——合同是双边文件，
不泄露给第三方（设计 §32 作用域纪律）。

**写**：
- `POST /contracts`（发布方）：创建即**锁资对价**（E11 的合同形态）；
- `POST /contracts/{id}/accept`（承接方）：`PENDING_ACCEPTANCE → ACTIVE → FUNDED`；
- `POST /contracts/{id}/fulfill`（承接方）：交付 ⇒ `FULFILLED → SETTLING → SETTLED`
  （多腿放款：净额给承接方 + 手续费进 Treasury/Burn）；
- `POST /contracts/{id}/cancel`（当事方，funded 之前）：取消并退款；
- `POST /contracts/{id}/fail`（承接方）：判定失败 ⇒ 由 `settle_refund` 退款。

结算本身**没有玩家端点**：履约即结算；退款由取消/失败/过期触发
（`settlement_key = contract:<id>` 幂等）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.economy.contracts import EconomicActor
from app.models.enums import ContractType
from app.repositories import economy as economy_repo
from app.schemas.economy import ContractCreateIn, ContractOut, ContractPageOut, EscrowOut
from app.services.economy.contracts import ContractError, ContractService
from app.services.economy.escrow import EscrowService
from app.services.economy.ledger import LedgerError

router = APIRouter(prefix="/contracts", tags=["contracts"])


def _company_or_404(company_id: int | None) -> int:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return int(company_id)


def _contract_out(contract, *, company_id: int, db: Session) -> dict:
    is_issuer = (
        contract.issuer_actor_kind == "company" and int(contract.issuer_actor_ref) == company_id
    )
    is_contractor = (
        contract.contractor_actor_kind == "company"
        and int(contract.contractor_actor_ref or 0) == company_id
    )
    view = EscrowService(db).get_for_contract(int(contract.id))
    return {
        "contract_id": int(contract.id),
        "code": contract.code,
        "contract_type": contract.contract_type,
        "title": contract.title,
        "subject": contract.subject,
        "terms": dict(contract.terms_json or {}),
        "consideration_amount": int(contract.consideration_amount),
        "currency": contract.currency,
        "status": contract.status,
        "issuer_company_id": int(contract.issuer_actor_ref)
        if contract.issuer_actor_kind == "company"
        else 0,
        "contractor_company_id": int(contract.contractor_actor_ref)
        if contract.contractor_actor_kind == "company" and contract.contractor_actor_ref
        else None,
        "reference_type": contract.reference_type,
        "reference_id": contract.reference_id,
        "effective_at": contract.effective_at,
        "expires_at": contract.expires_at,
        "fulfilled_at": contract.fulfilled_at,
        "settled_at": contract.settled_at,
        "settlement_transaction_id": contract.settlement_transaction_id,
        "policy_version": contract.policy_version,
        "is_issuer": bool(is_issuer),
        "is_contractor": bool(is_contractor),
        "escrow": (
            EscrowOut(
                escrow_id=int(view.id),
                status=view.status,
                amount=int(view.amount),
                currency=view.currency,
                account_balance=_escrow_balance(db, view),
                payee_company_id=int(view.payee_actor_ref)
                if view.payee_actor_kind == "company" and view.payee_actor_ref
                else None,
            )
            if view is not None
            else None
        ),
        "settlement": dict((contract.metadata_json or {}).get("settlement") or {}),
    }


def _escrow_balance(db: Session, escrow) -> int:
    return EscrowService(db).view(escrow).account_balance


@router.get("", response_model=ContractPageOut)
def list_contracts(
    status: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司参与的合同（发布方或承接方）——第三方看不到（作用域纪律）。"""
    current = _company_or_404(company_id)
    party = ("company", current)
    statuses = (status.upper(),) if status else None
    rows = economy_repo.list_contracts(
        db,
        party=party,
        statuses=statuses,
        contract_type=contract_type.upper() if contract_type else None,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_contract_out(row, company_id=current, db=db) for row in rows],
        "total": economy_repo.count_contracts(db, party=party, statuses=statuses),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(
    contract_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """合同详情（仅当事方）。"""
    current = _company_or_404(company_id)
    contract = economy_repo.get_contract(db, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    payload = _contract_out(contract, company_id=current, db=db)
    if not (payload["is_issuer"] or payload["is_contractor"]):
        raise HTTPException(status_code=404, detail="contract not found")
    return payload


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(
    payload: ContractCreateIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """创建合同（发布方）——**创建即锁资对价**；余额不足 409 且不留合同。"""
    current = _company_or_404(company_id)
    service = ContractService(db)
    try:
        contract_type = ContractType(payload.contract_type.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown contract type") from exc
    contractor = (
        EconomicActor.company(payload.contractor_company_id)
        if payload.contractor_company_id
        else None
    )
    try:
        contract = service.create_contract(
            issuer=EconomicActor.company(current),
            title=payload.title,
            consideration_amount=payload.consideration_amount,
            contract_type=contract_type,
            contractor=contractor,
            subject=payload.subject,
            terms=payload.terms,
            expires_at=payload.expires_at,
        )
    except ContractError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    except LedgerError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return _contract_out(contract, company_id=current, db=db)


def _transition(
    action, contract_id: int, current: int, db: Session
) -> dict:  # pragma: no cover - 由三个端点共用
    try:
        action()
    except ContractError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.reason) from exc
    return get_contract(contract_id, company_id=current, db=db)


@router.post("/{contract_id}/accept", response_model=ContractOut)
def accept_contract(
    contract_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """承接方接受：`PENDING_ACCEPTANCE → ACTIVE → FUNDED`（幂等）。"""
    current = _company_or_404(company_id)
    return _transition(
        lambda: ContractService(db).accept(contract_id, contractor=EconomicActor.company(current)),
        contract_id,
        current,
        db,
    )


@router.post("/{contract_id}/fulfill", response_model=ContractOut)
def fulfill_contract(
    contract_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """承接方交付 ⇒ 自动结算（多腿放款 + 手续费拆分）。"""
    current = _company_or_404(company_id)
    return _transition(
        lambda: ContractService(db).fulfill(contract_id, contractor=EconomicActor.company(current)),
        contract_id,
        current,
        db,
    )


@router.post("/{contract_id}/cancel", response_model=ContractOut)
def cancel_contract(
    contract_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """当事方取消（`FUNDED` 之前）⇒ 退款给出资人；已开工/已交付 409。"""
    current = _company_or_404(company_id)
    return _transition(
        lambda: ContractService(db).cancel(
            contract_id, actor=EconomicActor.company(current), reason="issuer_cancelled"
        ),
        contract_id,
        current,
        db,
    )


@router.post("/{contract_id}/fail", response_model=ContractOut)
def fail_contract(
    contract_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """承接方判定失败（`FUNDED → FAILED`）；退款由 `fail` 后的结算完成。"""
    current = _company_or_404(company_id)
    return _transition(
        lambda: ContractService(db).fail(
            contract_id, actor=EconomicActor.company(current), reason="contractor_failed"
        ),
        contract_id,
        current,
        db,
    )
