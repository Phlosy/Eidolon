"""Employee Brain → Behavioral Policy（契约见 docs/employee-brain-behavior-policy.md）。

分层（§3.1）：持久层 EmployeeBrain → 契约层 BrainTraits → 声明层 TraitSpec → 解析层
resolve() → 策略层四子策略 → 消费层 retrieval / reflection / priorities / adapter 出口。

业务代码只允许消费本包导出的 BehaviorPolicy，禁止直接读单个 trait（§3.3 有 AST 守卫）。
"""

from app.brain.config import DEFAULT_CONFIG, BehaviorPolicyConfig, config_for
from app.brain.policy import (
    BRAIN_EDITABLE_FIELDS,
    DEFAULT_POLICY,
    POLICY_VERSION,
    BehaviorPolicy,
    LearningPolicy,
    ReflectionPolicy,
    RetrievalPolicy,
    RuntimeBehaviorProfile,
)
from app.brain.registry import TRAIT_REGISTRY, TraitSpec, trait_spec
from app.brain.resolver import merge_traits, policy_for, preview_policy, resolve, traits_for
from app.brain.traits import BrainTraits, TraitOutOfRange, UnknownTrait, write_traits_to_brain

__all__ = [
    "BRAIN_EDITABLE_FIELDS",
    "DEFAULT_CONFIG",
    "DEFAULT_POLICY",
    "POLICY_VERSION",
    "BrainTraits",
    "BehaviorPolicy",
    "BehaviorPolicyConfig",
    "LearningPolicy",
    "ReflectionPolicy",
    "RetrievalPolicy",
    "RuntimeBehaviorProfile",
    "TRAIT_REGISTRY",
    "TraitSpec",
    "config_for",
    "merge_traits",
    "policy_for",
    "TraitOutOfRange",
    "UnknownTrait",
    "preview_policy",
    "resolve",
    "trait_spec",
    "traits_for",
    "write_traits_to_brain",
]
