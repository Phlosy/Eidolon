"""M2.4 管理决策域：`DecisionRecord`（意图）+ `ToolAudit`（执行事实）。

**三层不混**（DR1，用户拍板）：

```text
DecisionRecord  = 管理 Agent **为什么**做出这个决定          ← 管理语义
ToolAudit       = 为执行这个决定，系统**实际执行了什么**      ← 执行事实
Domain State    = 事实最终变成了什么（tasks / assignments …） ← 真相
```

四条结构纪律：

1. **tool call ≠ decision**（DR2）：一条决策落成 N 个动作，所以 `ToolAudit.decision_id`
   是**多对一**；反过来不存在"一次工具调用自动产生一条决策"的路径。
2. **关联只有一个方向**（DR3）：`ToolAudit.decision_id → DecisionRecord.id`。
   查询某决策的执行动作一律 `WHERE decision_id = …` 反查；
   **不存在** `DecisionRecord.audit_ids[]` 这第二份关系真相。
3. **DecisionRecord 不复制执行细节**（DR5）：tool 名、入参、出参、错误全在 `ToolAudit`；
   决策行只留管理语义 + 有界上下文快照 + 状态。
4. **决策不授予权限**（DR4）：`DecisionRecord` 里写"我要 offboard Bob"**不产生任何授权**；
   每个动作执行时仍要重新走 `Actor → 任职 → AuthorityGrant → Scope → Domain Validation`。

`DecisionRecord` 的状态可以停在 `PARTIALLY_APPLIED`（DR6）—— 一条决策的多个动作
不一定都成功，表达不了"部分生效"就会逼着人用谎言覆盖事实。

**不是 append-only 的例外说明**：`status` / `resolved_at` / `outcome_note` / `superseded_by_id`
是**追加式推进**（PROPOSED → EXECUTING → 终态），语义字段（reason / intended_outcome /
context / actor / parent）**永不 UPDATE**；`ToolAudit` 行本身完全不可改写。
"""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import DecisionSemantics, DecisionStatus, ToolSideEffect


class DecisionRecord(TimestampMixin, Base):
    """一条管理决策（**管理语义**，DR1/DR5）。

    字段按用户拍板 §4 裁剪：

    | 组 | 字段 |
    | --- | --- |
    | 谁 | `actor_person_id` / `actor_employee_id` / `acting_position_assignment_id` /
      `acting_position_definition_id` / `acting_position_code` |
    | 关于什么 | `company_id` / `scope`（"kind:id"）/ `project_id` / `task_id` |
    | 决定什么 | `decision_type`（`DecisionKind`）/ `reason` / `intended_outcome` |
    | 依据什么 | `context_json`（有界快照）/ `context_hash` / `context_version` / `authority_json` |
    | 结果 | `status` / `outcome_note` / `resolved_at` |
    | 树 | `parent_decision_id` / `superseded_by_id` |

    **刻意没有**：`tool_name` / `input` / `output` / `error` / 动作计数
    —— 前者属于 `ToolAudit`（DR5）；计数是派生量，读时按 `decision_id` 反查即可，
    存下来就是第二份真相（仓库 ADR-12：派生字段不得给默认值）。
    """

    __tablename__ = "decision_records"
    __table_args__ = (
        Index("ix_decision_records_company_created", "company_id", "id"),
        Index("ix_decision_records_scope", "scope"),
        Index("ix_decision_records_parent", "parent_decision_id"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    # ---- 谁（人级 + 公司成员身份 + 职位）----
    actor_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    actor_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: 决策当时的任职行（时间轴上的那一段）—— 换人之后历史仍指向**当时**的任职
    acting_position_assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acting_position_definition_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 职位 code 快照（定义改名/下架后历史仍可读）
    acting_position_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ---- 关于什么 ----
    scope: Mapped[str] = mapped_column(String(80), default="company:0")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # ---- 决定什么 ----
    decision_type: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    #: Agent 自述的**意图**（不是判据；系统不核验它是否达成）
    intended_outcome: Mapped[str] = mapped_column(Text, default="")
    # ---- 依据什么（有界快照 + 稳定引用 + 哈希，DR9）----
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    context_hash: Mapped[str] = mapped_column(String(64), default="")
    context_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    #: 决策时刻的授权快照（`grants_hash` / `position_grants_hash`）—— 只作证据，不授予权限（DR4）
    authority_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # ---- 结果（追加式推进）----
    status: Mapped[str] = mapped_column(
        String(24), default=DecisionStatus.proposed.value, index=True
    )
    outcome_note: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ---- 决策树（DR8）----
    parent_decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ToolAudit(TimestampMixin, Base):
    """一次工具调用的**执行事实**（M2.3 起校验、M2.4 起成为正式表）。

    M2.3 把工具审计暂存在 `audit_logs.after_json`（复用既有表的最小改动）。
    M2.4 需要按 `decision_id` **反查**某条决策的执行动作 —— JSON blob 里没有可索引的列，
    所以这里落成正式表。`audit_logs` 继续承载**人/领域**动作（入职、评审、账号…）；
    `tool.*` 不再写进它（同一个事实不留两个落点）。

    字段按用户拍板 §5：tool_name / actor context / decision_id / input /
    authority check+result+grant version / execution status / output / error / timestamps。
    """

    __tablename__ = "tool_audits"
    __table_args__ = (
        # 决策反查（DR3）：唯一需要的关联查询就是这一条
        Index("ix_tool_audits_decision", "decision_id", "id"),
        Index("ix_tool_audits_tool_created", "tool_name", "id"),
        Index("ix_tool_audits_company_created", "actor_company_id", "id"),
    )

    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    #: 多对一：**执行事实指向决策意图**；没有反向数组（DR3）
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decision_records.id"), nullable=True, index=True
    )
    # ---- 执行结果 ----
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    side_effect: Mapped[str] = mapped_column(
        String(16), default=ToolSideEffect.read.value, server_default=text("'read'")
    )
    decision_semantics: Mapped[str] = mapped_column(
        String(16),
        default=DecisionSemantics.none.value,
        server_default=text("'none'"),
    )
    autonomy: Mapped[str] = mapped_column(String(24), default="auto_allowed")
    transport: Mapped[str] = mapped_column(String(16), default="internal")
    # ---- actor context（身份只能由系统注入，DR 同 T5）----
    actor_employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(24), default="internal")
    work_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- input ----
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict)
    arguments_digest: Mapped[str] = mapped_column(String(64), default="")
    # ---- authority check / result / grant version ----
    authority_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    authority_allowed: Mapped[bool | None] = mapped_column(nullable=True)
    authority_reason: Mapped[str] = mapped_column(String(64), default="")
    #: 命中的 grant 行 id 列表（"这次动作是哪几条授权批的"）
    authority_grant_ids: Mapped[list] = mapped_column(JSON, default=list)
    #: 生效授权摘要快照（可从当时 grant 行重算对拍，见 `authority.hash_effective_grants`）
    authority_grants_hash: Mapped[str] = mapped_column(String(64), default="")
    # ---- output ----
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @property
    def applied(self) -> bool:
        return self.outcome == "applied"
