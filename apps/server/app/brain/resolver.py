"""BehaviorPolicyResolver：trait → 策略的**唯一**映射处（设计契约 §6）。

这里集中了全部阈值判断。retrieval / reflection / priorities / orchestrator / adapter
只消费解析结果，**绝不**再写 `if curiosity > 0.7`（§3.3 的 AST 守卫会拦住）。

不变量：
* 未注册 trait 与未声明的 policy 字段都保持默认值 —— 注册新 trait 不会改老行为；
* `resolve(None)` / 开关关闭 / 学习关闭 ⇒ DEFAULT_POLICY（回滚锚点）；
* 学习关闭只影响**运行期派生值**，不改写持久化的 traits（§13.4）。
"""

import hashlib
import json
import logging
from typing import Any

from app.brain.config import DEFAULT_CONFIG, BehaviorPolicyConfig, config_for
from app.brain.policy import (
    DEFAULT_POLICY,
    BehaviorPolicy,
    LearningPolicy,
    ReflectionPolicy,
    RetrievalPolicy,
    RuntimeBehaviorProfile,
)
from app.brain.traits import BrainTraits
from app.core.config import settings

logger = logging.getLogger(__name__)


def _brain_repo():
    """repository 层会 `from app.brain...` 写 traits，所以这里延迟导入避免循环。"""
    from app.repositories import runtimes as runtime_repo

    return runtime_repo


def resolve(brain: Any, config: BehaviorPolicyConfig = DEFAULT_CONFIG) -> BehaviorPolicy:
    """从 brain 派生策略。任何异常路径都必须落回 DEFAULT_POLICY，不能打断任务。"""
    if not settings.behavior_policy_enabled or brain is None:
        return DEFAULT_POLICY
    if not learning_enabled(brain):
        return DEFAULT_POLICY
    traits = BrainTraits.from_brain(brain)
    return _policy_from(traits, config)


def policy_for(db, employee_id: int, company: Any = None) -> BehaviorPolicy:
    """业务侧唯一入口：读 brain + 公司覆盖配置 ⇒ BehaviorPolicy。"""
    return resolve(_brain_repo().get_brain(db, employee_id), config_for(company))


def traits_for(db, employee_id: int) -> BrainTraits:
    brain = _brain_repo().get_brain(db, employee_id)
    return BrainTraits.from_brain(brain) if brain is not None else BrainTraits({})


def merge_traits(brain: Any, patch: dict[str, Any] | None) -> dict[str, Any]:
    """部分更新 traits：以**当前有效特质**（含 legacy 镜像回落）为基底叠加 patch。

    直接拿 `BrainTraits.merge(None, patch)` 会把未提交的 trait 打回注册表默认值，覆盖掉
    老行的 curiosity 镜像 —— 所以基底必须是 from_brain 的结果，不是空值。
    """
    current = BrainTraits.from_brain(brain).snapshot()
    if patch:
        current.update(patch)
    return BrainTraits.build(current)


def learning_enabled(brain: Any) -> bool:
    """learning_policy.enabled 只作为**运行期门控**读取，绝不改写持久 traits。"""
    policy = getattr(brain, "learning_policy", None) or {}
    if not isinstance(policy, dict):
        return True
    return bool(policy.get("enabled", True))


def _policy_from(traits: BrainTraits, config: BehaviorPolicyConfig) -> BehaviorPolicy:
    curiosity = traits["curiosity"]
    band = traits.band("curiosity", config.band_boundaries)
    knowledge_limit = _knowledge_limit(curiosity, config)
    retrieval = RetrievalPolicy(
        knowledge_limit=knowledge_limit,
        include_candidate_skills=curiosity >= config.candidate_skill_threshold,
        candidate_min_success_rate=config.candidate_skill_threshold,
        candidate_min_attempts=config.candidate_min_attempts,
        novel_topic_ratio=round(curiosity * config.novel_topic_ratio_max, 3),
        max_context_items=min(config.max_context_items_cap, knowledge_limit + 3),
    )
    reflection = ReflectionPolicy(
        open_question_count=min(
            config.open_question_max, _ladder(curiosity, config.open_question_max)
        ),
        alternative_hypotheses=min(
            config.alternative_hypotheses_max, _ladder(curiosity, config.alternative_hypotheses_max)
        ),
        note_style="exploratory" if curiosity >= config.band_boundaries[1] else "standard",
    )
    learning = LearningPolicy(
        followup_topics_per_task=min(
            config.followup_topics_max, _ladder(curiosity, config.followup_topics_max)
        ),
        followup_priority_score=min(
            config.priority_score_cap,
            config.priority_score_floor + _ladder(curiosity, config.priority_score_span),
        ),
        priority_score_cap=config.priority_score_cap,
        topic_source=(
            "kind_map+interest" if curiosity >= config.interest_topic_threshold else "kind_map"
        ),
    )
    runtime = RuntimeBehaviorProfile(
        trait_snapshot=tuple(sorted(traits.snapshot().items())),
        work_directives=_directives(curiosity, band, config),
        band=band,
        profile_revision=_revision(traits, config),
        policy_version=config.policy_version,
    )
    return BehaviorPolicy(
        retrieval=retrieval, reflection=reflection, learning=learning, runtime=runtime
    )


def _knowledge_limit(curiosity: float, config: BehaviorPolicyConfig) -> int:
    """以 trait=0.5 为锚点（等于改造前的常量 5），向两端连续摆动。"""
    low, high = config.knowledge_limit_range
    value = int(round(config.knowledge_limit_mid + (curiosity - 0.5) * config.knowledge_limit_span))
    return max(low, min(high, value))


def _ladder(curiosity: float, cap: int) -> int:
    """把连续 trait 量化成整数额度（0..cap），避免同一档位内出现无意义抖动。"""
    if cap <= 0:
        return 0
    return int(round(curiosity * cap))


def _directives(curiosity: float, band: str, config: BehaviorPolicyConfig) -> tuple[str, ...]:
    """工作指令风格的**唯一**渲染处 —— 与策略字段同源，措辞不外溢到业务模块（§8.3）。"""
    lines: list[str] = []
    if band == "high":
        lines += [
            "主动探索：与目标相关但非必需的信息也值得追问，不要因为「已经够了」就停。",
            "对首个解释保持怀疑：给结论前列出至少一个备选解释。",
        ]
    elif band == "moderate":
        lines += [
            "按任务需要探索：遇到关键不确定点再追问，不必为所有分支铺开。",
        ]
    else:
        lines += [
            "聚焦交付：优先用现有知识与既定流程完成验收标准，少开新战线。",
        ]
    if curiosity >= config.candidate_skill_threshold:
        lines.append("允许试用未完全验证的技能与相邻领域的经验，并把结果记录下来。")
    return tuple(lines)


def preview_policy(
    trait_values: dict[str, float] | None,
    *,
    learning_enabled: bool = True,
    company: Any = None,
) -> BehaviorPolicy:
    """把**尚未落库**的人格值换算成策略，供 UI 预览档位与额度。

    业务层（api/）只能调这里，不能自己 `resolve()`：阈值与档位算法只有一份（§3.3）。
    越界值按边界夹紧 —— 预览是"给我看极端情况"的地方，不该弹 422；
    未知 trait 仍然抛错，避免前端拼错字段名后静默显示默认档位。
    """
    if not settings.behavior_policy_enabled or not learning_enabled:
        # 与 resolve() 同一条回落路径：预览不能显示一套线上不存在的策略。
        return DEFAULT_POLICY
    values = {name: min(1.0, max(0.0, float(raw))) for name, raw in (trait_values or {}).items()}
    payload = BrainTraits.build(values)  # 未知 trait 在此抛错（§6.3 fail-closed）
    return _policy_from(BrainTraits.coerce(payload), config_for(company))


def _revision(traits: BrainTraits, config: BehaviorPolicyConfig) -> int:
    payload = json.dumps(
        {
            "traits": traits.snapshot(),
            "config": {key: value for key, value in sorted(vars(config).items())},
            "policy_version": config.policy_version,
        },
        sort_keys=True,
        default=str,
    )
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)
