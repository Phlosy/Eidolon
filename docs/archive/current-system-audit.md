# Eidolon Current System Audit

> 审计日期：2026-09-11 · 审计方式：**只读**（文档 + 代码 + DB + migration + service + repo + API + 前端 + 测试 + 实际业务链交叉验证）
> 本文件是事实快照，不是计划。不含任何新架构提案。

---

## 1. Repository Baseline

| 项 | 事实 |
| --- | --- |
| branch | `dev` |
| HEAD | `063423f2a12c32a6cd4e27914cdd77d175744d51` — `docs(m1): Progress Log 补稳定化 commit 与连跑证据（M1.10）`（2026-09-11 17:54 +0800） |
| ahead/behind | vs `origin/dev`(`1677ad0`)：**ahead 95 / behind 0**（未推送）；vs `origin/main`(`cb06e29` = Initial commit)：**ahead 255 / behind 0**。`main` 是 `dev` 的祖先。 |
| working tree | 干净（0 tracked changes / 0 staged） |
| untracked | **64 个**，全部在 `tmp/`（教程走查截图 `tmp/tutorial-audit/*`、`tmp/*.png`、`tmp/commit-msg*.txt`、`tmp/r15_final_check.py`）。`tmp/` 未被 `.gitignore` 覆盖（`.gitignore` 只忽略 `/tmp-*`）。 |
| stash | 空 |
| 本地分支 | `dev`(当前) / `main` / `wip/office-phaser-rebuild` |

### 规模

| 项 | 数量 |
| --- | --- |
| 后端 `app/` | 270 个 `.py` / 53,794 行 |
| 后端测试 | 104 个 `test_*.py` / 30,482 行 / **1018 tested functions（pytest 1018 passed）** |
| migration versions | **40** 个文件；alembic head = `64fec2d13d9b`（v39，NPC 经济） |
| 前端 `src/` | 361 个 `.ts/.tsx` / 40,105 行 |
| 前端测试 | 83 个 test file / 351 tests |
| docs | 46 个 `.md` |
| API 端点（装饰器计数） | **256** |
| DB | SQLite `apps/server/data/eidolon.db`（alembic current = `64fec2d13d9b (head)`） |

### 当前 dev 服务状态

- 后端：uvicorn `pid 1070`，`127.0.0.1:26881` LISTEN，`GET /health` → `{"status":"ok"}`
- 前端：vite `pid 64281`，`*:26880` LISTEN，`GET /` → `200`
- `.env`：`EIDOLON_RUNTIME_MODE=mock`（真实 Runtime 被网关强制旁路）
- Docker：可用；存在 2 个 hermes 容器 `eidolon-hermes-ada-3f763e`(Up 26h)、`eidolon-hermes-ada-891d91`(Up 27h，**孤儿**：DB 里没有对应行)

### 当前门禁基线（本次全部实跑）

| Gate | 结果 |
| --- | --- |
| `pytest apps/server/tests -q` | **1018 passed, 6 deselected**（43s）✅ |
| `ruff check app tests` | All checks passed ✅ |
| `ruff format --check app tests` | **5 files would be reformatted**（既有 WIP 红）⚠️ |
| `alembic check` | `No new upgrade operations detected` ✅ |
| `tsc --noEmit` | ✅ |
| `eslint .` | ✅ |
| `prettier --check .` | ✅ |
| `vitest run` | 83 files / 351 passed ✅ |
| `pnpm build` | ✅（chunk >500kB 警告） |
| 集成测试（`-m integration`，需真 Docker） | **未运行（UNKNOWN）** — 会创建容器，属副作用，本次跳过 |

**既有 WIP 红（非回归）**：`app/services/auth.py`、`app/services/providers.py`、`tests/test_authentication.py`、`tests/test_employee_providers.py`、`tests/test_providers.py`。
→ `docs/handover.md §4` 仍写「6 个 WIP 未格式化文件（含 `employees.py`）」，**文档已过期**（现为 5 个，`employees.py` 已格式化）。

**无新增回归**：全部门禁红项均为既有 WIP，无本次新引入。

---

## 2. Executive Summary（20 条事实）

1. Eidolon 当前是一个 **「AI 公司经营 + 单机人才/经济模拟」的完整产品**，具备真人认证、空公司起步、领域状态驱动教程、完整经济账本、契约/托管/结算、人才培养与市场、能力证据链与考核。**M1（经济）与 T2（人才市场）均已冻结**。
2. 代码中**不存在 `Mission` 领域**。全仓 `app/**` 无 mission 实体/服务/表；文档中仅 `docs/ui-redesign.md` 出现过一次。**Mission = DOC-ONLY（1 处提及），无任何实现**。
3. 代码中**不存在 `Agent` 实体**。README 与代码注释里的「One Employee = One Agent」指的是 **`Employee` 行 + 其 `RuntimeInstance`**，Agent 不是一等领域对象。
4. **Person / Employee / CharacterProfile / RuntimeInstance 是四层不同东西**：Person 是身份聚合根；Employee 是「公司成员身份」（1:1 挂 person）；CharacterProfile 是「培养/市场视角」的 1:1 扩展；RuntimeInstance 是执行环境。四者可缺省，只有 Person 是必需。
5. **存在一条真实的 Agent 执行链**：`Task(status=todo)` → `Orchestrator._dispatch_pending` → `WorkSession` → `RuntimeAdapter`（mock/hermes/openclaw）→ `ProducedArtifact` → `DriveNode` → `task.done` → 反思/知识/技能。这条链**有 E2E 测试**（`test_project_workflow.py`）。
6. **但这条执行链的执行图是硬编码的**：`GRAPH_TEMPLATE = [(Discovery,research,researcher),(Build,development,engineer),(Verify,testing,qa),(Release,final_review,ceo)]`，`_generate_graph()` 按模板建 4 个 Task，**没有 LLM planner、没有动态拆解**。
7. **任务分配是「按职位编制找人」的固定映射**，不是 Fit 驱动：`position_compat.employee_by_legacy_role(db, company, role)`。**Fit 引擎完全没有接入任务分配路径**。
8. **不存在 Team Fit / Task Fit / TaskRequirement**。Fit 的唯一形态是 `Person|Employee × PositionDefinition（PositionProfileVersion 需求）`。
9. **Agent 之间不传递上下文**。每个生成的 Task 的 `description` 都是 `project.source_order_text`（同一段原文）；`TaskContext.prior_knowledge` 只来自**执行者自己的**知识/技能检索；产物只落 Drive，**没有 handoff 机制**。
10. **不存在 Agent 间通信**。`messages` 表与 `POST /messages` 只有手工写入口，orchestrator 不产生消息。
11. **Project 有两条互不相同的实现路径**：
    - `create_order()`（legacy 简单路径）→ status=requested → order_review → planning → 硬编码 4 阶段 graph → **会真的跑 Agent**；
    - `create_structured_project()`（结构化交付路径）→ status=in_progress → 11 个 ProjectPhase + ProjectRequirement + 模板生成文档 + 人工评审门 → **完全不跑 Agent**。
    **前端立项向导（Intake Wizard）永远走第二条**（因为恒填 `code`/`background`/`objectives`/`requirements` ⇒ `is_structured=True`）。**UI 触达不到 Agent 执行路径。**
12. **交付物系统事实上是 Drive**：`artifacts` 表已废弃（0 行、无写入），真相是 `drive_nodes(kind=document, zone=projects)` + `drive_revisions`（版本 + sha256）。兼容层 `services/artifacts.py` 继续把 DriveNode 映射回旧 ArtifactOut 形状。
13. **WorkOrder（官方 + 玩家）与经济完全闭环，但与 Agent 执行完全脱钩**：领取/提交/验收/结算/托管全部真实；提交时玩家手打 summary，`project_id` 与 `artifact_refs` 只是**无人校验的自由字段**，没有任何代码把 WorkOrder 接到 Task/Project/Orchestrator。
14. **「任务完成」在当前系统里没有被真正评审**：`Orchestrator._finalize` 里 `in_review → done` 是同一事务内的自动连跳（auto-approve）。唯一的真实评审是（a）结构化交付的人工 ReviewMeeting，（b）M1 的 Evaluation（CLI/确定性规则），（c）competency 的 AssessmentRun（统计聚合）。
15. **Agent 成长闭环是真实的**：`task.completed/failed → EvidencePipeline → CompetencyEvidence → EmployeeCompetency(score/confidence/trend) → AssessmentRun → CareerEvent/DevelopmentPlan/简历读面`，且有 mock 环境 0.5x 可靠性打折与「无期望不造证据」守卫。但证据来源只覆盖 5 类事件（task/project/learning/skill/project_end assessment）。
16. **经济系统非常完整**：复式账本（4 表、append-only）、钱包投影可重建、MonetaryAuthority 令牌化 mint/burn、7 类自助奖励、官方/玩家工作订单、Escrow 锁资、Contract 多腿结算、经营成本 Sink、NPC 经济、政策配置化、失败注入 + 并发矩阵 + E1–E31 不变量锚点。**这一块已经足以支撑玩法，不需要继续横向扩展。**
17. **人才市场（T2）在「档案 → Fit → 招募 → 入职」上是真实的**（有 `test_t2_golden_path.py` 26 步 E2E），但**招募不创建 runtime instance**：`recruitment.recruit_existing_person` 把 employee 建成 `runtime_type=mock`、`runtime_config={}`，注释明说「本阶段不做全链开通」。**买来的人不能立刻执行 Agent 任务**（需另行配置 runtime/provider）。
18. **Onboarding 真实存在且由后端领域状态驱动**：`COMPANY_FOUNDING_TUTORIAL` 12 步（含可选），通关把公司从 `FOUNDING` → `OPERATING`；另有一条可选「实战教程」（Classic Snake）走结构化交付全流程。**但它引导的是「搭组织 → 配 runtime/provider → 建文档 → 招第二人」，其终点不是「创建一个 Agent 并完成一个任务」。**
19. **当前产品重心更接近 A+D 的混合体**（见 §22），Execution 分量最轻：整个「多 Agent 协作」只有一条固定 4 阶段流水线 + 一个硬编码回退重做，没有 planner / coordinator / team formation / artifact handoff / review gate。
20. **「经济 / 市场 / 培养」这三块的投入远超「执行」**：M1+T2 两个冻结阶段的代码量（economy 1 个 578 行模型 + 8 个服务约 4,300 行；talent 约 4,000 行）显著大于执行侧的 `workflow/orchestrator.py`（565 行 + 3 个 adapter）。

---

## 3. Domain Map

> 成熟度：`PRODUCTION-LIKE` / `FUNCTIONAL` / `PARTIAL` / `SCAFFOLD` / `PLACEHOLDER` / `DOC-ONLY`

### 3.1 User / Auth — `PRODUCTION-LIKE`
- **实体**：`User`、`UserSession`、`PasskeyCredential`、`WebAuthnChallenge`、`EmailVerificationToken`、`PendingRegistration`、`AccountActionToken`、`CompanyMembership`、`UserAuditEvent`
- **SoT**：`users`（+ `company_memberships` 决定 company 作用域）
- **service/repo**：`services/auth.py`(733)、`services/email_delivery.py`、`repositories/`（内联）
- **API**：`api/v1/auth.py`(344) — register / verify / login（username|email）/ passkey / me / password / email / delete / sessions
- **UI**：`pages/auth/{login,register,verify,confirm-action}-page.tsx`、`features/auth/*`
- **状态机**：`onboarding_status`；session TTL；CSRF double-submit（`main.py` 中间件）
- **依赖**：Company（注册即建公司 + OWNER）
- **成熟度**：PRODUCTION-LIKE（Argon2id、opaque session + HttpOnly cookie、passkey、反探测、审计）

### 3.2 Company — `FUNCTIONAL`
- **实体**：`Company`、`Department`
- **SoT**：`companies`（`settings` JSON 承载 behavior_policy / learning_policy / learning_usage）
- **service**：`services/seed.py`、`services/auth.py`（注册建公司）
- **API**：`GET /company`、`GET /settings`
- **UI**：Dashboard `CompanyHero`、`components/company/*`
- **状态机**：`stage`: `FOUNDING → OPERATING`（由教程 `sets_operating_stage` 推进）
- **依赖**：几乎所有领域（company_id 作用域）
- **重复真相**：`Company.memberships` 与「不建多租户鉴权」并存 —— `api/scope.resolve_company_id` 单公司部署假设

### 3.3 Person — `FUNCTIONAL`
- **实体**：`Person`（`persons` 表：slug/name/avatar/username）
- **SoT**：`persons`（**slug 是命名权威**，`employees.slug` 是同源镜像）
- **service/repo**：`repositories/persons.py`（`resolve_person_id(s)` 是唯一换算入口）、`talent/person/read_model.py`、`talent/person/access.py`
- **API**：`api/v1/persons.py`(119) — `GET /persons/{id}`、`/{id}/timeline`、`/{id}/evidence`、`/{id}/fit`
- **UI**：`components/person/person-profile.tsx`、`traits-list`、`competency-list`
- **依赖**：被 Employee / CharacterProfile / EmployeeBrain / Knowledge / Skill / Evidence 以 `person_id` 列（**刻意不加 FK**）挂载
- **重复真相**：**兼容期双写**——`employee_id` 与 `person_id` 在多张表同时存在，读口径已切 person，旧列保留为镜像（R1.4）

### 3.4 Character（培养/市场视角） — `FUNCTIONAL`
- **实体**：`CharacterProfile`（1:1 person，person_id UNIQUE；identity_id `CH-`+12、origin、owner_company_id、lifecycle）、`TrainingProgram`、`EducationEvent`
- **SoT**：`character_profiles`（**不是新的人实体**）
- **service**：`services/cultivation.py`、`talent/cultivation/engine.py`(391)、`talent/cultivation/templates.py`(352)
- **API**：`api/v1/cultivation.py`(197)
- **UI**：`pages/cultivation/{cultivation-page,cultivation-detail-page}.tsx`、`components/cultivation/*`
- **状态机**：`CultivationState`: `cultivating → ready`（`listed`/`hired` 已于 T2.0 废弃，有守卫测试）
- **依赖**：Person + Knowledge/Learning/Evidence + Brain(traits)
- **成熟度**：FUNCTIONAL（三模板 + 际遇 + 确定性 RNG + 教育证据分级 + 显式结业）

### 3.5 Employee — `FUNCTIONAL`
- **实体**：`Employee`（`employees`：company_id、department_id、person_id、role/title、status、lifecycle_status、runtime_type/runtime_config、workspace_path、memory_namespace、current_task_id）
- **SoT**：`employees`（公司成员身份）；**职位真相在 `employments`**，`employee.role` 只是镜像
- **repo/service**：`repositories/organization.py`、`services/lifecycle.py`(971)、`services/employees.py`、`workforce/{access,status}.py`
- **API**：`api/v1/employees.py`(325)、`api/v1/lifecycle.py`(284)
- **UI**：`pages/employees/{employees-page,employee-detail-page}.tsx`
- **状态机**：`EmployeeStatus`（idle/working/researching/learning/reflecting/meeting/error/offline）× `LifecycleStatus`（pending/onboarding/active/transferring/suspended/offboarding/offboarded）→ 派生 `WorkforceStatus`（不落库）
- **重复真相**：`Employee.role` ↔ `PositionAssignment→Slot→Definition.legacy_role`（由 `services/position_compat.py` 独家读取，AST 守卫）

### 3.6 Position — `PRODUCTION-LIKE`
- **实体**：`PositionDefinition`（模板）、`PositionDefinitionPackage`、`PositionSlot`（编制）、`PositionAssignment`（**物理表 `employments`**，作业唯一任职真相）、`PositionProfileVersion`、`PositionCompetencyRequirement`
- **SoT**：`employments`（关闭行 + 开新行，永不 UPDATE/DELETE 语义）；占用态派生不落库（ADR-2）
- **service/repo**：`services/position_service.py`(921)、`services/position_profile.py`(530)、`repositories/position.py`(517)
- **API**：`api/v1/position_profiles.py`(350)、`position_fit.py`(66)、`position_candidates.py`(50)、`talent_roster.py`(215)
- **UI**：`pages/positions/*`、`components/position-{fit,profile,candidates}/*`
- **状态机**：`SlotAdministrativeStatus`(planned/active/frozen/closed) × `AssignmentType`(primary/acting/temporary/secondary) × `AssignmentStatus`(active/closed/superseded)
- **残留**：**`positions` 表是 v0.4 legacy**（model `models/lifecycle.py::Position`），仅 `services/lifecycle.py:847` 一处写入（seed 兼容），DB 有 5 行；已被 `position_definitions`+`position_slots` 取代

### 3.7 Talent / Workforce Roster — `FUNCTIONAL`
- **实体**：读模型（`_Derived`），无新表
- **service**：`services/talent_roster.py`(373)、`services/candidate_analysis.py`(310)
- **API**：`GET /talent-roster`、`GET /talent-roster/stats`、`GET /positions/{id}/candidates`
- **UI**：`pages/talent-roster/talent-roster-page.tsx`(244)

### 3.8 Cultivation — 见 §3.4

### 3.9 Competency / Trait — `PRODUCTION-LIKE`
- **实体**：`CompetencyDomain`、`CompetencyDefinition`、`EmployeeCompetency`(score/confidence/trend/status)、`CompetencyEvidence`、`AssessmentRun`、`CompetencyExpectation`
- **SoT**：`competency_evidence`（原始证据）→ `employee_competencies`（派生聚合，可重算）
- **service**：`services/competency.py`(651)、`competency/catalog.py`(236)
- **API**：`api/v1/competencies.py`(168)
- **Trait**：`brain/registry.py` 注册 **8 维**（curiosity/warmth/independence/conscientiousness/collaboration/risk_tolerance/adaptability/creativity），**只有 curiosity 接入 BehaviorPolicy**，其余 7 维只建 schema/UI/投影（`affects=()`，有测试钉死）
- **重复真相**：`EmployeeBrain.traits`（权威）↔ `EmployeeBrain.curiosity`（legacy 镜像）；`CompetencyEvidence.quality`（P5 旧口径）↔ `strength/reliability`（P6 新口径）；`EmployeeCompetency.employee_id` ↔ `person_id` 双写

### 3.10 Evidence — `PRODUCTION-LIKE`
- **实体**：无独立表（写 `competency_evidence`）
- **service**：`evidence/{pipeline,collectors,normalize,policy,expectations,reconcile}.py`（约 1,000 行）
- **事件消费**：`EVENT_DISPATCH = {task.completed, task.failed, project.completed, learning.completed, skill.validated}`
- **触发**：`settings.evidence_pipeline_enabled=True`（生产默认开，测试默认关）
- **关键不变量**：mock 环境 reliability ×0.5；无 expectation 不造证据；`lenient/unknown ≠ bad`

### 3.11 Assessment — `FUNCTIONAL`
- **实体**：`AssessmentProfile`、`AssessmentCriterion`、`AssessmentCriterionCompetency`、`AssessmentResult`
- **service**：`services/assessment.py`(524)、`assessment/catalog.py`(493)、`services/competency.py::assess_employee_competencies`
- **API**：`api/v1/assessment.py`(507)
- **触发类型**：`automatic/project_end`（`project_end` 由证据流水线在 `project.completed` 时自动跑）；`manual/periodic/promotion/position_change/position_fit` 仅枚举预留
- **UI**：员工详情 assessment 区块

### 3.12 Knowledge / Memory — `FUNCTIONAL`
- **实体**：`KnowledgeItem`（scope=private|department|company、status、confidence、freshness_status、proposed_scope、`knowledge_items_fts` FTS5 trigram）、`MemoryEntry`、`Skill`、`SkillUsage`、`LearningRecord`、`LearningPriority`
- **SoT**：`knowledge_items`；晋升无独立表（提案写在行上 `proposed_scope`）
- **service/repo**：`learning/{retrieval,reflection,priorities}.py`、`repositories/knowledge.py`(474)、`knowledge/promotion.py`(K1 物化到 Drive handbook)
- **API**：`api/v1/knowledge.py`(48)、`learning_api.py`(266)
- **UI**：`pages/knowledge/knowledge-page.tsx`(303)
- **检索**：K1 scope 分层 + K2 FTS5（fallback token-overlap）；排序 = 命中强度 → scope 权重 → stale 靠后
- **缺口**：`freshness_status` **没有任何路径把它置 stale**（K2 只支持降权，驱动未实现，代码注释自认）

### 3.13 Provider / Runtime — `FUNCTIONAL`
- **实体**：`Provider`、`ModelBinding`、`Secret`、`RuntimeInstance`、`RuntimeImage`、`EmployeeBrain`
- **SoT**：`providers`（scope=company|employee）+ `secrets`（Fernet）；执行环境 = `runtime_instances`
- **service**：`services/providers.py`(390)、`services/runtimes.py`(410)、`runtimes/manager/docker_manager.py`(669)、`runtimes/updates/service.py`(324)
- **API**：`api/v1/providers.py`(104)、`runtimes.py`(73)、`git.py`(78)
- **UI**：`pages/runtime/runtime-page.tsx`、`pages/settings/{providers,runtime,git}-section.tsx`
- **状态机**：`RuntimeInstanceStatus`(created→starting→running→idle→stopping→stopped / unhealthy / crashed / error / deleting)

### 3.14 Agent（概念，无实体） — 详见 §4/§5
- 无 `Agent` 表、无 Agent service、无 Agent API。**Agent = Employee 行 + RuntimeInstance 行 + Provider/ModelBinding + EmployeeBrain/traits + Knowledge/Skill/Evidence(person 口径)**。

### 3.15 Project — `FUNCTIONAL`（双路径）
- **实体**：`Project`（name/description/status/goal/owner_id/source_order_text/schedule/code/priority/customer/background/objectives/technical_requirements/constraints/deliverables/review_configuration/participants/tutorial_accelerated）
- **service**：`services/projects.py`(169)、`services/project_delivery.py`(1305)
- **API**：`api/v1/projects.py`(114)、`project_delivery.py`(123)、`tasks.py`(76)、`artifacts.py`(59)
- **UI**：`pages/projects/{projects-page,project-detail-page,review-room-page}.tsx`、`components/project/*`（intake wizard 756 行、lifecycle board 337、gantt 396）
- **状态机**：`ProjectStatus`(requested/planning/in_progress/in_review/completed/cancelled/rejected) **+** 11 个 `ProjectPhaseStatus`
- **依赖**：Company、Employee、Task/Milestone、Drive、Evidence、Economy（compute cost）

### 3.16 Mission — `DOC-ONLY`
- 代码零实现。仅 `docs/ui-redesign.md` 一次词面出现。**不存在**。

### 3.17 Task / Work Item — `FUNCTIONAL`
- **实体**：`Task`、`TaskDependency`、`Milestone`、`WorkSession`、`Message`
- **SoT**：`tasks`（`task_dependencies` 是边表）
- **service**：`services/tasks.py`(141)、`workflow/orchestrator.py`(565)
- **API**：`GET|PATCH /tasks/{id}`（**只有这两个**；任务创建只在 service 内部）
- **UI**：`components/project/task-table.tsx`（只读表格）、`milestone-execution-plan.tsx`
- **状态机**：`TaskStatus`(backlog/todo/in_progress/in_review/done/failed/rejected) + `WorkSessionStatus`(running/completed/failed/cancelled)，转移表在 `services/tasks.py`
- **缺口**：`TaskDependency` 只是 3 条边的形状（research→dev→test→final_review），**没有依赖调度器**（`_unblock_dependents` 只做「前置全 done 才开」）

### 3.18 Workflow — `PARTIAL`
- **后端**：`workflow/orchestrator.py` —— **事件驱动但非通用引擎**，硬编码 4 阶段流水线 + 1 类回退（testing 失败 → development 回 todo）
- **前端**：`components/workflow/project-graph.tsx`（68 行，`@xyflow/react` **只读可视化**，不驱动后端）
- **结论**：React Flow **只是 UI 展示**；后端流程由 `_advance()` 的 `if task.kind == ...` 串成

### 3.19 Event Engine — `PRODUCTION-LIKE`
- **实体**：`Event`（events 表，append-only）+ 内存 `EventBus`（asyncio 队列 + WS 广播 + redaction）
- **service**：`events/bus.py`(91)、`events/engine.py`(206)（分区并发调度器、handler 注册表、重试退避、死信、`stats()`）
- **已注册 handler（3 个）**：`position-access`、`evidence-pipeline`、`economy-cost-consumers`
- **API**：`GET /events`（100 条）、`WS /ws/events`（company 作用域）
- **UI**：`pages/office/activity-ticker.tsx`、`features/activity-feed`

### 3.20 Talent Market — `PRODUCTION-LIKE`
- **实体**：`MarketParticipant`(player_company/npc_company/system_issuer)、`MarketListing`（active/closed + recruited_* 回填）、`TalentCommercialTerm`
- **SoT**：`market_listings`（部分唯一索引保证一人一 active）
- **service**：`talent/market/{read_model,eligibility,issuer,npc,adapter,local_adapter,contracts}.py`（约 1,400 行）
- **API**：`api/v1/market.py`(239)、`talent_roster.py`、`talent_trade.py`(219)
- **UI**：`pages/market/{market-page,market-listing-page}.tsx`
- **依赖**：Cultivation + Fit + Knowledge/Evidence + M1（价格/Offers/Contract/Escrow）+ Recruitment

### 3.21 Recruitment — `FUNCTIONAL`
- **service**：`services/recruitment.py`(324) — `recruit_existing_person`（CAS 抢 listing → 建 Employee(person_id 复用) → 可选任职 → CareerEvent）
- **API**：`POST /market/listings/{id}/recruit`
- **缺口**：**不建 RuntimeInstance、不建 workspace、不配 provider**（`runtime_type=mock`, `runtime_config={}`），注释自认「本阶段不做全链开通」

### 3.22 Economy / Ledger — `PRODUCTION-LIKE`
- **实体**：`LedgerAccount`、`LedgerTransaction`、`LedgerEntry`(append-only)、`WalletProjection`(可重建)、`RewardGrant`、`ComputeUsage`、`NpcEconomicProfile`
- **SoT**：**Ledger 是事实**，`wallet_projection` 是可删除重建的物化投影（E29）
- **service**：`services/economy/{accounts,ledger,monetary,balances,projection,authority,rewards,work_orders,evaluations,settlement,escrow,contracts,costs,consumers,talent_trade,npc_economy,stats}.py` ≈ 4,300 行
- **API**：`api/v1/economy.py`(496) 只读 + 奖励领取 + admin 面
- **UI**：`pages/economy/economy-page.tsx`(236)
- **状态机**：`WorkOrderStatus`(12 态)、`ContractStatus`(11)、`EscrowStatus`(5)、`SettlementStatus`(4)、`RewardStatus`(4)、`OfferStatus`(5)
- **不变量**：E1–E31（`tests/test_m1_invariants.py` 锚点表 + `test_m1_golden_path.py` 全库复式平衡）

### 3.23 Work Market — `FUNCTIONAL`（**与执行脱钩**）
- **service**：`services/economy/work_orders.py`(855)
- **API**：`api/v1/work_orders.py`(284) — 读 / 领 / 提交 / 取消；**发布·验收·结算只走 CLI**（`scripts/work_orders.py` + `make work-order-*`）
- **UI**：`pages/work-orders/work-orders-page.tsx`(175) — 领取 + 手动 summary 提交
- **缺口**：`submit(project_id=..., artifact_refs=[...])` 是**自由字段，无任何校验与联动**

### 3.24 Contract / Escrow / Settlement — `PRODUCTION-LIKE`
- **实体**：`Contract`、`Offer`、`Escrow`
- **service**：`services/economy/contracts.py`(886)、`escrow.py`(488)、`settlement.py`(250)
- **API**：`api/v1/contracts.py`(256)、`talent_trade.py`
- **UI**：`pages/contracts/contracts-page.tsx`(197)
- **纪律**：创建即锁资、履约即结算（多腿净额 + Treasury + Burn）、release-vs-refund 由 CAS 裁定

### 3.25 NPC / MarketParticipant — `FUNCTIONAL`
- **service**：`talent/market/npc.py`(439)、`services/economy/npc_economy.py`(580)
- **API**：**无玩家路由**（CLI `make npc-economy-*` / `make market-npc`）
- **纪律**：注入是唯一 mint 入口，受 `budget_cap` 封顶；NPC 出手只是转移

### 3.26 Drive / Artifact / Deliverable — `PRODUCTION-LIKE`
- 见 §8。

### 3.27 Lifecycle / Provisioning — `FUNCTIONAL`
- **实体**：`ResourceProvider`、`ResourceAccount`、`Entitlement`、`AccessPackage(+Item)`、`EmployeePackage`、`ProvisioningJob(+Step)`、`ResourceAsset`、`AuditLog`、legacy `Position`
- **service**：`lifecycle/{engine,access,audit,naming}.py`、`lifecycle/provisioners/{workspace_local,docs_builtin,gitea}.py`、`workforce/access.py`
- **API**：`api/v1/lifecycle.py`(284)
- **韧性**：单步超时 120s + 启动补收敛 `sweep_stale_provisioning_jobs` + `SkippableStepError`

### 3.28 Git — `FUNCTIONAL`
- **实体**：`GitConnection`
- **service**：`services/git.py`(305)；**builtin gitea** 走 docker
- **API**：`api/v1/git.py`(78)；UI `settings/git-section.tsx`
- **现状**：本地无 gitea 容器 ⇒ `git:*` 步骤 `skipped`（不阻塞入职）

### 3.29 Tutorial — `PRODUCTION-LIKE`
- **实体**：`TutorialProgress`(user_tutorial_progress)、`TutorialProgress`(user × tutorial)
- **service**：`services/tutorial.py`(488)、`app/tutorials/{company_founding,first_project_practice,requirements,schema}.py`
- **API**：`api/v1/tutorial.py`(118)
- **UI**：`components/tutorial/*`（聚光灯、碰撞避让、CoachPanel）、`pages/settings/tutorial-section.tsx`
- **纪律**：完成只认后端领域事实（`requirements.evaluate(requirement, facts)`），前端不得伪造

---

## 4. Identity Model

### 4.1 真实关系（按代码）

```text
User (users)                      ← 真人账号，登录主体
  └─ CompanyMembership (role=OWNER)   ← 作用域来源：identity.company_id
       └─ Company (companies)         ← 一台部署可多公司；resolve_company_id 用会话身份
            ├─ Department (departments)
            ├─ Employee (employees)          ← 「公司成员身份」；person_id 1:1（部分唯一索引）
            │    ├─ PositionAssignment (employments)  ← 任职真相（时间轴）
            │    │     └─ PositionSlot → PositionDefinition
            │    ├─ EmployeeBrain (traits 权威 + curiosity 镜像)
            │    ├─ RuntimeInstance (1:1，person_id 镜像索引)
            │    │     └─ ModelBinding → Provider → Secret
            │    ├─ Entitlement / ResourceAccount / EmployeePackage
            │    └─ current_task_id → Task
            ├─ Project → Milestone/Task/TaskDependency/WorkSession/Message
            ├─ DriveNode(tree) → DriveRevision
            └─ LedgerAccount(kind=actor)

Person (persons)                  ← 身份聚合根；「人」先于「任职」
  ├─ Employee (最多 1 行；person_id 可有可无 —— legacy 行为 NULL)
  ├─ CharacterProfile (0..1，person_id UNIQUE)   ← 培养/市场视角，不是新的人
  ├─ EmployeeBrain (person_id 部分唯一)
  ├─ RuntimeInstance (person_id 部分唯一)
  ├─ KnowledgeItem / Skill / LearningRecord / SkillUsage / MemoryEntry / LearningPriority (person 口径)
  ├─ CompetencyEvidence / EmployeeCompetency(person_id) / AssessmentRun
  ├─ DevelopmentPlan / CareerEvent
  └─ MarketListing (可发现性；person_id 无 FK)
```

### 4.2 逐问回答（全部以代码为准）

| 问题 | 答案 | 依据 |
| --- | --- | --- |
| Person 是什么？ | 身份聚合根：slug/name/avatar/username。「人」先于「任职」存在 | `models/person.py`；`docs/person-core-migration.md` |
| Character 是什么？ | **不是实体**。`CharacterProfile` 是 1:1 挂在 Person 上的**培养/市场扩展**（identity_id / origin / owner_company_id / lifecycle） | `models/cultivation.py` docstring「不建新『人』实体」 |
| Employee 是什么？ | 「这个人在这个公司里的成员身份」：company_id/department_id/role 镜像/status/lifecycle_status/runtime 配置/workspace/current_task | `models/organization.py` |
| Agent 是什么？ | **不是实体**。= Employee + RuntimeInstance（+ Provider/ModelBinding + Brain + 知识/技能/证据） | 无 `Agent` 表/服务；README「One Employee = One Agent」 |
| 是否一一对应？ | Person↔Employee **至多 1:1**（部分唯一索引 `uq_employees_person_id`），非必须（person_id 可 NULL） | `models/organization.py` `__table_args__` |
| Agent 是否一定绑定 Person？ | **否（结构上）**。`RuntimeInstance.person_id` 可 NULL（legacy 行）；但**语义上**是「员工的执行环境」 | `models/runtime.py` |
| Employee 是否一定拥有 Agent？ | **否**。Employee 只是 DB 行；`runtime_instances` 行由 `POST /employees/{id}/runtime` 创建（`ensure_employee_runtime_state` 会为种子员工补 mock 行）。`recruitment` 招募来的新员工 **没有** runtime 行 | `services/seed.py`、`services/recruitment.py` |
| Character 是否只是培养视角？ | **是**。缺少 CharacterProfile 的 Person（纯入职员工）是合法状态，身份字段返回 null | `talent/person/read_model.py::identity_out` |
| 一个 Person 是否可以没有 Employee？ | **可以**（候选人 = 无 employee 行的 Person；市场挂牌的人就是这种形态） | `docs/person-core-migration.md`、`market/eligibility.py` |
| 一个 Employee 是否可以没有 Agent？ | **可以**（招募入职的员工 `runtime_type=mock`、无 runtime 容器，直到另行开通） | `services/recruitment.py` |
| 一个 Agent 是否可以换 Person？ | **不可以**。`RuntimeInstance.employee_id` UNIQUE、`person_id` 部分唯一索引；换人 = 新行 | `models/runtime.py` |
| Provider/runtime 配置属于谁？ | **属于 Employee**（`employee.runtime_type/runtime_config`、`runtime_instances.employee_id`、`model_bindings.employee_id`）；`Provider` 本身 scope 可以是 `company` 或 `employee`；`employee_brains` 是 person 口径 | `models/{organization,runtime,provider}.py` |

### 4.3 结论

用户示例图

```text
Person
 ├── CharacterProfile
 ├── Employee
 └── AgentConfig
```

**方向正确但不精确**：`AgentConfig` 应改为 `RuntimeInstance + ModelBinding`，且必须补上——**Employee 是 Employer 关系的行，而 Brain/Knowledge/Skill/Evidence 在 Person 口径，Runtime/Provider 在 Employee 口径**。这个分层是 R1 重构的核心成果，后续任何「Agent 资产」设计必须尊重它（人级资产不能随任职/运行时变更而复制或重置，T2 不变量 I4/I5 有测试钉死）。

---

## 5. Agent Runtime Audit

### 5.1 一个 Agent 实际包含什么

| 维度 | 存在？ | 存在哪里 |
| --- | --- | --- |
| identity | ✅ | `persons`（slug/name）+ `character_profiles.identity_id`（`CH-…`） |
| persona | ✅ | `employee_brains.personality/goals/interests` + `traits`（8 维注册表） |
| behavior policy | ✅ | 派生读模型 `app/brain/{resolver,policy,projection,trait_policies}.py`（**不落库**）；只有 `curiosity` 接入 |
| provider | ✅ | `providers`（company/employee scope） |
| model | ✅ | `model_bindings`（is_primary） |
| runtime | ✅ | `employees.runtime_type` + `runtime_instances`（部署模式/容器/镜像/健康） |
| system prompt | ⚠️ **部分** | 无持久化 system prompt 字段。任务时**拼 prompt**：`orchestrator._run_task` 的 `f"任务：{title}\n\n{desc}\n\n验收标准：{criteria}"` + `behavior_policy` block（`runtimes/*/adapter.py::append_behavior_block`） |
| memory | ✅ | `memory_entries`（person 口径）+ OpenClaw 自身 `MEMORY.md`（runtime 侧，未统一） |
| tools | ❌ **无模型** | 无工具注册表/权限表。工具能力只在 `RuntimeCapabilities` 上以布尔位声明（`filesystem/terminal/web/...`），由 runtime 自己实现 |
| permissions | ⚠️ | 只有**资源开通**语义（`entitlements`/`resource_accounts`），不是 Agent 工具/数据权限 |
| knowledge | ✅ | `knowledge_items`（private/department/company）+ FTS5 检索 |
| skills | ✅ | `skills` + `skill_usages`（success 客观 / outcome 人工） |
| thinking level | ❌ | 无 |
| execution config | ✅ | `employee.runtime_config`（如 `mock_fail_rate`、`run_timeout_seconds`）+ `runtime_instances` 资源限制 |

### 5.2 Runtime 实现矩阵（以代码为准）

| runtime_type | 有 Adapter 类？ | `implemented` | `detect()` | capabilities | 备注 |
| --- | --- | --- | --- | --- | --- |
| `mock` | ✅ `MockAdapter` | **True** | True | 全 True（含 artifacts / brain_projection） | 产出为模板 Markdown（`runtimes/mock/templates.py`） |
| `hermes` | ✅ `HermesAdapter` | **True** | `docker.ping()` | 全 True **除 `artifacts=False`** | 真 SSE `/v1/runs/{id}/events` 驱动；实测版本 `Hermes Agent v0.21.0` |
| `openclaw` | ✅ `OpenClawAdapter` | **True** | `docker.ping()` | 全 True（含 `artifacts=True`） | WS `session.*` + `agent.wait` |
| `codex` | ❌ | False | False | 空 | **枚举值 + `/runtime-types` 里的 `implemented=false`** |
| `claude_code` | ❌ | False | False | 空 | 同上 |
| `opencode` | ❌ | False | False | 空 | 同上 |
| `custom` | ❌ | False | False | 空 | 同上 |
| `ollama` | — | — | — | — | **不是 runtime**，是 `ProviderType.ollama`（模型供应商） |
| `vLLM` | ❌ | — | — | — | 代码中**完全不存在** |

**用户列表需要校正**：`Ollama` 属 Provider 层；`vLLM` 未出现；`Codex / Claude Code / OpenCode` 只是 UI/枚举占位。

**关键运行时事实**：
- `EIDOLON_RUNTIME_MODE=mock` 时 `RuntimeGateway.adapter_for()` **无条件返回 MockAdapter**，真实 adapter 全部旁路（当前 `.env` 即为此状态）。
- DB 里**已有一台真实 hermes 实例**（`runtime_instances.id=1`, `eidolon-hermes-ada-3f763e`, `running`, `Hermes Agent v0.21.0`）且绑定了真实 DeepSeek provider —— 只是因为 mock 模式而未被执行链使用。
- `docker ps` 另有一个孤儿容器 `eidolon-hermes-ada-891d91`（DB 无对应行）。

### 5.3 Agent 执行能力

| 能力 | 状态 | 依据 |
| --- | --- | --- |
| 接受任务 | ✅ DONE | `Orchestrator._dispatch_pending`（`tasks.status=todo` + assignee + employee idle + 无 running session） |
| 执行任务 | ✅ DONE | `_run_task` → `gateway.get_or_create_instance` → `adapter.create_session/send_task/stream_events` |
| 调用工具 | ⚠️ PARTIAL | 无工具层；由 runtime 自己决定（Hermes/OpenClaw 有自己的工具） |
| 读取文件 | ⚠️ PARTIAL | `filesystem=True` capability；workspace 路径来自 `employee.workspace_path`，但 prompt 里没有任何文件/仓库上下文注入（只看 `runtime_config`） |
| 修改项目 | ❌ NOT IMPLEMENTED | 无 git 提交/回写逻辑；`GitConnection` 只在权限开通侧使用 |
| 写 Artifact | ✅ DONE | `adapter.get_artifacts` → `artifact_service.record_project_artifact` → DriveNode(+Revision+sha256) |
| 与其他 Agent 通信 | ❌ NOT IMPLEMENTED | `messages` 只有手工 POST |
| 提交结果 | ✅ DONE | `_finalize` 写 `WorkSession.summary/error/cost` |
| 被 Review | ❌ NOT IMPLEMENTED | `_finalize` 里 `in_review → done` 自动连跳 |
| 被 Retry | ⚠️ PARTIAL | 仅 1 条规则：testing 任务失败 ⇒ 同项目 `development` 任务回 `todo`（`_advance`）。`task.failed` 之后无通用 retry |
| 并发 | ✅ | 一员工同时**只能**一个 running WorkSession（不变式 §3.4.2，`self._running[employee_id]`） |

---

## 6. Project / Mission / Task Audit

### 6.1 Project

**不只是 metadata container**，它有两套彼此独立的语义：

| | 路径 A：`create_order`（legacy） | 路径 B：`create_structured_project` |
| --- | --- | --- |
| 触发 | `POST /projects` 且 `is_structured=False` | `POST /projects` 且 `is_structured=True`（前端 Intake Wizard 恒真） |
| 初始 status | `requested` | `in_progress` |
| 结构 | order_review Task → planning Task → 4 Milestone/Task graph | 11 个 `ProjectPhase` + `ProjectRequirement` + `DocumentArtifact` |
| 执行者 | **Agent（WorkSession + RuntimeAdapter）** | **人类**（`POST /phases/{id}/complete`、`POST /reviews/{id}/decision`） |
| 文档来源 | Agent 产出（mock 模板 / 真实 LLM） | `document_generation_service` **模板渲染**（Markdown/DOCX/PPTX/ZIP） |
| requirements | ❌ 无（只有 `source_order_text`） | ✅ `project_requirements`（code/title/priority/acceptance_criteria/traceability refs） |
| stages | ✅ `milestones`（4 个） | ✅ `project_phases`（11 个 + gate 标记） |
| members | ⚠️ `owner_id` + `participants` JSON | ⚠️ `owner_id` + `participants` JSON + `phase.owner_employee_id` |
| artifacts | ✅ DriveNode（project zone） | ✅ `document_artifacts`（版本 + drive_revision + review_status + baseline_status） |
| timeline | ✅ planned/actual start/end | ✅ phase started/completed + review scheduled/completed |
| status | 7 值 | 7 值 + `ProjectPhaseStatus` 8 值 |

**结论**：Project **既有 requirements/stages/members/artifacts/timeline/status**，但是**并行两套**，且**没有任何项目同时具备两套**。

### 6.2 Mission

**不存在 Mission Domain。** 无表、无 model、无 service、无 API、无 UI。代码中 `mission` 词面仅出现在无关注释/字符串。文档中仅 `docs/ui-redesign.md` 一处提及。

→ 用户提出的 Mission 概念 **100% 是新增设计**，与现有代码无重叠。

### 6.3 Task

| 项 | 状态 |
| --- | --- |
| Task entity | ✅ `tasks`（title/description/kind/status/priority/assignee_id/acceptance_criteria/sequence/planned+actual times/phase_id） |
| Task dependency | ⚠️ `task_dependencies(task_id, depends_on_id)` 存在，但**只有 `_generate_graph` 写入的 3 条边**；无拓扑调度、无环检测、无关键路径 |
| Task assignment | ⚠️ 只有两种方式：(a) `position_compat.employee_by_legacy_role`（模板固定角色）；(b) `_ensure_development_tasks` 里 engineer 或 project.owner 兜底。**无 Fit、无人工指派 UI 之外的路径**（`PATCH /tasks/{id}` 可改 assignee 字段但无校验） |
| Task state | ✅ 7 态 + 合法转移表（`services/tasks.py`，有 `test_task_lifecycle.py`） |
| Task execution | ✅ 经 `WorkSession` → Adapter；但**只对 `todo` 状态 + 有 assignee + 员工 idle 的任务生效** |
| Task 创建入口 | ❌ **没有公开 API**；只有 service 内部（`create_order`/`_generate_graph`/`_ensure_development_tasks`）+ `PATCH` |

### 6.4 Workflow (React Flow)

- 前端：`components/workflow/project-graph.tsx`（68 行）— 渲染 `GET /projects/{id}/graph` 的 nodes/edges。
- 后端：`services/projects.py::get_project_graph` 从 `task_dependencies` 生成。
- **React Flow 是纯只读可视化，不驱动后端执行**。后端执行由 `orchestrator._advance()` 的 `if task.kind ==` 分支串成。
- 没有拖拽改图 → 改执行的路径。

### 6.5 Execution 持久化实体

| 候选 | 是否存在 | 记录什么 |
| --- | --- | --- |
| `WorkSession` | ✅ **唯一** | task_id / employee_id / runtime_type / runtime_session_ref / behavior_snapshot(+hash) / status / summary / started_at / ended_at / cost{policy_version,behavior_revision,duration_sec,tokens} / runtime_instance_id / provider_id / model / error |
| `TaskRun` | ❌ | — |
| `AgentRun` | ❌ | — |
| `Execution` | ❌ | — |
| `Job` | ⚠️ 只有 `ProvisioningJob`（**权限开通**，不是任务执行） |
| `runtime_session_ref` | ⚠️ 字符串 | 存 adapter 侧 session id（`mock-xxxx` / `hermes-xxxx`），**不落 RuntimeEvent 流** |
| RuntimeEvent | ❌ 不落库 | `thinking/message/tool_call/status/error/completed` 只经内存 asyncio 队列；WS 只广播 `bus.publish` 的类型化事件（如 `task.started`），**不是 runtime 事件流** |

→ **执行状态的持久化只有 `WorkSession` 一行 + `events` 表里的聚合事件**。没有可回放的 runtime 事件日志、没有 token 明细（`tokens: 0` 硬编码）、没有中途快照。

---

## 7. Multi-Agent Execution Audit

| 能力 | 现状 | 代码位置 / 证据 |
| --- | --- | --- |
| **planner** | ❌ 无 LLM planner。`TaskKind.planning` 任务被派给 PM，其结果由 `_generate_graph()` **硬编码模板**决定 | `orchestrator.py:GRAPH_TEMPLATE`、`_generate_graph` |
| **task decomposition** | ⚠️ 固定 4 阶段（Discovery/Build/Verify/Release），每阶段 1 个 Task。**任务粒度 = 阶段**，不会拆成子任务 | `GRAPH_TEMPLATE` |
| **assignment** | ⚠️ 角色映射：`position_compat.employee_by_legacy_role(company, role)`。**不用 Fit、不用负载、不用能力** | `_generate_graph`、`create_order`、`_ensure_development_tasks` |
| **orchestration** | ⚠️ 单进程 asyncio：一个 dispatcher loop（500ms sweep）+ 每个 employee 一个 task。**无调度优先级、无并行度控制、无依赖就绪队列** | `Orchestrator._run`、`_dispatch_pending` |
| **communication** | ❌ 无 | — |
| **handoff** | ❌ 无。后继任务拿到的 `description` 是 `project.source_order_text`（同一段原文），**不含上游产物** | `_generate_graph(description=project.source_order_text)` |
| **context 传递** | ⚠️ 仅「执行者自己的知识/技能」：`retrieval.retrieve_for_task(db, employee.id, title, description, policy)` → `TaskContext.prior_knowledge/validated_skills` | `_run_task` |
| **retry** | ⚠️ 1 条硬编码规则（testing fail → development 回 todo）；`task.failed` 后无重试计数、无退避、无 max attempts | `_advance` |
| **replan** | ❌ 无 | — |
| **review gate** | ❌ `in_review → done` 自动连跳 | `_finalize` |
| **dependency scheduling** | ⚠️ `_unblock_dependents`：前置全 done 才开。**无 fan-out/fan-in 语义** | `_advance` |

### 当前"多 Agent"属于哪一层？

**介于 B 和 C 之间**：

- **不是 A**（有调度器，不是各自独立跑）。
- **不是 D**（无动态拆解、无动态分配、无依赖 DAG 调度器）。
- **部分 B**（固定角色 → 固定 worker，像委派但没有 Leader 决策）。
- **部分 C**（固定 Workflow：4 阶段模板）。
- **E 的一小部分**：只有一处 retry（回退重做），**无 replan、无 review**。

→ **准确描述：C（固定 Workflow）为主 + 一条硬编码回退的残缺 E**。

### E2E 证据

`tests/test_project_workflow.py::test_project_workflow` 在 mock 模式下真的跑通 `order → completed`，断言 milestone/task kinds、5 种 artifact 类型、graph 边数（3）、以及 5 个员工的 learning records + skills。**这条链是真实的**，但它是「固定流水线 + 5 个预置角色」，不是自主协作。

---

## 8. Artifact / Workspace Audit

### 8.1 统一模型

**存在，但它叫 Drive，不叫 Artifact。**

```text
DriveNode (drive_nodes)
  kind = folder | document
  zone = projects | knowledge | skills | handbook
  company_id / parent_id / path(UNIQUE) / project_id / doc_type
  owner_employee_id(镜像) / owner_person_id(权威)
  current_version / work_session_id
      └── DriveRevision (drive_revisions)
            version / sha256 / author_employee_id(镜像) / author_person_id / message
DriveCollaborator (node_id, employee_id, role)
```

- 磁盘真相：`{data_root}/drive/{zone}/...`；DB 只是索引。
- `TYPE_TO_DIR`（`services/drive.py`）把 9 种 ArtifactType 映射到目录。

### 8.2 逐问回答

| 问题 | 答案 |
| --- | --- |
| 代码如何保存？ | Agent 产出 → `record_project_artifact` → `drive_service.create_project_document` → 写盘 + `DriveNode` + `DriveRevision(sha256)`。**没有 git / 没有可执行产物隔离** |
| 报告如何保存？ | 结构化交付路径 → `document_artifacts`（category/format/version_major.minor/drive_node_id/drive_revision_id/review_status/baseline_status/source_document_id）+ Drive 文件（Markdown/DOCX/PPTX/ZIP） |
| Agent 生成文件如何保存？ | 同「代码」；`work_session_id` 回链 |
| 不同 Agent 能否共享 Artifact？ | ⚠️ 读面按 company + zone 过滤（`list_artifact_nodes`），**结构上可读**；但**没有任何机制把 A 的产物交给 B**（无 handoff、无引用注入） |
| 是否关联 Project / Mission / Task / Agent？ | Project ✅（`drive_nodes.project_id`）；Task ⚠️（`Artifact.task_id` 存在于 deprecated 表；DriveNode **无 task_id 列**，只有 `work_session_id`）；Agent ✅（owner_person_id / author_person_id）；Mission ❌ |
| 是否有版本？ | ✅ `current_version` + `drive_revisions` 全历史 |
| 是否有 lineage？ | ⚠️ `document_artifacts.source_document_id`（文档派生）+ `DriveRevision` 时间链；**没有跨 Agent/跨任务的血缘图** |
| 是否有 final deliverable 概念？ | ⚠️ 有 `DeliveryPackage`（version/status/manifest/drive_node_id/content_hash）与 `Baseline`（requirements/design/acceptance），但**只在结构化交付路径**，且内容是模板生成的清单 |

### 8.3 重复真相

`artifacts` 表（`models/project.py::Artifact`）**已废弃**：0 行、无写入点、`services/artifacts.py` 自述 deprecated；但 model + `ArtifactOut` schema + `/artifacts` API + `/projects/{id}/artifacts` API 全部保留（兼容层）。`Artifact.task_id` 在转换时被硬编码为 `None`。

---

## 9. Knowledge / Memory Audit

| Scope | 是否存在 | 载体 |
| --- | --- | --- |
| Person Knowledge | ✅ | `knowledge_items` with `owner_person_id` + `scope=private` |
| Department Knowledge | ✅ | `scope=department` + `department_id` |
| Company Knowledge | ✅ | `scope=company`（可物化到 Drive `handbook` zone） |
| Project Knowledge | ❌ | **无 project scope**。项目上下文不进知识库 |
| Mission Context | ❌ | 无 Mission |
| Agent Memory | ✅ | `memory_entries`（person 口径，kind=note/observation/summary）；**写入点稀疏** |
| Skill | ✅ | `skills` + `skill_usages`（`uq_skill_usage(task_id, skill_id)`） |
| LearningRecord | ✅ | `learning_records`（kind=reflection/research/question） |

- **检索**：`learning/retrieval.py::retrieve_for_task` 查 private + department + company 三层，K2 FTS5 trigram 命中，排序（强度 → scope 权重 → stale 靠后 → 稳定序）；配额由 `BehaviorPolicy.retrieval` 决定（默认 `knowledge_limit=5`、`include_candidate_skills=False`）。公司隔离在 repo 层 SQL 下推。
- **晋升**：`knowledge/promotion.py` — `propose(target_scope)`（禁止 private）→ `review(approve)` → 物化到 Drive（company→handbook，department→knowledge/dept 夹）+ 事件 `knowledge.promoted`。**没有独立提案表**（提案写在 `knowledge_items.proposed_scope`），不能并存多个提案。
- **「任务结束 → 重要知识晋升」**：❌ **不存在自动路径**。晋升只能人/API 显式触发。任务结束只产生 `LearningRecord`（reflection）。
- **「临时上下文污染永久记忆」**：结构上**风险较低**——`LearningRecord`/`KnowledgeItem` 是两个不同的表，任务产出默认进 `learning_records`（reflection）而非 `knowledge_items`；`knowledge_items` 的写入点集中在 `services/learning.py` 的产出原语 + 培养引擎 + 晋升物化。但**进入检索上下文的 `prior_knowledge` 来自 `knowledge_items`，所以任务临时观察不会直接污染后续任务上下文**。
- **明确缺口**：`freshness_status` 永远只写 `"fresh"`，**无任何置 stale 的驱动**（K2 只做了降权读取侧）。

---

## 10. Review / Evaluation Audit

系统里**存在四套互不相通的"评审/评价"**：

| # | 名称 | 由谁判定 | 判定什么 | 能否导致 rework | 载体 |
| --- | --- | --- | --- | --- | --- |
| 1 | **Task review** | **无**（自动连跳） | — | ❌ | `orchestrator._finalize`: `in_review → done` 同事务连跳 |
| 2 | **Formal ReviewMeeting** | **人类**（`POST /reviews/{id}/decision`，带 `expected_version` 乐观锁） | requirements/design/acceptance review | ✅ `CHANGES_REQUESTED` → 源阶段回 `changes_requested`；`REJECTED` → gate `blocked` + project `rejected` | `ReviewMeeting` + `ReviewPackage` + `Baseline` + `ProjectPhase` |
| 3 | **M1 Evaluation** | `auto`（确定性规则，如 `deliverables.required_keys` 校验）/ `manual`（CLI）/ `none` | WorkOrder 交付是否达标 | ✅ `verdict=rejected` → 订单回 `IN_PROGRESS`，可 `attempt+1` 重提 | `evaluations` 表 + `services/economy/evaluations.py` |
| 4 | **Competency Assessment** | 统计聚合（`aggregate`） | 从证据算 score/confidence/trend | ❌ 不是 gate | `assessment_runs` + `employee_competencies` |

**「Agent 做完一个任务之后谁判断做得对不对？」**
→ **没有任何人**。第 1 套自动通过。Agent 的执行结果只影响 `WorkSession.status` 与后续证据权重（`success` 布尔）。

**是否支持 `FAILED REVIEW → REWORK`？**
- Agent 任务层：❌ 不支持（没有 review，只有 `task.failed` + 一条 testing→development 回退）。
- 结构化交付层：✅ 支持（人类 `CHANGES_REQUESTED`）。
- WorkOrder 层：✅ 支持（`Evaluation.verdict=rejected` → 重提）。

**有无 `Reviewer Agent`？** ❌ 无。无 reviewer 角色、无 agent-as-reviewer 实现。

---

## 11. Agent Growth Audit

### 真实任务 → 成长的真实路径

```text
WorkSession 结束（success/fail）
      │  Orchestrator._finalize
      ├── bus.publish("task.completed" | "task.failed")
      │        └── EvidencePipeline（gated by settings.evidence_pipeline_enabled）
      │              → WorkItemCollector._collect_one
      │                 · expectations: task expectation → position expectation → TASK_KIND_HINTS
      │                 · source_type = "test" if kind==testing else "task"
      │                 · signal = POLICY.signal_default(source, "done"|"failed")   (80/30/82/35)
      │                 · reliability × 0.5 if environment == "mock"
      │              → normalize.upsert_evidence → competency_evidence（幂等 dedup）
      ├── reflection.reflect(task_id, employee_id, ...)  → learning_records（reflection）
      ├── knowledge_repo.create_skill_usage(...) 已在派发时写入
      ├── SkillUsage.success = success（客观事实；outcome 仍为人工判断，永不自动置 useful）
      └── project.completed → assessment.run_project_end_assessments() → run_assessment(type="project_end")
```

| 目标 | 结果 |
| --- | --- |
| Person timeline | ⚠️ `GET /persons/{id}/timeline` **只返回 EducationEvent**（培养履历），**不含工作经历**。工作经历在 `career_events`（`GET /employees/{id}/timeline`） |
| Evidence | ✅ 真实写入（`competency_evidence`，DB 现有 61 行） |
| Competency | ✅ `EmployeeCompetency` score/confidence/trend（聚合器写，无直接写分路径，有守卫） |
| Knowledge | ⚠️ 只产生 `learning_records`（reflection）；**只有显式晋升才进 `knowledge_items`** |
| Experience / Resume | ⚠️ 无简历实体。有 `career_events`（CareerEvent audit）+ `career_paths`/`development_plans` + `/persons/{id}` 读面（能力+证据+履历）——**这是事实上的简历**，但工作履历与培养履历分居两表 |
| Skill | ✅ attempts/success_count/avg_duration/avg_cost/validation_status（candidate → validated） |

**结论**：**任务确实推动 Agent 成长**（这是系统里最完整的闭环之一），但成长结果**不回写到 Agent 的"可交易价值"**——T2 市场只看 `quality_tier`（发行参数）与 Fit（Position×能力），**没有「战绩/信誉/交付质量」这类市场维度**。

---

## 12. Fit / Team Formation Audit

### 12.1 Fit 当前到底对什么计算

**唯一形态：`(Person | Employee) × PositionDefinition（ACTIVE PositionProfileVersion 的需求）`。**

- 引擎：`talent/fit/engine.py`(659) — requirement 级 `evaluate` → `UNRATED/INSUFFICIENT_CONFIDENCE/BELOW_MINIMUM/MEETS_MINIMUM/MEETS_TARGET`；`known_fit`、`fit_confidence = 加权 known confidence × required_coverage`、`coverage`（**永远基于全部需求**，unknown 不稀释）。
- 策略常量：`POLICY`、`POSITION_FIT_ENGINE_VERSION`、`POSITION_FIT_POLICY_VERSION`；`inputs_hash` 只吃 person 口径（保证市场/在册两条路径逐字段一致，有对拍测试）。
- 输出**派生不落库**（T2 设计：Fit 是函数不是第二真相）。

### 12.2 逐问

| 问题 | 答案 |
| --- | --- |
| Person ↔ Position | ✅ 有（`calculate_person_fit`，`GET /persons/{id}/fit`、`GET /market/listings/{id}/fit`） |
| Employee ↔ Position | ✅ 有（`calculate_fit`，`GET /employees/{id}/position-fit/{def_id}`、`/position-fit/current`） |
| **Person ↔ Task** | ❌ **无** |
| **TaskRequirement** | ❌ 无此类实体 |
| **CapabilityRequirement** | ⚠️ 只有**职位**级的 `PositionCompetencyRequirement`（有 critical/required/preferred 权重与 target/minimum）；**没有任务级** |
| 任务如何找能力 | ⚠️ `evidence/policy.py::TASK_KIND_HINTS`：7 种 `TaskKind` → 硬编码 competency code 列表（development→execution/backend_engineering/code_quality 等），注释明说「order_review/final_review/general 不给提示——没有可靠映射就不编证据」 |
| **Team Fit** | ❌ **完全不存在**。无「Mission 需要 Research+Coding+Testing，A+B+C 覆盖多少」的任何实现 |
| 候选人推荐 | ⚠️ `api/v1/position_candidates.py` + `services/candidate_analysis.py` → 对**职位**做候选排序，非 Team |
| 全公司 ranking | ❌ 刻意不做（`position_fit.py` docstring 自述「不做全公司 ranking / recommended-candidates（P9）」——**P9 尚未实现**） |

### 12.3 现有可复用地基

`fit/engine.py` 的 requirement-evaluate/normalize/hash/policy 分层**已经是通用的**：只要有一组 `(competency_definition_id, role, minimum, target, weight)` 就可用。**把它从 Position 换到 Task/Mission 的 requirement 上，是纯适配工作，不需要新引擎。**

---

## 13. Talent Market Audit

| 环节 | 状态 | 证据 |
| --- | --- | --- |
| 官方发行 Agent | ✅ FUNCTIONAL | `talent/market/issuer.py` + `scripts/issue_talent.py` + `make market-issue`。**走真实培养链**（三档 normal/fine/rare 只影响采样参数，能力仍由证据聚合）；`MarketParticipant(kind=system_issuer, company_id=NULL)` |
| 玩家培养 Agent | ✅ FUNCTIONAL | `/cultivation` 页 + 三模板 + 自由养成 + 际遇 + 显式结业 |
| 挂牌 | ✅ | `POST /market/listings`（要求 `lifecycle=ready` 且无生效主职） |
| 浏览 | ✅ | `GET /market/listings`（跨公司可见）+ `pages/market/*` + 履历=证据链视图（score 与 confidence 并列） |
| Evidence 下钻 | ✅ | `GET /market/listings/{id}` 返回 timeline + evidence + competencies |
| Fit | ✅ | `GET /market/listings/{id}/fit`（person 口径，市场投影不含 `inputs_hash`） |
| **价格** | ✅ | `talent_commercial_terms`（M1.7）：`sale_mode`(buyout/negotiation)、价格、条款 |
| **购买** | ✅ | `POST /market/offers` → `POST /market/offers/{id}/accept` → Contract 锁资 → **T2 招募（commit=False）** → 多腿放款，同一事务 |
| Recruitment | ✅ | `RecruitmentService.recruit_existing_person`（CAS 抢 listing，不新建 Person，identity_id 不变） |
| NPC | ✅ | `talent/market/npc.py`（复用同一个 Fit 引擎）+ `services/economy/npc_economy.py` + CLI；**无玩家路由** |

### 关键问题：市场里的 Agent 是"可用 Agent"还是"人才档案"？

**目前是「人才档案 + 真实但未开通的执行身份」。**

代码原文（`services/recruitment.py`）：

```python
status=EmployeeStatus.idle.value,
# 人已到位；运行时/工作区开通沿用既有员工流程（本阶段不做全链开通）
lifecycle_status=LifecycleStatus.active.value,
runtime_type=RuntimeType.mock.value,
runtime_config={},
```

→ **购买/招募之后不能立刻执行 Agent Task**：
1. `runtime_type=mock` ⇒ 即使派任务，跑的是 MockAdapter 模板产出，不是这个人的"能力"。
2. 没有 `runtime_instances` 行 ⇒ 需人工 `POST /employees/{id}/runtime` 建容器。
3. 没有 Provider/ModelBinding ⇒ 需人工绑模型。
4. 没有 `position_slots` 任职（除非招募时显式传 `position_slot_id`）⇒ `position_compat` 兜底会给 `engineer`。

**这是「人才市场 ↔ Agent 执行」之间最大的断裂点**，且是**已知并被有意推迟的**（注释自认）。

---

## 14. Economy / Work Market Audit

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Ledger | ✅ PRODUCTION-LIKE | 4 表；append-only entries；`post()` 唯一写入路径；`Σdebit = Σcredit` 有 property 测试 |
| WalletProjection | ✅ | 同事务维护；`rebuild_wallet_projection` 可重建；`verify_wallet_projection` 只报告；CAS 乐观并发 |
| Mint | ✅ | 仅 `MonetaryAuthority`（令牌守卫），唯一入口 |
| Burn | ✅ | 手续费按 `treasury_ratio/burn_ratio` 拆分 |
| Reward | ✅ | 7 类自助奖励（starter/profile/company_profile/tutorial/daily/weekly/achievement）+ 救援经济；金额只来自政策（claim 无金额入参）；官方类不可自助 |
| Official WorkOrder | ✅ | 全生命周期；预算内发行（`official_max_reward` / `official_outstanding_budget`）；发布/验收/结算走 CLI |
| Player WorkOrder | ✅ | `POST /work-orders`（发布前锁资 E11，余额不足 409 不留半单）；`POST /{id}/cancel` 退款 |
| Escrow | ✅ | 独立 `ledger_accounts` 行（`kind=escrow, subject_ref=escrow.id`）；release-vs-refund 由 CAS 裁定 |
| Contract | ✅ | `contracts`/`offers` + 多腿结算（净额 + Treasury + Burn）；创建即锁资、履约即结算 |
| Settlement | ✅ | `settlement.py`；幂等；`SettlementStatus` 4 态 |
| Talent Commercialization | ✅ | `talent_commercial_terms` + Offers + 与 T2 招募同事务 |
| NPC Economy | ✅ | 预算注入（唯一 mint 入口，受 `budget_cap` 封顶）+ deterministic 出手 |
| 货币供给 | ✅ | `minted/burned/supply/circulating` 快照 + `make economy-supply` |
| 观测 | ✅ | `make economy-stats|economy-check|economy-policy-reload` + `POST /economy/admin/policy/reload`（`EIDOLON_ECONOMY_ADMIN_ENABLED`） |

### 经济系统是否足够支撑「Agent + Task」玩法？

**记账、托管、结算、合约、激励全部足够，甚至过剩。真正缺的是「经济活动与 Agent 工作之间的语义连接」：**

| 已经足够 | 真正缺失（玩法层，不是经济层） |
| --- | --- |
| 钱怎么记、怎么锁、怎么放、怎么对账、怎么防重复 | 一个 WorkOrder 被接受后 **没有进入 Agent 执行系统** |
| 奖励资格与幂等 | 任务完成质量 **不进入交付定价**（无质量→价钱的映射） |
| 公司/个人/NPC/系统四类主体账户 | Agent 的**履历/战绩不影响 market 价格**（价格由玩家手填 `talent_commercial_terms`） |
| 算力成本计量（`compute_usage`，status=paid/unpaid） | 计量数据**没有反馈到 Agent 效率/成长**（`tokens: 0` 硬编码） |
| 手续费/税率/财政销毁可配 | 「Agent 完成任务 → 公司收入」这条链**只对 WorkOrder 成立，对 Project 不成立**（Project 不产生收入） |

**明确不做/未做**（用户说不要扩展：不列建议）：多币种、联合出资 Escrow、争议仲裁、股权分红、NPC 出售发布、真实 provider 成本映射。

---

## 15. Current User Journey

### 15.1 导航结构（`components/layout/company-sidebar.tsx`）

```text
command        / (Dashboard)      /office
workforce      /talent-roster     /positions    /cultivation    /market
work              /projects       /work-orders  /contracts
economy           /economy
assets            /drive          /knowledge
infrastructure    /runtime
settings          /settings/{profile,security,preferences,tutorial,providers,git,runtime,about}
```

### 15.2 页面清单（全部实测存在）

| 页面 | 存在 | 行数 | 主要能力 |
| --- | --- | --- | --- |
| Dashboard `/` | ✅ | 81 | 公司 hero / 名册概览 / 活跃项目看板 / 基础设施 / 资产产出 / 事件流 |
| Office `/office` | ✅ | 125 | 按部门分区 + 员工快捷面板（**不是 Phaser 场景**；Phaser 只在登录页） |
| Employees `/employees`, `/employees/:id` | ✅ | 182 / 231 | 招聘向导；详情含 runtime/provider/知识/技能/学习/考核/权限 |
| Talent Roster `/talent-roster` | ✅ | 244 | 名册（能力矩阵 + 当前职位 + WorkforceStatus） |
| Positions `/positions`, `/positions/:id` | ✅ | 87 / 144 | 职位模板 + 在职人 Fit + 能力画像 |
| Cultivation `/cultivation`, `/cultivation/:id` | ✅ | 72 / 101 | 建角色 / 培养会话 / 时间线 / 结业 / 挂牌 |
| Market `/market`, `/market/:listingId` | ✅ | 131 / 195 | 挂牌浏览 + 履历 + Fit + 招募 + 条款/出价 |
| Projects `/projects`, `/projects/:id` | ✅ | 134 / 171 | 立项向导（6 步）/ 生命周期板 / 甘特 / 任务表 / 变更单 |
| Review Room `/projects/:id/reviews/:reviewId` | ✅ | 320 | 评审室（Presenter/议程/文档/PPT/Comments/Decision） |
| Drive `/drive` | ✅ | 751 | 云文档（新建/上传/版本/协作） |
| Knowledge `/knowledge` | ✅ | 303 | 公司知识浏览 + 晋升提案/评审 |
| Economy `/economy` | ✅ | 236 | 余额/收支/流水/钱包/奖励领取 |
| Work Orders `/work-orders` | ✅ | 175 | 在招/我承接 + 领取 + 手动提交 |
| Contracts `/contracts` | ✅ | 197 | 接受/交付结算/取消 + 多腿明细 |
| Runtime `/runtime` | ✅ | 160 | 实例列表/镜像/健康 |
| Settings | ✅ | 8 个分节 | profile/security/preferences/tutorial/providers/git/runtime/about |

**不存在**：Mission 页、Task 执行详情页（任务只读表格）、Agent 详情/编排页、Team 页、Workflow 编辑器、Artifact 统一视图（只有 Drive）。

### 15.3 第一分钟 / 第十分钟 / 第一小时

| 阶段 | 实际能做什么 |
| --- | --- |
| **第一分钟** | 注册 → 邮箱验证（console 打印链接）→ 进入 `FOUNDING` 空公司 → Dashboard 显示 `CompanyFoundingState` → 核心教程第 1 步聚光到公司概览 |
| **第十分钟** | 教程驱动：招聘向导招 CEO（选 runtime=mock / 后期配 provider / 选权限包）→ 到员工详情配 runtime + 新建 provider + 绑定模型 → 配公司资源（workspace/docs，git 可跳过）→ 新建一篇云文档 → （可选）Git → 招工程师 → 教程 12 步完成 → 公司 `OPERATING` |
| **第一小时** | 分岔：<br>① **结构化交付**（UI 唯一可达）：立项向导 6 步 → 11 阶段流程 → 阶段 `complete` → 人工评审室做决定 → 生成模板文档/DOCX/PPTX/交付包 → 项目 completed。<br>② **经济玩法**：`/work-orders` 领官方订单 → 手打 summary 提交 → auto 结算进账；`/market` 挂牌/买人/招募；`/contracts` 接受/交付结算。<br>③ **培养玩法**：`/cultivation` 建角色 → 跑模板/自由会话 → 结业 → 挂牌 → 被别人（或自己）招募。<br>④ **Agent 执行**（**UI 不可达**，只能直接 `POST /projects {name, description}`）：触发固定 4 阶段流水线，5 个角色自动跑，产出模板 artifact |

### 15.4 最明显的体验断点

1. **「招人 → 买人 → 培养」都做完了，却没法让这些人真的干活。** UI 的立项向导走结构化交付，Agent 完全不参与；能触发 Agent 的 legacy 路径没有 UI 入口。
2. **市场买来的人不能立即执行**（需另行配置 runtime/provider）。
3. **任务执行没有可见性**：没有 WorkSession 详情页、没有 runtime 事件流、没有 token/成本明细（`tokens: 0`）。
4. **WorkOrder 的「交付」是手打一段文字**。
5. **「完成任务」没有质量判定**，因此「任务 → 成长 → 价值」这条玩家能感知的闭环缺了关键一环。

---

## 16. End-to-End Real Problem Test

**输入**：「帮我开发一个完整的小型 Web 应用，包括需求分析、架构、前后端实现、测试和最终交付。」

按当前代码逐环追踪：

| # | 环节 | 状态 | 实际代码位置与行为 |
| --- | --- | --- | --- |
| 1 | **Input**（用户输入） | **DONE** | `ProjectIntakeWizard` 6 步表单（name/code/owner/background/objectives/requirements/deliverables/reviews/participants） |
| 2 | **API** | **DONE** | `POST /api/v1/projects` → `services/projects.create_order` |
| 3 | **Project 创建** | **DONE** | `is_structured=True`（向导恒填 code/background/objectives/requirements）→ `create_structured_project`：status=`in_progress`、`code` 去重、11 个 `ProjectPhase`、`ProjectRequirement` 落库、Drive 目录树、Project Charter 模板文档生成 |
| 4 | **Mission 结构化** | **MISSING** | 无 Mission 概念。Project = 最高容器 |
| 5 | **Planner（需求分析）** | **PARTIAL** | 结构化路径：`requirements_analysis` 阶段 + `_generate_project_charter` **模板文档**，无 LLM。legacy 路径：`TaskKind.planning` 任务派给 PM，但结果是硬编码 `GRAPH_TEMPLATE` |
| 6 | **Agent selection** | **MISSING** | 结构化路径**不选任何人**。legacy 路径按 `position_compat.employee_by_legacy_role` 固定角色映射 |
| 7 | **Task decomposition** | **PARTIAL** | 结构化路径：`design review approved` → `_ensure_development_tasks` 生成 **5 个硬编码任务**（Game Engine / Input Control / Score System / User Interface / Automated Tests），全部 `backlog` + `assignee=engineer`。legacy 路径：4 个固定阶段任务 |
| 8 | **Agent execution** | **NOT IMPLEMENTED（本路径）** | 结构化路径的 DEV 任务 `backlog` ⇒ orchestrator 的 `list_tasks_by_status(todo)` **不会派发**；人类点 `complete_phase(development)` 时被**强制批量标 done**：<br>`if task.phase_id == phase.id and task.status != done: task.status = done` |
| 9 | **Agent coordination** | **NOT IMPLEMENTED** | 无 coordinator / 无 handoff / 无通信 |
| 10 | **Artifact** | **PARTIAL（模板）** | `_generate_development_outputs` → `source_manifest` / `build_manifest`（Markdown）+ 若 `tutorial_accelerated` 则额外塞一个 zip 源码包与生产构建包；**内容全部模板字符串**，非真实实现 |
| 11 | **Review** | **PARTIAL（人类）** | 3 个人工评审门（requirements / design / acceptance），`POST /reviews/{id}/decision`，生成 Baseline + Minutes DOCX + PPTX ReviewPackage |
| 12 | **Delivery** | **PARTIAL（模板）** | `delivery` 阶段：`_generate_delivery_package`（User Manual / Deployment Manual / Test Report / Checklist，DOCX）+ `DeliveryPackage` 行 + `project_archive` 自动 completed |
| 13 | **真实可运行产物** | **NOT IMPLEMENTED** | 交付物是文档与清单，**没有可运行应用**。`_source_code_archive` 只在 `tutorial_accelerated=True` 时生成（教程专用，含 Snake） |
| 14 | **Experience 回到 Agent** | **NOT IMPLEMENTED（本路径）** | 结构化路径无 Agent、无 WorkSession、无 SkillUsage；`task.completed` 由 `complete_phase` 直接标 done（**不发 bus 事件、不走证据流水线**）。唯一成长来自人类评审决定 → `review.*` 证据只对 presenter 生效 |

### 真实链路（当前系统真能走通的）

**A. UI 可走通的（结构化交付，无 Agent）**

```text
用户填 6 步向导
 → POST /projects（is_structured=True）
 → 11 ProjectPhase（initiation/requirements_analysis 已就绪）
 → 人类 complete_phase × N + 人类 review decision × 3
 → document_generation_service 模板生成 Markdown/DOCX/PPTX/ZIP
 → DeliveryPackage + Baseline + project.completed
 →（可选）project.completed 触发 project_end 自动考核 → 证据 → 能力
```

**B. 能真跑 Agent 的（legacy，无 UI 入口）**

```text
POST /projects {"name","description"}（不带 code/background/objectives/requirements）
 → order_review Task(ceo, todo) → orchestrator dispatch
 → WorkSession + RuntimeAdapter(mock|hermes|openclaw)
 → planning Task(pm) → GRAPH_TEMPLATE 4 Milestone+Task（research→dev→test→final_review）
 → 按 kind 逐阶段 todo → 派发给对应角色的 employee → 产出 artifact 到 Drive
 → testing 失败 ⇒ development 回 todo 重做
 → final_review 成功 ⇒ project.completed
 → 反思 + 知识 + 技能 + 证据（真实闭环）
```

**C. 经济闭环（真能赚钱/花钱，与 Agent 工作无关）**

```text
注册 → STARTER_GRANT(mint)
 → /work-orders 领官方订单 → 手打 summary 提交 → auto Evaluation → Settlement(mint)
 → 或 POST /work-orders 发布玩家订单（Escrow 锁资）→ 他公司领取 → 提交 → 结算（转账，不 mint）
 → /market 挂牌 + commercial terms → 他公司出价 → Contract 锁资 → 招募 → 多腿放款
```

**结论**：**「用户提出复杂问题 → 多 Agent 自主完成」这条链在当前系统中不存在。** 存在的是三条互不连接的链：模板化文档交付链、固定 4 阶段 mock 流水线、经济结算链。

---

## 17. Existing Systems We Should Stop Expanding

以下系统已达到 **GOOD ENOUGH**，继续投入的边际收益低于机会成本：

| 系统 | 判据 |
| --- | --- |
| **M1 经济 / Ledger / Escrow / Contract / Settlement** | 已 FROZEN；E1–E31 有锚点测试；失败注入 + 并发矩阵 + 全库复式平衡 E2E。**继续加币种/金融衍生品不会让"+1 个真实任务"变成可能** |
| **T2 人才市场（本地）** | 已 FROZEN；26 步 Golden Path E2E；I1–I13 不变量有测试。缺的不是市场机制，而是「买来的人能不能干活」 |
| **Cultivation（T1）** | 三模板 + 际遇 + 确定性 RNG + 显式结业 + 教育证据分级 + 完整 UI。**再加深培养数值只会放大「培养完也没用」的空转** |
| **Competency / Evidence 引擎** | 10 类来源 + 策略集中 + mock 打折 + 无期望不造证据 + reconcile 幂等。**它已经在等上游产生真实工作事实** |
| **Position / Slot / Assignment 域** | 时间轴 + 部分唯一索引 + 派生占用态 + AST 守卫（禁止读 `employee.role`）。组织侧已经足够 |
| **Lifecycle / Provisioning** | 四层根因已修（超时兜底 + 启动补收敛 + 可跳过 + ON CONFLICT 幂等）；有 resilience 测试 |
| **Tutorial 引擎** | 领域状态驱动 + 前后端不伪造 + 实机 17 步走查。**不该再往里加步骤** |
| **Provider / Secret / RuntimeImage 更新回滚** | 11 类厂商预设 + 探测 + Fernet 加密 + managed update + 回滚。**codex/claude_code/opencode adapter 在没有 Mission 之前不值得实现** |

---

## 18. Top 10 Core Product Gaps

> 排序标准：**对「多 Agent 帮用户完成真实复杂任务」的阻碍程度**，不是代码洁癖。

| # | Gap | 为什么它排在前面 | 事实依据 |
| --- | --- | --- | --- |
| **1** | **没有「用户问题 → 结构化工作定义」这一层**（Mission/Requirement 缺失） | 现在用户输入只能落成 Project（结构化交付模板）或 legacy order（固定 4 阶段）。没有任何实体表达「要解决什么问题 + 成功标准 + 约束」并驱动后续一切 | 无 Mission；`Project.requirements` 只在结构化路径，且不驱动执行 |
| **2** | **没有 Task → Agent 的动态分配**（无 Task Fit / 能力需求） | 分配是 `position_compat.employee_by_legacy_role` 的字符串映射。多 Agent 组队的前提不存在 | `_generate_graph`、`create_order`；Fit 引擎未接入 |
| **3** | **没有真正的 Planner**（任务拆解是硬编码常量） | `GRAPH_TEMPLATE` 永远 4 阶段 1 任务/阶段。复杂问题无法被拆成可并行的子工作 | `orchestrator.py:GRAPH_TEMPLATE` |
| **4** | **没有 Artifact / 结果在 Agent 间的传递（handoff）** | 后继任务的 `description` 是同一段原文；B 看不到 A 的产出。多 Agent 协作在信息论上不成立 | `_generate_graph(description=project.source_order_text)` |
| **5** | **没有 Review / Quality Gate（任务级）** | `in_review → done` 自动连跳。没有质量判定 ⇒ 没有 rework ⇒ 没有「做不对就重做」的能力 | `_finalize` |
| **6** | **WorkOrder 与执行系统零桥接** | 经济侧的 26 步闭环与执行侧的 4 阶段流水线**没有一条边相连**。官方/玩家订单没有任何代码创建 Task/Project | `work_orders.py::submit(project_id, artifact_refs)` 是无校验自由字段 |
| **7** | **招募/购买得到的人不能立即执行** | 人才市场投入巨大的闭环终点是一个 `runtime_type=mock`、无 runtime instance、无 provider 的 Employee | `recruitment.py` 注释「本阶段不做全链开通」 |
| **8** | **没有执行可见性 / 可回放** | `WorkSession` 只有 1 行汇总；RuntimeEvent 不落库；`tokens: 0` 硬编码；无续跑/恢复。真实长任务失败后无法诊断 | `models/project.py::WorkSession`、`orchestrator._finalize` |
| **9** | **没有失败恢复 / 重试策略 / Replan** | 唯一 retry 是 testing→development 硬编码回退；`task.failed` 之后无重试计数、无退避、无 human escalation | `_advance` |
| **10** | **两条 Project 路径并存，UI 走的是不跑 Agent 的那条** | 用户/开发者对「Eidolon 能不能跑 Agent」的认知与实际相反；任何新工作都会先撞上这个分叉 | `create_order` vs `create_structured_project`；`ProjectCreate.is_structured` |

---

## 19. Gameplay Gaps

> 核心能工作但不好玩。

1. **Agent 无「战绩/信誉」可感知**：市场只展示能力分与 Fit；没有完成率、交付质量、协作记录、领域专精度。玩家无法说「这个 Agent 很可靠」。
2. **任务默认不可见**：无 WorkSession 详情、无执行日志、无实时进度条（只有 employee.status 脉冲）。
3. **无任务推荐 / 无难度匹配**：WorkOrder 列表无「我该做哪个」的推荐；无难度标签与 Agent 能力的匹配提示。
4. **成长反馈延迟且不可见**：任务完成 → 证据 → 聚合 → 能力分变化，玩家看不到这条因果。
5. **市场体验薄**：无筛选/排序/对比、无「同价位对比」、无收藏/关注。
6. **公司经营无收入侧**：Project 不产生收入（只有 WorkOrder 有），公司财务只能靠接单与卖人。
7. **Office 是列表不是场景**：`/office` 是部门分区网格；Phaser 只用在登录页背景。
8. **无社交/排行/成就**：`achievement` 奖励类型存在但没有成就系统。
9. **无 24/7 世界感**：除 NPC 经济 CLI 与镜像更新检查外，没有后台"世界活动"循环。

---

## 20. Technical Debt

> **不与产品 Blocker 混淆**。

1. **`artifacts` 表已废弃但完整保留**（model + schema + 2 个 API + 兼容 service；`task_id` 恒 None）。
2. **`positions` 表（v0.4 legacy）** 与 `position_definitions`/`position_slots` 并存；仅 1 处写入。
3. **双写镜像列遍地**：`employee_id` ↔ `person_id`（knowledge/skill/learning/evidence/brain/runtime/artifact/message/drive/document_artifacts/review_meetings），全部注释为 deprecated 但保留。`Employee.slug` 是 `Person.slug` 的镜像。
4. **`EmployeeBrain.curiosity`** 是 `traits` 的 legacy 镜像列。
5. **`CompetencyEvidence.quality`** 是 P5 旧口径，与 P6 的 `strength/reliability` 并存。
6. **`position_access_sync` / `evidence_pipeline_enabled` / `economy_cost_consumers_enabled` / `autonomous_learning_enabled` 全是配置开关**，生产/测试默认值不同 ⇒ 后台消费者与测试抢状态的经典风险（已在 conftest 里用环境变量关掉，属于"靠约定"而非"靠结构"）。
7. **`freshness_status` 无任何置 stale 的驱动**（K2 只做了读取侧降权）。
8. **`_ensure_development_tasks` 与 `GRAPH_TEMPLATE` 两处硬编码任务生成**，语义重叠。
9. **`_finalize` 里内联 `from app.services.economy.costs import ComputeCostService`**（循环依赖规避的局部 import，散落多处）。
10. **`Orchestrator.stop()` 用 MockAdapter 停所有实例**：`gateway.stop_all()` 里 `self.adapter_for(RuntimeType.mock).stop(instance)` —— 对真实 hermes/openclaw 实例是错的操作（虽然当前 mock 模式下无实际危害）。
11. **孤儿容器**：`eidolon-hermes-ada-891d91` 在跑但 DB 无行；`drop_instance`/`stop_all` 不清理容器。
12. **`tmp/` 未被 gitignore**，64 个未跟踪文件混在工作树里。
13. **`docs/handover.md §4` 的 WIP 文件清单过期**（6 → 5，`employees.py` 已格式化）；`§5b` 的 `alembic head` 与 migration 计数与当前不一致（写 `64fec2d13d9b` 正确，但 §3 迁移链只列到 v20）。
14. **`WorkSession.cost.tokens` 恒为 0**；`runtime_cost_usd` 在前端用 `attempts × 0.05` 硬编码估算。
15. **`chunk >500 kB`** 构建警告（`index` 693kB、`create-game` 1.22MB）。
16. **`pytest` 118 warnings**（未归零）。

---

## 21. Duplicate Domain / Source-of-Truth Risks

| 风险对 | 严重度 | 事实 |
| --- | --- | --- |
| **Project(简单路径) vs Project(结构化路径)** | 🔴 **最高** | 同一张 `projects` 表，两套互斥语义；`is_structured` 决定走哪条；两条路径的任务/文档/评审/完成条件完全不同。**任何新功能都必须先决定挂在哪条上** |
| **WorkOrder vs Task/Project** | 🔴 高 | 两套「任务」概念：WorkOrder 是**市场契约**（有雇主/赏金/托管），Task 是**执行单元**（有 assignee/依赖/产物）。二者**零桥接**，`submit.project_id` 是无校验自由字段 |
| **ProjectTask vs WorkOrderTask vs DEV-00x** | 🔴 高 | 系统里有三处"任务"命名空间：`tasks`（orchestrator 图）、`work_order_submissions`（交付尝试）、`_ensure_development_tasks` 的 DEV-001..005（结构化交付）。三者互不感知 |
| **Agent vs Person vs Employee** | 🟡 中 | 概念已澄清（§4），但**命名混乱**：README/代码注释用"Agent"、DB 用 employee、person 口径资产用 person_id。**新代码容易读错归属** |
| **Evaluation vs Assessment vs ReviewMeeting vs Task review** | 🟡 中 | 四套"评价"语义（§10）分布在四个模块，无统一接口。命名冲突（`eval`/`assess`/`review`）在代码中会互相误导 |
| **Artifact vs Knowledge** | 🟢 低 | 分区清晰（Drive 是产物，KnowledgeItem 是知识），但**知识晋升会把 KnowledgeItem 物化成 Drive 文档**（`knowledge/promotion.py`），同一内容两个身份、无双向一致性约束 |
| **Workflow vs Event Engine** | 🟢 低 | 已明确分工（orchestrator 消费 dispatch signal，engine 只做分发/保序/重试）。但 `orchestrator` **自己也是一个事件消费者**（`notify`），与 E0 引擎是两套并发模型 |
| **Position(lifecycle) vs PositionDefinition/Slot** | 🟡 中 | 两张 `positions`/`position_definitions` 表并存（§3.6），legacy 表仍在写入 |
| **`drive_nodes` vs `document_artifacts`** | 🟡 中 | 同一份文档两个身份：Drive 是内容真相，`document_artifacts` 是"正式交付元数据"。版本要两处同步（`drive_revision_id`），无一致性校验 |
| **`MarketListing.quality_tier` vs 真实能力** | 🟢 低 | 已明确「不是战力」，有守卫测试 |
| **`Employee.role` vs `PositionDefinition.legacy_role`** | 🟢 低 | 已被 `position_compat` 单点收口 + AST 守卫 |
| **`persons.slug` vs `employees.slug`** | 🟢 低 | 已声明 person 权威、employee 镜像，但唯一索引在 employee 上（`slug` UNIQUE） |

---

## 22. Reuse Map（现有系统如何服务未来核心玩法）

```text
E0 事件引擎（分区并发 + 注册表 + 重试/死信 + 启动补收敛）
      ↓
   任何「异步多步骤、需要保序与失败隔离」的执行调度都能挂上去
   （现成 handler：position-access / evidence-pipeline / economy-cost）

K1/K2 知识检索（scope 分层 + FTS5 + freshness 降权 + 公司隔离）
      ↓
   Mission / Task 上下文的「组织知识注入」层
   （retrieval.retrieve_for_task 已经是 policy 驱动的纯函数）

T1 Cultivation（模板 + 际遇 + 教育证据分级 + 确定性 RNG）
      ↓
   Agent Growth：任务之外的第二条成长来源；也是「人才供给」的生成器
   （证据分级与真实工作证据天然隔离，有守卫）

T2 Fit 引擎（requirement→evaluate→normalize→hash→policy）
      ↓
   Team Formation / Task Assignment —— 只需把 requirement 源从
   PositionProfileVersion 换成 Task/Mission requirement，引擎无需重写

M1 WorkOrder / Contract / Escrow / Settlement / Reward
      ↓
   Task Marketplace 与 Mission Reward 的结算层（已完整，缺的是上游事件）

M1 经营成本（compute_usage + ledger category）
      ↓
   Agent 执行的「真实成本记账」；等执行链产生可计量数据

P5/P6 Competency + Evidence 引擎（10 类来源 + 策略集中 + 幂等 reconcile）
      ↓
   Task Experience → Evidence Promotion 的唯一入口（已就绪，等真实工作事实）

P4d Position / Slot / Assignment（时间轴 + 派生占用）
      ↓
   团队组建的「组织约束」层（谁在哪个编制、能不能兼）

v0.4 Lifecycle / Provisioning（entitlements + 资源账户 + 幂等开通）
      ↓
   Agent 执行环境的「供给管道」（现在缺的是招募后的自动触发）

Drive + DriveRevision（版本 + sha256 + work_session 回链）
      ↓
   Artifact / Delivery 的现成存储与版本层（只需补 lineage / task 维度）

Runtime Gateway + Adapter ABC + capabilities 诚实位
      ↓
   Agent 执行的执行层（mock/hermes/openclaw 已可用；强制 mock 开关需按场景放开）

Tutorial 引擎（领域状态驱动 + 不伪造 + 聚光交互）
      ↓
   新玩法的引导层（复用同一套 requirement/facts 机制）

v0.7 Auth + CompanyMembership + resolve_company_id
      ↓
   多公司与权限边界（现单公司部署假设）
```

**反向结论：Eidolon 不缺地基，缺的是把地基连起来的那一层。**

---

## 23. Evaluation of Proposed Product Direction

> 待评估方向：Agent 是核心资产；Mission 是核心工作；Artifact 是核心交付；Company 是组织容器；Economy 是激励与结算基础；当前不做公司交易/股权金融；核心目标是多 Agent 自主完成现实复杂任务。

### 支持点

1. **Agent 作为资产**：代码已经具备资产所需的**人级持久层**——identity（person.slug + identity_id）、persona（8 维 traits）、knowledge（scope 分层）、skills（成功率）、evidence/competency（score+confidence+trend）、career events、learning records。且 R1/T2 已用不变量测试钉死「人级资产不随任职/培训/交易被复制或重置」（I4/I5/I7）。**这是一份成熟的资产底座。**
2. **Company 作为组织容器**：现状已经是「Agent / Knowledge / Wallet / Position 的容器」，且**没有公司估值/股权/交易**——与「暂不做公司金融」的假设**天然一致**，无需改架构。
3. **Economy 作为激励与结算基础**：Ledger/Escrow/Contract/Settlement/Reward 已冻结，**足够支撑任务市场与 Mission 结算**，且玩家间转移绝不 mint 的纪律已在 E8 钉死。
4. **Artifact 作为交付**：Drive + DriveRevision 已提供内容、版本、hash、work_session 回链。这是能直接复用的交付层。
5. **不做公司交易/股权金融**：现有代码里**一行都没有**，与 M1 FROZEN 面（§39b 明确把股权分红外推到 M2+）一致。这是**零成本的路线收敛**。

### 冲突点

1. **「Mission 是核心工作」与现有架构冲突**：现在的核心工作对象有两种（Project 结构化交付 / WorkOrder 市场契约），**都不是 Mission**。如果直接新建 Mission 实体，会立刻产生**第四套任务实体**（Project/Task 图 + WorkOrder + DEV-00x + Mission）——这正是 §21 里最严重的重复真相风险，也是用户在 §34 明确警告要避免的。
2. **「多 Agent 自主」与现有 4 条硬编码串联冲突**：`GRAPH_TEMPLATE`、`_generate_graph`、`_ensure_development_tasks`、`_advance` 的 kind 分支，全部假设「阶段 = 任务 = 角色」固定三元组。自主协作需要的动态拆解/依赖/分配，与它们是**局部改造 vs 重写**的关系，不是扩展关系。
3. **「Agent 是资产生命周期」与 Runtime 归属冲突**：资产的持久层在 **person** 口径，执行配置在 **employee** 口径。若要把「Agent」做成一等对象，必须显式决定它是 person 级的（跨公司可携带）还是 employee 级的（属于公司）。**现在市场交易的是 person，执行的是 employee。**这是必须先做的概念裁决。
4. **「真实验复杂任务」与强制 mock 冲突**：`EIDOLON_RUNTIME_MODE=mock` 时 `adapter_for()` 无条件返回 MockAdapter。要走真实任务，必须放开这个开关，并接受成本/失败/超时/并发/SQLite 单写者的现实约束。

### 需要调整点

1. **不要新建第四套任务实体**。应先在 `Project`（或 `WorkOrder`）上判定谁承担 Mission 语义，再做最小扩展。`Project` 已有 requirements/participants/deliverables/review_configuration/background/constraints，**结构上已接近 Mission Spec**，只是没有能力需求与执行绑定。
2. **Artifact 需要补的只有两件事**：`task_id` 维度（现在 DriveNode 只有 `work_session_id`）与 lineage（上游产物 → 下游输入）。不要新建 Artifact 表。
3. **Agent 成长与市场价值之间需要一条显式连接**（现在完全断开：市场只看 quality_tier + Position Fit）。这是「Agent 是资产」能否成立的关键。但注意：**不要改 T2 已冻结契约定价面**，应作为新的**读面/派生指标**加入。
4. **执行层必须先有 Review/Quality Gate**，否则「任务 → 成长」是单向且盲目的（现在 `success` 只看 adapter 有没有报 error）。

---

## 24. Readiness Matrix

| 能力 | 状态 | 依据 |
| --- | --- | --- |
| Problem Intake | **PARTIAL** | 有 6 步立项向导（结构化交付语义），但产出的是文档流程输入，不是可执行工作定义 |
| MissionSpec | **MISSING** | 无 Mission 实体；Project 有近似字段但不驱动执行 |
| Capability Requirement | **PARTIAL** | 只有职位级 `PositionCompetencyRequirement`（critical/required/preferred + min/target/weight）；任务级只有 `TASK_KIND_HINTS` 硬编码 |
| Single Agent Fit | **READY** | `talent/fit/engine.py`，person/employee 双路径对拍一致，市场投影白名单 |
| Team Fit | **MISSING** | 无任何实现 |
| Team Formation | **MISSING** | 无 |
| Task Decomposition | **MISSING**（有 PARTIAL 替身） | `GRAPH_TEMPLATE` 固定 4 阶段 = 常量；`_ensure_development_tasks` 固定 5 条。无 LLM/规则拆解器 |
| Execution DAG | **PARTIAL** | `task_dependencies` 表存在且 orchestrator 会用 `_unblock_dependents`；但只有 3 条硬编码边，无拓扑执行、无并行扇出 |
| Agent Assignment | **PARTIAL** | 可派（`todo` + assignee + idle + 无 running session），但选择逻辑是角色字符串映射，非能力 |
| Multi-Agent Execution | **PARTIAL** | 并发执行是真实的（每员工 1 session，多员工并行），但**无协作** |
| Shared Workspace | **PARTIAL** | Drive 有 company scope + `DriveCollaborator` 表（viewer/editor）**但无任何写入点**；workspace 是 per-employee 目录，无共享区 |
| Artifact | **READY**（存储层） | Drive + DriveRevision + sha256 + 版本 + work_session 回链 |
| Handoff | **MISSING** | 无 |
| Review | **PARTIAL** | 3 套真实评审（人类 ReviewMeeting / M1 Evaluation / Assessment），**任务级缺失** |
| Evaluation | **PARTIAL** | 无任务级质量判定；`success` = adapter 没报 error |
| Rework | **PARTIAL** | 仅 testing→development 硬编码一条；WorkOrder 层有 attempt 递增重提 |
| Replan | **MISSING** | 无 |
| Final Delivery | **PARTIAL** | `DeliveryPackage` + `Baseline` + 交付包文档（**模板生成**），仅在结构化路径 |
| Task Experience | **PARTIAL** | `WorkSession` + `SkillUsage(success)` + `LearningRecord(reflection)`；但结构化路径完全绕过（不走事件、不走证据） |
| Evidence Promotion | **READY** | `EvidencePipeline` + 10 类 source + policy 集中 + reconcile 幂等 + mock 打折 |
| Agent Growth | **PARTIAL** | Evidence→Competency 完整；但产出**不回写市场/可交易价值**，且 `person timeline` 只有教育事件（工作履历在 career_events） |
| Official Task Integration | **MISSING** | WorkOrder 与 Task/Project/Orchestrator 零连接 |
| Player Task Integration | **MISSING** | 同上；`submit.project_id/artifact_refs` 是无校验自由字段 |
| Agent Marketplace Integration | **PARTIAL** | 市场→招募→Employee 是真实的；**招募→可执行 Agent 缺失**（无 runtime/provider/workspace 自动开通） |

---

## 25. Recommended Next Mainline

### 🎯 主推荐：**「把 Project 变成可执行的 Mission：需求 → 能力需求 → 组队 → 动态拆解 → 执行 → 评审 → 交付」——复用 Project/Task/Orchestrator，不新建第四套实体**

**为什么选它**
- 它是**当前唯一能把已有 7 大子系统（Fit / Knowledge / Evidence / Economy / Drive / Runtime / Event Engine）串成一个玩法的层**。其余所有候选都是在已经 GOOD ENOUGH 的模块上继续加码。
- 它直接消灭 Top 10 里的 #1–#5、#10 五个 blocker。
- 它与用户提出的三核心对象（Agent / Mission / Artifact）**完全对齐**，且不需要推翻任何已冻结契约。

**现有可复用地基**
- `Project` 已有 requirements/objectives/constraints/deliverables/participants/review_configuration/background（**MissionSpec 的 80% 字段已在**）。
- `Task` + `TaskDependency` + `TaskStatus` + `WorkSession` + `Orchestrator` + `RuntimeAdapter`（**执行底座已在**，只是决策层是常量）。
- `talent/fit/engine.py`（**通用 requirement 评估器已在**，换 requirement 源即可）。
- `Drive` + `DriveRevision`（**Artifact 存储已在**）。
- `EvidencePipeline`（**成长入口已在**，只等真实工作事实）。
- `EventEngine`（**异步编排底座已在**）。
- `Ledger/Escrow/Contract`（**结算已在**）。
- `workforce/lifecycle` provisioning（**环境开通管道已在**）。

**核心缺口（必须新做的，按依赖顺序）**
1. Mission/Task 级 **CapabilityRequirement**（复用 Fit 的 requirement 结构）。
2. **Task Decomposition 决策层**（把 `GRAPH_TEMPLATE` 常量换成可插拔的分解器，第一版可以是规则 + 能力需求驱动，不必是 LLM）。
3. **Assignment 决策层**（`position_compat` → Fit + 负载 + 可用性）。
4. **Artifact handoff**（Task 输入 = 上游 Task 的 Artifact 引用；DriveNode 补 task 维度 + lineage）。
5. **Task-level Review Gate**（替换 `in_review → done` 自动连跳）。
6. **两条 Project 路径收敛决策**（这必须先于以上任何一项，否则会加重 §21 的头号风险）。

**风险**
- **最大风险是「第四套任务实体」**：如果 Mission 被新建为独立实体而不是 Project 的语义升级，重复真相会从 2 套变 4 套，后续每一步都更贵。
- **次要风险是 mock/real 切换**：真实 runtime 会带来成本、超时、失败率、并发与 SQLite 单写者问题，`Orchestrator` 目前没有这些语义（无重试计数、无超时、无事件流持久化）。
- **第三风险是工作量**：`orchestrator.py` 只有 565 行且高度耦合（dispatch/finalize/advance/publish 全在一个类里），改造会先撞上它自身的结构。

### 备选 1（若优先「Agent 作为资产」）：**Agent 资产生命周期闭环 —— 招募即开通 + 战绩回写价值**

- 理由：T2 已经把所有供给侧做完了，但「买来的人不能干活」直接否定了整个市场的意义。
- 地基：`recruitment.py`（招募）+ `lifecycle/provisioning`（开通）+ `runtime_instances`/`model_bindings`（环境）+ `competency_evidence`（战绩）。
- 缺口：招募后自动 provisioning 编排、Provider 继承策略、Workspace 建立、以及**一个用真实任务数据（完成率/交付质量/领域专精/协作）计算的 Agent 价值读面**。
- 风险：无 Mission 的话，战绩来源仍然只有固定 4 阶段流水线与 WorkOrder 手填 —— **可能只是把同一个空转包装得更漂亮**。建议作为主推荐的**同步小切口**而非独立主线。

### 备选 2（若优先「经济玩法」）：**WorkOrder ↔ 执行桥接**

- 理由：M1 已经是全项目最完整的子系统，只差一条边（订单被接受 → 生成工作 → 交付回填 → 验收）。
- 地基：`work_orders.py`（全生命周期）+ Escrow + Evaluation + `task`/`project` 实体。
- 缺口：`accepted` 事件 → 创建工作 → 执行 → 提交时校验 `project_id`/`artifact_refs` 真实性。
- 风险：**它其实是主推荐的一个子集**——如果先做它，会先把 Project/Task 的执行语义固定下来，可能反过来约束 Mission 设计。**除非明确只做「官方 bounty」这种小工作，否则不建议先做。**

---

## 26. Questions That Must Be Decided Before Planning

> 只列**产品/方向决策**，代码自己能判断的一律不列。

1. **Mission 的载体是什么？** Project 语义升级 / WorkOrder 语义升级 / 新实体（承担第四套实体的风险）？
2. **Agent 的身份归属是哪一层？** person 级（可跨公司携带，与市场交易一致）还是 employee 级（属于公司，与执行一致）？现在的分裂必须被裁决。
3. **两条 Project 路径如何处理？** 收敛为一条、并行保留（并明确各自定位）、还是废弃结构化交付路径？
4. **玩家的"真实复杂任务"具体指哪一类？** 「软件开发」/ 通用研究 / 内容生产 / 不限？这决定 CapabilityRequirement 的域与 Artifact 类型体系。
5. **第一版 Mission 的成功标准由谁定？** 用户手填 acceptance criteria / 系统生成 / 两者结合？这决定 Review Gate 的判定源。
6. **Review 由谁执行？** 人类 / Reviewer Agent / 确定性规则 / 组合？这决定是否需要「reviewer 也是一种 Agent 角色」。
7. **Agent 的价值如何被玩家感知与定价？** 新派生指标（完成率/质量/专精）/ 仍由玩家手填价格 / 两者组合？
8. **真实 Runtime 何时开？** 第一版 Mission 允许 mock 吗？如果允许，「完成真实问题」的承诺如何自洽？
9. **经济侧要不要给 Project 收入？** 现在只有 WorkOrder 产生收入；Mission 完成后是否有付款方（用户/NPC/官方）？
10. **是否符合预期：暂不做公司交易/股权？** 已确认（代码零实现，与路线一致）——但需要用户明确「何时可以重开」，以免后续反复。
11. **Agent 之间的上下文共享范围？** 项目内共享 / Mission 内共享 / 显式授权共享？这决定 Workspace 与 Knowledge scope 是否需要新维度。
12. **单个 Mission 的规模上限？** （任务数、并发 Agent 数、持续时长）这决定是否需要真正的 DAG 调度器，以及 SQLite 单写者是否够用。

---

## 附录 A：本次审计的验证命令（可复现）

```bash
git branch --show-current && git log -1 --format='%H%n%ad%n%s'
git status --porcelain -uall | wc -l
git rev-list --left-right --count origin/dev...HEAD

find apps/server/app -name '*.py' | xargs wc -l | tail -1
find apps/server/tests -name 'test_*.py' | wc -l
ls apps/server/migrations/versions/*.py | wc -l
find apps/web/src -name '*.ts' -o -name '*.tsx' | xargs wc -l | tail -1

python -m pytest apps/server/tests -q                 # 1018 passed, 6 deselected
python -m ruff check apps/server/app apps/server/tests
python -m ruff format --check apps/server/app apps/server/tests
cd apps/server && python -m alembic check && python -m alembic current
cd apps/web && pnpm exec tsc --noEmit && pnpm exec eslint . && pnpm exec prettier --check .
cd apps/web && pnpm exec vitest run && pnpm build

sqlite3 apps/server/data/eidolon.db ".tables"
sqlite3 apps/server/data/eidolon.db "SELECT id,employee_id,runtime_type,deployment_mode,container_name,status FROM runtime_instances;"
```

## 附录 B：UNKNOWN / 未能确认的事实

1. **集成测试（`tests/integration/*`）是否通过** —— 未运行（会产生 Docker 副作用）。Docker 可用、hermes 容器在跑，**推断可用但未验证**。
2. **真实 Hermes/OpenClaw 容器在 `EIDOLON_RUNTIME_MODE=auto` 下跑通完整 Task 的时延/成功率** —— 未实测。DB 里有真实 hermes 实例与 DeepSeek 绑定，说明曾被真实配置过。
3. **`tmp/tutorial-audit/` 的走查脚本**（`tmp/tutorial_audit.py`）当前是否仍可重跑 —— 未验证。
4. **`docs/handover.md` §1.6 声称的「17 步全部无功能性遮挡」** —— 未复验（截图存在）。
5. **`test_updates.py::test_managed_update_success` 的 flaky 问题是否仍存在** —— 本次全量跑绿，未做重复连跑验证。
6. **前端 i18n 中英逐键一致的完整性** —— 有测试覆盖（handover 声称），未逐键复核。
7. **生产部署路径**（`deployments/docker/*`、`docker-compose.yml`）是否可用 —— 未运行。
