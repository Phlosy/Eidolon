"""Position Profile Version —— 岗位能力画像的版本化载体（P7）。

岗位标准必然会变（2026 SE 测试权重 10% → 未来 20%）。直接 PATCH 同一批 Requirement
会让历史消失；所以标准挂在**版本**下：

    DRAFT   （可编辑）
    ACTIVE  （当前职位标准 —— 一个定义最多一个 ACTIVE，由部分唯一索引保证）
    RETIRED （历史标准，只读）

历史语义：员工在某段时间任职时，职位标准是当时的 version；`PositionAssignment` /
`AssessmentRun` 未来可通过该版本恢复"当时岗位要求什么"（P8 Historical Fit）。

系统模板 = PositionDefinition（company_id IS NULL 只读）下的 ACTIVE profile；
公司用 Clone / Apply Template 生成公司自有 Draft（公司只能编辑自己的）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PositionProfileVersion(TimestampMixin, Base):
    __tablename__ = "position_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "position_definition_id",
            "version",
            name="uq_position_profile_version",
        ),
        # 一个定义最多一个 ACTIVE（DRAFT 允许多个？不 —— 服务层保证只有一个 draft，
        # 索引只锁 ACTIVE；DRAFT 的并发由服务层查询保证，不牺牲索引给罕见场景）
        Index(
            "uq_position_profile_active",
            "position_definition_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | retired
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_note: Mapped[str] = mapped_column(String(500), default="")
    created_by_user_id: Mapped[int | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
