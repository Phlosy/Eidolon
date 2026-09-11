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
