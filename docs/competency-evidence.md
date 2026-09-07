# 能力证据（Competency Evidence）

规格：`competency-system.md §6`；表落地：migration v15（P5）。
一句话：**证据是"为什么这个人是 82 分"的答案来源；能力任何变化都应能回链到证据。**

## 1. 为什么需要证据表

`employee_competencies` 上的 score/confidence 是**评估产物**。评估把一堆既有事实
（任务做成了、评审通过、测试 18/18、技能被判定有用……）折算成能力估计 —— 折算过程
如果不可追溯，分数就是黑箱。证据表把每个"输入事实"落成一行，聚合器只对这些行做
确定性计算，于是：

> "Communication 72 / confidence 68% / 17 Evidence / ↑+4" —— 每条证据都能点回源对象，
> 每个分数都能解释。

## 2. 字段

| 列 | 语义 |
| --- | --- |
| `employee_id`, `competency_definition_id` | 这条证据证明谁的哪个能力 |
| `source_kind` | `task` / `project` / `test` / `review` / `artifact` / `user_feedback` / `peer_review` / `assessment` / `learning` / `skill_usage` |
| `source_id`, `source_ref` | 指向一等对象的 id 与人类可读引用（如 `TEST-102 18/18 passed`） |
| `assessment_run_id` | 哪次考核/重算引用过它（可空） |
| `signal` | 0-100 水平观测；**NULL = 只记录"发生过"，不判分** |
| `quality` | 0..1 证据可信度；NULL = 聚合时按来源固定质量表取值 |
| `occurred_at` | 发生时间（新近度衰减的依据） |
| `metadata_json` | 附加（如 project_id，供同项目冗余折扣） |

## 3. 来源语义（哪些事实能当证据）

- **Skill ≠ Competency**，但 Skill 的**真实使用**可以成为 Competency 的证据：
  技能映射了 `competency_definition_id` 且其 SkillUsage 被**人评 useful** 后，产生
  一条 `skill_usage` 证据（幂等，`services/competency.skill_usage_evidence_for`）。
  人没确认过"有用"的用法不产生证据。
- 人评只进证据、**永不改写** `skill_usages.success` / 成功率（沿用既有铁律）。
- 任务/评审/测试/交付物的自动采集器在 P10（collect）；P5 先把证据模型与确定性
  聚合链路立起来，读面 `GET /employees/{id}/competency-evidence` 已可用。

## 4. 纪律（都有测试）

1. 证据归属正确：跨员工/跨公司不串（`test_competency_assessment.py`）。
2. 聚合确定性：同证据 + 同 `window_end` ⇒ 同 inputs_hash、同输出。
3. 无 signal 的证据不参与判分（不编分）。
4. 证据只增不伪造：没有"自动补证据"的机制。
5. 能力行只能由聚合服务从证据更新 —— 没有直接改 score 的 API。
