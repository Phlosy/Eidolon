# M2 — Agent Work & Organizational Runtime（领域设计 / 契约冻结）

> 上游文档：[current-system-audit.md](current-system-audit.md)（Repository Audit 事实基线）、
> [concept-architecture.md](concept-architecture.md)（抽象骨架）、
> [talent-ecosystem-plan.md](talent-ecosystem-plan.md)（E0/K1/K2/R1/T1/T2/M1 阶段线）、
> [position-system.md](position-system.md) + [workforce-domain-refactor.md](workforce-domain-refactor.md)（组织侧 ADR）、
> [architecture.md](architecture.md)（五条不可破坏边界）。
> 执行基线：[m2-implementation-plan.md](m2-implementation-plan.md)。

---

## 0. 一句话目标

> 让一个真实复杂问题进入公司后，**由公司的管理 Agent 根据职位责任、公司事实、人员能力、知识、资源和经验自主决策**，
> 组织多个 Agent 完成工作；Eidolon 只承担**工作框架、规则验证、执行、资源、上下文、记录与审计**。

M2 **不是**「让 Eidolon 系统自己变成一个超级 Planner 或超级 CEO」。

---

## 1. 最高架构原则：System provides facts. Agent makes decisions.

```text
System provides facts.        ← 状态 / 规则 / 权限 / 持久化 / 工具 / Runtime / 资源
Agent makes decisions.        ← 判断 / 取舍 / 拆解 / 选人 / 返工 / 重新规划
System validates.             ← 权限、DAG 正确性、状态机、经济权威、并发不变式
System executes.              ← DAG 就绪调度、Runtime 会话、资源开通
System records.               ← 事件、决策记录、执行事实、审计
```

### 1.1 系统拥有（System Responsibility）

| 面 | 具体职责 | 现有落点 |
| --- | --- | --- |
| 状态 | 实体生命周期状态机与合法迁移；派生读模型 | `TaskStatus`/`ProjectStatus`/`WorkOrderStatus`… |
| 规则 | 硬约束校验的**执行者**（规则本身由公司/产品声明） | `services/tasks.py::ALLOWED_TRANSITIONS` |
| 权限 | 授权判定（有无某项 Authority 的额度/范围） | `entitlements` / `position_definition_packages` / `api/scope.py` |
| 持久化 | 唯一写入路径、事务边界、幂等 | repository 层 + `ON CONFLICT` 惯例 |
| 工具 | 提供给 Agent 的**只读事实查询 + 受校验的写操作** | M2.3 冻结的 Tool 面 |
| Runtime | 容器/进程生命周期、健康、能力位 | `runtimes/*`、`RuntimeCapabilities` |
| 资源 | workspace / docs / git / 权限包开通 | `lifecycle/*`、`workforce/access.py` |
| 事件 | 总线、分区保序、重试、死信 | `events/bus.py`、`events/engine.py` |
| Task DAG 执行 | **就绪判定、扇出/扇入、并发、状态迁移** | M2.5（承接 `workflow/orchestrator.py`） |
| Artifact | 存储、版本、sha256、lineage 记录 | `drive_nodes` / `drive_revisions` |
| Knowledge retrieval | scope 分层检索、FTS、freshness 降权 | `learning/retrieval.py` |
| Fit calculation | 需求×能力→已知分/覆盖/置信（**只算，不选**） | `talent/fit/engine.py` |
| Ledger | 复式账本、托管、结算 | `services/economy/*` |
| 审计 | 谁在什么时候以什么身份做了什么 | `audit_logs` / `user_audit_events` / M2 `decision_records` |

### 1.2 系统**不得**决定（Agent Decision Boundary）

```text
应该接什么任务        应该怎么拆任务        应该选谁
谁负责什么            是否需要招聘          应该买哪个 Agent
是否返工              如何重新规划          是否接受最终交付
是否培养某人          是否调岗              是否替换某人
```

这些属于 **CEO / CTO / PM / Team Lead / Reviewer / HR** 等公司内部 Agent 的职责，
或（在人类公司语境下）属于 **Owner / 用户**。

### 1.3 边界不是"谁更聪明"，而是"谁拥有它"

- 系统**可以**给出事实：`Fit 42%`、`Research experience low`、`Load 80%`。
- 系统**不可以**给出结论：`系统决定选择 Bob`。
- 系统**可以**拒绝：`该 actor 无 spend_credits 权限`。
- 系统**不可以**拒绝：`该职位通常不做研究`。

**Eidolon 是 Agent Organization Runtime / Agent Society OS，不是 CEO。**

---

## 2. 禁止演进的形态

```text
❌  User Problem → System Planner → System 组队 → System 分工 → System 管理所有 Agent
✅  User Problem → Project Context → 按公司职责规则路由给管理职位
              → Manager Agent 读取事实 → 自主分析 → 自主决定
              → 通过 Tool 提交决策 → System Validate → System Apply → Worker 执行
```

M2 内**不做**中央 AI Planner、系统自动组队、系统自动招聘、系统自动辞退。

---

## 3. Position 的正式语义（M2 冻结）

### 3.1 Position 表达什么

```
Position = Responsibility + Authority + Expectations
```

| 维度 | 含义 | 现有落点 |
| --- | --- | --- |
| **Responsibility** | 这个职位**通常负责什么** | `position_definitions.responsibilities`（JSON list） |
| **Authority** | 这个职位**有权做什么**（硬边界，见 §4） | `position_definition_packages` + `entitlements`（M2.2 扩展 Authority Projection） |
| **Expectations** | 公司**希望**这个职位具备什么能力（目标值，不是门槛） | `position_profile_versions` + `position_competency_requirements`（min/target/critical/weight，**已存在**） |

### 3.2 Position 不是什么

```
❌ 固定 Skill Package      （不注入能力 —— ADR-7 已锁定「能力只能被证明，不能被分配」）
❌ 固定 Prompt             （不写"你是一个 CEO，请按以下步骤思考"）
❌ 固定 Workflow / SOP     （不规定先干什么后干什么）
❌ 固定能力的快照          （不授予 score / skill / knowledge）
❌ 工作边界（硬）          （见 §3.3）
```

### 3.3 Soft Scope（职责范围是**建议**，不是硬门）

CEO 通常负责战略与高层管理，但仍然**可以**写代码、读论文、帮助 Debug。
Engineer 通常负责实现，但仍然**可以**提架构建议、参与规划、做 Review。

只要满足：

```text
权限允许 ∧ Manager Assignment 合法 ∧ Runtime 支持 ∧ 资源可用
```

系统就不应因为「这不是你的职位职责」拒绝工作。

> **裁决（W5 / W12）**：系统**不得**以职位名称为由拒绝分配；
> 系统**可以**以硬约束为由拒绝（§4）。

### 3.4 Expectations 只是期望

```
CTO 期望：Architecture target=80  Leadership target=70  Communication target=70
Person A： Architecture 95        Leadership 52         Communication 68
```

Person A **仍然可以**成为 CTO。系统**不能**因为 `Leadership < 70` 拒绝任命
（`minimum_score` 是**岗位胜任参考线**，用于 Fit 与 Gap 报告，不是任命门禁）。

---

## 4. Hard Constraints vs Soft Constraints

### 4.1 Hard Constraints（系统强制，可拒绝）

```text
Security                 凭证/密钥/越权访问
Permission               该 actor 是否被授予该项 Authority
Company isolation        跨公司读写（现状：repo 层 SQL 下推）
Economic authority       金额上限、mint/burn 令牌、托管归属
Runtime capability       runtime 是否具备该能力位
Resource availability    并发槽位、配额、余额
Task lifecycle           合法状态迁移
Concurrency invariant    一员工同时一个 running WorkSession
Database invariant       唯一约束、复式守恒、append-only
```

例：**普通 Engineer 没有 100000 Credits 的采购权限** → 系统拒绝。

### 4.1b Authority 的落点与校验语义（M2.2 / v41）

```text
employee → 生效 PRIMARY 任职 → PositionSlot → PositionDefinition → position_authority_grants
```

| 项 | 结论 |
| --- | --- |
| 载体 | **新薄表** `position_authority_grants`（M2-ADR-16）；`position_definition_packages` 保持资源开通语义 |
| 默认 | **default-deny**：无任职 / 无该授权 / 作用域说不清 / 金额缺失或超限 ⇒ 拒绝，并给出机器可读原因码 |
| 生效范围 | 随 Active PositionAssignment 生效与失效；**不是** Person 的永久资产（W38） |
| 禁止来源 | `employee.role` 字符串、`legacy_role`、Fit 分数、能力分、访问包、职责面、工作模式（W37） |
| 作用域 | `company`（且被指向的部门/员工必须真在本公司）/ `department`（`scope_ref`=部门）/ `direct_reports`（汇报子树，**不含自己**） |
| 金额 | `spend_credits` 必须给金额且不超上限；**没有上限 ≠ 不限**，而是「无法确认在授权内」⇒ 拒绝 |
| 硬安全约束 | `offboard` / `release_position` 不允许作用于自己（`self_target`）—— 这是安全规则，不是管理判断 |
| 只校验 | 授权层**不**选人、不排序、不给建议（W39） |
| 版本/审计 | append-only + 时间窗；`grants_hash`（这次凭什么）+ `position_grants_hash`（当时手里有什么）双摘要，历史 DecisionRecord 可解释（W40） |

### 4.2 Soft Constraints（系统只报告，不强制）

```text
Position scope            职位通常的工作范畴
Competency expectation    岗位能力期望（min/target）
Fit score                 匹配度
Experience match          经历匹配
Specialization            专业方向
Work habit                工作习惯 / 人格倾向
Advisory load             建议负载（不是配额）
```

例：Backend Engineer 被分配 Research Task，即使 Fit 低，**系统也不能拒绝**。

> **契约落点**：`app/work/contracts.py::HARD_CONSTRAINTS` / `SOFT_CONSTRAINTS`，
> 由 `tests/test_m2_contract.py` 钉住两类**互斥且完备**。

---

## 5. RoleContext（Derived Read Model，不是第二真相）

Agent 上岗后，系统为其投影：

```text
RoleContext
 ├── position_definition / position_code / department
 ├── responsibilities            ← PositionDefinition.responsibilities
 ├── authority[]                 ← Authority Projection（硬边界，可判定）
 ├── expectations[]              ← ACTIVE PositionProfileVersion 的 competency requirements
 ├── advisory_scope[]            ← 通常做什么（soft）
 ├── resource_index[]            ← Role Resource Index（§6）
 ├── direct_reports[]            ← position_slots.manager_slot_id 派生的下线
 ├── company_policy_keys[]       ← Company.settings 里的策略键（可读集合）
 ├── current_project_ids[]       ← 本公司未完结项目
 └── knowledge_scopes[]          ← 可访问的 knowledge scope
```

**纪律**：

1. **优先是派生读模型 / Context Projection**，不要轻易新建 SoT（W21）。
2. 字段必须**可从现有表推导**；推导不出来的一律不写进契约（M2.0 的字段集已逐个核对，见
   `tests/test_m2_contract.py::test_role_context_fields_are_all_derivable`）。
3. RoleContext **不含**：分数、评级、"你应该怎么做"、任何注入式建议。
4. RoleContext **不含**任何写能力；写只能经 M2.3 的 Tool 面。

---

## 6. Role Resource Index

PositionDefinition 可以引用：

```text
recommended knowledge topics
recommended playbooks
policy documents
company handbook sections
recommended skills（仅名字，不含等级）
```

表达的是：

> 「建议你学习 / 使用这些资源。」

**不是**：

> 「任命之后你自动拥有这些能力。」

### 6.1 契约

```python
RoleResource(kind, ref, note="", required=False)
```

- `kind ∈ {knowledge_topic, playbook, policy, handbook, skill_hint}`
- **不携带任何数值（score / level / weight）** —— 由 `tests/test_m2_contract.py` 用
  字段扫描钉死（AST 级），防止「资源」演化成「注入」。

### 6.1b 解析语义（M2.2 / v41）

每条资源**必须**解析到既有内容或既有配置（C6 / W41），三种结果：

| `resolution` | 含义 | 允许出现吗 |
| --- | --- | --- |
| `resolved` | `pointer` 指向既有内容/配置：`knowledge_item:{id}` / `drive_node:{id}` / `company_setting:{key}` | ✅ |
| `advisory` | 按设计不指向内容（`skill_hint` 只是一个"建议学的技能名"） | ✅ |
| `missing` | 指针目标**尚不存在**（公司还没发布对应知识/手册） | ✅ —— 如实报告，**不**造假内容 |

载体是薄表 `position_definition_resources(position_definition_id, kind, ref, note, required)`；
内容永远住在 `knowledge_items` / `drive_nodes` / `companies.settings` 里 ——
「给新 CEO 一份阅读清单」不能变成第二套文档系统。

### 6.2 Starter Playbook

官方可以提供 `CEO / CTO / PM / QA Starter Playbook`，用途仅限：

```text
新公司冷启动 / 教程 / 基础管理知识参考
```

它**只是 Knowledge / Reference**，**不是**固定 System Prompt、固定思考流程、固定任务拆解模板。

玩家替换 CEO 后，新 CEO **仍然可以**读取公司允许访问的 CEO Handbook / 历史决策 / 公司制度 / 项目历史；
但**怎么理解、怎么工作**由它自己决定。

---

## 7. Institutional Memory vs Personal Memory

### 7.1 两条平面

```
Institutional Memory（随公司存续；换人不迁移、不丢失、不复制）
  ├── Company / Department Knowledge        knowledge_items(scope=company|department)
  ├── Policies                             Company.settings / 政策文档
  ├── Decision History                     decision_records（M2 新增；append-only）
  ├── Project History                      projects / project_phases / baselines / change_requests
  ├── Playbooks / Handbook                 drive_nodes(zone=handbook|knowledge)
  ├── Artifacts                            drive_nodes(zone=projects) + drive_revisions
  └── Commercial Records                   work_orders / contracts / escrows / evaluations

Personal Memory（随 Person 存续；换职位不迁移、不重置）
  ├── Memory                               memory_entries
  ├── Learning                             learning_records / learning_priorities / learning_sessions
  ├── Traits                               employee_brains.traits
  ├── Skills / Skill Usage                 skills / skill_usages
  ├── Experience                           career_events / education_events
  ├── Evidence                             competency_evidence
  └── Competency                           employee_competencies
```

### 7.2 纪律

```text
W9   公司知识在人员更替后仍然是制度资产（换 CEO 不带走公司知识）
W10  Personal Memory 永远属于 Person（换职位不迁移）
W8   职位任命从不复制 Person 的 knowledge / skill / evidence
W7   职位任命从不授予 competency score
```

### 7.3 契约落点

> **Authority 不属于任何记忆平面**：`position_authority_grants` 挂的是**职位**，
> 既不随人走（不是个人资产），也不是制度知识（不是内容）。人一卸任就失效 ——
> 这正是 M2-ADR-17（W38）要表达的东西。

`app/work/contracts.py::MEMORY_PLANE_SURFACES` 把**每一张表**声明到所在平面；
`tests/test_m2_contract.py` 校验：表名真实存在于模型注册表、两平面**不相交**、
且 Personal 平面里的表都带 `person_id` 口径（有真实的字段级断言，不是声明式装饰）。

---

## 8. Adaptive Role Onboarding

Position Assignment 发生之后：

```text
❌  inject_role_skills()
❌  inject_role_knowledge()
❌  grant_role_competency()
❌  copy_personal_assets_from_previous_holder()
```

正确路径：

```text
Read RoleContext
      ↓
Inspect Expectations
      ↓
Inspect Own Competencies
      ↓
Find Gaps
      ↓
Search Company Knowledge
      ↓
Create Learning Priorities        ← 复用 learning_priorities / development_plans
      ↓
Learn / Research / Practice       ← 复用 learning_sessions / cultivation 原语
      ↓
Work
      ↓
Evidence
      ↓
Competency changes                ← 复用 evidence pipeline + competency aggregator
```

**全部构件已存在**（`learning_priorities`、`development_plans`、`learning_sessions`、
`EvidencePipeline`、`competency` 聚合器）。M2 只负责**把 RoleContext 交到 Agent 手里**，
不新增注入路径。

> **禁止动作清单**：`app/work/contracts.py::FORBIDDEN_ONBOARDING_ACTIONS`，
> 由 AST 守卫测试扫描 `app/` 全仓，确保**没有任何函数**以这些名字出现（W4/W7/W8）。

---

## 9. 能力不足不是死刑

Agent 在职位上真实工作后：

```text
Projects / Tasks / Reviews / Rework / Evidence
```

不断产生事实。系统**可以报告**：

```text
Role expectation gap
Review failure rate
Repeated rework
Low reliability
Missing capability
```

但：

```text
W13  系统不得自动辞退 / 自动降级 / 自动撤职
W14  调岗 / 替换决定属于被授权的管理 Agent 或 Owner
```

最终由 `CEO / Owner` 根据权限决定：`Coach / Training / Reassign / Demote / Replace / Offboard`。

**同样适用于 CEO 本身**：CEO 决策差、项目失败、组织效率低、重复判断错误 →
未来也可能被 Owner 替换。这是 AI Company 的玩法价值之一。

---

## 10. DecisionRecord（管理决策：Decision Envelope，M2.4）

### 10.1 三层不混（DR1）

```text
DecisionRecord  = 管理 Agent **为什么**做出这个决定      ← 管理语义（表 `decision_records`）
ToolAudit       = 为执行它，系统**实际执行了什么**        ← 执行事实（表 `tool_audits`）
Domain State    = 事实最终变成什么样（tasks / assignments / projects …）  ← 真相
```

三者**不得混为同一层**：决策行里没有 tool 名/入参/出参/错误（那些在 `ToolAudit`），
`ToolAudit` 里没有 reason/intended_outcome/parent（那些在决策行）。

### 10.2 Decision Envelope（一次提交，而不是两次仪式）

**不采用 "tool call = decision"**（DR2）：一个真实管理决策通常产生**多个**动作。
所以执行面接受一次提交：

```text
Decision Envelope
├── decision_type
├── reason
├── intended_outcome
├── scope                 "project:12" / "company:1" / "task:34" …
├── context               有界事实快照（+ 稳定引用）
├── parent_decision_id    可选：管理决策树（DR8）
└── actions[]             [{tool, args}, …]   ← 一条决策 → N 个 Tool Action
```

执行顺序：

```text
① 校验信封（**结构**，不评价内容）          validate_decision_intent
② 落 DecisionRecord（PROPOSED）并**先提交**  意图独立成短事务：崩在动作中途也留痕
③ 逐个执行 actions，ToolAudit 自动挂 decision_id（DR3）
④ 聚合 → APPLIED / PARTIALLY_APPLIED / FAILED（DR6）
⑤ 写 resolved_at + outcome_note（追加式推进，不重写语义字段）
```

**原子性**（不假定整个决策是一个事务）：短决策（建任务 + 连依赖 + 派活）逐个动作提交；
长生命周期决策（plan → execute → review → replan）用
`open_decision()`（PROPOSED）→ 分阶段 `execute_actions()` → `resolve_decision()`，
**不持有长 DB transaction**。`PARTIALLY_APPLIED` 正是为这种局面准备的诚实状态。

### 10.3 关联只有一个方向（DR3）

```text
ToolAudit.decision_id → DecisionRecord.id          ✅ 唯一方向
DecisionRecord.audit_ids[]                          ❌ 不建（第二份关系真相）
```

查询某决策执行了什么，一律 `WHERE decision_id = …` 反查；
决策读面里的动作计数是**派生量**（读时反查），不落列（仓库 ADR-12）。

### 10.4 决策字段（管理语义）

| 组 | 字段 |
| --- | --- |
| 谁 | `actor_person_id` / `actor_employee_id` / `acting_position_assignment_id` / `acting_position_definition_id` / `acting_position_code` |
| 关于什么 | `company_id` / `scope` / `project_id` / `task_id` |
| 决定什么 | `decision_type` / `reason` / `intended_outcome` |
| 依据什么 | `context_json`（有界）/ `context_hash` / `context_version` / `authority_json` |
| 结果 | `status` / `outcome_note` / `resolved_at` |
| 树 | `parent_decision_id` / `superseded_by_id` |

`acting_position_assignment_id` 是**当时那一段任职**：换人之后历史仍指向它，
而不是拿今天的组织去解释昨天的决定。

### 10.5 Decision ↔ Tool 的语义（DR7）

工具声明它与决策的关系（注册时强制）：

| `decision_semantics` | 工具 | 执行面行为 |
| --- | --- | --- |
| `none` | 全部读工具 | 挂了 `decision_id` ⇒ **拒绝**（事实查询不是决策动作） |
| `optional` | `update_task` / `request_review` / `mark_task_blocked` | 可独立执行，也可作为决策的一部分 |
| `required` | `create_task` / `create_dependency` / `assign_task` / `delegate_project` / `request_rework` / `cancel_task` | 没有 `decision_id` ⇒ **拒绝**（`decision_required`） |

### 10.6 决策不授予权限（DR4）

```text
DecisionRecord 里写"我要 offboard Bob" ≠ 获得 offboard 授权
```

每个动作执行时**重新**走一遍：
`Actor → 生效任职 → PositionAssignment → PositionAuthorityGrant → Scope/Constraints → Domain Validation`。
`DecisionRecord.authority_json` 是**决策当时的授权快照**（证据），不是通行证。

### 10.7 系统只记录，不评价「想法」

```text
记录：谁在什么时候、以什么职位、基于什么事实、决定了什么、结果如何
不产生："我觉得 CEO 的决定不好"
```

结果由真实事实形成证据；`Decision → Outcome` 只留**可追踪**能力（DR10），
**不做** CEO/CTO 能力评分（那是 M3 Agent Career 的活）。

### 10.8 状态机

```text
PROPOSED ──▶ EXECUTING ──┬──▶ APPLIED              （全部动作成功）
                         ├──▶ PARTIALLY_APPLIED    （部分成功，DR6）
                         └──▶ FAILED               （全部失败/被拒）
任意终态 ◀── SUPERSEDED（被后续决策取代；原记录**不改写**，只记 superseded_by_id）
```

`DecisionOutcome`（M2.0 预留的"结果回填"枚举）**已退役** ——
同一个概念留两个枚举就是两个真相；结果现在只由 `DecisionStatus` 表达。

## 11. Project / Task / WorkOrder 边界（M2 冻结）

### 11.1 边界表

| 概念 | 是什么 | 不是什么 |
| --- | --- | --- |
| **WorkOrder** | 商业 / 经济需求（雇主、赏金、托管、验收、结算） | ❌ 执行图 ❌ Task 容器 ❌ 工作拆解 |
| **Project** | **执行载体与唯一权威工作根**（Canonical Executable Work Root） | ❌ 商业契约 ❌ Mission（不新建 Mission SoT） |
| **Task** | Project 内的工作单元（DAG 节点） | ❌ 商业契约 ❌ 独立经济体 |
| **DecisionRecord** | 管理决策的审计记录 | ❌ 工作单元 ❌ 执行状态 |

```text
WorkOrder ACCEPTED
      ↓  （M2.9 Bridge：建立绑定，不复制语义）
Project created / bound
      ↓
Company Management Agent（自主）
      ↓
Execution（Task DAG）
      ↓
Deliverables（Artifact）
      ↓
Submission → Evaluation → Settlement     ← 仍然走 M1 既有契约（E 系列不变量不动）
```

### 11.2 W20 / W22 / W23

```text
W20  No new Mission source-of-truth table.
W22  Project becomes the canonical executable work root.
W23  WorkOrder remains economic/commercial wrapper, not execution truth.
```

**裁决**：M2 **不新建 Mission 实体**。`Project` 承担工作根语义；
`WorkOrder` 保持商业包装；两者的连接是一条**绑定边**，不是一套新语义。

### 11.3 Project 两路径的最终处理决策（M2.1 已落地）

现状（Audit §6.1/§18）：

```text
路径 A  create_order()              → order_review → 固定 GRAPH_TEMPLATE 4 阶段 → 真的跑 Agent
路径 B  create_structured_project()  → 11 ProjectPhase + 人工评审门 + 模板文档 → 完全不跑 Agent
前端立项向导恒走 B（is_structured=True）；A 没有 UI 入口。
```

**决策（M2-ADR-1 / M2-ADR-11 / M2-ADR-12）：`Project` 是唯一执行根；
路由由两个**显式且正交**的维度决定，不再由 `is_structured` 隐式分叉。**

```text
维度 1（产品）        work_mode         = guided | managed
维度 2（基础设施）    planning_fixture  = none | deterministic_template
```

| 组合 | 语义 | 谁规划 |
| --- | --- | --- |
| `guided` + `none` | 引导/协助形态：Manager 仍自主决策，关键动作要**人类确认与讲解** | Manager Agent（+ 人类确认） |
| `managed` + `none` | 自主管理形态：Manager Agent 自主规划与委派 | Manager Agent |
| `*` + `deterministic_template` | **基础设施项目**：确定性模板替掉 Manager 的规划 | 固定模板（仅 CI/教程/测试/演示） |

**收敛规则**（M2.1 已落地，逐阶段演进）：

| 阶段 | 动作 | 状态 |
| --- | --- | --- |
| M2.0 | 冻结 `ProjectWorkMode` 与收敛映射；不新增列、不改行为 | ✅ DONE |
| **M2.1** | `Project` 承载 Canonical Spec；`work_mode` 快照；单一 `create_project()`；Work Intake 责任路由；规划 fixture 拆出产品维度 | ✅ **DONE** |
| **M2.5** | `DETERMINISTIC_TEMPLATE_PLAN` 彻底退出业务路径：编排器变纯调度器，模板搬到 `app/work/planning_fixture.py`（建完即退出） | ✅ **DONE** |
| M2.7 | `guided` 的评审门改为**复用同一套 Review 契约**（不保留第二套决策语义） | PENDING |
| M2.9 | `WorkOrder` → `Project` 绑定边落地；`submit.project_id` 从"自由字段"变成"受校验引用" | PENDING |

**不做什么**（避免把可用的子系统拆掉）：

```text
❌ 不删除 v0.5 正式交付域（ProjectPhase / ReviewMeeting / Baseline / DeliveryPackage）
❌ 不删除 order_flow 的历史数据与 API（历史项目 work_mode 留 NULL = 未分类）
❌ 不新建第四套任务实体
❌ 不把 deterministic 模板做成"第三种玩法模式"（它是 Execution Fixture，不是产品模式）
```

### 11.4 Canonical Project Spec（Facts，不是 Execution Plan）

`Project` 必须至少能表达：

```text
Background / Goal / Requirements / Constraints / Deliverables
Acceptance Criteria / Priority / Deadline / Context
```

注意：这些是 **Facts / Requirements**，**不是**系统给出的 **Execution Plan**。
拆解由 Manager Agent 决定（W2 / W16）。

字段映射（M2.1 已落地，全部落在既有列上 ⇒ **不需要新表**）：

| 契约字段 | 承载 |
| --- | --- |
| background | `projects.background` |
| goal | `projects.goal` |
| requirements | `project_requirements`（guided）+ `projects.source_order_text`（一句式兜底） |
| constraints | `projects.constraints` |
| deliverables | `projects.deliverables` |
| acceptance_criteria | `project_requirements.acceptance_criteria`（逐需求） |
| priority | `projects.priority` |
| deadline | `projects.planned_end_at`（缺省 = 创建 + 18 天，B4 的历史默认窗口） |
| context | `projects.description` |

### 11.5 Work Intake 是**责任路由**，不是 CEO 特权（D1，M2-ADR-11）

```text
Project Created
      ↓
resolve company work-intake responsibility      ← Company.settings["work_routing"]
      ↓  职位 code（缺省 ceo，公司可配 COO / PM Lead / Research Director / 自定义）
PositionDefinition → PositionSlot → 生效 PRIMARY PositionAssignment
      ↓
把 Project Context 交给该 Manager Agent（一个「工作接收」任务）
```

**不是**：

```text
Project Created → if CEO: ...            ❌ 硬编码 CEO 特权
Project Created → 随便挑一个员工          ❌ 违反 Agent makes decisions.（W32）
Project Created → 系统自己生成执行图      ❌ 违反 W2 / W34
```

| 路由结果 | 系统行为 |
| --- | --- |
| `routed` | 快照 `management_employee_id/person_id/assigned_at`；创建一个「工作接收」任务（`kind=order_review`），描述里带 **Project Brief**（Canonical Spec 的事实摘要）；派发 |
| `no_position` | 项目 `status=waiting_for_management`，**零任务零规划**，事件 `project.waiting_for_management`（带原因与 Owner） |
| `no_incumbent` | 同上 |
| `incumbent_unavailable` | 同上（人在任但未就绪 / 已停用 / 离职中） |

**多条在任者不是歧义**：公司指定的是**职位**，该职位的每一位在任者都按定义承担这份责任；
路由取 `effective_from` 最早的一位，并把**全部在任者**放进 `candidate_employee_ids` 供审计。
这**不**构成"系统替公司选人"（W1）。

**管理 actor 的存储纪律**：`projects.management_*` 是**当前指针快照**，不是长期领域真相：

```text
权威：责任路由 → PositionSlot → PositionAssignment     （可重解析）
历史：DecisionRecord（M2.4）                            （append-only，不被反推）
```

项目行**不**保存 `ceo_employee_id` 这样的角色特化字段 —— CEO 换人不会让项目失去历史，
读面通过 `management.stale` 如实报告"快照与当前责任持有者不一致"（不静默改写）。

### 11.6 规划 fixture：基础设施，不是产品模式（D3，M2-ADR-12）

```text
❌ 把 deterministic 模板当作"第三种玩法模式"
✅ 把它当作 Execution Fixture：CI / 教程 / golden path / 开发演示的确定性替身
```

两条硬纪律（W33）：

1. **生产项目永远不会落到它头上** —— 没有"Manager 没反应 → 偷偷用模板"；
2. 只能**显式请求**（`planning_fixture=deterministic_template`）且受
   `settings.allow_planning_fixtures` 门控（默认 false）；未开启时 **422**，
   **绝不静默降级**成 `none`（静默降级会让测试以为自己在测确定性链）。

**它保留的理由**：仍然需要一条完全确定性的
`Project → Task Graph → 执行 → Artifact → Review → Completed`
用于 CI / 迁移回归 / orchestrator 回归 / runtime 回归 / 教程 / golden path。
若所有测试都依赖 LLM Manager Agent，测试会变得非确定、昂贵、慢、难复现。

**命名纪律（M2.5 更新）**：模板**已经整体搬出编排器**，住在
`app/work/planning_fixture.py`（`DETERMINISTIC_PLAN_STAGES` /
`build_deterministic_plan`）—— 任何开发者看到文件名与函数名都该立刻明白
**这不是生产环境的公司决策逻辑，而是替掉 Manager 规划的替身**。
编排器里已**不存在**任何模板、建图、按 `kind` 推进的代码（R12 的 AST 守卫钉住这一点）。

**Manager 缺失或失败时的正确行为**（不是 fallback）：

```text
Management Action Failed → retry → escalate → ask Owner → replace Manager
```

### 11.7 `work_mode` 是项目级快照（D2，W35）

```text
FOUNDING  → 默认 guided      （冷启动要有明确的人类确认与讲解）
首次真实项目完成 → 公司默认推进为 managed
OPERATING → 默认 managed
```

- 解析优先级：请求显式值 → 公司默认（`Company.settings["work_mode_default"]` → 公司阶段）。
- 解析结果**写入项目行**，之后公司默认值怎么变都**不改写**它 —— 执行中的语义不会漂移。
- 公司默认值的推进（`promote_after_project_completion`）**只改默认值**，不改任何项目；
  用户显式配置过默认值的公司**永不**被自动改写。
- `guided` 与 `managed` 的差别只有 **human involvement level**，不是 decision ownership（W36）：

```text
managed：Manager 提出计划 → 系统 validate → 执行
guided ：Manager 提出计划 → UI 展示并讲解 → 人类确认 → 系统 validate → 执行
```

两种模式里「接不接受 / 怎么拆 / 选谁 / 是否返工 / 是否交付」都来自 Manager Agent 或 Human Owner。

---

## 12. 评审 / 返工 / 重新规划的归属

### 12.1 三套 verdict 不得互相替代

Audit 已指出系统里存在 4 套"评价"。M2 冻结**边界表**，把它变成显式契约而不是隐含风险：

| 面 | 枚举 | 判定者 | 对象 | 作用 |
| --- | --- | --- | --- | --- |
| 任务级技术评审 | `ReviewVerdict` = PASS / REWORK / REJECT / ESCALATE | **Reviewer Agent**（M2.7） | Task 产出 | 决定 Task 下一步 |
| 交付阶段门 | `ReviewDecision` = approved / conditionally_approved / changes_requested / rejected | **人类**（现有 `ReviewMeeting`） | 阶段产出 | 决定 Baseline / 阶段推进 |
| 商业验收 | `EvaluationVerdict` = approved / rejected / revise | 管理面（CLI）或确定性规则 | WorkOrder 提交 | 决定结算 |
| 能力考核 | `AssessmentResult`（数值） | 统计聚合 | 人的能力 | 只影响能力，不是 gate |

**纪律（W29）**：四者**不得互相替代**；跨面引用必须经显式映射，不得把一面的枚举塞进另一面。
`tests/test_m2_contract.py` 钉住：三个枚举值集**互不相同**，且 `ReviewVerdict` 不被
economy 模块引用、`EvaluationVerdict` 不被 work 模块引用。

### 12.2 归属

```text
W16  Task DAG execution is system responsibility; DAG design is management responsibility.
W17  Task review acceptance is not automatically decided by system heuristics.
W18  Deterministic tests provide facts, not management judgment.
```

具体：

| 问题 | 答案 |
| --- | --- |
| 谁判断 Task 做得好不好？ | **Reviewer Agent**（默认由发起评审的 Manager 指派；`ReviewVerdict`） |
| 系统能自动 PASS 吗？ | ❌ 不能。系统只提供 Fact（test result / lint / build / artifact / acceptance criteria） |
| 失败后谁决定返工？ | Manager Agent（`request_rework`）或 Reviewer（`rework` verdict） |
| 需要重新规划时谁决定？ | **负责该 Project 的 Manager Agent**（`replan`） |
| 系统能自动 replan 吗？ | ❌ 不能 |
| 系统能自动辞退吗？ | ❌ 不能（W13） |

### 12.3 现有自动连跳必须退役

现状：`Orchestrator._finalize` 里 `in_review → done` 同事务连跳（Audit §10）。
M2.7 起：`in_review` 只能由 `ReviewVerdict.passed` 推进 → `done`。

---

## 13. Fit 只是 Decision Support

```text
Eidolon 可以返回：
  Alice  Python 88  Architecture 76  Fit 84%  Confidence 91%  Load 30%
  Bob    Python 72  Architecture 92  Fit 87%  Confidence 75%  Load 80%

Eidolon 不能返回：
  "系统决定选择 Bob。"
```

```text
W11  Fit is decision-support only.
```

现有约束的延续（`workforce-domain-refactor.md` ADR-9「Fit 只用于推荐，不碰执行」）
在 M2 被**加强**为可测试的不变量：`talent/fit` 模块不得出现在 assignment 决策路径上，
除非调用方是 Agent Tool（M2.3）或角色上下文投影（只读）。

`tests/test_m2_contract.py` 用 AST 守卫钉住：`app/workflow/` 与 `app/services/tasks.py`
**不得** import `app.talent.fit`。

---

## 14. M2 不变量（W1–W42 / T1–T12 / DR1–DR10 / R1–R12）

| # | 不变量 | M2.0 状态 |
| --- | --- | --- |
| **W1** | Eidolon system does not choose team members. | 冻结（M2.4 使能；M2.5 强制执行） |
| **W2** | Eidolon system does not make project decomposition decisions. | 冻结（M2.4/M2.5） |
| **W3** | Management decisions must originate from an authorized Agent/User actor. | 冻结（M2.3/M2.4） |
| **W4** | Position defines responsibility/authority/expectations, not fixed workflow. | **M2.0 强制** |
| **W5** | Position scope is advisory, not a hard work boundary. | **M2.0 强制** |
| **W6** | Permissions and security remain hard boundaries. | **M2.0 强制** |
| **W7** | Position assignment never grants competency score. | **M2.0 强制** |
| **W8** | Position assignment never copies Person knowledge/skill/evidence. | **M2.0 强制** |
| **W9** | Company knowledge remains institutional after personnel replacement. | **M2.0 强制** |
| **W10** | Personal memory remains with Person. | **M2.0 强制** |
| **W11** | Fit is decision-support only. | **M2.0 强制**（AST 守卫） |
| **W12** | An Agent may work outside its normal role scope if authorized. | 冻结（M2.4） |
| **W13** | Failure to satisfy role expectations does not automatically offboard an Agent. | **M2.0 强制** |
| **W14** | Reassign/replace decisions belong to authorized management Agents or Owner. | 冻结（M2.4） |
| **W15** | All management decisions are auditable. | 冻结（M2.4 落表） |
| **W16** | Task DAG execution is system responsibility; DAG design is management responsibility. | 冻结（M2.5） |
| **W17** | Task review acceptance is not automatically decided by system heuristics. | 冻结（M2.7） |
| **W18** | Deterministic tests provide facts, not management judgment. | **M2.0 强制** |
| **W19** | Artifact lineage must be preserved. | 冻结（M2.6） |
| **W20** | No new Mission source-of-truth table. | **M2.0 强制**（表存在性守卫） |
| **W21** | No new Agent source-of-truth table without explicit future ADR. | **M2.0 强制**（表存在性守卫） |
| **W22** | Project becomes the canonical executable work root. | 冻结（M2.1 收敛） |
| **W23** | WorkOrder remains economic/commercial wrapper, not execution truth. | **M2.0 强制** |
| **W24** | Task completion must produce real work facts before Evidence is created. | **M2.0 强制**（沿用 P6 既有守卫） |
| **W25** | Mock completion must never masquerade as real production success. | **M2.0 强制**（沿用 mock 0.5x + environment 标记） |
| **W26** | Position never derives a per-person workflow, prompt, or SOP. | **M2.0 强制** |
| **W27** | Role resources are advisory references; they never carry scores or grants. | **M2.0 强制**（字段扫描） |
| **W28** | DecisionRecord is append-only; outcomes are appended, decisions are never rewritten. | **M2.0 强制**（契约声明 + M2.4 落表） |
| **W29** | The four verdict/assessment surfaces must not be substituted for each other. | **M2.0 强制** |
| **W30** | `guided` and `managed` project modes share one Task/Assignment/Review substrate. | 冻结（M2.1/M2.5） |
| **W31** | A recruited Agent is not READY_TO_WORK until provisioning completes. | 冻结（M2.8） |
| **W32** | Work Intake is a company-configurable responsibility, not a CEO privilege; the system never picks an arbitrary employee. | **M2.1 强制** |
| **W33** | Deterministic planning fixtures are explicit, gated infrastructure; production projects never fall back to them. | **M2.1 强制** |
| **W34** | When the responsible manager is absent or fails, the project waits or escalates; the system never takes over planning. | **M2.1 强制** |
| **W35** | work_mode is snapshotted per project at creation; later company-default changes never rewrite it. | **M2.1 强制** |
| **W36** | guided and managed differ only in human involvement level, never in decision ownership. | **M2.1 强制** |
| **W37** | Authority is default-deny and resolved from the active PositionAssignment, never from role strings or scores. | **M2.2 强制** |
| **W38** | Authority follows the assignment and never becomes a permanent Person asset. | **M2.2 强制** |
| **W39** | Authority validation only validates; it never selects, ranks, or judges management actions. | **M2.2 强制** |
| **W40** | Authority grants are append-only and time-versioned; any decision can pin why it was legal. | **M2.2 强制** |
| **W41** | Role resources are pointers into existing content; the index never stores content. | **M2.2 强制** |
| **W42** | position_definition_packages stays resource provisioning; management authority lives only in position_authority_grants. | **M2.2 强制** |
| **T1** | Agent tools do not own business truth. | **M2.3 强制** |
| **T2** | HTTP APIs and Agent tools share the same application/domain services. | **M2.3 强制** |
| **T3** | No generic player-facing write /tools API. | **M2.3 强制** |
| **T4** | Internal tool calls never bypass Authority. | **M2.3 强制** |
| **T5** | Actor identity is injected by runtime/system context, not trusted from model arguments. | **M2.3 强制** |
| **T6** | Fit and other read tools provide facts, never make management decisions. | **M2.3 强制** |
| **T7** | A successful Tool call means: Agent decided, System validated, System applied. | **M2.3 强制** |
| **T8** | PositionAuthorityGrant is the management authorization source. | **M2.3 强制** |
| **T9** | Resource Package is never used as a substitute for Authority. | **M2.3 强制** |
| **T10** | Transport choice does not change domain invariants. | **M2.3 强制** |
| **T11** | Human management APIs and Agent management tools must produce equivalent domain effects. | **M2.3 强制** |
| **T12** | Tool execution must be auditable. | **M2.3 强制** |
| **DR1** | DecisionRecord is intent, ToolAudit is execution, domain state is truth: three layers never merged. | **M2.4 强制** |
| **DR2** | One decision may produce N tool actions; a tool call is never by itself a decision. | **M2.4 强制** |
| **DR3** | The decision/audit link is one-way: ToolAudit.decision_id points at DecisionRecord; there is no reverse array. | **M2.4 强制** |
| **DR4** | DecisionRecord never grants authority; every action is re-validated at execution time. | **M2.4 强制** |
| **DR5** | DecisionRecord never copies tool input or output; those live in ToolAudit. | **M2.4 强制** |
| **DR6** | Decision status can express PARTIALLY_APPLIED; partial success is never rounded to success or failure. | **M2.4 强制** |
| **DR7** | Every tool declares its decision semantics (none/optional/required) and the executor enforces it. | **M2.4 强制** |
| **DR8** | parent_decision_id expresses the management decision tree; no separate workflow model is introduced. | **M2.4 强制** |
| **DR9** | Decision context is a bounded snapshot with stable refs and a hash, never a copy of the database. | **M2.4 强制** |
| **DR10** | Decision outcome is traceable (decision to outcome); no capability scoring in M2.4. | **M2.4 强制** |
| **R1** | System may automatically dispatch only to the already-authorized assignee. | **M2.5 强制** | **M2.5 强制** |
| **R2** | System never selects an assignee when a task becomes ready. | **M2.5 强制** | **M2.5 强制** |
| **R3** | Structural readiness and dispatchability are distinct concepts. | **M2.5 强制** | **M2.5 强制** |
| **R4** | A ready unassigned task requires a management decision. | **M2.5 强制** | **M2.5 强制** |
| **R5** | Runtime/resource failure does not cause automatic reassignment. | **M2.5 强制** | **M2.5 强制** |
| **R6** | guided and managed share the same DAG runtime. | **M2.5 强制** | **M2.5 强制** |
| **R7** | Fixture graphs share the same dispatcher/runtime after graph creation. | **M2.5 强制** | **M2.5 强制** |
| **R8** | task.ready is a fact event, not a management approval request. | **M2.5 强制** | **M2.5 强制** |
| **R9** | Normal DAG progress does not require a new DecisionRecord. | **M2.5 强制** | **M2.5 强制** |
| **R10** | Any reassignment must originate from an authorized Agent/User decision. | **M2.5 强制** | **M2.5 强制** |
| **R11** | Manager Agents are invoked for decisions/exceptions, not ordinary scheduling. | **M2.5 强制** | **M2.5 强制** |
| **R12** | No production fallback may silently assign or plan work on behalf of management. | **M2.5 强制** | **M2.5 强制** |

> **"M2.0 强制"** = M2.0 就有可执行测试锚点；
> **"冻结"** = M2.0 冻结契约与归属，锚点在其 owner 阶段落地。
> `tests/test_m2_contract.py::test_every_invariant_has_a_live_anchor_or_an_owner_stage` 保证
> **没有任何一条不变量被静默丢弃**（M1.10 锚点表同款纪律）。

---

## 14b. M2.3 管理工具面（Read Shared / Write Internal，T1–T12）

### 14b.1 三个面，一个领域

```text
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Human / Product（HTTP）     │        │  Agent Runtime（内部执行面）  │
│  /projects /tasks /positions │        │  ToolRegistry → ToolExecutor │
└──────────────┬───────────────┘        └──────────────┬───────────────┘
               │                                       │
               └──────────► Application / Domain Service ◄──────────┘
                                   （唯一真相与规则）
```

* **读能力是共享的**：读工具与 HTTP 读面复用同一批 QueryService / ReadModel，
  不为 Agent 再写一套查询（T2）。UI 继续走自己的领域读面，**不**新增 `/tools` 读路由。
* **写能力只有内部面**：不存在通用玩家 `POST /api/v1/tools/*`（T3）；
  人类管理动作走各领域自己的正式 API。

### 14b.2 Tool Registry

每个工具是**可枚举的自描述条目**（`app/work/tools.py::ToolSpec`）：

```text
name / description / input_schema / output_schema
side_effect_level (READ | WRITE | HIGH_IMPACT) / required_authority
authority_target / handler / autonomy（由副作用等级派生）
```

注册时强制（`ToolSpec.__post_init__` + `assert_registry_is_sound`）：

```text
READ  : 不得声明 required_authority / authority_target（事实不需要管理授权）
WRITE : **必须**同时声明 required_authority 与 authority_target（T4）
HIGH_IMPACT : M2.3 不注册任何此类工具（不为完整列表写空业务）
参数键 : 不得出现身份字段（actor_* / company_id / …，T5）
```

### 14b.3 执行五步（顺序不可交换）

```text
① 解析 spec        未注册 → unknown_tool
② 参数校验         含"身份字段不得出现在参数里" → invalid_arguments / not_authorized
③ Authority 校验   default-deny；**内部面照样做**（T4）；快照随结果返回（T8/W40）
④ Autonomy 门禁    requires_confirmation ⇒ **拒绝执行**（M2.3 没有确认通道）
⑤ 应用 + 审计      handler 调既有 service；每次调用留一条 audit_logs（T12）
```

### 14b.4 Authority ≠ Autonomy

```text
Authority      = 这个职位**有没有**组织权力执行该动作      （v41 grant 表，default-deny）
AutonomyPolicy = **AI** 是否允许在无人确认下执行该动作      （M2.3 只冻结边界）
```

「CEO 有 `spend_credits`」**不等于**「CEO AI 可以无限额度自主花钱」。M2.3 冻结的当前行为：

| side_effect | autonomy | 含义 |
| --- | --- | --- |
| `read` | `auto_allowed` | 事实查询无需确认 |
| `write` | `auto_allowed` | 授权内的组织动作可自主执行（这正是 M2.3 的目标）|
| `high_impact` | `requires_confirmation` | 经济/招聘/解雇/合同/高风险资源 —— 没有确认通道就**拒绝** |

### 14b.5 第一批工具

**读（15 个）**：`inspect_project` / `list_company_projects` / `list_company_people` /
`inspect_person` / `inspect_position` / `inspect_assignments` / `inspect_role_context` /
`inspect_work_intake` / `get_competencies` / `get_evidence` / `calculate_task_fit` /
`get_current_load` / `get_runtime_status` / `search_company_knowledge` / `inspect_artifact`

**写（9 个）**：`create_task` / `update_task` / `create_dependency` / `assign_task` /
`delegate_project` / `request_review` / `request_rework` / `mark_task_blocked` / `cancel_task`

写工具的授权映射：工作图类（建/改/连依赖/阻塞/取消/请评审）→ `plan_project_work`（M2.3 新增）；
派活 → `assign_task`；返工 → `request_rework`；项目委派 → `delegate_management`。

**M2.3 的诚实边界**：`request_review` 只做状态推进 + 留痕 + 事件，
持久的 `ReviewRequest` 实体是 M2.7；返回值里用 `review_entity: deferred_to_M2.7` 明说。

### 14b.6 调试口（不是业务入口）

`scripts/agent_tools.py` + `make agent-tools / agent-tool-call`：

```text
默认关闭（EIDOLON_AGENT_TOOL_CLI_ENABLED=false）
不进玩家 router · 走**同一段**执行代码 · Authority / 领域校验 / 自主等级门禁 / 审计一样不少
它只解决"谁能发起"，不解决"可以绕过什么"（T10）
```

## 14d. M2.5 Canonical Task Graph Runtime（用户拍板的执行语义）

一句话：

```text
Manager chooses.   ← 建哪些 Task、依赖、**谁负责**、是否改派/取消/重规划
System schedules.  ← 依赖是否满足、是否就绪、能不能派、何时派
Worker executes.   ← WorkSession → Runtime → Artifact
Manager intervenes only when judgment is required.
```

### 14d.1 决策边界（§1）

| 谁 | 决定什么 |
| --- | --- |
| **管理（Manager Agent / 授权人）** | 建哪些 Task、依赖怎么连、**谁负责**、是否改派 / 取消 / 重规划、是否接受交付 |
| **系统** | 依赖是否满足、任务是否就绪、现在能不能派、派给谁**（只能是已存在的负责人）**、何时记录事实 |

系统永远不回答"应该由谁来干这件事"，也永远不回答"下一步该建什么 Task"（W1/W2/R1/R2/R12）。

### 14d.2 规范流程（§2）

```text
resolve_ready_tasks()            ← 契约纯函数：依赖全部 done 且自身未开始（**事实**）
  → task.ready                   ← 事实事件，不是审批请求（R8）
  → dispatch.evaluate_dispatch() ← 运行检查：负责人存在/可用 · runtime 可用 · 项目可执行
      ├─ dispatchable → 派给**它自己的** assignee（backlog→todo→in_progress）
      ├─ queued       → 负责人正忙，排队（不是异常，不改派）
      └─ needs_management → 发 Decision-needed 事件，**等管理层**（R4/R5/R10）
  → WorkSession → Runtime → Artifact → task.completed
```

### 14d.3 结构就绪 ≠ 可派发（§3/§4/R3）

```text
结构就绪（Structural Ready）  = 依赖全部完成 —— 纯结构事实，不看人、不看资源
可派发（Dispatchable）        = 就绪 + 负责人有效 + runtime 可用 + 项目可执行 + 无冲突会话
```

两者**分开登记**在契约里：

```text
SYSTEM_BLOCKING_REASONS     = {not_ready, project_not_executable, task_held}
REQUIRES_MANAGEMENT_DECISION = {assignee_missing, assignee_inactive,
                                runtime_unavailable, provider_missing, task_failed}
DISPATCH_BLOCK_REASONS      = 前两者的并集（完备性有断言钉住）
```

### 14d.4 五条硬纪律（§3–§10）

```text
① task.ready 是**事实**，不是批准：发布它不授予任何权限、不产生 DecisionRecord（R8/R9）
② 就绪但没负责人 ⇒ task.assignment_required，系统**绝不**自动挑人（R2/R4）
③ 负责人不可用 ⇒ 报告原因并等待，**绝不**自动改派（R5）
④ 失败 ⇒ project.replan_required，**绝不**自动重做/自动回退上游（R10）
⑤ guided / managed / fixture **共用同一个 DAG 运行时**，没有 per-mode 派发器（R6/R7）
```

### 14d.5 Decision-needed 事件集是封闭的（§10/R11）

```text
task.assignment_required   就绪但没人负责
task.runtime_unavailable   负责人 / Runtime / 模型绑定不可用
task.blocked               有人显式标记阻塞
task.failed                执行失败 ⇒ 谁来 replan 是管理决策
task.review_failed         评审不通过（M2.7 落地）
project.replan_required    计划需要重做
```

事实事件（`FACT_EVENTS`）与它**不重名**：正常 DAG 推进只发事实，不唤醒管理层（R9）。
编排器的发布口有运行时断言 + AST 守卫双重检查：未登记的事件没有出路。

### 14d.6 Manager Agent 不是调度器（§9）

```text
❌ 不订阅 task.ready / task.completed 之类的事实事件去"接着排下一步"
✅ 只在这些事件上介入：Decision-needed 六件套 + 交付验收 + 人的显式指令
```

### 14d.7 运行时**不存在**的东西（§6/§12，AST 守卫钉住）

```text
× 按 TaskKind 分支推进（order_review → planning → research → …）
× 生成 Task 图（`_generate_graph` / 模板）
× 失败后把上游任务打回 todo（`_unblock_dependents`）
× 选择或更换负责人
```

确定性模板（`app/work/planning_fixture.py`）只负责**建图**，建完即退出：
它不碰调度、不碰 WorkSession、不碰推进 —— 与 Manager Agent 建的图进入运行时后完全等价。

## 15. M2 明确不做

```text
公司股权 / 公司买卖 / 融资 / 分红 / 贷款 / M&A / 复杂金融
中央 AI Planner
系统自动组队 / 自动招聘 / 自动辞退
固定职位 Skill 注入 / 固定职位 Prompt / 固定职位 Workflow
新建 Mission SoT / 新建 Agent SoT
第二套 Artifact System
第二套 Fit 引擎
```

---

## 16. 与既有冻结面的关系

| 冻结面 | M2 的关系 |
| --- | --- |
| **M1 经济（E1–E31，§39b）** | **只消费，不改契约**。WorkOrder/Contract/Escrow/Evaluation/Settlement 的语义与写入路径不动 |
| **T2 人才市场（I1–I13，§10）** | **只消费，不改契约**。市场投影白名单、身份不变、不复制人级资产继续由既有测试钉住。M2.8 只在**招募之后的开通**上加桥 |
| **K1/K2 知识** | RoleContext 的 `resource_index` 只引用既有 knowledge scope；不新增 scope |
| **R1 PersonCore** | RoleContext / DecisionRecord 一律使用 `person_id` 作为**人级**口径；`employee_id` 只作公司成员身份 |
| **P4d 职位层 ADR-1..10** | M2 全部继承。ADR-7（能力只能被证明）与 ADR-9（Fit 只推荐）在 M2 被加强为可测试不变量 |
| **v0.5 正式交付域** | 保留；降级为 `guided` 模式（§11.3） |

---

## 17. 关键裁决记录（M2 ADR）

| # | 裁决 | 理由 |
| --- | --- | --- |
| **M2-ADR-1** | Project 是唯一执行根；两路径降级为 `ProjectWorkMode` | 避免第四套任务实体（Audit §21 最高风险） |
| **M2-ADR-2** | 不新建 Mission 表（W20） | Project 已承载 MissionSpec 的 80% 字段 |
| **M2-ADR-3** | 不新建 Agent 表（W21） | 现有 Person + Employee + RuntimeInstance + Brain 已经是完整 Agent，缺的是**读面与决策记录**，不是实体 |
| **M2-ADR-4** | RoleContext 是**派生读模型**，不落表 | 派生态一旦落库必然与任职时间轴漂移（沿用 ADR-2/ADR-4 的同款理由） |
| **M2-ADR-5** | 管理决策统一走 `DecisionRecord`（append-only） | 可审计 + M3 的 Management Experience 需要它 |
| **M2-ADR-6** | `ReviewVerdict` 独立于 `EvaluationVerdict` / `ReviewDecision` | 三者对象不同（Task / WorkOrder / 阶段门），合并会把商业判定与质量判定绑死 |
| **M2-ADR-7** | Position → Authority 由**公司声明**，系统强制；Position → 能力**永不授予** | 权限是硬边界（W6），能力只能被证明（P4d ADR-7） |
| **M2-ADR-8** | 官方 Starter Playbook 只作 Knowledge，不做 System Prompt | W4/W26；否则玩家换 CEO 就失去意义 |
| **M2-ADR-9** | 路由（哪个职位负责 Work Intake）是**公司可配置的规则**，不是系统硬编码 | §2：系统只提供事实与规则，不替公司决定"应该怎么组织" |
| **M2-ADR-10** | M2.0 无迁移、无行为改动，只冻结契约 | 与 M1.0 / T2.0 同一纪律 |
| **M2-ADR-11** | Work Intake 是**公司可配的责任路由**（默认 CEO），不是 CEO 特权；解析不到负责人 ⇒ `waiting_for_management`，系统不代管 | 用户拍板 D1；`Agent makes decisions.` 的直接推论 |
| **M2-ADR-12** | 产品工作模式只有 `guided` / `managed`；确定性模板是**独立的基础设施轴**（`PlanningFixture`） | 用户拍板 D3；两个维度混在一个 enum 会让"测试替身"看起来像一种玩法 |
| **M2-ADR-13** | `work_mode` 与责任目标都是**项目级快照**；公司默认值的变化不改写既有项目 | 用户拍板 D2/B12；避免执行中语义漂移 |
| **M2-ADR-14** | 管理 actor 在项目上只存**当前指针**，权威是责任路由、历史归 DecisionRecord | 用户拍板"Project 不要保存 CEO 决定"；换人不丢历史 |
| **M2-ADR-15** | guided 是**教学/协助**层：Manager 仍自主决策，人类只在关键动作上确认 | 用户拍板 D2/B8；防止 guided 变成"系统替 CEO 规划" |
| **M2-ADR-16** | 管理授权落在**新薄表** `position_authority_grants`（v41）；`position_definition_packages` **保持**资源开通语义 | 用户拍板方案 A：管理权与「能访问什么资源」是两件事，混在一张表里必然互相污染 |
| **M2-ADR-17** | Authority **default-deny**，随 Active PositionAssignment 生效/失效；**永不**读 `employee.role` 字符串（也不读 Fit/分数/访问包） | 用户拍板；`role` 是 deprecated 镜像，用它判权限等于让镜像变成真相 |
| **M2-ADR-18** | Authority **只校验**（在不在授权内），**不**替 Agent 选人/排序/判断该不该做 | W39；`Agent makes decisions.` 的直接推论 |
| **M2-ADR-19** | 授权 **append-only + 时间窗**，并提供 `grants_hash` / `position_grants_hash` 快照，使历史 DecisionRecord 能解释「当时为什么有权」 | 用户要求 v41 同时提供审计/版本语义 |
| **M2-ADR-20** | 作用域只支持 `company` / `department` / `direct_reports` + 金额上限；**不**建通用 ABAC 引擎 | 用户拍板：够用即可，策略语言是长期负债 |
| **M2-ADR-21** | Role Resource Index 只存**指针**（`knowledge_items` / `drive_nodes` / `companies.settings`），目标不存在就报 `missing` | C6/W41；「给新 CEO 一份阅读清单」不能变成第二套文档系统 |
| **M2-ADR-22** | 读能力共享、写能力只在内部执行面：**没有**玩家面 `/tools` 写路由；人类动作走各领域正式 API，二者调用**同一个** application/domain service | 用户拍板方案 3 修正版；`UI rules == Agent rules`（T2/T3/T11）|
| **M2-ADR-23** | 工具是**自描述注册表条目**：`side_effect` / `required_authority` / `authority_target`；缺一声明在**注册时**炸 | 用户拍板 §4；让"忘了校验"在启动时暴露（T4）|
| **M2-ADR-24** | **Transport 不代表信任**：内部面同样做完整 Authority 校验；调试口只是"谁能发起" | 用户拍板 §5；禁止 `if internal_call: bypass_permission()` |
| **M2-ADR-25** | Actor 身份由 Runtime Session / WorkSession / 系统上下文注入，**参数里的身份字段一律拒绝** | 用户拍板 §6；身份若可被提示词指定，审计与权限同时失效（T5）|
| **M2-ADR-26** | **Authority ≠ Autonomy**：前者是组织权力，后者是"AI 能否无人确认执行"；M2.3 只冻结边界，且对 `requires_confirmation` **拒绝执行** | 用户拍板 §11；比"先放行、以后再补确认"安全 |
| **M2-ADR-27** | Side-effect 分三级 `READ / WRITE / HIGH_IMPACT`；M2.3 **不注册**任何 high_impact 工具 | 用户拍板 §9/§10；不为完整列表写空业务 |
| **M2-ADR-28** | 三层不混：`DecisionRecord`（意图）/ `ToolAudit`（执行事实）/ domain state（真相）各自独立成层，互相不复制 | 用户拍板方案 3 + Envelope；一次决策 → N 个动作，所以 tool call 不能等于 decision |
| **M2-ADR-29** | 关联**单向**：`ToolAudit.decision_id → DecisionRecord.id`；不建 `audit_ids[]` 反向数组 | 用户拍板 §2；同一关系存两处就一定会有对不上的那一天（DR3）|
| **M2-ADR-30** | Decision Envelope：Agent 一次提交 `{type, reason, intended_outcome, scope, context, actions}`，执行面负责落记录 + 执行 + 聚合状态 | 用户拍板 §3；`submit_decision()` 之后再逐个调工具是无意义仪式 |
| **M2-ADR-31** | `decision_semantics`（none/optional/required）是**工具属性**且注册时强制；管理动作必须隶属决策 | 用户拍板 §6；写动作不允许"不知道自己算不算决策"（DR7）|
| **M2-ADR-32** | 决策**不授予权限**；每个动作重新做 Authority 校验；`authority_json` 只是当时的证据 | 用户拍板 §10 = DR4；决策是 intent，不是 authorization |
| **M2-ADR-33** | 决策状态必须能表达 `PARTIALLY_APPLIED`；执行面用 **SAVEPOINT** 隔离失败的动作，不裸 rollback | 用户拍板 §8/§9；一个决策的多个动作不一定都成功，四舍五入就是用谎言覆盖事实 |
| **M2-ADR-34** | 决策上下文是**有界键集**快照 + 稳定引用 + 哈希，不是数据库副本 | 用户拍板 §11；"顺手把整张表塞进 JSON" 会让哈希失去意义（DR9）|
| **M2-ADR-35** | 工具执行事实落 `tool_audits` 而不是 `audit_logs`；`audit_logs` 继续承载人/领域动作 | 需要按 `decision_id` 反查，JSON blob 里没有可索引列；同一事实不留两个落点 |

---

## 18. 术语表（M2 冻结）

| 术语 | 定义 |
| --- | --- |
| **Work Root** | 一件被授权执行的工作的根（当前 = `Project`） |
| **Work Mode** | 工作根的执行形态（`managed` / `guided` / `template_graph`） |
| **Manager Agent** | 被授权做管理决策的 Agent（CEO / CTO / PM / Team Lead / Reviewer） |
| **Worker Agent** | 被分配执行 Task 的 Agent |
| **Role Context** | 履职上下文投影（派生读模型） |
| **Role Resource Index** | 建议 Agent 读取/学习的资源清单（只读引用） |
| **Decision Record** | 管理决策的 append-only 审计记录 |
| **Institutional Memory** | 随公司存续的资产（知识/政策/决策史/项目史/产物） |
| **Personal Memory** | 随 Person 存续的资产（记忆/学习/人格/技能/履历/证据/能力） |
| **Review Verdict** | 任务级技术评审结论（PASS/REWORK/REJECT/ESCALATE） |
| **Hard Constraint** | 系统强制并可拒绝的约束 |
| **Soft Constraint** | 系统只报告、不拒绝的约束 |
| **READY_TO_WORK** | 员工已具备 runtime + provider + workspace 的可执行状态（M2.8） |
| **Work Intake** | 组织责任：谁负责接收工作、做高层判断与委派（默认 CEO，公司可配） |
| **Planning Fixture** | 确定性规划替身（CI/教程/测试/演示基础设施，不是产品模式） |
| **Human Involvement** | guided 与 managed 的**唯一**差别：关键动作是否需要人类确认与讲解 |
| **Authority Grant** | 职位被授权做什么（`position_authority_grants` 一行）；default-deny、随任职生效失效 |
| **Authority Scope** | 授权的有限作用域：`company` / `department` / `direct_reports`（+ 金额上限）；不是 ABAC |
| **Role Resource Index** | 建议 Agent 读/学的**指针**清单（内容住在既有表里） |
| **Tool Registry** | 自描述的管理工具清单（含副作用等级与所需授权） |
| **Tool Executor** | 唯一执行面：参数校验 → Authority → Autonomy 门禁 → 应用 → 审计 |
| **TRUSTED ACTOR** | actor 身份只能来自 WorkSession / 系统上下文，不来自工具参数 |
| **Decision Envelope** | 一次提交的决策意图 + N 个动作（`decision_records` + `tool_audits`）|
| **Tool Audit** | 一次工具调用的执行事实（入参 / 授权结果 / 出参 / 错误）；可挂 `decision_id` |
| **Decision Semantics** | 工具与决策的关系：none / optional / required |
