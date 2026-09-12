# M2 执行基线 — Agent Work & Organizational Runtime

> 领域设计：[m2-agent-work-runtime-design.md](m2-agent-work-runtime-design.md)（**契约与原则的唯一家**）
> 上游事实基线：[current-system-audit.md](current-system-audit.md)
> 前序冻结面：M1（`docs/m1-economy-design.md` §39b，E1–E31）· T2（`docs/t2-talent-market-design.md` §10，I1–I13）
>
> 本文是**落地计划**：阶段拆解、每阶段的范围 / 交付物 / 不变量 / 验收 / 风险。
> 与设计文档冲突时以设计文档为准；与代码冲突时以代码 + 测试为准（Audit 纪律）。

---

## 0. 总目标

> 让一个真实复杂问题进入公司后，**由公司的管理 Agent 根据职位责任、公司事实、人员能力、知识、资源和经验自主决策**，
> 组织多个 Agent 完成工作；Eidolon 只承担工作框架、规则验证、执行、资源、上下文、记录与审计。

**M2 的成功不是"系统会规划"，而是"公司里的 Agent 会规划，且系统只在它该管的地方管事"。**

---

## 1. 范围边界

### 1.1 M2 做

```text
Work & Role 域契约冻结（Project / Task / WorkSession / Artifact / Review / Decision / Position / RoleContext / WorkOrder）
Project 两路径收敛（单一执行根 + WorkMode）
Canonical Executable Project Spec
RoleContext + Role Resource Index + Authority Projection（派生读模型）
Management Agent Tooling（只读事实查询 + 受校验的写操作）
Leadership Planning & Delegation（Agent 提交结构化 Decision，系统 validate/apply/audit）
Dynamic Task Graph Runtime（DAG 正确性 + 就绪调度；模板退出业务路径、编排器变纯调度器）
Artifact Handoff & Lineage（基于现有 Drive，不新建第二套）
Review / Rework / Replan（Reviewer Agent 判定；Manager 决定 replan）
Recruit → READY_TO_WORK（招募后的自动开通编排）
WorkOrder ↔ Project Bridge（商业需求 → 执行载体绑定）
Golden Path × 3 + 冻结
```

### 1.2 M2 不做

```text
公司股权 / 公司买卖 / 融资 / 分红 / 贷款 / M&A / 复杂金融
中央 AI Planner · 系统自动组队 · 系统自动招聘 · 系统自动辞退
固定职位 Skill 注入 / Prompt / Workflow
新建 Mission SoT（W20）· 新建 Agent SoT（W21）
第二套 Artifact System · 第二套 Fit 引擎
统一简历 / 可靠度 / 领域专精 / 协作记录（→ M3）
官方任务市场扩展 / 玩家任务市场扩展 / 声誉（→ M4）
```

### 1.3 锁死的既有冻结面

| 面 | 纪律 |
| --- | --- |
| M1 经济 | **只消费不改**。所有资金变化仍只经 `LedgerService.post()`；只有 `MonetaryAuthority` 能 mint/burn。M2 新增的任何"付款方"必须走既有 Escrow/Contract/Settlement |
| T2 市场 | **只消费不改**。市场投影白名单、身份不变、不复制人级资产（I4/I5/I7）不得放松 |
| R1 PersonCore | 人级一律 `person_id`；`employee_id` 只表示公司成员身份 |
| P4d ADR-7/ADR-9 | 「能力只能被证明」与「Fit 只推荐」在 M2 被加强为可测试不变量（W7/W8/W11） |
| v0.5 正式交付域 | 保留，降级为 `guided` 模式 |

---

## 2. 阶段总表

| 阶段 | 名称 | 迁移 | 依赖 | 对应不变量 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **M2.0** | Work & Role Domain Contract Freeze | **无** | Audit | W4/W5/W6/W7/W8/W9/W10/W11/W13/W18/W20/W21/W23/W24/W25/W26/W27/W28/W29 | **DONE** |
| **M2.1** | Canonical Executable Project Spec | 有（v40） | M2.0 | W22/W30/W32/W33/W34/W35/W36 | **DONE** |
| **M2.2** | Role Context & Adaptive Onboarding | 有（v41） | M2.1 | W4/W5/W7/W8/W12/W27/W37–W42 | **DONE** |
| **M2.3** | Management Agent Tooling | 无 | M2.2 | W3/W6/W11/T1–T12 | **DONE** |
| **M2.4** | Leadership Planning & Delegation | 有（v42） | M2.3 | W1/W2/W3/W14/W15/W28/DR1–DR10 | **DONE** |
| **M2.5** | Dynamic Task Graph Runtime | 无（收敛既有表） | M2.4 | W2/W16/W30/R1–R12 | ✅ **DONE** |
| **M2.6** | Artifact Handoff & Shared Work Context | 有（v43：归属列 + 声明表 + 使用表） | M2.5 | W19/H1–H8 | ✅ **DONE** |
| **M2.7** | Review / Rework / Replan | 有（v44：评审请求 + 评审事实表） | M2.6 | W17/W29/RV1–RV8 | ✅ **DONE** |
| **M2.8** | Recruit → Ready-to-Work | 无（复用 provisioning + settings） | M2.7 | W31/RD1–RD7 | ✅ **DONE** |
| **M2.9** | WorkOrder Bridge | 有（binding） | M2.8 | W23 | PENDING |
| **M2.10** | Golden Path × 3 & Freeze | 无 | M2.9 | 全部 | PENDING |

> 顺序理由：**0 → 1 → 2 → 3 → 4 → 5** 是硬链（先有工作定义，才有履职上下文，才有工具，
> 才有决策，才有动态图）。**6 → 7 → 8 → 9** 可在 5 之后视情况并行（6 只碰 Artifact，
> 8 只碰 provisioning，9 只碰绑定边）；但 **10 要求全部收口**，因此顺序保留作默认路径。

---

## 3. M2.0 · Work & Role Domain Contract Freeze `[无迁移]` — **DONE**

### Goal

把 M2 全部**决策边界与领域契约**代码化，并在**零行为改动、零迁移**的前提下，
把「系统拥有事实 / Agent 拥有判断」变成可执行、可测试的契约。

### 交付物

- `docs/m2-agent-work-runtime-design.md`（契约与原则）
- `docs/m2-implementation-plan.md`（本文）
- `app/work/contracts.py` —— 纯契约层（不碰 Session / 不建表 / 不发事件）
  - **决策边界**：`FactKind`（系统事实）/ `DecisionKind`（Agent 决策）/ 两集互斥
  - **职责面**：`ResponsibilityArea` + `DEFAULT_DECISION_AUTHORITY`（**路由建议**，不是门禁）
  - **Position 契约**：`PositionContract` / `AuthorityKind` / `PositionExpectation` / `advisory_scope`
  - **约束分类**：`HARD_CONSTRAINTS` / `SOFT_CONSTRAINTS`（互斥且完备）
  - **RoleContext**：`RoleContext` / `RoleResource` / `RoleResourceKind`（无分值字段）
  - **Adaptive Onboarding**：`ROLE_ONBOARDING_PATH` / `FORBIDDEN_ONBOARDING_ACTIONS`
  - **记忆平面**：`MemoryPlane` / `MEMORY_PLANE_SURFACES`
  - **决策记录**：`DecisionRecord` / `DecisionAction` / `DecisionOutcome` / `validate_decision_record()`
  - **评审归属**：`ReviewVerdict` + `verdict_boundary_table()`
  - **工作根**：`ProjectWorkMode` / `PROJECT_SHAPE_CONVERGENCE` / `CANONICAL_PROJECT_FIELDS`
  - **DAG 正确性**：`validate_task_graph()`（纯函数：自环/环/悬空依赖/重复边）
  - **不变量表**：`INVARIANTS`（W1–W31，含 `enforced` / `owner_stage`）
- `app/models/enums.py` 追加纯 Python 枚举（无列引用 ⇒ 无迁移）：
  `ResponsibilityArea` / `DecisionKind` / `DecisionOutcome` / `ReviewVerdict` /
  `ProjectWorkMode` / `RoleResourceKind` / `MemoryPlane`
- `tests/test_m2_contract.py` —— 契约测试 + AST 守卫 + 不变量锚点表

### 明确不做

```text
不建表 · 不写迁移 · 不改任何 service 行为 · 不改 orchestrator
不实现 RoleContext 投影 · 不实现 Tool 面 · 不实现 DAG 调度器
```

### Acceptance

| # | 判据 |
| --- | --- |
| A1 | `FactKind` 与 `DecisionKind` 值集**互斥**（系统事实不可被当作 Agent 决策，反之亦然） |
| A2 | `HARD_CONSTRAINTS` 与 `SOFT_CONSTRAINTS` **互斥且覆盖**设计 §4 全部分类 |
| A3 | `RoleResource` **不含任何数值字段**（AST/字段扫描） |
| A4 | `RoleContext` 每个字段都能映射到**现有表/现有列**（无凭空字段） |
| A5 | `FORBIDDEN_ONBOARDING_ACTIONS` 在 `app/` 全仓**不存在同名函数**（AST 守卫） |
| A6 | `MEMORY_PLANE_SURFACES` 全部表名真实存在，两平面不相交，Personal 侧表带 person 口径 |
| A7 | 禁止出现新表 `missions` / `agents` / `mission_*` / `agent_*` SoT（W20/W21，模型注册表级守卫） |
| A8 | `validate_decision_record()` **只校验结构**，不评价理由（含反例测试） |
| A9 | `validate_task_graph()` 能检出 self-loop / cycle / dangling / duplicate（含属性测试） |
| A10 | 四个 verdict/assessment 面**互不替代**：值集不同、模块引用方向受守卫 |
| A11 | W1–W31 每条不变量**要么有现存测试锚点，要么有合法 owner 阶段**（无静默丢弃） |
| A12 | `app/workflow/` 与 `app/services/tasks.py` 不 import `app.talent.fit`（W11 AST 守卫） |
| A13 | 完整门禁全绿（pytest / ruff / format / alembic check / tsc / eslint / prettier / vitest / build），仅剩既有 WIP |
| A14 | **零迁移**：`alembic check` 无漂移，head 仍为 `64fec2d13d9b` |

### Risks

| 风险 | 对策 |
| --- | --- |
| 契约过度设计（写了一堆 M2.5 才用得到的类型） | 只冻结**会被 M2.1–M2.9 真正用到**的面；每个数据类都有 owner 阶段注释 |
| 契约变成第五套"评价" | §12.1 边界表 + W29 守卫：显式声明对象与判定者，禁止互相替代 |
| 守卫写成声明式装饰 | 沿用 M1/T2 纪律：守卫必须做**反例注入验证**（注入违规 → 转红 → 撤回） |
| 误把 target invariant 当作现状 | 不变量带 `enforced` / `owner_stage`；测试断言"无静默丢弃"，不假装已完成 |

---

## 4. M2.1 · Canonical Executable Project Spec `[有迁移]` — **DONE**

### Goal

让 `Project` 成为**唯一权威工作根**并承载 Canonical Spec；把两条历史路径
降级为**两个显式且正交的维度**（产品 `work_mode` / 基础设施 `planning_fixture`），
而不是两套互不可见的领域语义。

### 已拍板的产品决策（本轮正式落地）

| # | 决策 | 冻结为 |
| --- | --- | --- |
| **D1** | Work Intake 默认 CEO，但**公司可配**（COO / PM Lead / Research Director / 自定义职位）；解析不到负责人 ⇒ `waiting_for_management`，系统**绝不**随便挑人或代管规划 | M2-ADR-11 / W32 / W34 |
| **D2** | 新公司默认 `guided`；首次真实项目完成后公司默认转 `managed`；`work_mode` **项目级快照**，公司默认变化不改写既有项目；guided 与 managed 的差别只有 **human involvement** | M2-ADR-13/15 / W35 / W36 |
| **D3** | 确定性模板**保留但降级为 Test/Tutorial/CI Fixture**；生产项目**永不** fallback 到它；只能显式请求 + 部署门控 | M2-ADR-12 / W33 |

### 范围（已实现）

- **迁移 v40**（`a1c2e3f40517`，纯 additive）：`projects.{work_mode, planning_fixture,
  spec_version, work_intake_position_code, management_employee_id, management_person_id,
  management_assigned_at}` + 索引 `ix_projects_work_mode`；**事实驱动回填**（有 phase ⇒
  guided/none；无 phase 但有模板任务 ⇒ fixture；其余两边留 NULL = 未分类）
- **单一立项入口** `services/projects.create_project()`：
  `planning_fixture=deterministic_template` → 基础设施项目；`guided` → 引导仪式；
  `managed` → Work Intake 责任路由。`create_order()` 保留为**退役别名**（不再含分叉）
- **两条正交维度**：`ProjectWorkMode`（guided | managed，产品）+ `PlanningFixture`
  （none | deterministic_template，基础设施）；`GRAPH_TEMPLATE` 先更名
  `DETERMINISTIC_TEMPLATE_PLAN`，**M2.5 再整体搬出编排器**到
  `app/work/planning_fixture.py`（编排器里已无模板/建图/`kind` 分支代码，R12）
- **Work Intake 责任路由**（`app/work/work_intake.py`，只读）：公司配置 → 职位 code →
  PositionSlot → 生效 PRIMARY 任职 → Employee；4 种结果状态；多条在任者按
  `effective_from` 取最早并上报全部在任者（审计）
- **公司工作策略 API**：`GET|PATCH /company/work-policy`（`work_mode` + `work_intake_position_code`）
- **Canonical Spec 读面**：`GET /projects/{id}/spec` 回答 8 个问题（spec / completeness /
  work_mode / planning_fixture / work_intake / management（含 `stale`）/ execution）
- **不接管规划**（W34）：当时 `orchestrator._advance` 的两个规划分支都以
  `_uses_deterministic_plan()` 门控；非 fixture 项目在接收任务完成后发
  `project.awaiting_management_action` 并**停住**
  → **M2.5 更新**：那两个"规划分支"已整体删除（它们本身就是按 `kind` 推进）。
  现在没有任何分支：图由 Manager Agent 或 `planning_fixture` 建，
  就绪后没人负责 ⇒ `task.assignment_required`；没活了但项目没执行 ⇒ `awaiting_management_action`
- **默认值推进**（B7）：`work_defaults.promote_after_project_completion()` 在两个完成点
  （orchestrator final_review / 交付域 delivery 阶段）调用；只改默认值、幂等、
  用户显式配置过的公司永不被自动改写
- **前端**：项目详情页新增工作模式面板（模式 + 责任职位 + `waiting_for_management`
  引导 + 负责人/stale 提示）；`work_mode` 显式声明于立项向导与实战教程模板（与公司阶段无关）

### 明确不做

```text
不实现管理 Agent 的 Tool 面（M2.3）
不实现 DecisionRecord 落表（M2.4）
不退役确定性模板（M2.5）· 不统一 verdict 语义（M2.7）
不改 M1/T2 任何冻结契约
```

### Acceptance

| # | 判据 | 状态 |
| --- | --- | --- |
| B1 | 任何 Project 都能回答 Canonical Spec（8 个问题） | ✅ `test_project_answers_the_eight_canonical_questions` + `test_spec_reports_gaps_without_blocking_creation` |
| B2 | `POST /projects` 不再通过 `is_structured` 进入两套互不可见的语义 | ✅ `test_is_structured_no_longer_routes_domain_semantics`（同一载荷换 `work_mode` 得到两种确定形状） |
| B3 | guided / managed 共用同一 `projects` 行与同一 `tasks` 表 | ✅ `test_guided_and_managed_share_one_substrate` |
| B4 | 旧客户端只传 name/description 时行为逐字段兼容 | ✅ `test_legacy_bare_request_still_accepted_field_compatible`（两种模式各过一遍） |
| B5 | SQLite 只做 additive migration，不重建 `projects` 表 | ✅ `test_projects_migration_is_additive_only` + `alembic upgrade/downgrade/upgrade` 实测 |
| B6 | managed 默认 Work Intake = CEO，但通过 Responsibility Routing 实现，不硬编码 CEO 特权 | ✅ `test_work_intake_is_responsibility_routing_with_configurable_target`（改配到 QA → 路由随之改变；未知 code ⇒ 422） |
| B7 | 新公司默认 guided；完成首次真实项目后 company default 转 managed | ✅ `test_company_default_work_mode_follows_company_stage` + `test_first_completed_project_promotes_company_default` |
| B8 | guided 与 managed 的差别是 human involvement，不是 decision ownership | ✅ `test_work_mode_never_encodes_decision_ownership`（AST/契约级：不存在以 work_mode 为键的决策表） |
| B9 | template_graph 不得成为生产 fallback | ✅ `test_no_implicit_template_fallback_path_exists`（AST 守卫：模板调用必须在 fixture 门控内） |
| B10 | deterministic graph 只能被 tutorial/test/dev fixture 显式使用 | ✅ `test_planning_fixture_requires_explicit_request_and_gate`（未开启 ⇒ 422，不静默降级） |
| B11 | Manager 缺失或失败时进入 waiting/escalation，不允许系统代规划 | ✅ `test_missing_work_intake_manager_enters_waiting_not_fallback` + `test_managed_project_does_not_plan_itself` |
| B12 | Project 的 work_mode 必须 snapshot | ✅ `test_work_mode_is_snapshotted_and_survives_company_default_change` |

### Risks

| 风险 | 对策 |
| --- | --- |
| `guided` 是实战教程的载体 ⇒ 收敛破坏教程 | 教程模板**显式**声明 `work_mode=guided`（与公司阶段解耦）；有 `test_practice_template_declares_guided` |
| 测试共享公司状态 ⇒ 顺序依赖 | 依赖阶段/策略的用例**显式设置并还原**，或用不落库的纯函数路径；纯容器用例用 `no_work_intake` fixture |
| 既有"一键立项即跑 Agent"的用法消失 | 这是 D3 的**有意**行为变更：基础设施用法改走 `planning_fixture`（测试/CI 已改）；生产改走 managed 路由 |
| `work_mode` 留 NULL 的历史项目读面缺值 | 读面如实显示"历史项目（未分类）"；不做猜测性回填 |

## 5. M2.2 · Role Context & Adaptive Onboarding `[有迁移 v41]` — **DONE**

### Goal

让 Agent 上任后拿到的是 **RoleContext（履职上下文投影）**，而不是被注入的能力；
并把「职位被授权做什么」落成**可校验、可审计、default-deny** 的硬边界。

### 已拍板的产品决策（方案 A，本轮落地）

| # | 决策 | 冻结为 |
| --- | --- | --- |
| **A1** | 新增**薄表** `position_authority_grants`（migration v41）承载管理授权 | M2-ADR-16 / W42 |
| **A2** | `position_definition_packages` **保持**纯 Resource Provisioning / Entitlement 语义，不承载管理授权 | W42 |
| **A3** | Position 的职责与能力要求仍是 **Soft**；`AuthorityGrant` 是 **Hard Constraint** | W4 / W6 |
| **A4** | Authority 随 **Active PositionAssignment** 生效与失效，**不成为 Person 永久资产** | M2-ADR-17 / W38 |
| **A5** | **default-deny**；权限检查**不得**依赖 `employee.role` 字符串 | W37 |
| **A6** | 有限 scope/constraint：`company` / `department` / `direct_reports` + `spend max_amount`；**不建通用 ABAC 引擎** | M2-ADR-20 |
| **A7** | 系统只**校验** Agent 提交的管理动作是否合法，**不**利用 Authority 替 Agent 做任何管理决策 | M2-ADR-18 / W39 |
| **A8** | v41 提供**审计/版本语义**：append-only + 时间窗 + 双摘要，历史 DecisionRecord 可解释「当时为什么有权」 | M2-ADR-19 / W40 |

### 范围（已实现）

- **迁移 v41**（`b2d4f6a8c013`，纯 additive）：
  - `position_authority_grants`（新表）：`authority_kind` / `scope_kind` / `scope_ref`（**0 哨兵**，不用 NULL）
    / `max_amount` / `effective_from` / `effective_to` / `supersedes_grant_id` / 部分唯一索引
    `uq_position_authority_active`（同职位同授权同作用域至多一条生效行）
  - `position_definition_resources`（新表）：Role Resource Index 的**指针**
  - `position_definitions.advisory_scope`（新列）：advisory，不是工作边界
- **`app/work/authority.py`**：`resolve_actor_authority()` / `effective_grants()` / `authorizes()` /
  `requires()` / `grant_authority()`（幂等 append）/ `revoke_authority()`（关窗不删行）/
  `authority_snapshot_for()` / `direct_report_employee_ids()`（汇报子树，不含自己）
- **`app/work/role_context.py`**：`build_role_context()`（派生、现算、**不含分值**）+
  `resolve_role_resources()`（解析到 `knowledge_items` / `drive_nodes` / `companies.settings`，
  不命中如实报 `missing`）+ `declare_role_resource()`（幂等声明）
- **`app/work/authority_seed.py`**：冷启动默认授权（CEO 10 项 / PM 2 项 / QA 1 项 / 研究·工程 0 项）+
  默认 `advisory_scope` + 默认资源清单；**金额来自政策**（`settings.authority_default_spend_limit`）
- **`app/work/role_events.py`**：任职变化 → `role.context_available` / `role.context_withdrawn`
  （**只带事实**；不发"请去学习"的系统指令），受 `settings.role_context_events` 门控
- **API**：`GET /employees/{id}/role-context`（只读）
- **前端**：员工详情新增「履职上下文」Tab（职责 / 生效授权 / 期望引用 / 资源指针 / 履职事实）+ 中英文案

### 明确不做

```text
不建通用 ABAC / 策略语言（作用域只有三个值 + 金额上限）
不给玩家面的授权写端点（服务层入口 authority.grant_authority / revoke_authority）
不实现管理 Agent 的 Tool 面（M2.3）· 不落 DecisionRecord（M2.4）
不改 M1/T2 任何冻结契约
```

### Acceptance

| # | 判据 | 状态 |
| --- | --- | --- |
| C1 | 任命一个 Leadership 52 的人当 CTO **成功**（期望不是门禁） | ✅ `test_c1_expectation_shortfall_never_blocks_appointment` |
| C2 | 任命前后 `employee_competencies` / `skills` / `knowledge_items` **逐行不变**（W7/W8） | ✅ `test_c2_c4_appointment_never_touches_person_level_assets`（10 张人级表对拍） |
| C3 | 该 CTO 能读到公司允许的 Handbook 与公司知识（W9） | ✅ `test_c3_company_knowledge_stays_institutional_after_replacement` |
| C4 | 前任的 `memory_entries` / `learning_records` / `traits` **不迁移**（W10） | ✅ 同上（前任与新人两次对拍） |
| C5 | RoleContext 响应**不含**任何 score/level/rank；只含事实引用 | ✅ `test_c5_role_context_response_carries_no_capability_numbers`（递归键名扫描） |
| C6 | RoleResource 只能通过既有 `knowledge_items` / `drive_nodes` 解析（不造第二套内容） | ✅ `test_c6_role_resource_reports_missing_and_advisory_instead_of_inventing` + 列集扫描 |
| A1–A8 | 上述八条拍板 | ✅ 见 `tests/test_m2_role_context.py`（29 个用例） |

### Risks

| 风险 | 对策 |
| --- | --- |
| 授权表与资源包语义再次混在一起 | W42 结构守卫：packages 不允许出现授权列（已注入验证） |
| 测试共享库导致"别的用例的授权兜住断言" | `_authority_lab()` 给每个授权行为用例**独立职位定义**，隔离 grant 状态 |
| `DateTime` 列 naive/aware 混用崩溃（本仓已踩过） | `_naive()` 统一在比较前抹平时区；写入也用 naive |
| 金额授权"没有上限"被误读成"不限" | 契约与校验都写死：无上限 ⇒ **无法确认在授权内** ⇒ 拒绝 |
| RoleContext 变成"任命成绩单" | 期望只给引用 + 响应键名扫描守卫 |

## 6. M2.3 · Management Agent Tooling `[无迁移]` — **DONE**

### Goal

把「系统事实」与「受校验的写操作」暴露成**工具**，供管理 Agent 调用；
工具**不含任何决策逻辑** —— 系统只回答"在不在授权内、领域约束过不过"（T6/T7）。

### 已拍板的产品决策（方案 3 修正版：Read Shared / Write Internal）

| # | 决策 | 冻结为 |
| --- | --- | --- |
| **B1** | 读能力**共享**（Agent / UI / CLI 复用同一 QueryService），**不**新增 `/tools` 读路由 | M2-ADR-22 / T2 |
| **B2** | 写能力**只有内部执行面**；人类动作走各领域正式 API，二者调用**同一个** service | T2 / T11 |
| **B3** | **禁止**通用玩家 `POST /api/v1/tools/*` 写路由 | M2-ADR-22 / T3 |
| **B4** | Tool Registry 自描述：`name/description/input_schema/output_schema/side_effect/required_authority/handler` | M2-ADR-23 |
| **B5** | **Transport 不代表信任**：内部面照样完整 Authority 校验 | M2-ADR-24 / T4 / T10 |
| **B6** | Actor 身份由 WorkSession / 系统上下文注入；**参数里的身份字段一律拒绝** | M2-ADR-25 / T5 |
| **B7** | 系统只**校验并应用** Agent 已做出的决定；Fit 只作前置读事实 | T6 / T7 |
| **B8** | Side-effect 分三级 `READ / WRITE / HIGH_IMPACT`；M2.3 **不注册** high_impact 工具 | M2-ADR-27 |
| **B9** | **Authority ≠ Autonomy**：只冻结边界，且 `requires_confirmation` **拒绝执行** | M2-ADR-26 |
| **B10** | 调试口默认关、不进玩家 router、走同一段代码 | §12 / T10 |
| **B11** | 每次工具调用都留审计（读也留） | M2-ADR-23 / T12 |
| **B12** | 新增 `TaskStatus.blocked` / `cancelled` —— 让"卡住了"与"计划变了"有状态可表达 | 诚实状态 |

### 范围（已实现，**无迁移**）

- `app/work/tools.py`：`ToolSpec` / `ToolRegistry` / 参数子集校验 / `AUTONOMY_BY_SIDE_EFFECT` /
  `FORBIDDEN_TOOL_ARGUMENT_KEYS` / `assert_registry_is_sound`
- `app/work/tool_reads.py`：**15 个读工具**，全部是既有读面的适配器
- `app/work/tool_writes.py`：**9 个写工具**，全部调既有 service（状态机 / DAG 校验 /
  生命周期校验照走）
- `app/work/tool_executor.py`：注册表实例 + `context_for_work_session` /
  `context_for_employee` + `execute_tool` 五步 + 审计 + `describe_tools`
- `scripts/agent_tools.py`（`make agent-tools` / `make agent-tool-call`）：调试口，默认关
- `TaskStatus` 新增 `blocked` / `cancelled` + `ALLOWED_TRANSITIONS`（无迁移：status 是字符串列）
- `AuthorityKind.plan_project_work`（M2.3 新增；CEO 与 PM 种子授予）
- `AuthorityKind` 从契约层**移到** `models/enums.py`（它有宿主列了 —— 按仓库纪律归位，契约层 re-export）
- 前端：`TaskStatus` 联合类型 + 变体 + 节点配色 + i18n 中英（blocked/cancelled）

### 明确不做

```text
不实现 spend_credits / offboard_employee / purchase_talent 等 high_impact 工具
不建通用 ABAC / 策略语言 · 不建 Autonomy 策略引擎（只冻结边界）
不为 UI 增加 /tools 读出路由（UI 继续用领域读面）
不实现 ReviewRequest 实体（M2.7）· 不落 DecisionRecord（M2.4）
```

### Acceptance

| # | 判据 | 状态 |
| --- | --- | --- |
| T1 | 工具不拥有业务真相（不自建表、不直接写库、复用既有 service） | ✅ `test_tools_do_not_own_business_truth` |
| T2 | HTTP 与 Tool 共用同一应用/领域服务 | ✅ `test_read_tools_reuse_the_same_query_services_as_http`（逐字段对拍） |
| T3 | 没有玩家面 `/tools` 写路由 | ✅ `test_no_player_facing_tool_router_exists`（已注入验证） |
| T4 | 内部调用不绕过 Authority；缺声明在注册时炸 | ✅ `test_internal_transport_still_enforces_authority` + `test_registry_is_sound_...` + `test_declaring_a_write_tool_without_authority_is_impossible` |
| T5 | Actor 身份由上下文注入，参数里的身份被拒 | ✅ `test_actor_identity_comes_from_context_and_args_are_rejected` + `..._requires_a_running_session` |
| T6 | 读工具只给事实（`calculate_task_fit` 无排名、未知不当 0） | ✅ `test_calculate_task_fit_returns_facts_without_ranking` |
| T7 | 成功 = 决定 + 校验 + 应用（结果带 authority + audit_id） | ✅ `test_successful_write_is_decided_validated_and_applied` |
| T8 | `position_authority_grants` 是授权来源（撤回即刻失效） | ✅ `test_authority_source_is_the_grant_table` |
| T9 | 资源包不是授权替代物 | ✅ `test_resource_packages_do_not_grant_authority` |
| T10 | Transport 不改变领域不变量 | ✅ `test_transport_does_not_change_domain_invariants` |
| T11 | 人类 API 与 Agent 工具产出等价领域效果 | ✅ `test_human_and_agent_paths_produce_equivalent_domain_effects` + 结构守卫 |
| T12 | 每次调用可审计（读/写/被拒） | ✅ `test_every_tool_call_is_audited` + `test_audit_never_stores_raw_arguments` |

### Risks

| 风险 | 对策 |
| --- | --- |
| 写工具被绕过（未来有人加 HTTP 端点） | T3 守卫扫 `app/api/**`：不得出现 `/tools` 前缀、不得 import 执行面（已注入验证） |
| 授权被"顺手"跳过 | `ToolSpec.__post_init__` + `assert_registry_is_sound` 在**注册时**拒绝缺声明的写工具 |
| Actor 被提示词伪造 | 参数身份字段硬拒；审计记录上下文里的 actor（已注入验证） |
| 高影响动作被自动执行 | `AUTONOMY_BY_SIDE_EFFECT[high_impact] = requires_confirmation` ⇒ 无确认通道即拒绝（已注入验证） |
| `expire_on_commit=False` 导致关系缓存陈旧 | 依赖图从**表**读（`list_dependencies`），不从 ORM 关系读；注释写明踩坑 |
| 审计表增长 | 读调用也留审计（T12 是字面要求）；降噪应在观测层做聚合，而不是让某些调用不可追溯 |

## 7. M2.4 · Leadership Planning & Delegation `[有迁移 v42]` — **DONE**

### Goal

让管理 Agent 的决策**可审计地**变成真实工作：一条决策 = 一个 `DecisionRecord` +
**N 个 `ToolAudit`**；系统只记录、校验、执行，不生成管理判断。

### 已拍板的产品决策（方案 3 + Decision Envelope）

| # | 决策 | 冻结为 |
| --- | --- | --- |
| **E1** | 三层不混：`DecisionRecord`（意图）/ `ToolAudit`（执行事实）/ domain state（真相） | M2-ADR-28 / DR1 |
| **E2** | **不采用** `tool call = decision`；一条决策 → N 个动作 | M2-ADR-28 / DR2 |
| **E3** | 关联**单向**：`ToolAudit.decision_id → DecisionRecord.id`；不建反向数组 | M2-ADR-29 / DR3 |
| **E4** | Decision Envelope：一次提交 `{type, reason, intended_outcome, scope, context, actions}` | M2-ADR-30 |
| **E5** | `DecisionRecord` **不复制**执行细节（tool 名/入参/出参/错误） | DR5 |
| **E6** | `decision_semantics`（none/optional/required）是工具属性，注册时强制 | M2-ADR-31 / DR7 |
| **E7** | `parent_decision_id` 表达决策树；**不**新建 workflow 模型 | DR8 |
| **E8** | 状态必须能表达 `PARTIALLY_APPLIED`；失败的动作由 **SAVEPOINT** 隔离 | M2-ADR-33 / DR6 |
| **E9** | 不假定整个决策是一个事务：短决策逐动作提交，长决策可跨阶段 | 用户拍板 §9 |
| **E10** | 决策**不授予权限**；每个动作重新验证 Authority | M2-ADR-32 / DR4 |
| **E11** | 上下文 = 有界键集快照 + 稳定引用 + 哈希，不是数据库副本 | M2-ADR-34 / DR9 |
| **E12** | 只留 `Decision → Outcome` 可追踪能力；**不做** CEO/CTO 能力评分 | DR10 |

### 范围（已实现）

- **迁移 v42**（`c3e5a7b9d124`，纯 additive）：
  - `decision_records`（管理语义 + 有界上下文 + 状态 + parent/superseded）
  - `tool_audits`（tool 名 / decision_id / 入参与摘要 / 授权结果与 grant ids / 出参 / 错误 / 时间）
  - 二者都是新表，**不做数据回填**（M2.3 的 `tool.*` 行原样留在 `audit_logs` 里 ——
    把 JSON blob 拆成结构化行需要猜字段语义，"宁缺不错"）
- `app/models/decision.py`：`DecisionRecord` + `ToolAudit`
- `app/work/decisions.py`：`open_decision` / `execute_actions` / `resolve_status` /
  `resolve_decision` / `submit_envelope`（信封）/ `supersede` /
  `build_context` + `context_hash`（DR9）/ `decision_view` / `decision_stats`
- `app/work/tool_executor.py`：执行事实改落 `tool_audits`（T12/DR1）；
  `decision_id` 参数 + `decision_semantics` 门禁 + **SAVEPOINT 隔离失败动作**
- `app/work/tools.py`：`ToolSpec.decision_semantics` + 注册时校验（DR7）
- `app/work/tool_writes.py`：9 个写工具逐个声明语义（6 required / 3 optional）
- **只读 API**：`GET /decisions`（按 project/task/status 过滤）、`/decisions/{id}`、
  `/decisions/{id}/tool-audits`（反查执行事实）、`/decisions/stats`；**没有写端点**
- 前端：项目详情「管理决策」时间线（状态 / 理由 / 意图 / 动作定位 / 授权是否通过）+ i18n 中英

### 明确不做

```text
不建通用 workflow 模型（决策树只用一个 parent_decision_id）
不实现 Autonomy 策略引擎（M2.3 已冻结边界）
不做 CEO/CTO 能力评分（M3 的 Management Evidence）
不新增决策写端点（人类动作走各领域正式 API，与 Agent 共用同一批 domain service）
不改造 M2.3 已交付的读工具清单
```

### Acceptance

| # | 判据 | 状态 |
| --- | --- | --- |
| DR1 | 三层分离（表结构 + 模块不直接改领域状态） | ✅ `test_three_layers_are_separate`（已注入验证） |
| DR2 | 一条决策 → N 个动作；独立动作也合法 | ✅ `test_one_decision_produces_many_actions` + `test_direct_tool_call_without_decision_is_still_legal_for_optional_tools` |
| DR3 | 关联单向、无反向数组、读面反查 | ✅ `test_link_direction_is_single_way`（已注入验证） |
| DR4 | 决策不授予权限；被拒动作不落领域状态 | ✅ `test_decision_never_grants_authority` + `test_decision_authority_snapshot_is_evidence_not_a_pass`（已注入验证） |
| DR5 | 决策不复制执行细节 | ✅ `test_decision_record_does_not_copy_tool_payloads`（已注入验证） |
| DR6 | `PARTIALLY_APPLIED` 必须能表达、不被四舍五入 | ✅ `test_partial_apply_is_expressed_not_rounded` + `test_all_failed_actions_yield_failed_status`（已注入验证） |
| DR7 | 语义声明 + 执行门禁 + 注册时强制 | ✅ `test_decision_semantics_are_declared_and_enforced` + `test_declared_semantics_match_the_agreed_split`（已注入验证） |
| DR8 | `parent_decision_id` 决策树；无 workflow 表 | ✅ `test_parent_decision_forms_a_tree_without_a_workflow_model` |
| DR9 | 上下文有界 + 哈希可重算 + 拒绝未知键 | ✅ `test_context_is_bounded_and_hashed` + `test_unknown_context_keys_are_rejected`（已注入验证） |
| DR10 | 结果可追踪、无评分字段 | ✅ `test_outcome_is_traceable_without_scoring` |
| E1–E6（plan 旧判据） | 系统不建 Task（除非经 Tool）、决策含 actor/reason/context、不可 UPDATE、无授权即失败、系统不产出"决策好不好" | ✅ 见上述用例；`test_structure_is_validated_but_content_is_not` 覆盖 W18 |

### Risks

| 风险 | 对策 |
| --- | --- |
| **失败的动作把调用方的上下文一起回滚** | 实测踩到：一个动作在 handler 里被领域拒绝，`db.rollback()` 把整条 DecisionRecord 与前序成功动作全抹掉。修复：执行面用 **SAVEPOINT** 只回滚该 handler；决策意图**先提交**再执行动作（M2-ADR-33）|
| 决策行退化成审计转储 | DR5 守卫（字段与键级扫描 + 工具名不得出现在决策行）；完整入参出参只在 `tool-audits` |
| 把"部分生效"说成成功/失败 | DR6 三态聚合 + 专门用例（含领域状态断言） |
| 决策被当成权限 | DR4 用例：撤回授权后同一条决策的动作仍被拒 |
| 读面泄漏跨公司决策 | `test_decision_api_is_company_scoped` |
| 两个审计落点（`audit_logs` vs `tool_audits`） | 同一个事实只留一个落点：工具事实 → `tool_audits`；有专门用例断言 `audit_logs` 不再增长 |
| **SQLite 单写者争用（`database is locked`）** | 实测：套跑偶发失败在 `INSERT INTO tasks`。根因是**后台编排器**与测试的会话同时写；而且读→写升级在 SQLite 里是**死锁语义**（不等 busy timeout）。修复：新增 `settings.orchestrator_dispatch_enabled`（默认开、测试默认关，与 `position_access_sync` / `evidence_pipeline_enabled` 同一纪律），需要跑完整链的用例显式打开；同时把写 handler 里"提交后再查库"的顺序改掉（读事务会挡住 `bus.publish` 的写事务）|

## 8. M2.5 · Dynamic Task Graph Runtime `[无迁移]` — ✅ **DONE**

### 用户拍板的执行语义（设计 §14d，R1–R12）

```text
Manager chooses. System schedules. Worker executes. Manager intervenes only when judgment is required.
```

### 落地物

| 文件 | 角色 |
| --- | --- |
| `app/work/dispatch.py` | **"System schedules" 的唯一实现**：结构就绪（复用契约纯函数）+ 可派发判定 + 例外上报 |
| `app/work/planning_fixture.py` | 确定性模板的**新家**：6 阶段整图，建完即退出（只建图，不推进） |
| `app/workflow/orchestrator.py` | 重写成**纯调度器**：删除模板/建图/`kind` 分支/`_unblock_dependents` |
| `app/work/contracts.py` | §10b 就绪 vs 可派发 + 两类原因集 + 封闭的事件集；不变量 **R1–R12** |
| `tests/test_m2_dag_runtime.py` | 16 条用例（12 条锚点 + 4 条反例注入） |

### 门禁

| # | 判据 | 结果 |
| --- | --- | --- |
| F1 | 删除模板推进后，managed / guided / fixture 三种来源的图都能跑通 | ✅ |
| F2 | 就绪判定是**纯函数**（复用 M2.0 `resolve_ready_tasks`），不再有第二份口径 | ✅ |
| F3 | 扇出：多个无依赖 Task 同时就绪（受"一员工一 session"限制而排队） | ✅ |
| F4 | 扇入：等全部前置完成才就绪 | ✅ |
| F5 | 环 / 悬空依赖 / 自环 ⇒ 系统拒绝（M2.0 `validate_task_graph`） | ✅ 沿用 |
| F6 | 系统**不**回答"下一步建什么 Task"，编排器里没有自动补 Task 的路径（AST 守卫） | ✅ |
| F7 | 历史 `legacy_template` 项目仍可读 | ✅ 只加不删 |
| F8 | **R1–R12 每条都有锚点，且每条都用反例注入验证过**（注入 → 红 → 还原 → 绿） | ✅ 12/12 |

### 与计划的偏差（刻意）

| 计划 | 实际 | 原因 |
| --- | --- | --- |
| `app/work/task_graph.py` | `app/work/dispatch.py` + `app/work/planning_fixture.py` | 就绪判定本来就是契约纯函数；运行时只需要"适配 + 可派发判定"，一个模块足够，不为文件名造壳 |
| `tasks.origin = {legacy_template, manager, human}` | **未加** | 该阶段声明无迁移；且"图是谁建的"已由 `projects.planning_fixture` 与本阶段契约表达（R7）。需要独立标记时再单独立项 |
| `_finalize` 不再 `in_review → done` | **保留**，交 M2.7 | 那是当前**唯一**的完成通道；现在拆掉会让所有项目卡在 `in_review`。M2.7 用 `ReviewVerdict` 替换它（W17 的 owner 阶段就是 M2.7） |

---

## 9. M2.6 · Artifact Handoff & Shared Work Context `[有迁移]` — ✅ **DONE**

### Goal

让 Agent A 的输出真正成为 Agent B 的输入。**基于现有 Drive**，不建第二套。

```text
Manager 声明：B 要用 A 的产品   ← task_inputs（指向 Task，不是 artifact）
A 跑完 → 产物落 Drive          ← drive_nodes.task_id = A（产出归属，H2）
B 就绪 → 系统运行期解析输入     ← resolve_input_artifacts（H4）
B 开工 → 记下被谁在哪次会话用掉  ← artifact_links（H3/G5）
```

### 迁移 **v43** `43a70cd19cc7`（additive + 事实驱动回填）

| 变更 | 事实 | 说明 |
| --- | --- | --- |
| `drive_nodes.task_id`（+ 索引 + FK）| 产出归属 | 产出时写入、此后不变；人上传/历史为 NULL |
| `tasks.produces_json` | 声明的**预期**交付物类型 | `server_default '[]'`，枚举内校验 |
| `task_inputs`（新表）| 「要用谁的产品」= 计划声明 | 指向 **Task**；UNIQUE(task, source) |
| `artifact_links`（新表）| 「被谁在哪次会话用掉了」= 使用事实 | UNIQUE(artifact, task, role)；role 只有 `consumed_by` |

回填只搬库里**已经写着**的事实：`drive_nodes.task_id ← work_sessions.task_id`
（经 `work_session_id`）。没有会话关联的文档保持 NULL —— **不猜**。

### 落地物

| 文件 | 角色 |
| --- | --- |
| `app/work/handoff.py` | **唯一交接口径**：声明校验 / 运行期解析 / 使用事实 / lineage / 报告 |
| `app/repositories/handoff.py` | 两张表的查询层（表只被 repo 碰，守卫钉住）|
| `app/models/handoff.py` | `TaskInput` / `ArtifactLink`（**不是**产物存储）|
| `app/api/v1/tasks.py` | 读：`GET /tasks/{id}/artifacts`；写：`POST /tasks/{id}/inputs`、`.../consume`（越界 422）|
| `app/work/tool_reads.py` | 读工具 `list_task_artifacts`（与 HTTP 同一服务，T2/T11）|
| `app/work/tool_writes.py` | 写工具 `consume_artifact`（required）；`create_task` 增 `consumes` / `produces` |
| `app/workflow/orchestrator.py` | 解决输入 → `TaskContext.input_artifacts` → 记使用事实 → 写产出归属 |
| `app/runtimes/mock/templates.py` | 把输入内容渲染进产出（**可机器验证**的交接，G1）|
| `tests/test_m2_handoff.py` | 13 条（8 条 H 锚点 + G 验收 + 反例注入）|

### Acceptance

| # | 判据 | 结果 |
| --- | --- | --- |
| G1 | B 的 prompt / TaskContext 里能拿到 A 的 artifact **内容**（不是同一段 project 原文）| ✅ 产出里出现上游内容摘要（prompt 同样内联）|
| G2 | `GET /tasks/{id}/artifacts` 能追到上游 Task 链（≥2 跳）| ✅ `upstream` 带 depth 1/2 |
| G3 | 引用未完成的 Task 产物 → 系统拒绝（422）| ✅ 服务层 + HTTP 422 + 工具 `domain_rejected`（决策失败、审计留因）|
| G4 | 不新建 Artifact 表；`artifacts`（legacy）仍不被写入 | ✅ 表存在性 + 内容列扫描 + 跑完整项目后行数不变 |
| G5 | `artifact_links` 可追"这个产物被谁用了、用在哪次会话"| ✅ consumed 报告带 `work_session_id` / `actor_employee_id` |
| H1–H8 | 八条不变量各有锚点，且每条都有**反例注入**验证 | ✅ 11/11 条注入被守卫拦住 |

### 与计划的偏差（刻意）

| 计划 | 实际 | 原因 |
| --- | --- | --- |
| `artifact_links (produced_by / consumed_by)` | 只有 `consumed_by` | 产出归属由 `drive_nodes.task_id`(+`work_session_id`) 表达；再存一份 produced_by 就是同一事实两个落点 |
| `Task.consumes` 作为列 | `task_inputs` 表 | `consumes` 引用**别的行**（需要引用完整性与祖先校验），`produces` 只引用枚举值 ⇒ 前者建表、后者 `produces_json` |
| （计划未列）写端点 | 加 2 个小端点（inputs / consume）| G3 的 422 需要一个真实 HTTP 面；人类管理动作与 Agent 工具走**同一段**服务（T11）|

---

## 10. M2.7 · Review / Rework / Replan `[有迁移]` — ✅ **DONE**

### Goal

Worker 完成后**不再自动 done**。

```text
in_review → （只有结论）→ done / todo（返工）/ rejected / 原地（升级）
```

### 迁移 **v44** `b3aee926425c`（additive）

| 变更 | 内容 |
| --- | --- |
| `review_requests`（新表）| 谁请谁评 / `status ∈ {open, decided}` / `verdict` / `verdict_notes` / `verdict_decision_id` / 时间 |
| `review_facts`（新表）| **系统**收集的事实（`kind` + `payload_json` + `source`），**没有**结论列 |
| `tasks.rework_count` | 返工计数（从 0 起算，不回填：历史没有可复核记录，不猜）|

三条设计裁决：**状态不复述结论**（`status` 只有 open/decided）、
**结论追加式**（改判走新请求）、**事实与结论分开存**（谁写的可回答）。

### 落地物

| 文件 | 角色 |
| --- | --- |
| `app/work/reviews.py` | **唯一口径**：发起 / 出结论 / 落地映射 / replan 权限 |
| `app/work/review_facts.py` | 系统事实收集器（产物 / 声明差异 / 交接 / 会话 / 返工次数）|
| `app/work/review_fixture.py` | 确定性替身评审（与 planning fixture 同款门控，署在人头上）|
| `app/models/review.py` | `ReviewRequest` / `ReviewFact` |
| `app/api/v1/tasks.py` | 读+发起：`GET/POST /tasks/{id}/review` |
| `app/api/v1/reviews.py` | 出结论：`GET /task-reviews/{id}`、`POST /task-reviews/{id}/verdict` |
| `app/work/tool_writes.py` | `submit_review_verdict`（required）/ `replan_project`（required）/ `request_review` 升级为真实请求 |
| `app/work/tool_reads.py` | `inspect_task_review`（与 HTTP 同一份事实）|
| `app/workflow/orchestrator.py` | **删除** `in_review → done` 连跳；替身评审（门控）；`task.in_review` / `task.review_required` 事件 |
| `tests/test_m2_review.py` | 14 条（8 条 RV 锚点 + 端到端 + 工具 + 反例注入）|

### Acceptance

| # | 判据 | 结果 |
| --- | --- | --- |
| H1 | Task 完成后停在 `in_review`，直到有 `ReviewVerdict.passed` | ✅ 且 AST 守卫禁止连跳回归 |
| H2 | 系统**不产生**任何 verdict（无启发式自动 PASS） | ✅ AST 守卫 + 行为测试 + 事实收集器"无判断词"扫描 |
| H3 | `REWORK` → 回 `todo` + 记次数 + 理由可查 | ✅ `rework_count` +1、`actual_end_at` 清空、notes 落库 |
| H4 | `REPLAN` 只能由该 Project 的 Manager 发起 | ✅ 服务层 + 工具（required 语义）双重校验 |
| H5 | `ESCALATE` 不被系统自动处理 | ✅ 状态目标为 `None`，只发 `task.review_required` |
| H6 | 事实与 verdict 分开存 | ✅ 两张表、列级守卫（事实表无结论列、结论行无事实载荷）|
| RV1–RV8 | 八条不变量各有锚点 + **反例注入** | ✅ 12/12 注入被守卫拦住 |

### 与计划的偏差（刻意）

| 计划 | 实际 | 原因 |
| --- | --- | --- |
| Fact 含 `test_result` / `lint` / `build` | 不提供 | 本仓库没有可观察的运行器与产物；列出来就是空话（少而真）|
| `ReviewDecision ↔ ReviewVerdict` 映射表 | 固化 `GUIDED_REVIEW_DECISION_TO_VERDICT`（**单向**）| W29：不得互相替代；反向表不存在（有守卫）|
| （计划未列）替身评审 | `review_fixture.py`（门控 + 署名）| 与 D3/W33 的规划 fixture 同款：CI/教程/演示需要一条确定性链路，但**不能**变成"系统自动通过" |
| （计划未列）两个新事件 | `task.review_required`（决策需求）/ `task.review_passed` + `task.in_review`（事实）| 评审闭环必须能叫醒人，且不新增第三套语义（沿用 M2.5 的封闭事件集）|

---

## 11. M2.8 · Recruit → Ready-to-Work `[无迁移]` — ✅ **DONE**

### Goal

招募/购买得到的人**能立刻执行 Agent Task**。

```text
招募 → Employee → PositionAssignment → RoleContext
     → 环境编排（工作区 / 运行时实例 / 供应商绑定）
     → READY_TO_WORK（派生量）
```

### 无迁移（刻意的）

| 落点 | 复用 |
| --- | --- |
| 公司策略 | `companies.settings["runtime_defaults"]`（既有 JSON 列）|
| 编排步骤与失败原因 | `provisioning_jobs` / `provisioning_steps`（既有生命周期表）|
| 运行时实例 | `runtime_instances`（既有）|
| 供应商绑定 | `model_bindings` / `providers`（既有）|
| 就绪 | **派生**（不落列、不落枚举 —— RD1 有守卫）|

### 落地物

| 文件 | 角色 |
| --- | --- |
| `app/work/readiness.py` | **唯一口径**：策略解析/校验 + 四项事实 + 环境编排（不 commit）|
| `app/services/recruitment.py` | 招募时按策略建员工 + 同事务跑编排 + 返回就绪摘要 |
| `app/api/v1/employees.py` | 读 `GET /employees/{id}/readiness`；写 `POST /employees/{id}/provision`（重试编排）|
| `app/api/v1/company.py` | `GET/PATCH /company/work-policy` 增 `runtime_defaults`（禁止键 422）|
| `app/work/tool_reads.py` | 读工具 `inspect_readiness`（与 HTTP 同一份事实）|
| `app/work/dispatch.py` | 派发门禁新增 `assignee_not_ready`（W31；复用 `task.runtime_unavailable` 事件）|
| `tests/test_m2_readiness.py` | 12 条（7 条 RD 锚点 + I 验收 + 反例注入）|

### Acceptance

| # | 判据 | 结果 |
| --- | --- | --- |
| I1 | 招募后（mock 模式）该员工可被分配 Task 并真实跑完一个 WorkSession | ✅ 门禁放行 + mock 会话实测 |
| I2 | docker 模式（mock provider）真实运行时的环境事实齐备；失败时 job `partial` + 明确原因 | ✅ 缺供应商/供应商不存在 ⇒ `partial` + 步骤 error；补齐后 `done` |
| I3 | `READY_TO_WORK` 是**派生**的（不落列），逐项可解释 | ✅ 列扫描 + 枚举扫描 + AST 写入守卫 |
| I4 | 编排失败/回滚不留半个员工 | ✅ `orchestrate` 不 commit；回滚后无 job / 无实例 |
| I5 | 人级资产在任何开通步骤前后**逐行不变** | ✅ 快照对拍（技能行逐字段）|
| I6 | Default Runtime Policy 里**没有**人格/提示词/工作方式 | ✅ 允许键与禁止键导入期断言不相交 + HTTP 422 |
| RD1–RD7 | 七条不变量各有锚点 + **反例注入** | ✅ 11/11 注入被守卫拦住 |

### 与计划的偏差（刻意）

| 计划 | 实际 | 原因 |
| --- | --- | --- |
| "docker 模式 runtime instance 通过健康检查" | 编排**登记**实例（`created` / `unknown`），拉起容器仍归运行时管理器 | 同步编排路径不拉容器（那需要 docker + 供应商凭据）；就绪由**实例状态**决定，所以"拉起来了没有"依然是事实 |
| 提供 `lint`/`build` 之外的**环境**事实 | 只提供四项（position/workspace/runtime/provider）| 少而真：每一项都能落到可核对的行或目录 |
| 计划未列 | 就绪**门禁**与 `READY_TO_WORK` 分开（软契约项不进闸门） | W5：缺编制不该等于"永远不能干活"；真正拦人是环境跑不起来 |

---

## 12. M2.9 · WorkOrder Bridge `[有迁移]`

### Goal

连接商业需求与执行载体，**不把 WorkOrder 变成执行图**（W23）。

### 范围

- `work_orders.project_id` 从"无校验自由字段"变成**受校验引用**（同公司、同交付意图）
- `WorkOrderService.accept()` → 发 `work_order.accepted` → **路由给公司的 Work Intake 职位**
  （M2.4 路由规则）→ 由 Manager 决定"接不接 / 怎么组织"
- 交付提交时的 `artifact_refs` 校验：必须指向**本公司的真实 Artifact**（W19）
- `Evaluation` / `Settlement` 走 M1 既有路径，**不动 E 系列不变量**

### Acceptance

| # | 判据 |
| --- | --- |
| J1 | `ACCEPTED` 之后项目被绑定或由 Manager 显式拒绝（两条路径都有记录） |
| J2 | 提交一个不存在的 `project_id` / `artifact_refs` → 422 |
| J3 | 跨公司的 `project_id` → 404（隔离不破） |
| J4 | M1 Golden Path 逐条不变（E1–E31 锚点全绿） |
| J5 | WorkOrder 状态机**不新增**状态、不新增必经步骤（只加绑定边） |

---

## 13. M2.10 · Golden Path × 3 & Freeze `[无迁移]`

### 场景 A — 公司已有完整团队

```text
复杂问题 → Project → 路由到 CEO Agent → CEO 自主决定自己规划或委派 CTO
        → CTO 查 People/Competencies/Experience/Load/Runtime
        → CTO 自主建 DAG + 自主选人 → 系统 validate → 系统执行
        → Artifact → Handoff → Reviewer Agent → PASS
        → Final Delivery → Evidence → Agent Growth
```

### 场景 B — Agent 能力不足

```text
Project → Manager 查询团队 → 发现缺 Kubernetes 能力
        → Manager 自主四选一（学 / 调 / 招 / 改方案）—— 系统不替它选
        → 走对应路径并留下 DecisionRecord + 结果
```

### 场景 C — 新 CEO 接任

```text
CEO A 离任 → CEO B 上任
  B 未获得 A 的 Personal Skill / Memory / Traits / Evidence
  B 获得：CEO RoleContext + Company Policy + Company Knowledge + 历史 DecisionRecord + Current Projects
  B 自主学习 → 开始管理 → 产生自己的 DecisionRecord 与 Evidence
```

### Acceptance

| # | 判据 |
| --- | --- |
| K1 | A/B/C 三个场景各有一条 E2E 测试（真实调用链，不是接口 200） |
| K2 | W1–W31 全部有**现存**测试锚点（不再是 `owner_stage`） |
| K3 | 反例注入验证：抽掉任一条守卫 → 对应测试转红（守卫不是声明式装饰） |
| K4 | M1 / T2 / T1 / R1 的冻结锚点全绿（无回归） |
| K5 | 完整门禁全绿（含 build） |
| K6 | 冻结面落盘：形态 / 不变量 / 唯一写入路径 / 模块边界 / 关键裁决 / 留给 M3 的清单 |

---

## 14. 验收矩阵（阶段 × 不变量）

| 不变量 | M2.0 | M2.1 | M2.2 | M2.3 | M2.4 | M2.5 | M2.6 | M2.7 | M2.8 | M2.9 | M2.10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| W1 系统不选队员 | 冻结 | | | | ✅ | 强 | | | | | 锚点 |
| W2 系统不拆解 | 冻结 | | | | ✅ | 强 | | | | | 锚点 |
| W3 决策须授权 actor | 冻结 | | | ✅ | 强 | | | | | | 锚点 |
| W4 Position 三段式 | ✅ | | 强 | | | | | | | | 锚点 |
| W5 scope 非硬边界 | ✅ | | | | | | | | | | 锚点 |
| W6 权限是硬边界 | ✅ | | | 强 | | | | | | | 锚点 |
| W7 不授予能力 | ✅ | | 强 | | | | | | 强 | | 锚点 |
| W8 不复制人级资产 | ✅ | | 强 | | | | | | 强 | | 锚点 |
| W9 制度记忆存续 | ✅ | | ✅ | | | | | | | | 锚点 |
| W10 个人记忆随人 | ✅ | | ✅ | | | | | | ✅ | | 锚点 |
| W11 Fit 只做支持 | ✅ | | | ✅ | | | | | | | 锚点 |
| W12 可越界工作 | 冻结 | | ✅ | | ✅ | | | | | | 锚点 |
| W13 不自动辞退 | ✅ | | | | ✅ | | | | | | 锚点 |
| W14 替换属管理层 | 冻结 | | | | ✅ | | | | | | 锚点 |
| W15 决策可审计 | 冻结 | | | | ✅ | | | | | | 锚点 |
| W16 执行归系统/设计归管理 | 冻结 | | | | | ✅ | | | | | 锚点 |
| W17 评审不自动通过 | 冻结 | | | | | | | ✅ | | | 锚点 |
| W18 测试只给事实 | ✅ | | | | ✅ | | | ✅ | | | 锚点 |
| W19 lineage 保留 | 冻结 | | | | | | ✅ | | | ✅ | 锚点 |
| W20 不新建 Mission SoT | ✅ | ✅ | | | | | | | | | 锚点 |
| W21 不新建 Agent SoT | ✅ | | ✅ | | | | | | | | 锚点 |
| W22 Project 是唯一执行根 | 冻结 | ✅ | | | | 强 | | | | | 锚点 |
| W23 WorkOrder 只作包装 | ✅ | | | | | | | | | ✅ | 锚点 |
| W24 真实事实才产证据 | ✅ | | | | | 强 | | ✅ | | | 锚点 |
| W25 mock 不冒充成功 | ✅ | | | | | | | | | | 锚点 |
| W26 Position 不派生 prompt/SOP | ✅ | | ✅ | | | | | | ✅ | | 锚点 |
| W27 RoleResource 无分值 | ✅ | | ✅ | | | | | | | | 锚点 |
| W28 DecisionRecord append-only | ✅ | | | | ✅ | | | | | | 锚点 |
| W29 四个 verdict 面不互相替代 | ✅ | | | | | | | ✅ | | | 锚点 |
| W30 guided/managed 共用底座 | 冻结 | ✅ | | | | ✅ | | ✅ | | | 锚点 |
| W32 Work Intake 是责任路由 | 冻结 | ✅ | 强 | | ✅ | | | | | | 锚点 |
| W33 fixture 只可显式+门控 | 冻结 | ✅ | | | | 强 | | | | | 锚点 |
| W34 负责人缺失⇒等待不接管 | 冻结 | ✅ | | | ✅ | | | | | | 锚点 |
| W35 work_mode 快照 | 冻结 | ✅ | | | | | | | | | 锚点 |
| W36 模式差别只有人类参与 | 冻结 | ✅ | | | ✅ | | ✅ | | | | 锚点 |
| W37 Authority default-deny、不读 role | 冻结 | | ✅ | | ✅ | | | | | | 锚点 |
| W38 Authority 随任职生效失效 | 冻结 | | ✅ | | ✅ | | | | | | 锚点 |
| W39 Authority 只校验不决策 | 冻结 | | ✅ | | ✅ | | | | | | 锚点 |
| W40 Authority append-only + 双摘要 | 冻结 | | ✅ | | ✅ | | | | | | 锚点 |
| W41 资源只是指针 | 冻结 | | ✅ | | | | | | | | 锚点 |
| W42 packages 不承载管理授权 | 冻结 | | ✅ | | | | | | | | 锚点 |
| T1 工具不拥有业务真相 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T2 HTTP 与 Tool 同一 service | 冻结 | | | ✅ | | | | | | | 锚点 |
| T3 无玩家面 /tools 写路由 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T4 内部不绕过 Authority | 冻结 | | | ✅ | | | | | | | 锚点 |
| T5 Actor 身份由上下文注入 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T6 读工具只给事实 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T7 决定→校验→应用 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T8 授权来源是 grant 表 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T9 资源包≠授权 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T10 Transport 不改不变量 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T11 人机路径效果等价 | 冻结 | | | ✅ | | | | | | | 锚点 |
| T12 每次调用可审计 | 冻结 | | | ✅ | | | | | | | 锚点 |
| DR1 三层分离 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR2 一决策 N 动作 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR3 关联单向 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR4 决策不授权限 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR5 不复制执行细节 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR6 部分生效可表达 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR7 决策语义声明+门禁 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR8 决策树 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR9 上下文有界+哈希 | 冻结 | | | | ✅ | | | | | | 锚点 |
| DR10 结果可追踪、无评分 | 冻结 | | | | ✅ | | | | | | 锚点 |
| W31 未开通不可执行 | 冻结 | | | | | | | | ✅ | | 锚点 |

---

## 15. 横切要求（每个阶段都要过）

1. 落地前逐条自查设计 §14 的不变量；新增不变量登记到设计 §14 与 `app/work/contracts.py::INVARIANTS`。
2. 门禁全绿（pytest / ruff check / ruff format / alembic check / tsc / eslint / prettier / vitest / build）。
3. 每个阶段结束更新本文 §2 状态表 + §16 Progress + 相关域文档。
4. **不碰 M1/T2 冻结面**：若必须触碰，先跑 `test_m1_golden_path` / `test_m1_invariants` /
   `test_t2_golden_path` / `test_market_*` / `test_recruitment`。
5. **每个守卫必须做反例注入验证**（注入违规 → 转红 → 撤回），禁止声明式装饰。
6. **人级资产对拍**：任何触碰任免/招募/调岗的阶段，都必须对拍
   `traits / skills / knowledge / evidence / competency / memory / learning` 逐行不变。
7. API 变更同步 `apps/web/src/types` 与 i18n（中英逐键）。

---

## 16. M2 Progress

> 每完成一个阶段更新本表；新 Agent 从这里恢复上下文。

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| M2.0 Work & Role Domain Contract Freeze | **DONE**（2026-09-11） | `366c540` | 设计 + 执行基线 + 契约代码 + 守卫测试；**无迁移**；head 仍 `64fec2d13d9b` |
| M2.1 Canonical Executable Project Spec | **DONE**（2026-09-11） | `01060ab` | 迁移 **v40**；单一立项入口 + 两轴路由 + Work Intake 责任路由 + Canonical Spec 读面；W32–W36 强制 |
| M2.2 Role Context & Adaptive Onboarding | **DONE**（2026-09-11） | `164272c` | 迁移 **v41**；Authority Projection（default-deny / 有限作用域 / append-only 双摘要）+ RoleContext 投影 + Role Resource Index；W37–W42 强制 |
| M2.3 Management Agent Tooling | **DONE**（2026-09-11） | `e402c06` | 无迁移；Read Shared / Write Internal + Tool Registry + 执行五步 + 调试口；T1–T12 强制 |
| M2.4 Leadership Planning & Delegation | **DONE**（2026-09-11） | `073c0ab` | 迁移 **v42**；Decision Envelope + 三层分离 + 决策树 + 只读决策面；DR1–DR10 强制 |

### Progress Log

- **2026-09-11 · M2.0 DONE —— Work & Role Domain Contract Freeze**
  - **产出**：`docs/m2-agent-work-runtime-design.md`（§0–§18，含 W1–W31 不变量与 10 条 M2 ADR）、
    `docs/m2-implementation-plan.md`（本文）、`app/work/contracts.py`（纯契约层）、
    `app/models/enums.py` 追加 7 个纯 Python 枚举、`tests/test_m2_contract.py`。
  - **契约内容**：决策边界（`FactKind` / `DecisionKind` 互斥）、职责面与**路由建议**
    （`ResponsibilityArea` + `DEFAULT_DECISION_AUTHORITY`）、Position 三段式
    （`PositionContract` = Responsibility + Authority + Expectations）、硬/软约束分类、
    `RoleContext` / `RoleResource`（无分值字段）、Adaptive Onboarding 路径 + 禁止动作清单、
    记忆平面（制度 vs 个人，逐表声明）、`DecisionRecord`（append-only 字段契约 + 结构化校验）、
    `ReviewVerdict` 与四面 verdict 边界表、`ProjectWorkMode` 与两路径收敛映射、
    Canonical Project Spec 字段映射、`validate_task_graph()`、不变量注册表 W1–W31。
  - **关键裁决**：M2-ADR-1（Project 是唯一执行根，两路径降级为 WorkMode）、
    M2-ADR-2/3（不新建 Mission / Agent SoT）、M2-ADR-4（RoleContext 是派生读模型）、
    M2-ADR-6（`ReviewVerdict` 独立于 `EvaluationVerdict` / `ReviewDecision`）、
    M2-ADR-7（Position → Authority 由公司声明；Position → 能力永不授予）、
    M2-ADR-9（Work Intake 路由是公司可配置规则，不是系统硬编码）。
  - **迁移**：**无**。`alembic check` 无漂移；head 仍为 `64fec2d13d9b`（v39）。
  - **门禁**：pytest **1064 passed / 6 deselected**（+46 M2.0 契约测试；基线 1018）；
    ruff check 全绿；ruff format 仅 5 个**既有** WIP 红（未新增）；
    web vitest 83 files / 351 passed、tsc / eslint / prettier / build 全绿。
  - **守卫反例注入已验证**（4/4 转红后撤回）：
    (a) 注入 `inject_role_skills` → W4/W8/W26 守卫转红；
    (b) 注入后台 `offboard()` 调用 → W13 守卫转红；
    (c) 向契约层注入 `pick_best_engineer()` → "契约层不得替 Agent 决策" 守卫转红；
    (d) 让经济域 import `ReviewVerdict` → W29 守卫转红。
  - **风险**：R1（契约过度设计）— 只冻结 M2.1–M2.9 真正会消费的面，每个数据类标注 owner 阶段；
    R2（守卫变装饰）— 已做反例注入验证；R3（target invariant 被误读为现状）— 不变量带
    `enforced` / `owner_stage`，测试只断言"无静默丢弃"。

- **2026-09-11 · M2.1 DONE —— Canonical Executable Project Spec**
  - **拍板落地**：D1（Work Intake = 可配责任路由，默认 CEO）、D2（新公司 guided → 首次真实项目后
    转 managed；`work_mode` 项目级快照；模式差别只有 human involvement）、
    D3（确定性模板降级为 Test/Tutorial/CI Fixture，生产永不 fallback）
  - **迁移**：**v40** `a1c2e3f40517`（纯 additive：7 个新列 + 1 个索引 + 事实驱动回填）；
    `upgrade → downgrade -1 → upgrade` 实测通过；`alembic check` 无漂移
  - **代码**：`app/work/{work_intake,work_defaults}.py`（新增）、`app/services/projects.py`
    （单一入口 + Canonical Spec 读面）、`app/services/project_delivery.py`（guided 仪式体 + 完成点推进）、
    `app/workflow/orchestrator.py`（模板更名 + fixture 门控 + `awaiting_management_action`）、
    `app/api/v1/{projects,company}.py`（`/spec`、`/company/work-policy`）、
    `app/models/project.py` + 迁移、`app/models/enums.py`（`ProjectWorkMode` 收敛为两值 +
    `PlanningFixture` + `ResponsibilityKind` + `ProjectStatus.waiting_for_management`）
  - **前端**：项目详情工作模式面板 + `waiting_for_management` 状态与文案（中英逐键）+ `/spec` 类型
  - **测试**：新增 `tests/test_m2_project_spec.py`（23 个，B1–B12 全覆盖）；
    `test_m2_contract.py` 48 个（含跨模块不变量锚点解析）；既有 7 个依赖隐式模板的用例改为
    **显式**请求 fixture / 显式 managed 容器
  - **门禁**：pytest **1109 passed / 6 deselected**；ruff check 全绿；ruff format 仅既有 5 个 WIP 红；
    alembic check 无漂移（head = v40）；web tsc / eslint / prettier / vitest(351) / build 全绿
  - **风险**：R4（教程被收敛破坏）— 教程模板显式声明 guided，有测试；R11（测试顺序依赖）—
    依赖公司阶段/策略的用例显式设置并还原；R12（"一键立项即跑 Agent"消失）— 这是 D3 的**有意**
    行为变更，CI/测试改用显式 fixture，生产改走 managed 路由

- **2026-09-11 · M2.2 DONE —— Role Context & Adaptive Onboarding**
  - **拍板落地（方案 A）**：新薄表 `position_authority_grants`（v41）承载管理授权；
    `position_definition_packages` 保持资源开通语义；Position 职责/期望仍是 Soft、
    `AuthorityGrant` 是 Hard；Authority 随 Active PositionAssignment 生效/失效（不是人级资产）；
    **default-deny**、不读 `employee.role`；有限作用域（company/department/direct_reports +
    spend max_amount，**不建 ABAC**）；系统只**校验**不替 Agent 决策；
    append-only + 时间窗 + 双摘要（`grants_hash` / `position_grants_hash`）使历史可解释
  - **迁移**：**v41** `b2d4f6a8c013`（纯 additive：2 张新表 + 1 新列 + 3 索引）；
    `upgrade → downgrade -1 → upgrade` 实测；`alembic check` 无漂移
  - **踩坑记录**：迁移里的 `server_default` 必须用 `sa.text("'company'")`，
    普通字符串会被再包一层引号（SQLite 里存成 `'''company'''`，读回来 JSON 直接炸）——
    已写进迁移 docstring 防复用
  - **代码**：`app/work/{authority,role_context,authority_seed,role_events}.py`（新增）、
    `app/models/position.py`（2 模型 + `advisory_scope` 列）、`app/models/enums.py`（`AuthorityScopeKind`）、
    `app/api/v1/employees.py`（`GET /employees/{id}/role-context`）、
    `app/schemas/{work.py,position.py}`、`app/services/position_service.py`（advisory_scope 出口）、
    `app/main.py`（seed + 消费者注册）、`app/core/config.py`（2 个设置项）
  - **前端**：员工详情新增「履职上下文」Tab（职责 / 生效授权 / 期望引用 / 资源指针 / 履职事实）；
    `types` + `api` + `hook` + i18n 中英逐键
  - **测试**：新增 `tests/test_m2_role_context.py`（**29 个**，C1–C6 + 八条拍板全覆盖）；
    `test_m2_contract.py` 扩到 50 个（`AuthorityGrant` 对齐表结构 + 锚点跨三文件解析）
  - **门禁**：pytest **1120 passed / 6 deselected**（随机序与固定序均绿）；
    ruff check 全绿；ruff format 仅既有 5 个 WIP 红；alembic check 无漂移（head = v41）；
    web tsc / eslint / prettier / vitest(351) / build 全绿
  - **守卫反例注入已验证（4/4 转红后撤回）**：授权层读 `employee.role` / revoke 改成删行 /
    把 `authorizes` 改名成 `should_authorize` / 往 packages 里加授权列
  - **风险**：见 §5 Risks（测试隔离、naive/aware 时间、无上限≠不限、RoleContext 不变成成绩单）

- **2026-09-11 · M2.3 DONE —— Management Agent Tooling**
  - **拍板落地（方案 3 修正版）**：读能力共享（复用既有 QueryService，不新增 `/tools` 读路由）；
    写能力只在**内部执行面**；人类动作走各领域正式 API 且调用**同一个** application service；
    禁止通用玩家 `/tools` 写路由；Transport 不代表信任；Actor 身份由上下文注入；
    系统只校验并应用；side-effect 三级分类；Authority ≠ Autonomy（只冻结边界）；
    调试口默认关且走同一段代码；每次调用都留审计
  - **代码**：`app/work/{tools,tool_reads,tool_writes,tool_executor}.py`（新增，共约 1900 行）、
    `scripts/agent_tools.py`（新增调试口）+ Makefile 两个目标、`app/core/config.py`
    （`agent_tool_cli_enabled`）、`app/models/enums.py`（`ToolSideEffect` / `ToolTransport` /
    `AutonomyLevel` / `TaskStatus.blocked|cancelled` / `AuthorityKind` 归位 + `plan_project_work`）、
    `app/services/tasks.py`（状态机加两个状态）、`app/work/{contracts,authority_seed}.py`
  - **前端**：`TaskStatus` 联合类型 / 变体 / 节点配色 / i18n 中英（blocked、cancelled）
  - **测试**：新增 `tests/test_m2_tools.py`（**32 个**，T1–T12 全覆盖 + 行为面）；
    `test_m2_contract.py` 扩到 50 个（不变量家族 W/T 分组校验 + 锚点跨四文件解析）
  - **门禁**：pytest **1153 passed / 6 deselected**（固定序与随机序均绿）；ruff check 全绿；
    ruff format 仅既有 5 个 WIP 红；alembic check 无漂移（head 仍 v41，**M2.3 无迁移**）；
    web tsc / eslint / prettier / vitest(351) / build 全绿
  - **守卫反例注入已验证（5/5 转红后撤回）**：加玩家面 `/tools` 路由 / 授权校验放行 /
    接受参数里的身份字段 / 忽略自主等级门禁 / 不写审计
  - **踩坑记录**：`SessionLocal(expire_on_commit=False)` 下 ORM 关系会保持旧值 ——
    依赖图必须从**表**读（`list_dependencies`），否则同一会话里第二次建边时环检测会漏

- **2026-09-11 · M2.4 DONE —— Leadership Planning & Delegation**
  - **拍板落地（方案 3 + Decision Envelope）**：三层不混（意图/执行事实/真相）；
    一条决策 → N 个动作；关联单向；`decision_semantics`（none/optional/required）注册时强制；
    决策不授予权限；状态必须能表达 `PARTIALLY_APPLIED`；上下文是有界快照 + 哈希；
    决策树只用一个 `parent_decision_id`；只留 Decision → Outcome 可追踪能力
  - **迁移**：**v42** `c3e5a7b9d124`（纯 additive：2 张新表 + 索引，无回填）；
    `upgrade → downgrade -1 → upgrade` 实测；`alembic check` 无漂移
  - **代码**：`app/models/decision.py`、`app/work/decisions.py`（信封 + 聚合 + 读模型）、
    `app/work/tool_executor.py`（执行事实改落 `tool_audits` + `decision_id` + 语义门禁 +
    **SAVEPOINT 隔离**）、`app/work/{tools,tool_writes,contracts}.py`、
    `app/api/v1/decisions.py`（只读）、`app/schemas/work.py`
  - **契约**：`DecisionRecord` → `DecisionIntent`（提交前的意图形状）；
    `DecisionOutcome` **退役**（结果只由 `DecisionStatus` 表达）；新增 `DecisionStatus` /
    `DecisionSemantics` / `DECISION_CONTEXT_KEYS`；不变量 **DR1–DR10**（注册表 64 条）
  - **前端**：项目详情「管理决策」时间线 + types/api/hook + i18n 中英
  - **测试**：新增 `tests/test_m2_decisions.py`（**27 个**，DR1–DR10 全覆盖）；
    M2.3 的 32 个用例适配"写动作必须隶属决策"；`test_m2_contract.py` 50 个
  - **门禁**：pytest **1179 passed / 6 deselected**（连跑 3 次全绿）；ruff check 全绿；
    ruff format 仅既有 5 个 WIP 红；alembic check 无漂移（head = v42）；
    web tsc / eslint / prettier / vitest(351) / build 全绿
  - **守卫反例注入已验证（6/6 转红后撤回）**：决策行复制工具名 / 部分生效四舍五入成成功 /
    决策授予权限 / 建反向关联数组 / 写工具不声明语义 / 放过未知上下文键
  - **踩坑记录（真实 bug）**：执行面原来的 `db.rollback()` 会把**调用方**（决策信封）
    已写下的东西一起抹掉 —— 一个动作被领域拒绝，整条 `DecisionRecord` 与前序成功动作全部消失。
    修复：**SAVEPOINT** 只回滚该 handler + 决策意图先提交再执行动作。
    对应的用例也补强了"成功的动作必须真的留在领域状态里"

### ~~下一步（M2.5）~~ → **已完成**（见 §8）

`GRAPH_TEMPLATE` 已退出业务真相（搬到 `app/work/planning_fixture.py`，只建图不推进）；
`Orchestrator` 已是**纯调度器**；`resolve_ready_tasks` 已接进运行时（`app/work/dispatch.py`）；
Manager Agent 经 `create_task` / `create_dependency` / `assign_task` 建图，
系统只回答"哪些 Task 现在可以执行、能不能派给**它自己的**负责人"。

### ~~交付后暂停点~~ → **已拍板（2026-09-11，D1/D2/D3）**

1. **默认 Work Intake = CEO，但公司可配**（负责路由，不是 CEO 特权）→ M2-ADR-11 / B6
2. **新公司默认 guided**；首次真实项目完成后公司默认转 managed；`work_mode` 项目级快照 → M2-ADR-13/15 / B7/B12
3. **确定性模板保留**，但只作 Test/Tutorial/CI Fixture，生产**永不** fallback，且必须显式请求 + 部署门控 → M2-ADR-12 / B9/B10

---

## 17.0d M2.4 交付证据

**Commit**：`073c0ab`（`feat(work): M2.4 leadership planning & delegation (decision envelope)`，
36 files / +3163 / -198）。

### 文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `migrations/versions/c3e5a7b9d124_v42_decision_records.py` | 新增 | v42 纯 additive：2 张新表 + 索引，无回填 |
| `app/models/decision.py` | 新增 | `DecisionRecord`（管理语义）+ `ToolAudit`（执行事实） |
| `app/work/decisions.py` | 新增 | 信封执行者 + 状态聚合 + 上下文哈希 + 读模型 |
| `app/api/v1/decisions.py` + `app/schemas/work.py` | 新增/修改 | 只读决策面（4 个端点，**无写端点**） |
| `app/work/{tools,tool_writes,tool_executor}.py` | 修改 | `decision_semantics` 声明与门禁；执行事实改落 `tool_audits`；SAVEPOINT 隔离 |
| `app/work/contracts.py` | 修改 | `DecisionRecord` → `DecisionIntent`；`DecisionOutcome` 退役；DR1–DR10 |
| `app/models/enums.py` | 修改 | `DecisionStatus` / `DecisionSemantics` |
| `app/core/config.py` + `.env.example` | 修改 | `orchestrator_dispatch_enabled`（默认开、测试默认关） |
| `app/workflow/orchestrator.py` | 修改 | 派发门禁（后台写者不与调用方抢 SQLite） |
| 前端 6 个文件 | 新增/修改 | 项目管理决策时间线 + types/api/hook + i18n 中英 |
| `tests/test_m2_decisions.py` | 新增 | 27 个（DR1–DR10 + 领域状态断言） |
| `tests/{test_m2_tools,test_m2_contract,conftest}.py` | 修改 | 适配决策语义；`api_paths` fixture；派发门控 opt-in |
| `docs/{m2-agent-work-runtime-design,m2-implementation-plan,handover}.md` | 修改 | §10 重写 / ADR-28..35 / DR1–DR10 / 进度 |

### 门禁实测

```text
pytest apps/server/tests -q（默认随机序）      1179 passed, 6 deselected   ← 连跑 5 次全绿
ruff check app tests                         All checks passed
ruff format --check app tests                5 files would be reformatted（既有 WIP，未新增）
cd apps/server && alembic check              No new upgrade operations detected
alembic current                              c3e5a7b9d124 (head)   ← v42
alembic upgrade → downgrade -1 → upgrade     实测通过
cd apps/web && tsc / eslint / prettier / vitest(351) / build      全绿
```

### 守卫反例注入验证（6/6 转红后撤回）

| 注入 | 期望转红 | 结果 |
| --- | --- | --- |
| 决策行写入工具名 | `test_decision_record_does_not_copy_tool_payloads` | ✅ 转红 |
| 部分生效被四舍五入成 `APPLIED` | `test_partial_apply_is_expressed_not_rounded` | ✅ 转红 |
| 决策（有 decision_id）绕过 Authority | `test_decision_never_grants_authority` | ✅ 转红 |
| 建 `DecisionRecord.audit_ids[]` 反向数组 | `test_three_layers_are_separate` + `test_link_direction_is_single_way` | ✅ 转红 |
| 写工具不声明 `decision_semantics` | `test_decision_semantics_are_declared_and_enforced` | ✅ 转红 |
| 放过未知决策上下文键 | `test_unknown_context_keys_are_rejected` | ✅ 转红 |

### 本轮修掉的三个真实问题（都写进代码注释）

1. **执行面的 `db.rollback()` 会抹掉调用方的上下文**：一个动作在 handler 里被领域拒绝 ⇒
   整条 `DecisionRecord` 与其前序成功动作**全部消失**。修复：`SAVEPOINT` 只回滚该 handler +
   决策意图先提交再执行动作；用例补强为"成功的动作必须真的留在领域状态里"。
2. **写 handler 在 `commit()` 之后又查库再 `bus.publish`**：读事务会挡住另一个连接的写事务
   （SQLite 单写者）。改为"提交后不再查库 / `refresh` 后再 `commit` 一次"。
3. **套跑偶发 `database is locked`**：后台编排器与测试抢同一份 SQLite，而读→写**升级**是
   死锁语义（不等 busy timeout）。新增 `settings.orchestrator_dispatch_enabled`
   （默认开、测试默认关，与 `position_access_sync` / `evidence_pipeline_enabled` 同一纪律），
   需要跑完整链的用例显式打开。修复前 ~1/4 概率失败，修复后连跑 5 次全绿。

> **顺带修掉一条空断言**：M2.3 的"不得存在玩家面 `/tools` 路由"用 `client.app.routes` 枚举，
> 而新版 FastAPI 把 `include_router` 包成 `_IncludedRouter`，嵌套路由不再扁平出现 ⇒
> 那条断言实际上什么都没查。改用 OpenAPI 路径集合（`conftest.api_paths`）。

---

## 17.0c M2.3 交付证据

**Commit**：`e402c06`（`feat(work): M2.3 management agent tooling (read shared / write internal)`，
23 files / +3755 / -73）。

### 文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `app/work/tools.py` | 新增 | `ToolSpec` / `ToolRegistry` / 参数子集校验 / 自主等级表 / 身份字段禁止清单 |
| `app/work/tool_reads.py` | 新增 | 15 个读工具（既有读面的适配器） |
| `app/work/tool_writes.py` | 新增 | 9 个写工具（既有 service 的适配器，状态机 / DAG / 生命周期照走） |
| `app/work/tool_executor.py` | 新增 | 注册表 + Actor 上下文注入 + 执行五步 + 审计 + 诊断 |
| `scripts/agent_tools.py` + `Makefile` | 新增/修改 | 调试口（默认关）+ `make agent-tools` / `agent-tool-call` |
| `app/models/enums.py` | 修改 | `ToolSideEffect` / `ToolTransport` / `AutonomyLevel` / `TaskStatus.blocked|cancelled` / `AuthorityKind` 归位 + `plan_project_work` |
| `app/services/tasks.py` | 修改 | `ALLOWED_TRANSITIONS` 加 blocked/cancelled |
| `app/work/{contracts,authority_seed}.py` | 修改 | T1–T12 + Authority≠Autonomy 边界 + 工具参数禁止键；种子给 CEO/PM `plan_project_work` |
| `app/core/config.py` + `.env.example` | 修改 | `agent_tool_cli_enabled`（默认 false） |
| 前端 4 个文件 | 修改 | `TaskStatus` 联合/变体/配色 + i18n 中英 |
| `tests/test_m2_tools.py` | 新增 | 32 个（T1–T12 + 行为面） |
| `tests/{test_m2_contract,test_m2_role_context,conftest}.py` | 修改 | 不变量家族分组 + 锚点跨四文件解析 + `org_snapshot` 自动还原 |
| `docs/{m2-agent-work-runtime-design,m2-implementation-plan,handover}.md` | 修改 | §14b / ADR-22..27 / 进度 |

### 门禁实测

```text
pytest apps/server/tests -q -p no:randomly   1152 passed, 6 deselected
pytest apps/server/tests -q（默认随机序）      1152 passed, 6 deselected
ruff check app tests                         All checks passed
ruff format --check app tests                5 files would be reformatted（既有 WIP，未新增）
cd apps/server && alembic check              No new upgrade operations detected
alembic current                              b2d4f6a8c013 (head)   ← 仍 v41（M2.3 无迁移）
cd apps/web && tsc / eslint / prettier / vitest(351) / build      全绿
```

> **既有已知 flaky**：`test_updates.py::test_managed_update_success` 在这一次全量套跑里偶发失败一次
> （单跑必过、重跑全量也过）。它是 `docs/handover.md §4` 早已登记的 P6.1 flaky，**不是**本轮回归。

### 守卫反例注入验证（5/5 转红后撤回）

| 注入 | 期望转红 | 结果 |
| --- | --- | --- |
| 在 `router.py` 注册玩家面 `/tools` 路由 | `test_no_player_facing_tool_router_exists` | ✅ 转红 |
| 执行面跳过 Authority 校验 | 5 个授权用例（含 transport / 审计） | ✅ 转红 |
| 参数校验放过身份字段 | `test_actor_identity_comes_from_context_and_args_are_rejected` | ✅ 转红 |
| 忽略自主等级门禁 | `test_autonomy_gate_refuses_actions_requiring_confirmation` | ✅ 转红 |
| 审计不写行 | `test_every_tool_call_is_audited` | ✅ 转红 |

---

## 17.0b M2.2 交付证据

**Commit**：`164272c`（`feat(work): M2.2 role context & authority projection`，
28 files / +3552 / -56）。

### 文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `migrations/versions/b2d4f6a8c013_v41_position_authority.py` | 新增 | v41 纯 additive：2 新表 + 1 新列 + 3 索引 |
| `app/work/authority.py` | 新增 | 授权解析 + 校验 + append-only 写侧 + 双摘要快照 |
| `app/work/role_context.py` | 新增 | RoleContext 派生投影 + 资源指针解析 |
| `app/work/authority_seed.py` | 新增 | 冷启动默认授权 / advisory_scope / 资源清单 |
| `app/work/role_events.py` | 新增 | 任职事件 → `role.context_*` 事实通知 |
| `app/work/contracts.py` | 修改 | `AuthorityGrant` 对齐表结构 + `AuthorityScopeKind` + `AuthorityTarget/Decision` + 双摘要 + W37–W42 |
| `app/models/position.py` · `app/models/enums.py` | 修改 | 2 个新模型 + `advisory_scope` 列 + `AuthorityScopeKind` |
| `app/api/v1/employees.py` · `app/schemas/work.py`（新增）· `app/schemas/position.py` | 修改/新增 | `GET /employees/{id}/role-context` + 读模型 |
| `app/core/config.py` · `.env.example` · `app/main.py` · `tests/conftest.py` | 修改 | 2 个设置项 + seed/消费者接线 + 测试门控 |
| `apps/web/src/components/employee/role-context-tab.tsx`（新增）+ 5 个前端文件 | 新增/修改 | 履职上下文 Tab + types/api/hook/i18n |
| `tests/test_m2_role_context.py` | 新增 | 29 个（C1–C6 + 八条拍板） |
| `tests/test_m2_contract.py` | 修改 | 50 个（对齐 v41 表结构 + 锚点跨三文件解析） |
| `docs/{m2-agent-work-runtime-design,m2-implementation-plan,handover}.md` | 修改 | 决策/ADR/不变量/进度 |

### 门禁实测

```text
pytest apps/server/tests -q -p no:randomly   1120 passed, 6 deselected, 118 warnings
pytest apps/server/tests -q（默认随机序）      1120 passed, 6 deselected
ruff check app tests                         All checks passed
ruff format --check app tests                5 files would be reformatted（既有 WIP，未新增）
cd apps/server && alembic check              No new upgrade operations detected
alembic current                              b2d4f6a8c013 (head)   ← v41
alembic upgrade → downgrade -1 → upgrade     实测通过
cd apps/web && tsc / eslint / prettier / vitest(351) / build      全绿
```

> 收尾备注：中途一次 `ruff format apps/server/app/` 顺手把两个**既有 WIP** 文件
> （`services/auth.py`、`services/providers.py`）格式化了；已 `git checkout` 还原，
> 保持"不顺手清理技术债"的纪律，也让 diff 只含 M2.2。

### 守卫反例注入验证（4/4 转红后撤回）

| 注入 | 期望转红 | 结果 |
| --- | --- | --- |
| 授权层用 `employee.role == "ceo"` 当权限 | `test_authority_and_role_context_modules_never_read_role_strings_or_scores` | ✅ 转红 |
| `revoke_authority` 改成 `db.delete(row)` | `test_revoke_closes_the_window_and_history_stays_explainable` | ✅ 转红 |
| `authorizes` 改名为 `should_authorize` | `test_authority_layer_validates_but_never_decides` | ✅ 转红 |
| 往 `position_definition_packages` 加 `authority_kind` 列 | `test_v41_adds_authority_tables_without_touching_packages_semantics` | ✅ 转红 |

---

## 17.0 M2.1 交付证据

**Commit**：`01060ab`（`feat(work): M2.1 canonical executable project spec`，
39 files / +3063 / -218）。

### 文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `migrations/versions/a1c2e3f40517_v40_canonical_project.py` | 新增 | v40 纯 additive 迁移 + 事实驱动回填 |
| `app/work/work_intake.py` | 新增 | Work Intake 责任路由（只读，4 种结果状态） |
| `app/work/work_defaults.py` | 新增 | 公司默认工作模式 / 责任目标 / fixture 门控 / 完成点推进 |
| `app/services/projects.py` | 修改 | 单一立项入口 + 三条创建路径 + Canonical Spec 读面 + Project Brief |
| `app/services/project_delivery.py` | 修改 | `create_guided_project_body()`（仪式体）+ 完成点推进 |
| `app/workflow/orchestrator.py` | 修改 | 模板更名 + fixture 门控 + `awaiting_management_action` |
| `app/models/project.py` + `app/models/enums.py` + `app/core/config.py` | 修改 | 新列 / 两轴枚举 / `allow_planning_fixtures` |
| `app/api/v1/{projects,company}.py` + `app/schemas/{project,organization}.py` | 修改 | `/spec`、`/company/work-policy`、请求/响应字段 |
| `app/repositories/organization.py` | 修改 | `owner_user_id()`（无负责人时提示 Owner） |
| `app/services/tutorial.py` | 修改 | 实战教程模板显式声明 guided |
| 前端 7 个文件 | 修改/新增 | 工作模式面板 + 状态/i18n/类型 |
| `tests/test_m2_project_spec.py` | 新增 | 23 个（B1–B12） |
| `tests/test_m2_contract.py` + 6 个既有测试 + conftest | 修改 | 两轴模型 + W32–W36 + `no_work_intake` fixture + 显式 fixture/模式 |

### 门禁实测

```text
pytest apps/server/tests -q          1090 passed, 6 deselected, 118 warnings
pytest（默认随机序）                  1090 passed, 6 deselected      ← 无顺序依赖
ruff check app tests                 All checks passed
ruff format --check app tests        5 files would be reformatted（全部既有 WIP）
cd apps/server && alembic check      No new upgrade operations detected
alembic current                      a1c2e3f40517 (head)  ← v40
alembic upgrade/downgrade -1/upgrade  实测通过
cd apps/web && tsc / eslint / prettier / vitest(351) / build   全绿
```

### 守卫反例注入验证（4/4 转红后撤回）

| 注入 | 期望转红 | 结果 |
| --- | --- | --- |
| 移除 orchestrator planning 分支的 `_uses_deterministic_plan` 门控 | `test_no_implicit_template_fallback_path_exists` | ✅ 转红 |
| Work Intake 解析失败时偷选第一名员工 | `test_missing_work_intake_manager_enters_waiting_not_fallback`（+ vacant 用例） | ✅ 2 个转红 |
| `get_project_spec()` 里加 `db.commit()` | `test_spec_read_model_never_writes` | ✅ 转红 |
| 给 `projects.management_employee_id` 加 FK | `test_projects_migration_is_additive_only` | ✅ 转红 |

---

## 17. M2.0 交付证据

**Commit**：`366c54052937d349321bb2629ecafce093f14cdd`（`feat(work): M2.0 work & role domain contract freeze`，
8 files / +4690；本文件随后补记 commit hash 与门禁实测，同属 M2.0 收尾）。

### 17.1 实际修改/新增文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docs/m2-agent-work-runtime-design.md` | 新增 | 领域设计（§0–§18）：原则、决策边界、Position 三段式、硬/软约束、RoleContext、Role Resource Index、Adaptive Onboarding、记忆平面、DecisionRecord、Project/Task/WorkOrder 边界、W1–W31、M2-ADR-1..10 |
| `docs/m2-implementation-plan.md` | 新增 | 执行基线（本文）：M2.0–M2.10 拆解、验收 A–K、风险 R1–R10、Progress |
| `apps/server/app/work/__init__.py` | 新增 | M2 工作与组织运行域包 |
| `apps/server/app/work/contracts.py` | 新增 | 契约层（决策边界 / 职责面 / Position 三段式 / 硬软约束 / RoleContext / RoleResource / Onboarding / 记忆平面 / DecisionRecord / verdict 边界 / 工作根 / DAG 校验 / 不变量表） |
| `apps/server/app/models/enums.py` | 追加 | 8 个纯 Python 枚举（无列引用 ⇒ 无迁移）：`ResponsibilityArea` / `DecisionKind` / `DecisionOutcome` / `ReviewVerdict` / `ProjectWorkMode` / `RoleResourceKind` / `MemoryPlane` / `FactKind` |
| `apps/server/tests/test_m2_contract.py` | 新增 | 46 个契约测试 + AST 守卫 + 不变量锚点表 |

**未触碰**：任何 service / repository / API / migration / 前端。M2.0 零行为改动、零迁移。

### 17.2 门禁实测

```text
pytest apps/server/tests -q            1064 passed,  6 deselected, 118 warnings   （基线 1018）
ruff check  app tests                  All checks passed
ruff format --check app tests          5 files would be reformatted（全部为既有 WIP）
cd apps/server && alembic check        No new upgrade operations detected
alembic current                        64fec2d13d9b (head)   ← 与 M1 冻结面一致
cd apps/web && tsc --noEmit            ✅
cd apps/web && eslint .                ✅
cd apps/web && prettier --check .      ✅
cd apps/web && vitest run              83 files / 351 passed
cd apps/web && pnpm build              ✅
```

既有 WIP 红（非本次引入，基线不变）：`services/auth.py`、`services/providers.py`、
`tests/test_authentication.py`、`tests/test_employee_providers.py`、`tests/test_providers.py`。

### 17.3 守卫反例注入验证

| 注入 | 期望转红的测试 | 结果 |
| --- | --- | --- |
| `app/_guard_probe.py::inject_role_skills` | `test_forbidden_onboarding_actions_do_not_exist_anywhere` | ✅ 转红，撤回后恢复 |
| `app/_guard_probe.py::auto_offboard_low_performers` 调用 `lifecycle_service.offboard` | `test_no_automatic_offboarding_path_exists` | ✅ 转红，撤回后恢复 |
| `contracts.py::pick_best_engineer` | `test_contract_module_declares_no_decision_making_helpers` | ✅ 转红，撤回后恢复 |
| `economy/contracts.py` import `ReviewVerdict` | `test_work_and_economy_verdict_modules_do_not_cross_reference` | ✅ 转红，撤回后恢复 |

### 17.4 M2.0 Acceptance 对照

| # | 判据 | 状态 |
| --- | --- | --- |
| A1 | `FactKind` 与 `DecisionKind` 互斥 | ✅ `test_system_facts_and_agent_decisions_are_disjoint` |
| A2 | Hard / Soft 互斥且完备 | ✅ `test_constraint_classes_are_disjoint_and_complete` |
| A3 | `RoleResource` 无数值字段 | ✅ `test_role_resource_carries_no_numeric_field` |
| A4 | `RoleContext` 字段全部可推导 | ✅ `test_role_context_fields_are_all_derivable` + `..._sources_resolve_to_real_tables_and_columns` |
| A5 | 禁止的注入动作不存在 | ✅ `test_forbidden_onboarding_actions_do_not_exist_anywhere` |
| A6 | 记忆平面不相交且表真实存在 | ✅ `test_memory_planes_are_disjoint_and_resolve_to_real_tables` + person 口径核对 |
| A7 | 无 `missions` / `agents` SoT 表 | ✅ `test_no_mission_or_agent_source_of_truth_table_exists` |
| A8 | 决策校验不评价理由 | ✅ `test_decision_validation_never_judges_intent` |
| A9 | DAG 结构问题可检出 | ✅ `test_task_graph_validation_detects_structural_problems` + 属性测试 |
| A10 | 四个 verdict 面互不替代 | ✅ `test_verdict_surfaces_are_distinct_and_owned` + 跨域引用守卫 |
| A11 | W1–W31 无静默丢弃 | ✅ `test_every_invariant_has_a_live_anchor_or_an_owner_stage` + 文档对拍 |
| A12 | 执行路径不 import Fit | ✅ `test_fit_module_is_not_imported_by_execution_paths` |
| A13 | 完整门禁全绿 | ✅ §17.2 |
| A14 | 零迁移（head 不变） | ✅ `alembic current` = `64fec2d13d9b` |

---

## 18. 风险登记（M2 全阶段）

| # | 风险 | 影响 | 对策 |
| --- | --- | --- | --- |
| R1 | 契约过度设计 | 写一堆没人用的类型 | 每个数据类标注 owner 阶段；M2.0 只冻结会被消费的面 |
| R2 | 守卫写成声明式装饰 | 不变量形同虚设 | 强制反例注入验证 |
| R3 | target invariant 被误读为现状 | 报告失真 | `enforced` / `owner_stage` 双态 + "无静默丢弃"测试 |
| R4 | Project 收敛破坏教程 | `first-project-practice` 7 步走不通 | M2.1 必须跑教程 E2E；`guided` 保留到 M2.7 |
| R5 | 退役 `GRAPH_TEMPLATE` 破坏既有测试 | 大面积回归 | ✅ 已发生并处理：M2.5 保留了历史数据可读路径；受影响的是**断言分步生成的用例**（milestone 集合、边数、fixture 项目初始状态），已按新语义更新 |
| R6 | 真实 Runtime 放开后的成本/超时/并发 | 长任务失败不可诊断 | M2.5 起补 WorkSession 事件持久化与重试策略（承接 Audit Gap #8/#9） |
| R7 | 「Agent 决策」退化为「系统启发式穿着 Agent 外衣」 | 违背 M2 最高原则 | W1/W2/W17 的 AST 守卫 + 行为测试（关掉模板后项目不再自动推进） |
| R8 | 四个 verdict 面漂移 | 审计已指出的重复真相风险复活 | W29 + 边界表 + 模块引用方向守卫 |
| R9 | SQLite 单写者 + 多 Agent 并发 | `database is locked` | 沿用既有纪律：写事务短、重活在事务外、幂等约束收敛 |
| R10 | RoleContext 变成第二真相 | 与任职时间轴漂移 | M2-ADR-4：派生读模型，不落表 |
