# 能力体系（Competency System）

上级约束：`docs/workforce-domain-refactor.md`（ADR-6/7/8）
本模块回答：**这个人已经被证据证明能做什么**。
它和 `docs/employee-brain-behavior-policy.md`（trait：倾向怎样工作）是**两套独立数值**，
和既有 `skills`（具体可复用做法）是**第三套**。三者不得互相代偿。

---

## 1. 三层数值，互不换算

```
Trait        行为倾向    EmployeeBrain.traits JSON      0..1        不入库表、不考核
Competency   能力        employee_competencies          0..100 + 0..1 置信度   由 Assessment 产生
Skill        技能        skills（既有）                  状态机     作为 Competency 的 Evidence 来源
```

禁止的换算（都有守卫测试，§7.3）：

```
Curiosity 高      →  Learning Competency 高
Warmth 高         →  Communication Competency 高
Independence 高   →  Execution Competency 高
Conscientiousness 高 → Quality Competency 高
Competency 高     →  Runtime 成功率 / 验收 verdict 变化
```

允许的**长期相关性**：倾向改变行为 → 行为产生不同 Evidence → 考核把 Evidence 变成能力分。
中间两步都必须留痕（`CompetencyEvidence` + `AssessmentRun`），所以相关性可追溯、可解释，
而不是"人格直接给分"。

---

## 2. 八个行为倾向（§13，第一版固定 8 个，不多）

沿用既有 `app/brain/registry.py` 的 `TraitSpec`（`affects` 声明式 + `validate_specs()` 启动校验）。
`curiosity` 已经真实接入 `BehaviorPolicy`（behavior-v1，已上线），其余 7 个第一版**只建
schema / UI / 投影**，`affects=()`，因此不会改动任何额度 —— 这正是
`tests/test_brain_extensibility.py` 已经机器证明过的性质（注册 ≠ 生效）。

| code                | 中文       | 低 ↔ 高 行为语义                   | 第一阶段是否改变 Agent 行为  | 未来 `affects`（仅声明，不实现）                                |
| ------------------- | ---------- | ---------------------------------- | ---------------------------- | --------------------------------------------------------------- |
| `curiosity`         | 探索倾向   | 专注当前任务 ↔ 主动查相邻知识      | **是**（检索/反思/延伸额度） | 已接                                                            |
| `warmth`            | 热情与亲和 | 克制事务化 ↔ 积极回应、解释、鼓励  | 否                           | `communication_style`、`mentor_willingness`、`peer_interaction` |
| `independence`      | 独立倾向   | 频繁确认 ↔ 自主拆解推进            | 否                           | `clarification_budget`、`escalation_threshold`                  |
| `conscientiousness` | 严谨倾向   | 快速推进 ↔ 检查、验证、守流程      | 否                           | `verification_preference`、`checklist_depth`                    |
| `collaboration`     | 协作倾向   | 独立完成 ↔ 沟通、peer review、分享 | 否                           | `review_request_budget`、`knowledge_sharing`                    |
| `risk_tolerance`    | 风险偏好   | 成熟方案 ↔ 实验性技术              | 否                           | `candidate_tech_exposure`、`experiment_budget`                  |
| `adaptability`      | 变化适应   | 偏好稳定 ↔ 新岗位/新栈快速调整     | 否                           | `context_switch_penalty`（仅描述性）                            |
| `creativity`        | 创新倾向   | 既有方案 ↔ 替代方案与新组合        | 否                           | `alternative_generation`、`solution_diversity`                  |

约束（写进 registry 校验）：

- 每个 trait 都必须有 `affects` 元组；空元组 = 明确"尚未接入"，UI 要显示"当前不影响执行"。
- `affects` 只能指向 `BehaviorPolicy` 的**已有字段**（`validate_specs()` 已实现这条）。
- 任何 trait 的 `affects` **不得**指向 success/confidence/verdict/competency（`BehaviorPolicy`
  里根本没有这些字段，结构上就写不进去 —— 这是 behavior-v1 留下的护栏）。

> **落地补记（P5）**：8 维已全部注册（`registry.py`），curiosity 维持既有行为映射，其余 7 维
> `affects=()` 只建 schema / UI 数据契约 / 投影（注册 ≠ 生效）。traits 存 `EmployeeBrain.traits`
> JSON，无 migration；缺失键读侧补齐注册表默认值，写侧按注册表补全。每个 `TraitSpec` 带
> `label` / `description` /（由 affects 推导的）`affects_execution` 供 UI 消费。

UI 文案（§32）：只描述行为，不写加成。

```
Curiosity 78 —— 较强探索倾向，会在核心任务之外适度查阅相邻知识。
禁止：Curiosity +8% Success
```

展示值统一 `round(value*100)`，但存储与 API 仍是 0..1，避免两套数值口径。

---

## 3. 通用能力：10 个固定维度（§15）

`competency_domains(code=general)` 下 10 个 `competency_definitions(kind=general)`。
**不做单一总分**（§15 明确禁止 `General Ability = 82`）。

| code                            | 中文           | 覆盖内容                                                 |
| ------------------------------- | -------------- | -------------------------------------------------------- |
| `analysis_problem_solving`      | 分析与问题解决 | 理解/拆解/关键矛盾/归因/方案设计                         |
| `planning_organization`         | 规划与组织     | 任务拆解、优先级、里程碑、依赖、时间资源                 |
| `execution`                     | 执行能力       | 计划推进到成果、处理阻塞、闭环                           |
| `communication`                 | 沟通表达       | 文字、汇报、需求澄清、解释复杂问题、受众调整             |
| `collaboration`                 | 协作能力       | 配合、交接、知识共享、peer review、冲突处理              |
| `management_leadership`         | 管理与领导     | 分派、协调、调度、培养、监督、推动达成                   |
| `decision_making`               | 决策能力       | 方案比较、trade-off、不确定判断、风险权衡、承担          |
| `learning_growth`               | 学习与成长     | 从任务学习、形成可复用知识、学新工具、吸收反馈、转化效率 |
| `quality_reliability`           | 质量与可靠性   | 遵循验收标准、自检、稳定交付、低返工、低严重错误         |
| `efficiency_resource_awareness` | 效率与资源控制 | token/时间/算力/API 成本效率、避免无意义重复             |

`independence`(trait) ≠ `execution`(competency)：前者"喜欢独立做"，后者"独立做成的能力"。
两者可以相反（Independence 92 / Execution 58 是合法且要能显示出来的组合，§16）。

---

## 4. 专业能力目录（§17，数据不是列）

层级 `CompetencyDomain → CompetencyDefinition`，公司可增删（内置行 `built_in=True`）。

```
Software Engineering: frontend_engineering, backend_engineering, system_architecture,
                      testing, devops, database, security, performance, code_quality
Research:             information_retrieval, source_evaluation, evidence_synthesis,
                      literature_review, experiment_design, technical_writing
Product:              requirements_analysis, product_design, prioritization,
                      acceptance_design, stakeholder_communication
Management:           strategic_planning, delegation, resource_allocation, risk_management,
                      team_development, organizational_coordination
QA:                   test_design, test_automation, defect_analysis, regression_testing,
                      acceptance_testing, quality_assurance
```

表 `competency_domains`：`code`(uq)、`name`、`kind`(`general`|`professional`)、`parent_id?`、`order`、`built_in`。
表 `competency_definitions`：`domain_id`、`code`、`uq(domain_id, code)`、`name`、`description`、
`facets(JSON，子项说明)`、`evidence_kinds(JSON，允许哪些来源证明它)`、`built_in`。

Position **不决定人拥有什么能力**，只声明需要什么（§18）：需求写在
`position_competency_requirements`，UI 按当前职位重排显示顺序，**不隐藏**其它维度。

---

## 5. `employee_competencies`（§21，不只有 score）

| 列                                        | 语义                                                                   |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `employee_id`, `competency_definition_id` | `uq(两者)`                                                             |
| `score`                                   | 0–100，`NULL` = 未评（**不用 0 表示未评**，0 是"有证据表明很差"）      |
| `confidence`                              | 0–1，见 `assessment-system.md` §5                                      |
| `evidence_count`                          | 参与本次评估的证据条数（冗余，便于列表；真相仍是证据表）               |
| `status`                                  | `UNRATED` \| `PROVISIONAL` \| `ASSESSED` \| `STALE`（证据过期/久未评） |
| `last_assessed_at`, `last_used_at`        | 前者来自 run，后者来自任务/技能使用回流                                |
| `trend`                                   | 最近一次 run 与上一次的差值（§29），`NULL` = 无历史可比                |
| `trend_window`                            | 计算趋势所用的评估次数（可解释性）                                     |
| `metadata_json`                           | 展示附加（如 UI 强调色所需的 gap）                                     |

**新员工（§23）**：招募后**不建行** = UNRATED。只有：

- 跑了 Initial Assessment（有证据）→ `PROVISIONAL`
- 正常任务证据累计到阈值 → `ASSESSED`

禁止任何随机初始化；`app/services/competency.py` 的写路径 import `random` 会让守卫测试红。

---

## 6. Evidence（§24）

`competency_evidence`：

| 列                                        | 说明                                                                                                      |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `employee_id`, `competency_definition_id` | 这条证据证明哪个维度                                                                                      |
| `source_kind`                             | `TASK`\|`PROJECT`\|`TEST`\|`REVIEW`\|`ARTIFACT`\|`USER_FEEDBACK`\|`PEER_REVIEW`\|`ASSESSMENT`\|`LEARNING` |
| `source_id`, `source_ref`                 | 指向既有一等对象（task/project/review/artifact/skill_usage…）与人类可读引用（如 `TEST-102 18/18 passed`） |
| `signal`                                  | 0–100，这条证据"支持什么水平"（不是分数加成，是水平观测）                                                 |
| `quality`                                 | 0–1，证据本身可信度（自动验收 > 人工评审 > 自评）                                                         |
| `weight`                                  | 计算得出的有效权重（§assessment §4），存下来以便复现                                                      |
| `occurred_at`, `assessment_run_id?`       | 时间与新近度                                                                                              |
| `metadata_json`                           | 摘要、项目、指标                                                                                          |

示例（§24 的 Charlie）：三条 `TEST` / `REVIEW` / `ARTIFACT` 证据指向 `testing`，
每条都能从 UI 反查到源对象 —— **数字必须可解释**，否则不配显示。

Skill → Competency 证据（§45）：`SkillUsage`（behavior-v1 刚建的）是 `LEARNING`/`TASK` 类证据的天然来源，
例如 `Playwright E2E Testing` 的使用记录为 `testing` 供证；但**不合并两张表**，
`Skill` 仍是具体做法，`Competency` 仍是抽象领域。

---

## 7. 投影与不做什么

1. 能力摘要**可以**进 `PROFILE.md`（T2 人级只读上下文），但必须带限定：
   渲染成"近期观察：testing 84（置信度 0.35，证据 4 条）"，**不得**渲染成"你擅长/你不擅长"式指令。
2. `eidolon/behavior.md`（T3 行为块）只放 trait 结果，能力不进行为块 —— 行为块会直接影响
   Agent 执行风格，能力属于评估侧。
3. 不实现的东西写清楚：本阶段没有"训练/课程"系统，没有自动补齐能力的机制，没有能力衰减曲线
   （只允许把久未再证的维度标 `STALE`，不改分数）。

---

## 8. 人物数值面板（§31/§33 的数据契约）

`GET /api/v1/employees/{id}/capabilities`

```json
{
  "traits": [
    {
      "code": "curiosity",
      "value": 0.78,
      "display": 78,
      "description": "较强探索倾向…",
      "affects_execution": true
    }
  ],
  "general": [
    {
      "code": "analysis_problem_solving",
      "score": 89,
      "confidence": 0.71,
      "trend": 3,
      "evidence_count": 12
    }
  ],
  "professional": [
    {
      "code": "frontend_engineering",
      "score": 92,
      "confidence": 0.22,
      "trend": null,
      "evidence_count": 2
    }
  ],
  "position": {
    "code": "software_engineer",
    "fit": 91,
    "gaps": [{ "code": "system_architecture", "short_by": 6 }]
  },
  "unrated": ["management_leadership"]
}
```

详情（点击一项，§33）：`GET /employees/{id}/capabilities/{competency_code}` →
score / confidence / trend / evidence_count / recent evidence 列表（带源对象链接）/ 该维度被哪些职位需要。

UI 硬要求：`score` 与 `confidence` **同时出现**；证据数为 0 时显示 `未评估`，不显示 0 分。
