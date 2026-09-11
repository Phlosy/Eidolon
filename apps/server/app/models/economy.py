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

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import (
    ContractStatus,
    ContractType,
    Currency,
    EscrowStatus,
    EvaluationMode,
    EvaluationVerdict,
    FundingMode,
    LedgerAccountKind,
    LedgerAccountStatus,
    LedgerTransactionStatus,
    OfferStatus,
    WorkOrderKind,
    WorkOrderStatus,
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
    #: 业务类别（`EconomicCategory`，M1.5）：报表/观测的一等分类（NULL = 未分类）
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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


class WorkOrder(TimestampMixin, Base):
    """统一工作订单（M1.3 官方工作市场；设计 §17/§18）。

    **一个模型承载官方/玩家/NPC 的任务**，靠 `kind` / `funding_mode` / `evaluation_mode` 区分；
    M1.3 只开放官方（`issuer=system`、`funding_mode=system_mint`、结算时 mint）——
    玩家市场（Escrow 锁资、绝不 mint，E8/E11）在 M1.4 落地。

    纪律（设计 §17）：**金额、状态、双方、期限、资助模式是一等列，绝不塞进 JSON**；
    JSON 只放"需求/交付物"这类自由结构。
    """

    __tablename__ = "work_orders"
    __table_args__ = (
        Index("ix_work_orders_status_deadline", "status", "deadline_at"),
        Index("ix_work_orders_kind_status", "kind", "status"),
        Index("ix_work_orders_assignee", "assignee_actor_kind", "assignee_actor_ref"),
    )

    code: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default=WorkOrderKind.official_bounty.value)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    #: 需求/交付物（自由结构；金额与状态不在这里）
    requirements_json: Mapped[dict] = mapped_column(JSON, default=dict)
    deliverables_json: Mapped[dict] = mapped_column(JSON, default=dict)

    reward_amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    #: 官方发行上限快照（发布时的政策值，便于事后解释"为什么当时能发"）
    policy_version: Mapped[str] = mapped_column(String(40), default="")

    issuer_actor_kind: Mapped[str] = mapped_column(String(20), default="system")
    issuer_actor_ref: Mapped[int] = mapped_column(Integer, default=0)
    funding_mode: Mapped[str] = mapped_column(String(20), default=FundingMode.system_mint.value)
    evaluation_mode: Mapped[str] = mapped_column(String(12), default=EvaluationMode.auto.value)

    status: Mapped[str] = mapped_column(String(16), default=WorkOrderStatus.open.value, index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignee_actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assignee_actor_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 承接方关联的项目（复用既有 Project/Task/Artifact 体系；刻意不加 FK，保持域解耦）
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 结算交易（一次性；结算幂等由 settlement_key = ledger idempotency_key 保证，E12）
    settlement_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class WorkOrderSubmission(TimestampMixin, Base):
    """提交（一次尝试一条；被拒后可再提，attempt 递增）。"""

    __tablename__ = "work_order_submissions"
    __table_args__ = (Index("ix_work_order_submissions_order", "order_id", "id"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    #: 第几次提交（1 起；被拒后重提递增，便于审计"改了几版"）
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    deliverables_json: Mapped[dict] = mapped_column(JSON, default=dict)
    #: 产出物引用（artifact / 文件 / 链接；只存引用，不复制内容）
    artifact_refs: Mapped[list] = mapped_column(JSON, default=list)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Evaluation(TimestampMixin, Base):
    """验收（设计 §20）：**只记录判定，绝不改钱**（奖励发放走 Settlement/Reward/Ledger）。"""

    __tablename__ = "evaluations"
    __table_args__ = (Index("ix_evaluations_order", "order_id", "id"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("work_order_submissions.id"))
    mode: Mapped[str] = mapped_column(String(12), default=EvaluationMode.auto.value)
    criteria_json: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str] = mapped_column(String(12), default=EvaluationVerdict.approved.value)
    #: 奖励加成（如 {"early_delivery": 1000}）；最终奖励 = base + Σbonus（设计 §20）
    bonuses_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluated_by_actor_kind: Mapped[str] = mapped_column(String(20), default="system")
    evaluated_by_actor_ref: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class Escrow(TimestampMixin, Base):
    """托管（M1.4，设计 §19/§23）：**玩家之间的钱先锁在独立账户里，条件满足才放款**。

    - 每个 Escrow 一个独立的 `ledger_accounts` 行（`kind=escrow`、归 system actor、
      `subject_ref = escrow.id`）—— 资金既不属于付款人也不属于收款人（E7）；
    - `payer` 出资、`payee` 承接；`amount` 即订单奖励（锁资产 = 奖励额，不多不少）；
    - `status`：`UNFUNDED → FUNDED → RELEASED | REFUNDED | EXPIRED`（M1.0 冻结表）；
      **release 与 refund 的竞争由 CAS 裁定**（§33：只有一个能成功）；
    - 释放/退回后 escrow 账户余额必须归零（E25），`reserved` 通过账本归因自动回落（E30）；
    - 玩家间转移**绝不 mint**（E8）：这里的钱是 `escrow_fund` 从付款人账户移出来的。
    """

    __tablename__ = "escrows"
    __table_args__ = (
        # 一个订单/一个合同一个 Escrow（重复发布/重试由唯一约束收敛）
        UniqueConstraint("work_order_id", name="uq_escrow_work_order"),
        Index(
            "uq_escrow_contract",
            "contract_id",
            unique=True,
            sqlite_where=text("contract_id IS NOT NULL"),
            postgresql_where=text("contract_id IS NOT NULL"),
        ),
        Index("ix_escrows_status", "status"),
        Index("ix_escrows_payer", "payer_actor_kind", "payer_actor_ref"),
        Index("ix_escrows_status_expires", "status", "expires_at"),
    )

    #: 服务的业务对象（M1.4 只服务 WorkOrder；M1.6 的 Contract 复用同一张表）
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    #: 合同托管（M1.6）：**权威指针在这里**（合同读面通过它反查）；刻意不在 contracts 上
    #: 再放一个 escrow_id —— 两个指针会漂移
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", name="fk_escrows_contract_id"), nullable=True
    )
    payer_actor_kind: Mapped[str] = mapped_column(String(20))
    payer_actor_ref: Mapped[int] = mapped_column(Integer)
    payee_actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payee_actor_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)

    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    status: Mapped[str] = mapped_column(String(12), default=EscrowStatus.unfunded.value)

    #: 该笔托管自己的账本账户（subject_ref = escrow.id）
    escrow_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_accounts.id"), nullable=True
    )
    funded_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    released_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    refunded_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )

    funded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ComputeUsage(TimestampMixin, Base):
    """算力计量（M1.5，设计 §26）：**持续型 Sink** 的事实来源。

    - 计量总是发生（Agent 跑过就是事实），扣款尽力而为：`status ∈ {paid, unpaid}`；
    - `unpaid` 不是"免费"：它是一笔未清的成本（M1.9 的欠费/停服策略据此决策），
      而且**绝不产生负余额**（E24）；
    - `units` × `unit_price` = `amount`（整数最小单位；v1 的 1 unit = 1 分钟运行时），
      未来接 provider 真实成本映射时只改 `unit_price` 的来源（§26）；
    - `idempotency_key` 唯一：同一 session/任务重放不会重复扣款（E12 同族）。
    """

    __tablename__ = "compute_usage"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_compute_usage_idempotency"),
        Index("ix_compute_usage_company", "company_id", "id"),
        Index("ix_compute_usage_status", "status"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 关联的运行时/会话（只存引用，不复制内容）
    work_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_instance_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str] = mapped_column(String(120), default="")

    units: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(12), default="paid")
    unpaid_reason: Mapped[str] = mapped_column(String(60), default="")
    #: 扣款交易（treasury / burn 两条腿各自的交易；未扣款时为空）
    treasury_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    burn_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Contract(TimestampMixin, Base):
    """通用商业合同（M1.6，设计 §21）：工作/人才/服务/采购/科研共享一个核心。

    - **对价 `consideration_amount` 是一等列**（不塞 `terms_json`）：可查、可约束、可结算；
    - `parties` 用 actor 列表达（issuer/contractor）：`issuer` 必填，`contractor` 接受后回填；
    - 托管资金不从合同行上指 `escrow_id`（权威指针在 `escrows.contract_id`，避免两个指针漂移）；
    - `status` 走 M1.0 冻结状态机（§37）：
      `DRAFT → PENDING_ACCEPTANCE → ACTIVE → FUNDED → FULFILLED → SETTLING → SETTLED`
      （另有 CANCELLED / EXPIRED / FAILED / DISPUTED）；
    - `settled_transaction_id` 指向终局交易（E16）；结算幂等靠
      `settlement_key = contract:<id>`（E12）。
    """

    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_status_expires", "status", "expires_at"),
        Index("ix_contracts_issuer", "issuer_actor_kind", "issuer_actor_ref"),
        Index("ix_contracts_contractor", "contractor_actor_kind", "contractor_actor_ref"),
    )

    code: Mapped[str] = mapped_column(String(60), unique=True)
    contract_type: Mapped[str] = mapped_column(String(20), default=ContractType.work.value)
    title: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(Text, default="")
    #: 条款细节（自由结构）；金额/双方/状态/期限都是一等列（§21）
    terms_json: Mapped[dict] = mapped_column(JSON, default=dict)

    consideration_amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    policy_version: Mapped[str] = mapped_column(String(40), default="")

    issuer_actor_kind: Mapped[str] = mapped_column(String(20))
    issuer_actor_ref: Mapped[int] = mapped_column(Integer)
    contractor_actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contractor_actor_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(24), default=ContractStatus.draft.value, index=True)
    #: 业务锚点（订单/Offer/自定义）
    reference_type: Mapped[str] = mapped_column(String(40), default="")
    reference_id: Mapped[str] = mapped_column(String(64), default="")

    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settlement_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Offer(TimestampMixin, Base):
    """出价/申请（M1.6，设计 §22）：人才出价、合同申请、报价都用它。

    **Offer 本身不产生资金流**：被接受后生成 `Contract`（`contract_id` 回填）。
    锚点三选一（`work_order_id` / `listing_id` / 无锚点 = 直接报价给某主体）。
    """

    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_work_order", "work_order_id", "id"),
        Index("ix_offers_listing", "listing_id", "id"),
        Index("ix_offers_from", "from_actor_kind", "from_actor_ref"),
    )

    contract_type: Mapped[str] = mapped_column(String(20), default=ContractType.work.value)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    #: T2 挂牌（人才出价，M1.7 使用）；刻意不加 FK（跨域引用，T2 纪律）
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    from_actor_kind: Mapped[str] = mapped_column(String(20))
    from_actor_ref: Mapped[int] = mapped_column(Integer)
    to_actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_actor_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)

    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(12), default=Currency.credit.value)
    message: Mapped[str] = mapped_column(Text, default="")
    terms_json: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(12), default=OfferStatus.open.value, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 被接受后生成的合同（Offer 自己不产生资金流，§22）
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", name="fk_escrows_contract_id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
