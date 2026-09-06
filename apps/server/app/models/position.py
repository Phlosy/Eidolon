"""Position domain (人才名册 × 组织经营 的"组织"侧).

领域规格：`docs/position-system.md`；拍板记录：`docs/workforce-domain-refactor.md` §11。

三句话说明本模块的边界：

- `PositionDefinition` = **公司需要什么职位**（模板，不代表任何具体人）。
- `PositionSlot` = **哪个部门开第几号编制**（有限的组织资源；占用态是派生的）。
- `PositionAssignment` = **这段时间这个人承担这个坑** —— 物理表仍叫 `employments`
  （拍板 ADR-1：不改表名，避免 SQLite rename 牵扯 FK；"两份任职历史"靠单一实体映射已消除）。
  它就是唯一的任职关系 Source of Truth：调岗 / 晋升 / 降职 / 代理 / 兼任只操作这一条时间轴。

人级数据（brain / traits / runtime / provider / memory / skills / workspace / 成长史）
**不在本模块**，也不得被职位变更删除或重置。

本阶段刻意**不定义 ORM relationship()**：任职是时间轴，关系属性很容易诱导出 N+1 与
"随手取第一个" 的错误语义；读取一律走 `app/repositories/position.py` 的显式查询。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import (
    AssignmentType,
    OccupancyStatus,
    SlotAdministrativeStatus,
    TemplateScope,
)


class PositionDefinition(TimestampMixin, Base):
    """职位模板。`code` 在公司内稳定唯一，机器可引用（例如按 code 找里程碑负责人）。"""

    __tablename__ = "position_definitions"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_position_definition_code"),)

    # 内置模板 scope=system 且 company_id 为空；公司采用时**复制**成 company 行，
    # 绝不共享可变行 —— 否则改一次内置模板会污染所有公司的历史。
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    template_scope: Mapped[str] = mapped_column(String(20), default=TemplateScope.company.value)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000), default="")
    # job_family 决定 office 分区与统计口径（engineering/research/product/management/…）
    job_family: Mapped[str] = mapped_column(String(50), default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    career_path_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # 仅用于迁移与兼容镜像（映射到旧 EmployeeRole）；新业务禁止读（ADR-5 守卫锁死）。
    legacy_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # `assessment_profile_id` 留给 v17（考核表落地时再加 FK）：
    # 提前挂一个指向不存在表的 FK，会让本迁移在空库路径上变成一句谎话。


class PositionDefinitionPackage(Base):
    """职位默认权限包（取代 `lifecycle/access.py` 里 `ROLE_TO_PACKAGE_SLUG` 硬映射）。"""

    __tablename__ = "position_definition_packages"
    __table_args__ = (
        UniqueConstraint("position_definition_id", "package_id", name="uq_position_package"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    package_id: Mapped[int] = mapped_column(ForeignKey("access_packages.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PositionSlot(TimestampMixin, Base):
    """岗位编制：`Engineering / Software Engineer #1`。"""

    __tablename__ = "position_slots"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "position_definition_id",
            "headcount_index",
            name="uq_position_slot_index",
        ),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    slot_code: Mapped[str] = mapped_column(String(100), default="")
    headcount_index: Mapped[int] = mapped_column(Integer, default=1)
    # 只允许行政态；占用态由 derive_occupancy() 算（ADR-2：VACANT/OCCUPIED 无处可写）。
    administrative_status: Mapped[str] = mapped_column(
        String(20), default=SlotAdministrativeStatus.planned.value
    )
    manager_slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_slots.id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PositionAssignment(Base):
    """任职关系 —— **物理表 `employments`**（拍板 ADR-1）。

    行永不更新式改写：调岗是"关旧行 + 开新行"（`effective_to` 收尾），
    所以本表同时是职业履历的权威来源。
    """

    __tablename__ = "employments"
    __table_args__ = (
        # 两个**部分唯一索引**必须写进模型，否则 `alembic check` 会把它们当成
        # "库里多出来的东西"要求删除 —— 它们是这个设计的完整性地基，不是装饰。
        # WHERE 子句只约束 PRIMARY ⇒ 兼任 / 代理将来直接可用，不需改表。
        Index(
            "uq_employment_slot_primary",
            "position_slot_id",
            unique=True,
            sqlite_where=text(
                "position_slot_id IS NOT NULL AND effective_to IS NULL"
                " AND assignment_type = 'primary'"
            ),
            postgresql_where=text(
                "position_slot_id IS NOT NULL AND effective_to IS NULL"
                " AND assignment_type = 'primary'"
            ),
        ),
        Index(
            "uq_employment_employee_primary",
            "employee_id",
            unique=True,
            sqlite_where=text(
                "effective_to IS NULL AND assignment_type = 'primary' AND is_primary = 1"
            ),
            postgresql_where=text(
                "effective_to IS NULL AND assignment_type = 'primary' AND is_primary = true"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    # deprecated：v0.4 的 positions.id，仅为"旧代码继续跑"保留；新写入由 slot 派生（P6 清除）。
    # 刻意不加 index：这一列只会被逐步停用，为它建索引就是把噪声写进库里。
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    manager_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    employment_status: Mapped[str] = mapped_column(String(50), default="active")
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)
    effective_from: Mapped[datetime] = mapped_column(default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(nullable=True, index=True)

    # ---- v14 新增：把"任职"从"部门归属"里真正剥离出来
    #
    # 这四个非空列**必须带 server_default**：`employments` 已有历史行，
    # 没有库级默认值时 ADD COLUMN NOT NULL 要么直接失败，要么逼我们回填前先把表锁重建成
    # nullable（那会多一次无意义的表重建）。模型与迁移两边写同一个默认值，
    # `alembic check` 才不会报漂移。
    # 刻意**不声明外键**：本项目从不 `PRAGMA foreign_keys=ON`，SQLite 的 FK 根本不参与约束，
    # 而给已有历史表加 FK 会迫使 alembic 用 batch 重建 `employments`（它原有的 FK 全是
    # 匿名约束，重建时报 "Constraint must have a name"）。用一次高风险的表重建去换一个
    # 不生效的装饰，是纯亏。完整性由服务层 + 下面两个部分唯一索引保证。
    position_slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assignment_type: Mapped[str] = mapped_column(
        String(20),
        default=AssignmentType.primary.value,
        # 两侧（模型与迁移）都写成同一个 SQL 字面量：autogen 比的是规范化文本，
        # 用 Python 字符串会被渲染成另一种形式，`alembic check` 就会假报漂移。
        server_default=text("'primary'"),
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    # 操作者：可能是 employee id 也可能是 user id，用 metadata_json.assigned_by_kind 区分，
    # 因此这里**不加 FK**（加了就会把"人类管理员"这类合法来源变成约束错误）。
    assigned_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    # 任命当时的职位名：定义后来改名/下架，历史仍然可读（不拿今天的名字解释昨天的事）。
    position_title_snapshot: Mapped[str] = mapped_column(
        String(200), default="", server_default=text("''")
    )

    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    @property
    def status(self) -> str:
        """对外统一用 `status`；底层复用 `employment_status`，不再加一个同义列。"""
        return self.employment_status

    @property
    def is_active(self) -> bool:
        return self.effective_to is None


def derive_occupancy(
    slot: PositionSlot, active_primary_assignments: list[PositionAssignment]
) -> OccupancyStatus:
    """编制占用态：纯函数，**没有对应的写入口**。

    `FROZEN` 不映射成 `VACANT`：冻结的坑不该出现在招聘建议里，
    把它算成空缺会诱导用户去填一个不该填的坑。
    """
    if slot.administrative_status == SlotAdministrativeStatus.closed.value:
        return OccupancyStatus.closed
    if slot.administrative_status == SlotAdministrativeStatus.frozen.value:
        return OccupancyStatus.frozen
    occupied = any(
        assignment.is_primary and assignment.is_active and assignment.position_slot_id == slot.id
        for assignment in active_primary_assignments
    )
    return OccupancyStatus.occupied if occupied else OccupancyStatus.vacant
