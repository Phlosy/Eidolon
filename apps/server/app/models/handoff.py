"""M2.6 Artifact Handoff 的两张事实表（设计 §14c / H1–H8，W19）。

**它们不是新的 Artifact 存储**：交付物的内容、版本、sha256 仍在
`drive_nodes` / `drive_revisions`（H1）。这里只补两类工作域事实：

| 表 | 事实 | 为什么需要 |
| --- | --- | --- |
| `task_inputs` | 「要用**谁**的产品」= 计划声明 | 产物要等上游跑完才存在，声明只能指向 Task |
| `artifact_links` | 「被谁在哪次会话用掉了」= 使用事实 | lineage / 审计 / 交接解释（G5）|

两条边界：

1. **产出归属不在这里**：它是 `drive_nodes.task_id`（不可变的一列）。
   链接表只记**使用** —— 同一个事实不留两个落点（M2.4 的审计同款纪律）。
2. **声明不是引用**（H4）：`task_inputs` 指向 Task 而不是 artifact；
   产物由系统在运行期解析（`app/work/handoff.py`）。
"""

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaskInput(TimestampMixin, Base):
    """`task_id` 声明消费 `source_task_id` 的产出（H4/H5）。

    校验（`app/work/handoff.py::declare_inputs`）：同项目、不能自消费、
    `source_task_id` 必须是 `task_id` 的 **DAG 祖先** —— 否则"输入可能还没跑"，
    那就不是一条可执行计划（H5）。
    """

    __tablename__ = "task_inputs"
    __table_args__ = (
        UniqueConstraint("task_id", "source_task_id", name="uq_task_inputs_pair"),
        Index("ix_task_inputs_source", "source_task_id", "task_id"),
    )

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    #: 产出这条输入的**上游 Task**（不是 artifact：产物还没产生，见 H4）
    source_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))


class ArtifactLink(TimestampMixin, Base):
    """一次**使用**事实：`artifact_id` 被 `task_id` 在 `work_session_id` 里消费（H3）。

    `role` 的值域刻意只有一个成员（`ArtifactLinkRole.consumed_by`）：
    产出归属是 `drive_nodes.task_id`，在这里重复一遍会让同一个事实有两个落点。
    将来若真的需要"人工附加的产出归属"，再往值域追加成员即可（append-only）。
    """

    __tablename__ = "artifact_links"
    __table_args__ = (
        Index("ix_artifact_links_artifact", "artifact_id", "role"),
        Index("ix_artifact_links_task", "task_id", "role"),
        UniqueConstraint(
            "artifact_id", "task_id", "role", name="uq_artifact_links_artifact_task_role"
        ),
    )

    artifact_id: Mapped[int] = mapped_column(ForeignKey("drive_nodes.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    #: 用在哪次会话（G5）。声明阶段的消费可能还没起会话 ⇒ 允许 NULL
    work_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_sessions.id"), nullable=True
    )
    #: 谁做的这次使用（自动交接 = 负责人；显式声明 = 发起的管理 Agent）
    actor_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")


__all__ = ["ArtifactLink", "TaskInput"]
