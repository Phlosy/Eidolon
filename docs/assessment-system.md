# 考核体系（Assessment System）

上级约束：`docs/workforce-domain-refactor.md`（ADR-7/8/9）
一句话：**Work → Evidence → Assessment → Competency Update**。
任何绕过 Assessment 直接改 `employee_competencies.score` 的路径都不存在（写入口只有一个）。

---

## 1. 四张表 + 一条链

```
AssessmentProfile   考核档案（一个职位怎么考核：维度 + 权重）
  └── AssessmentCriterion   考核维度（权重合计 ≈ 1）
AssessmentRun       一次考核（谁、对谁、依据哪些证据、算法版本）
  ├── AssessmentResult      每个维度的观测（score/置信度/证据摘要）
  └── CompetencyEvidence     本次 run 采集或引用的证据
```

### 1.1 `assessment_profiles`

`id`、`company_id?`（NULL=内置模板）、`code`(uq)、`name`、`description`、
`applies_to_kind`(`position`|`initial`)、`position_definition_id?`、`min_evidence_count`、
`half_life_days`（证据新近度衰减半衰期，§4.2）、`algorithm_version`(`assessment-v1`)、
`built_in`、`created_at/updated_at`。

### 1.2 `assessment_criteria`

`profile_id`、`code`、`name`、`weight`(0-1)、`competency_definition_id?`
（该维度**贡献给哪个能力**；为空 = 只进报告不改进能力，例如"交付准时性"这类观察项）、
`evidence_kinds(JSON)`、`order`、`uq(profile_id, code)`。

一条 criterion 可以贡献给多个 competency 吗？第一版：**一对多**用 `assessment_criterion_competencies`
（`criterion_id`、`competency_definition_id`、`share` 0-1，同一 criterion 的 `Σshare ≤ 1`）。
理由：`Delivery Reliability` 同时轻微支撑 `execution` 与 `quality_reliability` 是真实情况，
硬压成一一对应会逼设计扭曲。约束由 `validate_profile()` 在写入与启动时检查。

### 1.3 `assessment_runs`

`id`、`company_id`、`employee_id`、`profile_id`、`position_assignment_id?`、
`triggered_by`(`manual`|`scheduled`|`task_batch`|`initial`|`promotion_review`)、
`status`(`pending`|`running`|`completed`|`failed`|`superseded`)、
`window_from`/`window_to`（本 run 只看这段时间的证据）、
`evidence_ids(JSON)`（**采集到的证据 id 清单，可重放的关键**）、
`algorithm_version`、`inputs_hash`（sha256(evidence ids + weights + config) → 同输入同输出的证明）、
`started_at`/`finished_at`、`error`、`metadata_json`。

`inputs_hash` 是"可追溯"的硬保证：同一 hash 必须产出同一结果，测试直接重放验证。

### 1.4 `assessment_results`

`run_id`、`criterion_id`、`competency_definition_id?`、`observed_score`(0-100, 可 NULL)、
`confidence`(0-1)、`evidence_count`、`contribution`（本 run 对能力分的净变化，事后记录，便于回答
"这次考核把 testing 从 78 抬到 81"）、`rationale`（人类可读：引用了哪些证据）、`metadata_json`。

---

## 2. 内置档案（§26–§28）

### 2.1 `software_engineer`

| criterion            | weight | 贡献能力                                          | 证据来源                         |
| -------------------- | ------ | ------------------------------------------------- | -------------------------------- |
| Correctness          | 20%    | `analysis_problem_solving`, `quality_reliability` | 验收 verdict、测试通过、缺陷回归 |
| Requirement Coverage | 15%    | `analysis_problem_solving`                        | 需求追溯、任务完成范围           |
| Testing              | 15%    | `testing`, `quality_reliability`                  | 测试执行、覆盖率变化             |
| Code Quality         | 15%    | `code_quality`                                    | Review 结果、静态检查            |
| Architecture         | 10%    | `system_architecture`                             | Review 意见、设计文档采纳        |
| Delivery Reliability | 10%    | `execution`                                       | 按时交付、阻塞处理、返工率       |
| Problem Solving      | 10%    | `analysis_problem_solving`, `decision_making`     | 方案数、被采纳方案               |
| Efficiency           | 5%     | `efficiency_resource_awareness`                   | token/成本/耗时                  |

### 2.2 `researcher`

`Research Quality 20%`、`Source Quality 20%`、`Evidence Coverage 15%`、`Accuracy 15%`、
`Synthesis 15%`、`Insight 5%`、`Communication 10%` → 主要贡献 `evidence_synthesis`、
`source_evaluation`、`information_retrieval`、`technical_writing`、`communication`。

### 2.3 `ceo_manager`

`Planning 15%`、`Delegation 15%`、`Decision Quality 20%`、`Resource Allocation 15%`、
`Project Outcome 15%`、`Risk Management 5%`、`Team Development 5%`、`Communication 5%`
→ 贡献 `management_leadership`、`decision_making`、`planning_organization`、`strategic_planning`、`delegation`。

**关键规则（§28）**：管理能力的证据只能来自**实际管理行为**（分派被接受、下属任务结果、
资源调度记录、评审决策）与项目结果。
`assign(employee, ceo_slot)` 这个动作**本身不产生任何能力证据** —— 当上 CEO 不会让
`management_leadership` 自动上升。这条要写成测试（§6 #3），因为它是最容易被"顺手加个职位加成"破坏的地方。

### 2.4 `initial_assessment`（§49）

`applies_to_kind=initial`，维度小集合（coding / testing / architecture 三个小任务）。
本阶段只建**框架**（profile + run + criteria + 手写证据录入端点），不实现自动出题与自动判分；
文档与 UI 都要如实写"当前需要人工提交证据"。

---

## 3. 证据采集（`app/assessment/collect.py`）

只读既有事实源，不新增业务写入：

| 源          | 现有对象                                                                      | 采集为                                              |
| ----------- | ----------------------------------------------------------------------------- | --------------------------------------------------- |
| 任务与验收  | `tasks`、`reviews`、`ReviewDecision`                                          | `TASK` / `REVIEW`                                   |
| 交付物      | `artifacts`、`project_delivery`                                               | `ARTIFACT`                                          |
| 测试与构建  | task kind=testing 的 verdict、CI 记录                                         | `TEST`                                              |
| 技能使用    | `skill_usages`（behavior-v1 刚建，含 `outcome`/`selection_reason`）           | `LEARNING` / `TASK`                                 |
| 学习记录    | `learning_records`（含 `question`）                                           | `LEARNING`                                          |
| 返工        | task 重开、评审打回次数                                                       | `TASK`（负向 signal）                               |
| 成本        | runtime token/耗时                                                            | `TASK`                                              |
| 人工反馈    | `PATCH /skill-usages/{id}/outcome` 那类人评（`outcome_source=manual_rating`） | `USER_FEEDBACK`（**永不改 success**，沿用已有铁律） |
| Peer review | review 记录中"评审者=该员工"                                                  | `PEER_REVIEW`                                       |

每条证据的 `signal` 由采集器按规则算，规则表**必须**导出到 UI（点"证据"能看到为什么是 82）。

---

## 4. 聚合算法（§47，第一版刻意简单）

### 4.1 证据权重

```
w(e) = source_quality(e) × recency(e) × redundancy_discount(e)
source_quality: ASSESSMENT 1.0 / TEST 0.9 / REVIEW 0.85 / ARTIFACT 0.8 /
                PEER_REVIEW 0.75 / TASK 0.7 / USER_FEEDBACK 0.6 / LEARNING 0.5
recency(e)   = 0.5 ** (age_days(e) / profile.half_life_days)        # 默认半衰期 90 天
redundancy_discount: 同一 source_kind + 同一 project 内第 n 条 → 1/(1 + 0.5(n-1))   # 防止刷同类证据
```

### 4.2 criterion 观测分

```
n = Σ w(e)                                    对 criterion 关联的证据求和
observed = Σ (w(e) × signal(e)) / n           n == 0 ⇒ observed = NULL（不是 0）
criterion_confidence = clamp(n / (n + K), 0, 1)   K = 3（3 单位证据 ≈ 0.5 置信度）
```

### 4.3 能力分更新（有界 EMA，可解释）

```
raw_new       = Σ over criteria (share(c,i) × observed(c))   # 该 competency 收到的观测
delta         = raw_new − score_old
step          = clamp(LEARN_RATE × criterion_confidence, 0.02, 0.35)   # LEARN_RATE = 0.25
score_new     = round(score_old + step × delta)              # 证据不足时几乎不动
UNRATED 首次  = score_new = raw_new，status = PROVISIONAL（若 conf < 0.4）否则 ASSESSED
```

`delta` 与 `step` 都写进 `assessment_results.contribution` / `metadata_json`，
所以"为什么只涨 1.2 分"可以精确回答：**因为证据少**。

### 4.4 不做的事

- 不做 `score += 5`；不做线性"完成 N 个任务升一级"；不做能力上限解锁。
- 不引入模型打分（LLM 判证据质量是后续课题，本阶段的 `source_quality` 是**固定表**，可预测）。

---

## 5. Confidence 与 Trend（§22/§29/§48）

### 5.1 能力级 confidence（Score 与 Confidence 永不混合）

```
conf = clamp(0.35 × min(1, n_units / 8)
           + 0.20 × min(1, distinct_source_kinds / 4)
           + 0.15 × min(1, distinct_projects / 3)
           + 0.20 × avg(source_quality)
           + 0.10 × recency_coverage, 0, 1)
```

单调性有测试保证：任何一条证据的加入不得使 confidence 下降（`recency` 例外会衰减，因此
衰减只发生在**时间推进**时，不由新证据引起 —— 这条区分很重要，否则会出现"多干活反而更不确定"）。

### 5.2 Trend

```
trend = score(本次 run) − score(上一次 completed run)      仅当两次 run 都 ASSESSED
窗口  trend_window = 参与比较的 run 数（默认相邻两次；UI 可切 3 次移动平均）
无历史 ⇒ trend = NULL，UI 显示 →（不要显示 ↑+0）
```

趋势**不预测**、不外推、不参与 fit 以外的任何计算。

### 5.3 UI 表达（§22 的硬要求）

```
Frontend   91   置信度 22%   证据 2      ←  看起来很好但基本没被验证
Testing    86   置信度 94%   证据 27     ←  被大量实际工作证实
```

两者视觉权重必须一致，不允许把 confidence 藏进 tooltip 而让 score 独占版面。

---

## 6. 机器验收（对应 §46/§52）

1. `random` 不得出现在 competency 写路径（AST 守卫）。
2. 无证据 ⇒ `observed = NULL` ⇒ score 不变；`evidence_count=0` 的行 `status=UNRATED`。
3. 分配 CEO 职位后 `management_leadership` **分数与置信度逐项不变**，且不产生证据。
4. Trait 任一维度调到任意值，`assessment_runs`/`results`/`evidence` 输出**逐字节相同**
   （把 trait 设为 0/0.5/1 各跑一次同一 run 输入，比对 `inputs_hash` 与结果）。
5. 同一 `inputs_hash` 重放 ⇒ 结果相同（确定性）。
6. 证据单调性：追加一条证据后 `confidence` 不下降（时间固定）。
7. 有界步长：单 run 的 |Δscore| ≤ 0.35 × |delta|（防一次性暴涨）。
8. 人评（`manual_rating`）参与 `USER_FEEDBACK` 证据但**不改** `skill_usages.success`（沿用既有测试）。
9. Fit 计算不出现在 runtime 成功路径：`grep` 守卫 + 行为测试（低 fit 任命后任务成功率统计口径不变）。
10. 考核 run 失败（证据源缺失）⇒ `status=failed` + `error`，**不产生半成品 result**。

---

## 7. 与 behavior-v1 的衔接（不重复造轮子）

- `SkillUsage` 的 `outcome`（人评）与 `selection_reason`（策略放行）已经是**现成证据源**；
  本模块只做"采集 + 聚合 + 落库"，不重新定义成功/失败语义。
- `BehaviorPolicy` 决定"探索预算"，Assessment 决定"被证明的能力"；
  两者唯一交点是 Evidence —— 高 curiosity 让员工查了更多东西，若因此产出更好的交付，
  证据自然变多，能力分才上升。这条因果链是本设计的核心，也是**唯一**允许的因果链。
- 证据不变式测试（`tests/test_evidence_invariants.py`，现 14 条）继续作为总闸门：
  人格不改成功、不改置信度、不改验收 verdict。
