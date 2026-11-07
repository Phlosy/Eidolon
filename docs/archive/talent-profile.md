# 人才档案（Talent Profile）与能力体系落地说明

一句话：**人格回答"倾向怎样工作"，能力回答"被证明能做什么"，两者永不混算。**

这是一份"实现状态"文档，规格本体见 [competency-system.md](competency-system.md)（能力目录/
语义）、[assessment-system.md](assessment-system.md)（证据→评估算法）、
[employee-brain-behavior-policy.md](employee-brain-behavior-policy.md)（人格/行为）。
与既有 `skills`（具体可复用做法）的关系也在下文。落地版本：v0.10 / migration v15（2026-09）。

---

## 1. 概念边界（不可换算，全部有测试）

```
Employee = 人
  ├── EmployeeBrain.traits        行为倾向 8 维（0..1，JSON）        —— "通常倾向怎样工作"
  ├── skills                     具体可复用技能（成功率/验证状态）    —— "会哪些具体做法"
  └── employee_competencies      被证据证明的能力（score/confidence） —— "能做什么、多大把握"

Competency  ≠ Trait   （Warmth 高 ≠ Communication 高）
Competency  ≠ Skill   （Skill 的真实使用可以成为 Evidence，但 Skill != Competency）
Score       ≠ Confidence（92 分 + 21% 置信 vs 86 分 + 94% 置信含义完全不同）
Trait       ↛ Success / Verdict / Competency（人格只改变工作方式）
Fit         ↛ Success Probability（Fit 只用于推荐/名单/向导）

数据流（唯一允许的因果链）：
  实际工作（Task / Review / Test / Artifact / SkillUsage…）
    → CompetencyEvidence
    → AssessmentRun（确定性聚合，assessment-base-v1）
    → EmployeeCompetency（score / confidence / trend）
```

## 2. 三层数值现状

| 层 | 载体 | 现状（2026-09） |
| --- | --- | --- |
| 人格（Behavioral Traits） | `EmployeeBrain.traits` + `app/brain/registry.py` | 8 维已注册；curiosity 已接 BehaviorPolicy，其余 7 维 `affects=()`（schema/UI/投影先行）。UI 数据契约：`GET /employees/{id}/traits` |
| 通用能力（General） | `competency_domains(kind=general)` 下 10 条 `competency_definitions` | 目录已种子（全局行）；员工按证据评估。**没有** `general_ability=82` 这类总分 |
| 专业能力（Professional） | `competency_domains(kind=professional)` 下 5 个领域 | 目录已种子；新增专业领域 = 数据插入，**不需要 migration** |

目录行 `company_id IS NULL` = 全局内置（全公司共享、built_in）；公司自定义域 = company 行。

## 3. 员工能力数据

- `employee_competencies`：`uq(employee_id, competency_definition_id)`；`score`(0-100,
  **NULL=未评估**，0 是"有证据表明很差")、`confidence`(0..1, NULL=未知)、`evidence_count`、
  `status`（unrated/provisional/assessed/stale；unrated **不落行**，由查询呈现）、
  `trend`（相邻两次 run 之差；NULL=无历史）、`trend_window`、`last_assessed_at/used_at`。
- 新员工**不预建行**：没有任何证据 ⇒ API 上就是 `unrated`（score=null、confidence=null、
  trend_direction=unknown）。**禁止随机初始化 60~90**（架构守卫扫 `random`）。
- 只有聚合服务（`app/services/competency.py`）能写行 —— `EmployeeCompetency(` 出现在别的
  文件会让守卫红。

## 4. 证据（Evidence）

`competency_evidence` 是"为什么这个人是 82 分"的答案来源。每条证据：employee /
competency / source_kind（task/project/test/review/artifact/user_feedback/peer_review/
assessment/learning/skill_usage）/ source_id / source_ref（人类可读，能反查）/
signal（水平观测，NULL=只记录发生过）/ quality（NULL=按来源固定质量表）/ occurred_at。

- Skill（映射了 `competency_definition_id`）的 SkillUsage 被**人评 useful** 后，
  可以幂等产生一条 skill_usage 证据（`services/competency.skill_usage_evidence_for`）。
- 采集器（把任务/评审/测试自动转证据）与完整考核档案在 P10；当前证据由
  聚合服务与测试/后续 collector 写入，读面
  `GET /employees/{id}/competency-evidence` 已可反查。

## 5. 确定性聚合（第一版，刻意简单）

`app/services/competency.py`：

- 证据权重 = 来源质量 × 新近度（半衰期 90 天）× 同项目冗余折扣；
- 观测分 = Σ(w·signal)/Σw（无 signal ⇒ NULL，不是 0）；
- confidence（0..1）只来自证据单位量/来源多样性/项目多样性/平均质量/新近度 ——
  与分数高低无关；
- 每次重算写 `assessment_runs`（evidence_ids + inputs_hash + outputs）；
  **同证据 + 同 window_end ⇒ 同 hash、同输出**（可重放）。

不做：LLM 打分、统计模型、自动晋升/推荐（P8/P9/P10）、能力衰减曲线（只允许标 stale）。

## 6. 读 API（全部只读，没有 PATCH score）

```
GET /competency-domains                目录（全局 + 公司自定义）
GET /competencies?domain_id=           能力定义
GET /employees/{id}/traits             8 维人格（value 0..1 + display + affects_execution）
GET /employees/{id}/competencies       通用 10 维（unrated 如实 null）+ 专业（动态，仅已评估）
GET /employees/{id}/competency-evidence 证据（倒序，可反查源）
```

员工能力分只能由 Assessment（聚合服务）更新；开发期造数用测试/聚合服务，不提供"手填分"入口。

## 7. 人物面板（UI，P5 基础版）

`EmployeeDetailPage` 新增 **能力** 标签（`CapabilitiesTab`）：Traits（8 维，带行为描述与
"当前不影响执行"标记）/ 通用能力 / 专业能力（Name + Score + Confidence + Trend + Evidence
Count，`未评估` 用徽章而非 0 分）/ 证据列表（可追溯）。UI 不显示"总能力分"。

## 8. 还没做（明确不背 P5 的名）

- 完整考核体系（profiles/criteria/results，P10）与自动证据采集（collect，P10）
- Position Fit / Skill Gap（P8/P11）
- 名册完整 UI（top competencies / traits_summary 进 roster 卡片，P12）
- 其余 7 个人格维度的**行为接入**（只有 curiosity 生效）
- career_events 时间轴（P6）
