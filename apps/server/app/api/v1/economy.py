"""/economy —— M1.1 经济读面（**只读**：没有 mint/burn/transfer 端点，E23/A18）。

作用域（A17）：一律以当前请求的公司为界 —— 余额与流水都只覆盖**本公司自己的账户**；
跨公司/跨主体一律看不到（市场那样的受控跨公司读取域是 T2 的事，与经济无关）。

写路径只有内部能力：`LedgerService.post()` / `MonetaryAuthority`（无 HTTP 入口）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.config import settings
from app.core.database import get_db
from app.core.request_context import get_request_identity
from app.economy.contracts import EconomicActor
from app.economy.policy import reload_policy
from app.models.enums import Currency, EconomicActorKind, RewardType
from app.repositories import economy as economy_repo
from app.schemas.economy import (
    CategoryFlowOut,
    ComputeUsageOut,
    ComputeUsagePageOut,
    EconomyOverviewOut,
    LedgerAccountOut,
    LedgerEntryOut,
    LedgerTransactionOut,
    LedgerTransactionPageOut,
    PersonalWalletOut,
    PolicySnapshotOut,
    RewardCatalogOut,
    RewardClaimIn,
    RewardClaimOut,
    WalletOut,
)
from app.services.economy.accounts import AccountService
from app.services.economy.balances import derive_wallets
from app.services.economy.costs import ComputeCostService
from app.services.economy.rewards import RewardError, RewardService

router = APIRouter(prefix="/economy", tags=["economy"])


def _company_actor(company_id: int | None) -> EconomicActor:
    if company_id is None:
        raise HTTPException(status_code=404, detail="company not found")
    return EconomicActor.company(company_id)


def _account_out(account, projection) -> dict:
    return {
        "account_id": int(account.id),
        "kind": account.kind,
        "currency": account.currency,
        "status": account.status,
        "subject_ref": int(account.subject_ref),
        "posted_balance": int(projection.posted_balance) if projection else 0,
        "available_balance": int(projection.available_balance) if projection else 0,
        "reserved_balance": int(projection.reserved_balance) if projection else 0,
        "version": int(projection.version) if projection else 0,
    }


@router.get("/balance", response_model=WalletOut)
def get_balance(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司钱包：`posted`（含锁定）/ `available`（可花）/ `reserved`（Escrow 锁定份额）。

    没有账务活动的公司返回全 0（不创建账户、不伪造数字）。
    """
    actor = _company_actor(company_id)
    accounts = AccountService(db).accounts_for_actor(actor)
    rows = [
        _account_out(account, economy_repo.get_projection(db, int(account.id)))
        for account in accounts
    ]
    currency = rows[0]["currency"] if rows else Currency.credit.value
    return {
        "actor_kind": actor.kind.value,
        "actor_ref": int(actor.ref),
        "currency": currency,
        "posted_balance": sum(row["posted_balance"] for row in rows),
        "available_balance": sum(row["available_balance"] for row in rows),
        "reserved_balance": sum(row["reserved_balance"] for row in rows),
        "accounts": rows,
    }


@router.get("/accounts", response_model=list[LedgerAccountOut])
def list_accounts(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    """本公司账户列表（含投影三值；账户不可删除，关闭后仍在此显示）。"""
    actor = _company_actor(company_id)
    accounts = AccountService(db).accounts_for_actor(actor)
    return [
        _account_out(account, economy_repo.get_projection(db, int(account.id)))
        for account in accounts
    ]


@router.get("/transactions", response_model=LedgerTransactionPageOut)
def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    transaction_type: str | None = Query(default=None),
    reference_type: str | None = Query(default=None),
    reference_id: str | None = Query(default=None),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司流水（分页，倒序）：只返回**触及本公司账户**的交易。

    腿（entries）随交易一起返回 —— 金额与方向是账本的原始事实，不做聚合隐藏。
    """
    actor = _company_actor(company_id)
    account_ids = [int(account.id) for account in AccountService(db).accounts_for_actor(actor)]
    transactions, total = economy_repo.transactions_for_accounts(
        db,
        account_ids=account_ids,
        limit=limit,
        offset=offset,
        transaction_type=transaction_type,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    entries_by_transaction = economy_repo.list_entries_for_transactions(
        db, transaction_ids=[int(transaction.id) for transaction in transactions]
    )
    items = []
    for transaction in transactions:
        entries = entries_by_transaction.get(int(transaction.id), [])
        items.append(
            {
                "transaction_id": int(transaction.id),
                "transaction_type": transaction.transaction_type,
                "currency": transaction.currency,
                "status": transaction.status,
                "reference_type": transaction.reference_type,
                "reference_id": transaction.reference_id,
                "reason": transaction.reason,
                "amount": sum(int(entry.amount) for entry in entries if entry.direction == "debit"),
                "occurred_at": transaction.occurred_at,
                "posted_at": transaction.posted_at,
                "entries": [
                    LedgerEntryOut(
                        account_id=int(entry.account_id),
                        direction=entry.direction,
                        amount=int(entry.amount),
                    )
                    for entry in entries
                ],
            }
        )
    return {
        "items": [LedgerTransactionOut(**item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/wallet/me", response_model=PersonalWalletOut)
def get_my_wallet(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """**我的钱包**（个人主体）：余额 + 最近流水 —— 个人奖励不再是"看不见的钱"。

    个人主体解析与奖励领取一致（请求身份优先，其次公司 OWNER）；
    没有个人主体 → 404（不伪造钱包）。
    """
    actor = _company_actor(company_id)
    service = RewardService(db)
    user_actor = service.resolve_user_actor(company_id=int(actor.ref), user_id=_current_user_id())
    if user_actor is None:
        raise HTTPException(status_code=404, detail="no_user_context")

    accounts = AccountService(db).accounts_for_actor(user_actor)
    account_ids = [int(account.id) for account in accounts]
    rows = [
        _account_out(account, economy_repo.get_projection(db, int(account.id)))
        for account in accounts
    ]
    transactions, total = economy_repo.transactions_for_accounts(
        db, account_ids=account_ids, limit=limit, offset=offset
    )
    entries_by_transaction = economy_repo.list_entries_for_transactions(
        db, transaction_ids=[int(transaction.id) for transaction in transactions]
    )
    items = []
    for transaction in transactions:
        entries = entries_by_transaction.get(int(transaction.id), [])
        items.append(
            {
                "transaction_id": int(transaction.id),
                "transaction_type": transaction.transaction_type,
                "currency": transaction.currency,
                "status": transaction.status,
                "reference_type": transaction.reference_type,
                "reference_id": transaction.reference_id,
                "reason": transaction.reason,
                "amount": sum(int(entry.amount) for entry in entries if entry.direction == "debit"),
                "occurred_at": transaction.occurred_at,
                "posted_at": transaction.posted_at,
                "entries": [
                    LedgerEntryOut(
                        account_id=int(entry.account_id),
                        direction=entry.direction,
                        amount=int(entry.amount),
                    )
                    for entry in entries
                ],
            }
        )
    return {
        "actor_kind": user_actor.kind.value,
        "actor_ref": int(user_actor.ref),
        "currency": Currency.credit.value,
        "posted_balance": sum(row["posted_balance"] for row in rows),
        "available_balance": sum(row["available_balance"] for row in rows),
        "reserved_balance": sum(row["reserved_balance"] for row in rows),
        "accounts": rows,
        "transactions": LedgerTransactionPageOut(
            items=[LedgerTransactionOut(**item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
    }


# ---------------------------------------------------------------- 系统/管理面（M1.9）


#: 系统/管理面开关（`EIDOLON_ECONOMY_ADMIN_ENABLED`，默认关）：
#: v1 没有 admin 角色体系（§32），这个面默认不暴露；打开后也只提供**只读/刷新**能力。
@router.post("/admin/policy/reload", response_model=PolicySnapshotOut)
def reload_economic_policy(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """**在线刷新经济政策**（admin；默认关闭时 404，不暴露面）。

    重新读环境/.env 的 `EIDOLON_ECONOMY_*` → 更新进程内 Settings → 清政策缓存，
    返回新快照供确认。完整政策中心（表化 + 版本审计）不在 M1.9。
    """
    del db
    _company_actor(company_id)
    if not settings.economy_admin_enabled:
        raise HTTPException(status_code=404, detail="not found")
    policy = reload_policy()
    return {
        "policy_version": policy.version,
        "starter_grant": policy.starter_grant,
        "daily_reward": policy.daily_reward,
        "market_fee_bps": policy.market_fee_bps,
        "contract_fee_bps": policy.contract_fee_bps,
        "official_max_reward": policy.official_max_reward,
        "npc_budget_injection": policy.npc_budget_injection,
        "npc_budget_cap": policy.npc_budget_cap,
    }


# ---------------------------------------------------------------- 经营报表（M1.5）

#: 类别展示名（报表读面用；枚举值是契约，展示名只是文案）
_CATEGORY_LABELS: dict[str, str] = {
    "STARTER": "启动资金",
    "REWARD": "奖励",
    "OFFICIAL_INCOME": "官方收入",
    "PLAYER_INCOME": "工作订单（收/支）",
    "TALENT_SALE": "人才交易",
    "SERVICE_SALE": "服务收入",
    "HIRING": "招聘支出",
    "TALENT_PURCHASE": "人才采购",
    "TRAINING": "培养支出",
    "COMPUTE": "算力成本",
    "MARKET_FEE": "市场手续费",
    "CONTRACT_FEE": "合同手续费",
    "TREASURY": "财政",
    "BURN": "销毁",
    "RECOVERY": "破产兜底",
}


@router.get("/overview", response_model=EconomyOverviewOut)
def get_overview(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司经营报表：收入 / 成本 / 净额 + 按业务类别拆分 + 算力成本（含欠费）。

    口径：`category` 描述业务事件，(debit, credit) 让读面按**本公司方向**解释收支 ——
    同一笔玩家订单，承接方看到收入、发布方看到支出。未分类的历史交易归入 `unclassified`。
    """
    actor = _company_actor(company_id)
    accounts = AccountService(db).accounts_for_actor(actor)
    account_ids = [int(account.id) for account in accounts]
    totals = economy_repo.category_totals_for_accounts(db, account_ids=account_ids)

    rows: list[dict] = []
    income_total = 0
    expense_total = 0
    for category, (debits, credits) in totals.items():
        # actor 账户是 debit-normal：debit = 进账（收入），credit = 出账（支出）
        income, expense = int(debits), int(credits)
        income_total += income
        expense_total += expense
        key = category or "unclassified"
        rows.append(
            {
                "category": key,
                "label": _CATEGORY_LABELS.get(key, "未分类" if key == "unclassified" else key),
                "income": income,
                "expense": expense,
                "net": income - expense,
            }
        )
    rows.sort(key=lambda row: (-(row["income"] + row["expense"]), row["category"]))

    wallets = derive_wallets(db, account_ids=account_ids)
    compute = ComputeCostService(db).totals(company_id=int(actor.ref))
    return {
        "actor_kind": actor.kind.value,
        "actor_ref": int(actor.ref),
        "currency": Currency.credit.value,
        "posted_balance": sum(wallet.posted_balance for wallet in wallets.values()),
        "available_balance": sum(wallet.available_balance for wallet in wallets.values()),
        "reserved_balance": sum(wallet.reserved_balance for wallet in wallets.values()),
        "income_total": income_total,
        "expense_total": expense_total,
        "net_total": income_total - expense_total,
        "by_category": [CategoryFlowOut(**row) for row in rows],
        "compute_paid": int(compute.get("paid", 0)),
        "compute_unpaid": int(compute.get("unpaid", 0)),
    }


@router.get("/compute-usage", response_model=ComputeUsagePageOut)
def list_compute_usage(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, description="paid / unpaid"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司算力用量明细（含未扣款记录 —— 欠费要看得见，不能当免费）。"""
    actor = _company_actor(company_id)
    service = ComputeCostService(db)
    rows = economy_repo.list_compute_usage(
        db,
        company_id=int(actor.ref),
        statuses=(status.lower(),) if status else None,
        limit=limit,
        offset=offset,
    )
    totals = service.totals(company_id=int(actor.ref))
    return {
        "items": [
            ComputeUsageOut(
                usage_id=int(row.id),
                employee_id=row.employee_id,
                work_session_id=row.work_session_id,
                model=row.model,
                units=int(row.units),
                unit_price=int(row.unit_price),
                amount=int(row.amount),
                tokens=int(row.tokens),
                duration_seconds=row.duration_seconds,
                status=row.status,
                unpaid_reason=row.unpaid_reason,
                occurred_at=row.occurred_at,
            )
            for row in rows
        ],
        "total": economy_repo.count_compute_usage(db, company_id=int(actor.ref)),
        "limit": limit,
        "offset": offset,
        "paid_total": int(totals.get("paid", 0)),
        "unpaid_total": int(totals.get("unpaid", 0)),
    }


# ---------------------------------------------------------------- 奖励（自助领取）


def _current_user_id() -> int | None:
    identity = get_request_identity()
    return identity.user_id if identity is not None else None


def _reward_error(exc: RewardError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.reason)


def _option_out(evaluation) -> dict:
    return {
        "reward_type": evaluation.reward_type.value,
        "label": evaluation.label,
        "actor_kind": evaluation.actor_kind,
        "actor_ref": int(evaluation.actor_ref),
        "amount": int(evaluation.amount),
        "currency": evaluation.currency,
        "reference_key": evaluation.reference_key,
        "claimable": bool(evaluation.claimable),
        "reason": evaluation.reason,
        "policy_version": evaluation.policy_version,
        "next_eligible_at": evaluation.next_eligible_at,
        "claimed_at": evaluation.grant.claimed_at if evaluation.grant else None,
        "grant_id": int(evaluation.grant.id) if evaluation.grant else None,
        "metadata": dict(evaluation.metadata or {}),
    }


@router.get("/rewards", response_model=RewardCatalogOut)
def list_rewards(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """本公司（与当前个人）的自助奖励清单：可领 / 已领 / 原因 / 下次可领时间。

    **只列自助可领的类型**：官方悬赏/合同/资助/采购/里程碑/活动等由各自业务流发放，
    不在这里（否则就成了"随便领钱"）。
    """
    actor = _company_actor(company_id)
    service = RewardService(db)
    items = [
        _option_out(evaluation)
        for evaluation in service.catalog(company_id=int(actor.ref), user_id=_current_user_id())
    ]
    return {"items": items}


@router.post("/rewards/{reward_type}/claim", response_model=RewardClaimOut)
def claim_reward(
    reward_type: str,
    payload: RewardClaimIn | None = None,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """领取奖励（**幂等**）：重复领取返回既有 grant（`created=false`），不会重复发钱。

    - 资格不足 → 409 + 机器可读 reason（`not_eligible` / `already_claimed` / `cooldown` / …）；
    - 非自助类型（官方类）→ 409 `not_self_service`；
    - 金额恒等于政策值（`EconomicPolicy`），请求体不能改金额（**没有金额入参**）。
    """
    actor = _company_actor(company_id)
    try:
        kind = RewardType(reward_type.upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown reward type") from exc

    service = RewardService(db)
    try:
        grant, created = service.claim(
            kind,
            company_id=int(actor.ref),
            user_id=_current_user_id(),
            reference_key=(payload.reference_key if payload else None),
        )
    except RewardError as exc:
        raise _reward_error(exc) from exc
    evaluation = service.evaluate(
        kind,
        company_id=int(actor.ref),
        user_id=_current_user_id(),
        reference_key=(payload.reference_key if payload else None),
    )
    return {
        "grant_id": int(grant.id),
        "reward_type": grant.reward_type,
        "label": evaluation.label,
        "actor_kind": grant.actor_kind,
        "actor_ref": int(grant.actor_ref),
        "amount": int(grant.amount),
        "currency": grant.currency,
        "reference_key": grant.reference_key,
        "status": grant.status,
        "policy_version": grant.policy_version,
        "ledger_transaction_id": grant.ledger_transaction_id,
        "claimed_at": grant.claimed_at,
        "posted_at": grant.posted_at,
        "created": created,
    }


def actor_kinds() -> list[str]:  # pragma: no cover - 文档/调试辅助
    return [kind.value for kind in EconomicActorKind]
