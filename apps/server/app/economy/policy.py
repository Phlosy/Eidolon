"""M1 经济政策（EconomicPolicy）—— 金额/费用/比例**必须**来自配置，不硬编码在 service。

docs/m1-economy-design.md §30：所有发给玩家或从玩家收取的金额都在这里定义，
并带 `policy_version` 落库（解释"为什么当年给 100k、现在给 50k"）。

值来自 `app/core/config.py::Settings`（`EIDOLON_ECONOMY_*` 环境变量），
本模块只做**校验 + 冻结封装**（M1.2 起 RewardService / FeeService / ComputeCostService 消费）。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.economy.contracts import MAX_AMOUNT, EconomyContractError, split_fee, validate_amount

#: 政策版本：任何金额/比例调整都应 bump，并写进文档与落库记录
ECONOMIC_POLICY_VERSION = "econ-1"


@dataclass(frozen=True)
class EconomicPolicy:
    """经济政策的冻结快照（不可变）。"""

    version: str
    # 发行侧（Source）
    starter_grant: int
    profile_reward: int
    company_profile_reward: int
    tutorial_reward: int
    daily_reward: int
    weekly_activity_reward: int
    recovery_grant: int
    recovery_threshold: int
    recovery_cooldown_hours: int
    official_reward_multiplier: float
    # 回收侧（Sink）
    market_fee_bps: int  # 基点（500 = 5%）
    fee_treasury_ratio: float
    fee_burn_ratio: float
    compute_credit_per_unit: int

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise EconomyContractError("policy version must not be empty")
        for name in (
            "starter_grant",
            "profile_reward",
            "company_profile_reward",
            "tutorial_reward",
            "daily_reward",
            "weekly_activity_reward",
            "recovery_grant",
            "compute_credit_per_unit",
        ):
            validate_amount(getattr(self, name))
        if not 0 <= self.market_fee_bps <= 10_000:
            raise EconomyContractError("market_fee_bps must be within [0, 10000]")
        if not 0 <= self.recovery_threshold <= MAX_AMOUNT:
            raise EconomyContractError("recovery_threshold out of range")
        if self.recovery_cooldown_hours < 0:
            raise EconomyContractError("recovery_cooldown_hours must be >= 0")
        if not 0.0 <= self.official_reward_multiplier <= 100.0:
            raise EconomyContractError("official_reward_multiplier must be within [0, 100]")
        # 比例必须守恒（拆分的整数守恒由 split_fee 保证）
        if abs((self.fee_treasury_ratio + self.fee_burn_ratio) - 1.0) > 1e-9:
            raise EconomyContractError("fee_treasury_ratio + fee_burn_ratio must equal 1")
        # 新手/兜底收益必须**远低于**官方任务：防止"靠签到比经营赚得多"（设计 §5/§16）
        if self.official_reward_multiplier <= 0:
            raise EconomyContractError("official_reward_multiplier must be > 0")

    def fee_for(self, amount: int) -> int:
        """按基点计算手续费（向下取整；0 手续费即 0）。"""
        validate_amount(amount)
        return amount * self.market_fee_bps // 10_000

    def split_fee(self, amount: int) -> tuple[int, int]:
        return split_fee(
            amount, treasury_ratio=self.fee_treasury_ratio, burn_ratio=self.fee_burn_ratio
        )


def _load_policy() -> EconomicPolicy:
    return EconomicPolicy(
        version=settings.economy_policy_version,
        starter_grant=settings.economy_starter_grant,
        profile_reward=settings.economy_profile_reward,
        company_profile_reward=settings.economy_company_profile_reward,
        tutorial_reward=settings.economy_tutorial_reward,
        daily_reward=settings.economy_daily_reward,
        weekly_activity_reward=settings.economy_weekly_activity_reward,
        recovery_grant=settings.economy_recovery_grant,
        recovery_threshold=settings.economy_recovery_threshold,
        recovery_cooldown_hours=settings.economy_recovery_cooldown_hours,
        official_reward_multiplier=settings.economy_official_reward_multiplier,
        market_fee_bps=settings.economy_market_fee_bps,
        fee_treasury_ratio=settings.economy_fee_treasury_ratio,
        fee_burn_ratio=settings.economy_fee_burn_ratio,
        compute_credit_per_unit=settings.economy_compute_credit_per_unit,
    )


@lru_cache(maxsize=1)
def economic_policy() -> EconomicPolicy:
    """当前政策快照（进程内缓存；测试/运维改配置后调用 `economic_policy.cache_clear()`）。"""
    return _load_policy()
