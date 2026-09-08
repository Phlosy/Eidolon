# 人格行为智能（Behavioral Intelligence，P11）

**Trait affects behavior, not truth。** 8 维人格通过统一 TraitPolicy → BehaviorPolicy v2
（advisory sections）改变"怎么工作"，绝不改变成功 / confidence / competency／评估。

## 边界（都有测试）
- Personality（0..1，8 维不新增）→ `TraitPolicyRegistry`（8 个 Policy，Curiosity 沿用
  behavior-v1 路径；其余 7 维新增 advisory sections）：Planning / Verification /
  Collaboration / Autonomy / Risk / Communication / Adaptation / Creativity。
- 优先级：System Safety > Company Policy > Project/Task 约束 > Budget > Personality。
  tutorial / incident / production-critical / 低预算 会收敛探索与实验（有测试）。
- 纯函数守卫：`trait_policies` 不 import model、不写库；`POLICY_VERSION` 保持
  behavior-v1（既有行为锚点），新增 `BEHAVIOR_POLICY_VERSION=v2` 只用于 advisory。
- 可解释：`GET /employees/{id}/behavior-policy`（Trait Snapshot + advisory 数值 +
  Working Style 语义 + reasons 映射）；WorkSession 存 `behavior_snapshot_json/hash`
  （为什么这次这么工作）；PROFILE.md 投影含"工作方式（行为化摘要）"。
- 明确不做：Relationship/Emotion 模拟、Mentor 关系图、人格→能力加成。

## 示例（Charlie）
Curiosity .82 / Warmth .45 / Independence .90 / Conscientiousness .91 / Collaboration .42 /
Risk .25 / Adaptability .72 / Creativity .65 ⇒
self_review_passes=2, communication_style=standard, autonomy budget=1,
experimental budget=1, alternatives=2；高独立 → 确认阈值低；tutorial 下实验归 0。
