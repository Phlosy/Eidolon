"""T2 市场契约数据类型（术语 / 状态轴 / 只读投影）。

**冻结范围**：这些值集与字段集是 T2.0 冻结的对外契约 —— 变更必须走设计文档评审
（docs/t2-talent-market-design.md §4/§6/§8），并由 tests/test_market_contract.py 钉住。

状态轴（三轴正交，设计 §4）：
- 培养轴落库在 `character_profiles.lifecycle`（见 `app.models.enums.CultivationState`）；
- **市场轴**由 `market_listings` 的存在性派生（本模块的 `MarketState`，不落库）；
- **任职轴**由 `employments`（`PositionAssignment`）派生，不在本模块表达。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.enums import MarketListingStatus, MarketParticipantKind

#: 枚举的唯一家是 app/models/enums.py（模型与迁移要 import 它们）；这里保持
#: T2.0 冻结的导入路径可用（re-export），契约测试按此钉住。
__all__ = [
    "MarketListingStatus",
    "MarketListingView",
    "MarketParticipantKind",
    "MarketSearchQuery",
    "MarketState",
]


class MarketState(StrEnum):
    """市场态（**派生，不落库**，设计 §4.1）。

    - `unavailable`：不具备可挂牌/可招募资格（培养未完成，或已有生效任职）；
    - `unlisted`：有资格但当前没有 active listing；
    - `listed`：存在 active listing，可被市场发现。
    """

    unavailable = "unavailable"
    unlisted = "unlisted"
    listed = "listed"


@dataclass(frozen=True)
class MarketListingView:
    """挂牌索引级只读投影（**不含** traits / competency / evidence）。

    人员档案内容由 Person Read Model 提供（T2.1）—— 这里只表达"谁在市、由谁挂、
    什么档位、什么时候"，避免两套聚合逻辑（设计 §8.3）。
    """

    listing_id: int
    person_id: int
    identity_id: str
    name: str
    origin: str  # TalentOrigin 值（保持 str：跨模块边界不绑死枚举类型）
    cultivation_state: str  # CultivationState 值
    status: MarketListingStatus
    quality_tier: str | None
    listed_by_participant_id: int
    listed_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class MarketSearchQuery:
    """市场检索条件（T2.3 实现；`position_definition_id` 为 T2.5 Fit 预留）。

    无经济字段（无价格/排序权重）：T2 不做交易排序（设计 D2/D10）。
    """

    text: str | None = None
    origin: str | None = None
    quality_tier: str | None = None
    position_definition_id: int | None = None
    limit: int = 50
    offset: int = 0
