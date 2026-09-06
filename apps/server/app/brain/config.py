"""BehaviorPolicyConfig：阈值与分档的唯一存放处。

设计契约 §6：业务代码里**绝对不允许**出现 `if curiosity >= 0.7` 这种散落判断，
所有阈值集中在这里，并且可以被公司级策略覆盖（`Company.settings["behavior_policy"]`）。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace

from app.brain.policy import POLICY_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BehaviorPolicyConfig:
    # 分档与阈值（决策 6：候选技能开放线 0.70；low / moderate / high）
    candidate_skill_threshold: float = 0.70  # = 候选技能的成功率下限（不是 confidence 字段）
    candidate_min_attempts: int = 1
    hypothesis_threshold: float = 0.60  # 独立于候选技能线，语义不耦合（§18.3 已定）
    band_boundaries: tuple[float, float] = (0.30, 0.70)
    interest_topic_threshold: float = 0.30  # 达到此值才把 interests 当作延伸学习主题源
    # 额度上下界
    knowledge_limit_range: tuple[int, int] = (1, 10)
    # knowledge_limit 的锚点：trait=0.5 时恰好等于改造前的常量 5（DEFAULT_POLICY 保持逐字一致）
    knowledge_limit_mid: int = 5
    knowledge_limit_span: float = 8.0
    open_question_max: int = 3
    alternative_hypotheses_max: int = 2
    followup_topics_max: int = 2
    novel_topic_ratio_max: float = 0.4
    max_context_items_cap: int = 12
    # 延伸学习项分数：floor + trait*span，且永远低于失败驱动的 70
    priority_score_floor: int = 30
    priority_score_span: int = 40
    priority_score_cap: int = 69
    policy_version: str = POLICY_VERSION


DEFAULT_CONFIG = BehaviorPolicyConfig()

# 公司级覆盖可接受的字段（policy_version 由代码决定，不允许外部覆盖）。
OVERRIDABLE_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(BehaviorPolicyConfig) if f.name != "policy_version"
)

# 阈值必须“看起来像阈值”：这里集中定义值域，因为策略一旦越界比直接报错更难查。
_RATE_FIELDS = {
    "candidate_skill_threshold",
    "hypothesis_threshold",
    "interest_topic_threshold",
    "novel_topic_ratio_max",
}
_PERCENT_FIELDS = {"priority_score_floor", "priority_score_span", "priority_score_cap"}
_COUNT_FIELDS = {
    "knowledge_limit_mid",
    "knowledge_limit_span",
    "open_question_max",
    "alternative_hypotheses_max",
    "followup_topics_max",
    "max_context_items_cap",
    "candidate_min_attempts",
}
_PAIR_FIELDS = {"knowledge_limit_range", "band_boundaries"}


def _validate_number(key: str, value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} 需要数值，收到 {type(value).__name__}")
    number = float(value)
    if key in _RATE_FIELDS and not 0.0 <= number <= 1.0:
        raise ValueError(f"{key}={number} 超出 0..1")
    if key in _PERCENT_FIELDS and not 0.0 <= number <= 100.0:
        raise ValueError(f"{key}={number} 超出 0..100")
    if key in _COUNT_FIELDS and number < 0:
        raise ValueError(f"{key}={number} 不能为负")
    return number


def _validate_pair(key: str, value: object) -> tuple:
    items = tuple(value) if isinstance(value, list | tuple) else None
    if items is None or len(items) != 2:
        raise ValueError(f"{key} 需要两个元素的数组")
    low, high = (_validate_number(key, item) for item in items)
    if low > high:
        raise ValueError(f"{key} 需要递增区间")
    return (int(low), int(high)) if key == "knowledge_limit_range" else (float(low), float(high))


def _validated_kwargs(raw: Mapping[str, object]) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for key, value in raw.items():
        if key not in OVERRIDABLE_FIELDS:
            raise ValueError(f"未知的行为策略字段：{key}")
        if key in _PAIR_FIELDS:
            kwargs[key] = _validate_pair(key, value)
        else:
            kwargs[key] = _validate_number(key, value)
    return kwargs


def config_for(company: object | None) -> BehaviorPolicyConfig:
    """公司级覆盖：`Company.settings["behavior_policy"]`。

    任何一个字段非法 ⇒ **整份**回退默认值并告警（ §6 ）：半套阈值比全套默认更难排查。
    """
    settings = getattr(company, "settings", None)
    if not isinstance(settings, Mapping):
        return DEFAULT_CONFIG
    raw = settings.get("behavior_policy")
    if not isinstance(raw, Mapping) or not raw:
        return DEFAULT_CONFIG
    try:
        config = replace(DEFAULT_CONFIG, **_validated_kwargs(raw))
    except (TypeError, ValueError) as exc:  # 脏配置绝不能打挂任务派发
        logger.warning("公司行为策略覆盖无效（整份回退默认）：%s", exc)
        return DEFAULT_CONFIG
    if not (
        config.knowledge_limit_range[0]
        <= config.knowledge_limit_mid
        <= config.knowledge_limit_range[1]
    ):
        logger.warning("knowledge_limit_mid 不在 knowledge_limit_range 内，回退默认")
        return DEFAULT_CONFIG
    if config.priority_score_cap >= 70:  # 与 FAILURE_PRIORITY_SCORE 的关系是契约，不是巧合
        logger.warning("priority_score_cap 不能抬到失败优先级以上，回退默认")
        return DEFAULT_CONFIG
    return config
