"""T2 市场域持久化（T2.3，docs/t2-talent-market-design.md §7 D8 / plan §4.4）。

两张表，**不做交易**（无价格/报单/结算 —— 那些属 M1，有守卫测试）：

- `market_participants`：市场参与者（玩家公司 / NPC 公司 / 系统发行方）。NPC **不写
  `companies`**（D8：companies 被大量外键引用，混入 NPC 会污染公司作用域读面）。
- `market_listings`：可发现性。一次"在市" = 一行 `active`；部分唯一索引
  `uq_market_listing_active_person` 保证同一 person 至多一条 active（并发/重试靠
  唯一索引 + ON CONFLICT 收敛，不靠"先查再插"—— 概念架构 §4 规则 7）。
"""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import MarketListingStatus, MarketParticipantKind


class MarketParticipant(TimestampMixin, Base):
    """市场参与者：谁在挂牌/招募（D8）。

    `company_id` 只在 `player_company` 上非空；NPC 与系统发行方不带公司外键
    （避免把"市场对手方"塞进公司域）。唯一索引只约束 player_company 的
    (kind, company_id) —— 幂等取用（`ensure_player_participant`）靠它兜底。
    """

    __tablename__ = "market_participants"
    __table_args__ = (
        Index(
            "uq_market_participant_company",
            "kind",
            "company_id",
            unique=True,
            sqlite_where=text("company_id IS NOT NULL"),
            postgresql_where=text("company_id IS NOT NULL"),
        ),
    )

    kind: Mapped[str] = mapped_column(
        String(20), default=MarketParticipantKind.player_company.value
    )
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    #: NPC 策略/发行方参数（T2.4/T2.7 消费）；player_company 留空
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(default=True)


class MarketListing(TimestampMixin, Base):
    """挂牌：**只表达"可被市场发现、可进入招募流程"**（设计 D2 —— 不是报单/出售要约）。

    - `status`：active/closed（`MarketListingStatus`）；关闭原因与招募回填字段让
      listing 自身成为"何时、被谁、以何理由结束"的事实来源（T2.6 招募在同一事务里关闭）；
    - `quality_tier`：发行方参数（T2.4），**不是战力**；
    - `person_id` / `listed_by_participant_id` 刻意不加 FK（D3 纪律 + 跨域引用）。
    """

    __tablename__ = "market_listings"
    __table_args__ = (
        # 同一 person 至多一条 active —— 重复挂牌/并发挂牌的收敛点（I6/I7 的地基）。
        Index(
            "uq_market_listing_active_person",
            "person_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    person_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default=MarketListingStatus.active.value, index=True
    )
    quality_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    listed_by_participant_id: Mapped[int] = mapped_column(Integer)
    listed_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    close_reason: Mapped[str] = mapped_column(String(200), default="")
    #: T2.6/T2.7c 招募回填（谁招走了）；delist 时留空。
    #: 玩家路径：company + employee 两列都写；NPC 路径（无公司行/员工行）只写 participant。
    recruited_company_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recruited_employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recruited_participant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
