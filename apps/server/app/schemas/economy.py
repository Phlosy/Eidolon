"""经济读模型（M1.1d）—— 只读 API 的响应模型（**没有任何写模型**：无 mint/transfer 入参）。

口径（设计 §11/§32）：
- 余额字段直接来自 `wallet_projection`（快速路径），**语义与账本推导一致**：
  `posted = available + reserved`；读 API 不做跨公司聚合（公司作用域）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LedgerAccountOut(BaseModel):
    """单账户余额（含 escrow/系统账户时也返回，便于解释"钱在哪"）。"""

    account_id: int
    kind: str
    currency: str
    status: str
    subject_ref: int
    posted_balance: int
    available_balance: int
    reserved_balance: int
    version: int


class WalletOut(BaseModel):
    """主体钱包（当前登录公司的 actor 账户聚合）。"""

    actor_kind: str
    actor_ref: int
    currency: str
    posted_balance: int
    available_balance: int
    reserved_balance: int
    accounts: list[LedgerAccountOut]


class LedgerEntryOut(BaseModel):
    account_id: int
    direction: str
    amount: int


class LedgerTransactionOut(BaseModel):
    transaction_id: int
    transaction_type: str
    currency: str
    status: str
    reference_type: str
    reference_id: str
    reason: str
    amount: int
    occurred_at: datetime
    posted_at: datetime
    entries: list[LedgerEntryOut]


class LedgerTransactionPageOut(BaseModel):
    items: list[LedgerTransactionOut]
    total: int
    limit: int
    offset: int


class RewardOptionOut(BaseModel):
    """一个可领/已领奖励的当前状态（读面）。

    `claimable=false` 时 `reason` 说明原因（`already_claimed` / `not_eligible` / `cooldown` /
    `recovery_not_needed` / `reference_required`）；冷却类带 `next_eligible_at`。
    """

    reward_type: str
    label: str
    actor_kind: str
    actor_ref: int
    amount: int
    currency: str
    reference_key: str
    claimable: bool
    reason: str
    policy_version: str
    next_eligible_at: datetime | None = None
    claimed_at: datetime | None = None
    grant_id: int | None = None
    metadata: dict = {}


class RewardCatalogOut(BaseModel):
    items: list[RewardOptionOut]


class RewardClaimIn(BaseModel):
    """领取请求（可选）。成就必须带 `reference_key`；教程可指定某个教程。"""

    reference_key: str | None = None


class RewardClaimOut(BaseModel):
    """领取结果。`created=false` 表示命中幂等、复用了既有 grant（没有再次发钱）。"""

    grant_id: int
    reward_type: str
    label: str
    actor_kind: str
    actor_ref: int
    amount: int
    currency: str
    reference_key: str
    status: str
    policy_version: str
    ledger_transaction_id: int | None = None
    claimed_at: datetime | None = None
    posted_at: datetime | None = None
    created: bool


class EscrowOut(BaseModel):
    """托管状态（玩家订单）：钱锁在哪、锁了多少、还剩多少（都来自账本）。"""

    escrow_id: int
    status: str
    amount: int
    currency: str
    account_balance: int
    payee_company_id: int | None = None


class WorkOrderCreateIn(BaseModel):
    """玩家发布订单（`player_bounty` / `player_contract`）。

    **金额由发布方自己出**（发布前必须锁资，E11）；请求体不能指定 funding_mode ——
    玩家订单一律 `player_escrow`，想要"印钱"必须走官方渠道（M1.3 CLI）。
    """

    title: str
    reward_amount: int
    kind: str = "PLAYER_BOUNTY"
    description: str = ""
    requirements: dict = {}
    deliverables: dict = {}
    evaluation_mode: str = "auto"
    deadline_at: datetime | None = None


class WorkOrderOut(BaseModel):
    """工作订单（公开字段：官方订单本身不含任何公司私有数据）。"""

    work_order_id: int
    code: str
    kind: str
    title: str
    description: str
    requirements: dict = {}
    deliverables: dict = {}
    reward_amount: int
    currency: str
    funding_mode: str
    evaluation_mode: str
    status: str
    deadline_at: datetime | None = None
    issuer_actor_kind: str
    accepted_at: datetime | None = None
    submitted_at: datetime | None = None
    settled_at: datetime | None = None
    assignee_company_id: int | None = None
    issuer_company_id: int | None = None
    is_mine: bool = False
    is_issuer: bool = False
    escrow: EscrowOut | None = None
    submission_count: int = 0
    payable_amount: int = 0
    policy_version: str = ""


class WorkOrderPageOut(BaseModel):
    items: list[WorkOrderOut]
    total: int
    limit: int
    offset: int


class WorkOrderSubmissionOut(BaseModel):
    submission_id: int
    attempt: int
    company_id: int
    summary: str
    deliverables: dict = {}
    artifact_refs: list = []
    project_id: int | None = None
    created_at: datetime


class WorkOrderEvaluationOut(BaseModel):
    evaluation_id: int
    mode: str
    verdict: str
    score: int | None = None
    bonuses: dict = {}
    notes: str = ""
    created_at: datetime


class WorkOrderDetailOut(WorkOrderOut):
    """详情：仅当订单由本公司承接时附带提交/验收记录（不泄露他人交付物）。"""

    submissions: list[WorkOrderSubmissionOut] = []
    evaluations: list[WorkOrderEvaluationOut] = []


class WorkOrderSubmitIn(BaseModel):
    """提交交付物（金额不在请求体里 —— 奖励由订单与验收决定）。"""

    summary: str = ""
    deliverables: dict = {}
    artifact_refs: list = []
    project_id: int | None = None
