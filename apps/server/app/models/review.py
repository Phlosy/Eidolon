"""M2.7 评审请求与评审事实（设计 §12 / RV1–RV8，W17 / W18）。

两张表刻意分开，因为它们**归属不同**（H6/RV3）：

| 表 | 谁写 | 内容 |
| --- | --- | --- |
| `review_facts` | **系统**（可复核） | artifact/inputs/session 等事实，**没有**通过/不通过 |
| `review_requests` | **人/Agent**（Reviewer） | 谁请谁评、结论（verdict）、理由、决策链接 |

`sessions` / `cost` 之类的事实不复制到这里：事实以**引用 + 摘要**落库，
原文仍在原处（与 M2.4 的"决策不复制执行细节"同款纪律）。

`review_requests.verdict` 是**追加式**的：一旦写下结论，行不再改写
（RV4）；改判走新一条请求，历史保留。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReviewRequest(TimestampMixin, Base):
    """一次任务级技术评审（`ReviewVerdict` 的**载体**）。"""

    __tablename__ = "review_requests"
    __table_args__ = (
        Index("ix_review_requests_task_status", "task_id", "status"),
        Index("ix_review_requests_reviewer_status", "reviewer_employee_id", "status"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    #: 谁发起（Manager / 上游 Reviewer）—— 发起人**不**决定结论
    requested_by_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    #: 谁来判断（Reviewer Agent）。系统**不**替管理层选人（W1/R2）：
    #: 发起时就必须指定，否则这次请求不成立。
    reviewer_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    #: `open` / `decided`（不复述结论，避免同一事实两个落点）
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    #: 发起理由（为什么需要这次评审）
    reason: Mapped[str] = mapped_column(Text, default="")
    #: 结论（`ReviewVerdict` 值）；`status=open` 时为 NULL
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verdict_notes: Mapped[str] = mapped_column(Text, default="")
    #: 与 M2.4 决策的单向链接：哪条决策承载/记录了这次结论（DR3 同款方向）
    verdict_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decision_records.id"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewFact(TimestampMixin, Base):
    """**系统**收集的一条评审事实（RV3/H6）。"""

    __tablename__ = "review_facts"
    __table_args__ = (Index("ix_review_facts_request_kind", "review_request_id", "kind"),)

    review_request_id: Mapped[int] = mapped_column(ForeignKey("review_requests.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    #: `REVIEW_FACT_KINDS` 之一
    kind: Mapped[str] = mapped_column(String(32))
    #: 事实载荷（结构化；**不含**判断词）
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    #: 事实来源（哪个系统组件读出来的，便于复核）
    source: Mapped[str] = mapped_column(String(64), default="system")


__all__ = ["ReviewFact", "ReviewRequest"]
