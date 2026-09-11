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

## 10. DecisionRecord（管理决策可审计）

### 10.1 字段契约

```text
actor_person_id            做出决策的 Person（人级资产口径）
acting_employee_id         以哪个员工身份行权（公司成员身份）
acting_position_definition_id  当时占据的职位（可为空：AVAILABLE 的人也能被授权）
decision                   DecisionKind（§10.2）
scope                      "company:1" | "project:12" | "task:34" | "listing:5" …
reason                     Agent 自己写的理由（**系统不评价**）
context_snapshot           决策时看到的事实快照（可复现"当时它是怎么想的"）
actions[]                  通过 Tool 提交的动作（tool + 参数摘要 + 结果）
result                     决策的**结果**（事后回填，不是事前判定）
created_at / updated_at
```

示例：

```text
CTO Alice
decision = assign_task
scope    = task:34
reason   = "Bob 的 Rust Fit 88%，有网络经验，负载 20%"
context  = { project: "X", task: "Y", candidates: [...] }
actions  = [assign_task(task_id=34, employee_id=57)]
```

### 10.2 决策类型（`DecisionKind`）

`accept_project / decline_project / decompose_project / delegate_management /
assign_task / reassign_task / create_dependency / request_review / request_rework /
mark_blocked / replan / accept_delivery / recruit / purchase_agent /
assign_position / release_position / enroll_learning / offboard`

### 10.3 系统只记录，不评价「想法」

```text
记录：它做了什么决定、依据是什么、结果是什么
不记录/不产生："我觉得 CEO 的决定不好"
```

结果由真实事实形成证据：

```text
Project success / Task review / Rework / Delivery quality → Evidence
```

**管理 Agent 本身也可以成长**（M3：Decision → Outcome → Evidence → Management Experience）。

### 10.4 纪律

```text
W3   Management decisions must originate from an authorized Agent/User actor
W15  All management decisions are auditable
W28  DecisionRecord is append-only（决策永不重写；结果回填是追加，不是修改）
W19  Artifact lineage must be preserved（决策引用的产物可追溯）
```

`validate_decision_record()` **只校验结构**，绝不校验"理由是否合理" ——
由 `tests/test_m2_contract.py::test_decision_validation_never_judges_intent` 钉住。

---

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

### 11.3 Project 两路径的最终处理决策

现状（Audit §6.1/§18）：

```text
路径 A  create_order()              → order_review → 固定 GRAPH_TEMPLATE 4 阶段 → 真的跑 Agent
路径 B  create_structured_project()  → 11 ProjectPhase + 人工评审门 + 模板文档 → 完全不跑 Agent
前端立项向导恒走 B（is_structured=True）；A 没有 UI 入口。
```

**决策：Project 是唯一执行根；两种"路径"降级为同一个 Project 上的 `ProjectWorkMode`，不是两套代码路径。**

```python
class ProjectWorkMode(StrEnum):
    managed        = "managed"         # M2 目标：Task DAG 由 Manager Agent 决定
    guided         = "guided"          # 现有结构化交付：11 阶段 + 人工评审门（文档仪式）
    template_graph = "template_graph"  # 现有 legacy：固定 GRAPH_TEMPLATE（M2.5 退役）
```

收敛规则（逐阶段落地，M2.0 **只冻结、不改行为**）：

| 阶段 | 动作 |
| --- | --- |
| M2.0 | 冻结 `ProjectWorkMode` 与收敛映射；**不新增列、不改行为** |
| M2.1 | `Project` 承载 Canonical Spec（Background/Goal/Requirements/Constraints/Deliverables/Acceptance/Priority/Deadline/Context）；`guided` 成为其上的**交付仪式配置**，不再是平行项目类型 |
| M2.5 | `template_graph` 退役：`GRAPH_TEMPLATE` 退出业务真相，`managed` 成为默认 |
| M2.7 | `guided` 的评审门改为**复用同一套 Review 契约**（`ReviewVerdict` + `ReviewMeeting` 的映射表），不保留第二套决策语义 |
| M2.9 | `WorkOrder` → `Project` 绑定边落地；`submit.project_id` 从"自由字段"变成"受校验引用" |

**不做什么**（避免把可用的子系统拆掉）：

```text
❌ 不删除 v0.5 正式交付域（ProjectPhase / ReviewMeeting / Baseline / DeliveryPackage）
❌ 不删除 order_flow 的历史数据与 API（只在 M2.5 停止新项目生成 template_graph）
❌ 不新建第四套任务实体
```

### 11.4 Canonical Project Spec（Facts，不是 Execution Plan）

`Project` 必须至少能表达：

```text
Background / Goal / Requirements / Constraints / Deliverables
Acceptance Criteria / Priority / Deadline / Context
```

注意：这些是 **Facts / Requirements**，**不是**系统给出的 **Execution Plan**。
拆解由 Manager Agent 决定（W2 / W16）。

字段映射现状（M2.1 落地，M2.0 只冻结）：

| 契约字段 | 现有承载 |
| --- | --- |
| background | `projects.background` |
| goal | `projects.goal` |
| requirements | `project_requirements`（结构化路径）+ `projects.review_configuration`（无 requirement 时的兜底） |
| constraints | `projects.constraints` |
| deliverables | `projects.deliverables` |
| acceptance_criteria | `project_requirements.acceptance_criteria`（逐需求）+ `Project.review_configuration` |
| priority | `projects.priority` |
| deadline | `projects.planned_end_at` |
| context | `projects.description` + `source_order_text` |

> 结论：**契约字段全部有现有承载**，M2 不需要新表；缺的是「让 managed 模式也读它们」。

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

## 14. M2 不变量（W1–W31）

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

> **"M2.0 强制"** = M2.0 就有可执行测试锚点；
> **"冻结"** = M2.0 冻结契约与归属，锚点在其 owner 阶段落地。
> `tests/test_m2_contract.py::test_every_invariant_has_a_live_anchor_or_an_owner_stage` 保证
> **没有任何一条不变量被静默丢弃**（M1.10 锚点表同款纪律）。

---

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
