"""Workforce domain —— 人才名册的"状态"侧（docs/talent-roster.md）。

目前只有一个组件：`WorkforceStatusResolver`（ADR-4 拍板命名）。放成独立包而不是塞进
`services/`，是因为它既不是 CRUD 服务也不是仓储 —— 它是**跨两个轴的派生规则**，
而派生规则一旦被复制进 service，就会开始各说各话。
"""

from app.workforce.status import (
    WorkforceStatusResolver,
    WorkforceView,
    derive_workforce_status,
    has_active_primary,
)

__all__ = [
    "WorkforceStatusResolver",
    "WorkforceView",
    "derive_workforce_status",
    "has_active_primary",
]
