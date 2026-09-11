"""M1.1 经济域持久化：复式账本底座（docs/m1-economy-design.md §10–§12b，plan §4/M1.1a）。

四张表，职责严格分层（**Ledger 是事实，Projection 是派生**）：

| 表 | 角色 |
| --- | --- |
| `ledger_accounts` | 账户（主体 × 货币 × kind × subject）；**不可删除**，只能 `closed` |
| `ledger_transactions` | 一笔业务的账务信封（幂等键 + 业务锚点 + 类型 + 币种） |
| `ledger_entries` | 借贷腿（append-only，**无 updated_at**：写入即历史，E17/E31） |
| `wallet_projection` | 可重建的操作型物化投影（快速查询 + CAS 并发控制） |

纪律（违反即契约破坏）：
- 金额一律**整数最小单位**（E21），方向只能由 `direction` 表达（禁止负金额）；
- 账户身份用 `(actor_kind, actor_ref, currency, kind, subject_ref)` 表达，**不写死 company_id**：
  未来 `user` / `npc_company` / 新 actor 类型不需要改表（§9）；
- `normal_side` 是 kind 的**派生值**（开户时落库），业务代码不得自行解释借贷方向（E26）；
- 除 `wallet_projection` 外，本模块的表都只追加，不 UPDATE/DELETE（E17/E31）；
- **没有任何域外余额字段**：财务真相只能住在这里（E1，守卫见 tests/test_m1_economy_contract.py）。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import (
    Currency,
    LedgerAccountKind,
    LedgerAccountStatus,
    LedgerTransactionStatus,
)


class LedgerAccount(TimestampMixin, Base):
    """账户（设计 §10）。

    - `actor_kind` / `actor_ref`：归属（`system` 指向 `SystemAccountKind`，`company` 指向
      companies.id，`npc_company` 指向 market_participants.id，`user` 指向 users.id）。
      **刻意不加 FK**：四个载体在不同表里，且系统账户没有宿主行
      （延续 T2 D8「系统角色不进 companies」）；
    - `subject_ref`：账户主体物（v1 只用于 escrow：= escrow id；普通账户 0）；
    - `normal_side`：由 `kind` 派生（debit-normal / issuance 为 credit-normal）；
    - `status`：`active | frozen | closed`；frozen/closed 一律拒绝过账。
    """

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint(
            "actor_kind",
            "actor_ref",
            "currency",
            "kind",
            "subject_ref",
            name="uq_ledger_account_identity",
        ),
        Index("ix_ledger_accounts_actor", "actor_kind", "actor_ref"),
    )

    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_ref: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    kind: Mapped[str] = mapped_column(String(20), default=LedgerAccountKind.actor.value)
    #: Escrow 账户的主体物 id（= escrow id）；普通账户恒为 0
    subject_ref: Mapped[int] = mapped_column(Integer, default=0)
    normal_side: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(
        String(10), default=LedgerAccountStatus.active.value, index=True
    )
    frozen_reason: Mapped[str] = mapped_column(String(200), default="")

    @property
    def is_system(self) -> bool:
        return self.actor_kind == "system"


class LedgerTransaction(TimestampMixin, Base):
    """一笔账务信封（设计 §12）。

    - `idempotency_key`：业务幂等键（部分唯一索引，允许 NULL —— 不要求所有过账都带键，
      但要求"带键的重复提交必须返回原交易"）；
    - `reference_type` / `reference_id`：业务锚点（E16：每一分钱都能解释成哪笔业务）。
      `reference_id` 用字符串以容纳非整型业务键；`reference_type` 未知时留空，不编造；
    - `initiated_by_*`：发起主体（审计用；系统发行时指向 system actor）；
    - `status`：v1 恒为 `posted`；`reversed` 留给未来 reversal 流程（不提供改金额路径）。
    """

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        Index(
            "uq_ledger_transaction_idempotency",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_ledger_transactions_reference", "reference_type", "reference_id"),
    )

    transaction_type: Mapped[str] = mapped_column(String(24), index=True)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    status: Mapped[str] = mapped_column(
        String(12), default=LedgerTransactionStatus.posted.value, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference_type: Mapped[str] = mapped_column(String(40), default="")
    reference_id: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")
    initiated_by_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    initiated_by_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class LedgerEntry(Base):
    """借贷腿（设计 §12）—— **append-only**。

    刻意不用 `TimestampMixin`：没有 `updated_at`，因为 entry 一旦写入就是历史（E17/E31）。
    金额必须 > 0（方向由 `direction` 表达，禁止用负金额记账）。
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_entries_account", "account_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"))
    direction: Mapped[str] = mapped_column(String(8))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WalletProjection(Base):
    """钱包投影（设计 §11）—— **可重建的物化缓存，不是财务事实来源**。

    - 一行 = 一个账户（`account_id` 即主键）；`currency` 冗余在此以便直接按币种查询；
    - `posted_balance`：主体总资产（含被 Escrow 锁定但仍属本主体的份额）；
    - `reserved_balance`：归因到本主体的 escrow 账户余额之和（锁定份额）；非 actor 账户恒为 0；
    - `available_balance`：`posted − reserved` = **可花额度**（消费判定与 CAS 的唯一口径）；
    - `version`：CAS 乐观并发计数（每次投影更新 +1）；
    - `last_entry_id`：最后一条导致变化的 entry（重建校验 / 追查用）。

    清空本表后必须能仅由账本三表完整重算（E29，`rebuild_wallet_projection`）。
    刻意不用 `TimestampMixin`：投影行"何时创建"没有意义（可被重建），只有 `updated_at` 有意义。
    """

    __tablename__ = "wallet_projection"

    account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    posted_balance: Mapped[int] = mapped_column(Integer, default=0)
    available_balance: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reserved_balance: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RewardGrant(TimestampMixin, Base):
    """一次奖励的资格/发放记录（M1.2，设计 §14）。

    - `unique(reward_type, actor_kind, actor_ref, reference_key)` 是**幂等的地基**（E10）：
      重复领取由约束收敛，服务层命中即返回既有 grant（绝不二次 mint）；
    - `reference_key`：同一 actor 在同一奖励类型下的"这一次"（例如 `daily:2026-09-11`、
      `achievement:first_employee`、`tutorial:company-founding`）；不同类型/不同主体互不干扰；
    - `amount` + `policy_version`：金额是**发放时的政策快照**，日后调政策不改历史（可解释性）；
    - `company_id`：奖励总是发生在某个公司上下文里（v1 单公司部署；个人奖励也记公司归属，
      便于公司作用域查询与审计）；
    - `status`：`ELIGIBLE → CLAIMED → POSTED`（VOID 留给未来人工冲正，见 §37 冻结状态机）；
    - `ledger_transaction_id`：指向真正把钱发出去的那笔账（reference 可追溯，E16）。
    """

    __tablename__ = "reward_grants"
    __table_args__ = (
        UniqueConstraint(
            "reward_type",
            "actor_kind",
            "actor_ref",
            "reference_key",
            name="uq_reward_grant_identity",
        ),
        Index("ix_reward_grants_company", "company_id", "id"),
        Index("ix_reward_grants_actor", "actor_kind", "actor_ref", "reward_type"),
    )

    reward_type: Mapped[str] = mapped_column(String(32), index=True)
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_ref: Mapped[int] = mapped_column(Integer)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    reference_key: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")
    policy_version: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(12), default="ELIGIBLE", index=True)
    ledger_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
