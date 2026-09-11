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
Dynamic Task Graph Runtime（DAG 正确性 + 就绪调度；退役 GRAPH_TEMPLATE）
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
| **M2.2** | Role Context & Adaptive Onboarding | 有（Authority Projection） | M2.1 | W4/W5/W7/W8/W12/W27 | PENDING |
| **M2.3** | Management Agent Tooling | 无（纯读 + 受校验写） | M2.2 | W3/W6/W11 | PENDING |
| **M2.4** | Leadership Planning & Delegation | 有（DecisionRecord） | M2.3 | W1/W2/W3/W14/W15/W28 | PENDING |
| **M2.5** | Dynamic Task Graph Runtime | 无（收敛既有表） | M2.4 | W2/W16/W30 | PENDING |
| **M2.6** | Artifact Handoff & Shared Work Context | 有（task 维度 + lineage） | M2.5 | W19 | PENDING |
| **M2.7** | Review / Rework / Replan | 有（ReviewRequest） | M2.6 | W17/W29 | PENDING |
| **M2.8** | Recruit → Ready-to-Work | 无（复用 provisioning） | M2.7 | W31/W13 | PENDING |
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
  （none | deterministic_template，基础设施）；`GRAPH_TEMPLATE` 更名
  `DETERMINISTIC_TEMPLATE_PLAN`，`_generate_graph` 更名 `_apply_deterministic_template_plan`
- **Work Intake 责任路由**（`app/work/work_intake.py`，只读）：公司配置 → 职位 code →
  PositionSlot → 生效 PRIMARY 任职 → Employee；4 种结果状态；多条在任者按
  `effective_from` 取最早并上报全部在任者（审计）
- **公司工作策略 API**：`GET|PATCH /company/work-policy`（`work_mode` + `work_intake_position_code`）
- **Canonical Spec 读面**：`GET /projects/{id}/spec` 回答 8 个问题（spec / completeness /
  work_mode / planning_fixture / work_intake / management（含 `stale`）/ execution）
- **不接管规划**（W34）：`orchestrator._advance` 的两个规划分支都以
  `_uses_deterministic_plan()` 门控；非 fixture 项目在接收任务完成后发
  `project.awaiting_management_action` 并**停住**
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

## 5. M2.2 · Role Context & Adaptive Onboarding `[有迁移]`

### Goal

Position Assignment 之后，Agent 拿到的是 **RoleContext**，不是注入的能力。

### 范围

- `app/work/role_context.py`：从现有表**派生** `RoleContext`（只读，不落表 —— M2-ADR-4）
- **Authority Projection**：`position_definitions` → `AuthorityKind` 声明的落地点
  （新表 `position_authority_grants` 或复用 `position_definition_packages`，M2.2 决定）
- **Role Resource Index**：`position_definition_resources`（薄表：kind/ref/note/required）
- `GET /employees/{id}/role-context`（只读）
- 上任事件 → `role.context_available`（**不发"请去学习"的系统指令**）

### Acceptance

| # | 判据 |
| --- | --- |
| C1 | 任命一个 Leadership 52 的人当 CTO **成功**（W15: 期望不是门禁） |
| C2 | 任命前后 `employee_competencies` / `skills` / `knowledge_items` **逐行不变**（W7/W8） |
| C3 | 该 CTO 能读到公司允许的 CEO/CTO Handbook 与历史决策（W9） |
| C4 | 前任的 `memory_entries` / `learning_records` / `traits` **不迁移**（W10） |
| C5 | RoleContext 响应**不含**任何 score/level/rank；只含事实引用 |
| C6 | RoleResource 只能通过既有 `knowledge_items` / `drive_nodes` 解析（不造第二套内容） |

---

## 6. M2.3 · Management Agent Tooling `[无迁移]`

### Goal

把「系统事实」与「受校验的写操作」暴露成**工具**，供管理 Agent 调用；
工具**不得**内含决策逻辑。

### 工具面（设计 §1.1 + 用户 §25）

```text
Organization   list_people / inspect_person / inspect_position / inspect_assignment
               inspect_team / inspect_current_load
Capability     get_competencies / get_evidence / get_skills / get_experience / calculate_task_fit
Knowledge      search_company_knowledge / search_person_knowledge / inspect_artifact
Work           create_task / update_task / create_dependency / assign_task
               request_review / request_rework / mark_blocked / cancel_task
Resource       runtime_status / provider_status / workspace_status / budget_snapshot
```

### 纪律

- 每个工具声明 `mode = read | write`、`authority_required`、`hard_constraints` 列表
- **read 工具**：直接返回事实，不含推荐排序结果（排序是决策，不是事实）
  - 例外：`calculate_task_fit` 返回**逐人 Fit 明细**，但**不得**返回"建议选谁"
- **write 工具**：走既有 service（不绕过状态机、不绕过 Ledger、不绕过权限）
- 工具的注册表是**数据**：`app/work/tools.py::TOOL_REGISTRY`，由测试钉住每项都有 schema + 权限声明

### Acceptance

| # | 判据 |
| --- | --- |
| D1 | 每个工具都有 `mode` / `authority_required` / `input_schema`（注册表完备性） |
| D2 | 不存在任何 `mode=write` 工具在**无授权 actor** 时成功（W3/W6） |
| D3 | `calculate_task_fit` 的返回里**没有** `recommended` / `best` / `rank` 字段（W11） |
| D4 | write 工具调用后产生一条 `DecisionRecord`（与 M2.4 同批落地） |
| D5 | 工具层不 import `app.talent.fit` 以外的决策模块（AST 守卫） |

---

## 7. M2.4 · Leadership Planning & Delegation `[有迁移]`

### Goal

让 CEO / CTO / PM 真正**自主**分析、规划、委派；系统只负责 validate / apply / audit。

### 范围

- `decision_records` 表（append-only，W28）
- `app/work/decisions.py`：`DecisionService.record()` / `.apply()` / `.outcome()`
- **路由规则**（M2-ADR-9）：公司可配置「Work Intake 由哪个职位负责」
  （`Company.settings["work_routing"]`，缺省 = `ceo`）→ 只决定**第一个被通知的职位**，
  不决定后续任何事
- 事件：`work.intake.routed` / `decision.recorded` / `decision.applied`
- Manager Agent 通过 M2.3 工具提交结构化 decision

### Acceptance

| # | 判据 |
| --- | --- |
| E1 | 系统**不**创建任何 Task（除非 Manager 通过 Tool 创建）—— 关掉 GRAPH_TEMPLATE 后项目不再自动推进 |
| E2 | 每条管理决策都有 `DecisionRecord`（含 actor_person_id + reason + context_snapshot） |
| E3 | `DecisionRecord` 不可 UPDATE（W28，append-only 守卫） |
| E4 | 无授权的 actor 提交 decision → 403（W3/W6） |
| E5 | 同一 decision 重放幂等（不产生两条记录） |
| E6 | 系统**不产生**任何"这个决策好不好"的字段或日志（W18） |

---

## 8. M2.5 · Dynamic Task Graph Runtime `[无迁移]`

### Goal

`GRAPH_TEMPLATE` 退出业务真相；系统只回答"哪些 Task 现在可以执行"。

### 范围

- `app/work/task_graph.py`：DAG 校验（复用 M2.0 `validate_task_graph`）+ 就绪判定 + 扇出/扇入
- `Orchestrator` 改为**纯调度器**：不再生成 Task、不再决定 assignee、不再按 `kind` 分支推进
- `_generate_graph()` / `_ensure_development_tasks()` 退役（删除或降级为测试夹具）
- Task 来源标记：`tasks.origin = {legacy_template, manager, human}`（便于迁移与观测）
- `_finalize` 不再 `in_review → done`（交 M2.7）

### Acceptance

| # | 判据 |
| --- | --- |
| F1 | 删除 `GRAPH_TEMPLATE` 后，M2.4 的 managed 路径仍能跑通（Agent 建的 DAG 被执行） |
| F2 | 就绪判定是**纯函数**，只依赖 (task.status, dependency statuses)，有属性测试 |
| F3 | 扇出：N 个无依赖 Task 可并发（受员工并发不变式限制） |
| F4 | 扇入：等待全部前置完成才就绪 |
| F5 | 环 / 悬空依赖 / 自环 → 系统拒绝（400/422），不静默忽略 |
| F6 | 系统**不**回答"下一步应该创建什么 Task"（无自动补 Task 路径，AST 守卫） |
| F7 | `legacy_template` 项目仍可读（历史数据不破坏） |

---

## 9. M2.6 · Artifact Handoff & Shared Work Context `[有迁移]`

### Goal

让 Agent A 的输出真正成为 Agent B 的输入。**基于现有 Drive**，不建第二套。

### 范围

- `drive_nodes.task_id`（Artifact 归属到 Task）+ `artifact_links`（produced_by / consumed_by）
- `TaskContext` 增加 `input_artifacts`（上游 Task 的产物引用 + 内容摘要）
- `Task.consumes` / `Task.produces` 的显式声明（由 Manager 在 create_task 时给）
- `GET /tasks/{id}/artifacts`（produced + consumed + lineage）
- Lineage 校验：consumed 的 artifact 必须由**已完成**的 Task 产出（W19）

### Acceptance

| # | 判据 |
| --- | --- |
| G1 | B 的 prompt / TaskContext 里能拿到 A 的 artifact 内容（不是同一段 project 原文） |
| G2 | `GET /tasks/{id}/artifacts` 能追到上游 Task 链（≥2 跳） |
| G3 | 引用未完成的 Task 产物 → 系统拒绝（422） |
| G4 | 不新建 Artifact 表；`artifacts`（legacy）仍不被写入 |
| G5 | `artifact_links` 可追"这个产物被谁用了、用在哪次会话" |

---

## 10. M2.7 · Review / Rework / Replan `[有迁移]`

### Goal

Worker 完成后**不再自动 done**。

### 范围

- `review_requests` 表：requested_by（Manager/Reviewer）、reviewer（Agent）、verdict、notes、facts
- Fact 收集器（系统提供，W18）：`test_result` / `lint` / `build` / `artifact` / `acceptance_criteria`
- `ReviewVerdict` → Task 状态目标映射（PASS→done / REWORK→todo / REJECT→rejected / ESCALATE→人工或 Manager）
- `request_rework` / `replan` 走 M2.3 工具 + M2.4 DecisionRecord
- `guided` 模式的 `ReviewDecision` 与 `ReviewVerdict` 之间建立**显式映射表**（不互相替代，W29）

### Acceptance

| # | 判据 |
| --- | --- |
| H1 | Task 完成后停在 `in_review`，直到有 `ReviewVerdict.passed` |
| H2 | 系统**不产生**任何 verdict（无启发式自动 PASS，AST 守卫 + 行为测试） |
| H3 | `REWORK` → Task 回 `todo` + 记 rework 次数 + 产生 DecisionRecord |
| H4 | `REPLAN` 只能由该 Project 的 Manager 发起（权限测试） |
| H5 | `ESCALATE` 不被系统自动处理（停在需人工/管理层） |
| H6 | review 事实（test/lint/build）与 verdict 分开存：事实是系统的，verdict 是 Agent 的 |

---

## 11. M2.8 · Recruit → Ready-to-Work `[无迁移]`

### Goal

招募/购买得到的人**能立刻执行 Agent Task**。

### 范围

- `recruit_existing_person` 之后自动编排：Employee → PositionAssignment → RoleContext → Provisioning
  → Workspace → RuntimeInstance → Provider Binding → `READY_TO_WORK`
- **Company Default Runtime Policy**（`Company.settings["runtime_defaults"]`）只解决**环境配置**，
  不解决"Agent 怎么工作"
- `GET /employees/{id}/readiness`：返回逐项事实（workspace / runtime / provider / position）
- 招募响应新增 `readiness` 摘要；未达 READY 时**明确告知缺什么**

### Acceptance

| # | 判据 |
| --- | --- |
| I1 | 招募后（mock 模式）该员工可被分配 Task 并真实跑完一个 WorkSession |
| I2 | 招募后（docker 模式，mock provider）runtime instance 被建出并通过健康检查；失败时 job `partial` + 明确原因 |
| I3 | `READY_TO_WORK` 是**派生**的（不落列），逐项可解释 |
| I4 | 招募失败 → 整笔回滚（沿用 E13/E14/E15 与 `_snapshot` 对拍） |
| I5 | 人级资产在任何开通步骤前后**逐行不变**（W7/W8/W10） |
| I6 | Default Runtime Policy 的字段里**没有**任何人格/提示词/工作方式配置（W26） |

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
| M2.1 Canonical Executable Project Spec | **DONE**（2026-09-11） | 见 §17.0 | 迁移 **v40**；单一立项入口 + 两轴路由 + Work Intake 责任路由 + Canonical Spec 读面；W32–W36 强制 |

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

### 下一步（M2.2，不在 M2.1 范围）

Role Context & Adaptive Onboarding：派生读模型 + Authority Projection + Role Resource Index。

### ~~交付后暂停点~~ → **已拍板（2026-09-11，D1/D2/D3）**

1. **默认 Work Intake = CEO，但公司可配**（负责路由，不是 CEO 特权）→ M2-ADR-11 / B6
2. **新公司默认 guided**；首次真实项目完成后公司默认转 managed；`work_mode` 项目级快照 → M2-ADR-13/15 / B7/B12
3. **确定性模板保留**，但只作 Test/Tutorial/CI Fixture，生产**永不** fallback，且必须显式请求 + 部署门控 → M2-ADR-12 / B9/B10

---

## 17.0 M2.1 交付证据

见本轮汇报与 §16 Progress Log（commit hash 由收尾提交补记）。

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
| R5 | 退役 `GRAPH_TEMPLATE` 破坏既有测试 | 大面积回归 | M2.5 保留 `legacy_template` 读路径 + 历史数据可读 |
| R6 | 真实 Runtime 放开后的成本/超时/并发 | 长任务失败不可诊断 | M2.5 起补 WorkSession 事件持久化与重试策略（承接 Audit Gap #8/#9） |
| R7 | 「Agent 决策」退化为「系统启发式穿着 Agent 外衣」 | 违背 M2 最高原则 | W1/W2/W17 的 AST 守卫 + 行为测试（关掉模板后项目不再自动推进） |
| R8 | 四个 verdict 面漂移 | 审计已指出的重复真相风险复活 | W29 + 边界表 + 模块引用方向守卫 |
| R9 | SQLite 单写者 + 多 Agent 并发 | `database is locked` | 沿用既有纪律：写事务短、重活在事务外、幂等约束收敛 |
| R10 | RoleContext 变成第二真相 | 与任职时间轴漂移 | M2-ADR-4：派生读模型，不落表 |
