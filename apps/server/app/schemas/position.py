"""Position schemas —— 职位域的读出契约（写入契约在 P4b 的分配工作流里）。

`CurrentPositionOut` 是**派生视图**，不是可写对象：它没有对应的 PATCH 入口，
改变它只能通过"创建/关闭 PositionAssignment"。这条边界是 ADR-1/ADR-2 的落点。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PositionORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CurrentPositionOut(PositionORMModel):
    """`employees.role` 的正式替代物。为 null ⇒ 这个人处于 `AVAILABLE`。"""

    definition_id: int
    code: str = Field(description="公司内稳定唯一，机器可引用（如里程碑负责人按 code 找）")
    name: str
    level: int
    job_family: str = Field(description="office 分区与统计口径")
    legacy_role: str | None = Field(
        default=None, description="仅用于兼容镜像；新业务禁止读（ADR-5）"
    )
    department_id: int | None = None
    department_name: str | None = None
    slot_id: int
    slot_code: str
    since: datetime
    assignment_type: str
    position_is_custom: bool = Field(
        default=False, description="True ⇒ 该职位没有 legacy role 对应，旧字段是兜底值而非真值"
    )
    # `fit` 不在这里：Fit 依赖能力域（P9）。在没有证据分数之前返回一个数字就是编造。
