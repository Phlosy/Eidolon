"""T2 人才市场（Talent Circulation / Local Talent Market）—— 领域契约。

**定位**：把已经存在的 Person/Character 从培养系统送入市场，被浏览、筛选、评价并招募，
且不复制任何"人级"资产。T2 **不是**商城、**不是**经济系统：
货币/账本/合同/escrow 属 M1，联网撮合属 M2（`MarketAdapter` 只预留抽象）。

术语冻结（docs/t2-talent-market-design.md §2）：Person（人）/ CharacterProfile（培养视角）/
Employee（公司任职视角）/ MarketListing（可发现性，不是报单）/ Recruitment（招募事务）。
`Candidate` 一词已被 P9（Employee × Position 候选分析）占用，市场侧不使用。

边界（守卫测试钉死）：
- 市场代码不得出现 M1 经济概念（wallet/ledger/price/escrow/…）；
- 市场读路径不得借用请求公司上下文（company scope）作为市场边界；
- 能力分只能由聚合器写（app 级守卫 test_competency_guards.py）。
"""

from app.talent.market.adapter import MarketAdapter
from app.talent.market.contracts import (
    MarketListingStatus,
    MarketListingView,
    MarketParticipantKind,
    MarketSearchQuery,
    MarketState,
)

__all__ = [
    "MarketAdapter",
    "MarketListingStatus",
    "MarketListingView",
    "MarketParticipantKind",
    "MarketSearchQuery",
    "MarketState",
]
