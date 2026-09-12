# M2 冻结面（Agent Work & Organizational Runtime）

> **状态：FROZEN（2026-09-12）** —— M2.0–M2.10 全部落地，`dev` 分支上 **v45 `325887b7109a`** 是
> M2 的最后一个迁移。本文件是 M2 的**收口声明**：形态、不变量、唯一写入路径、模块边界、
> 关键裁决、以及留给 M3 的清单。
>
> 改这里的任何一条 = 改 M2 的契约。请当作与 `app/work/contracts.py::INVARIANTS` 同等严肃对待：
> 代码侧有 105 条不变量与 ~70 条反例注入守着它。

---

## 1. 形态

**核心论点**（M2 的全部设计都服务于这一句）：

```text
System provides facts.     系统给事实（就绪 / 可派发 / 职责 / 成本 / 交付物 / 缺口）
Agent makes decisions.     决定由 Agent 或人做（接不接、怎么拆、选谁、是否返工、是否交付）
System validates.          系统只做硬校验（状态机 / DAG / Authority / 引用完整性 / 公司隔离）
System executes.           系统负责执行（WorkSession → Runtime → Artifact）
System records.            系统记录事实（事件 / 审计 / 决策 / 证据 / 成本）
```

**四个实体，各管一段**（不新增第四个任务实体 —— W20/W21）：

| 实体 | 它是什么 | 它**不是**什么 |
| --- | --- | --- |
| `Project` | Canonical Executable Work Root（含 Canonical Spec + `work_mode` 快照）| 不是商业包装 |
| `Task` | 可执行工作单元（DAG 节点）| 不是"人类待办清单" |
| `WorkOrder` | 商业/经济需求（雇主、赏金、托管、验收、结算）| ❌ 执行图 ❌ Task 容器 |
| `DecisionRecord` | 管理决策的**审计记录** | ❌ 工作单元 ❌ 执行状态 |

**端到端路径**（M2.10 的三条黄金路径各自验证一段）：

```text
WorkOrder ACCEPTED →（M2.9 绑定边）→ Project →（M2.1 路由 / M2.2 RoleContext）
  → Manager 决策（M2.3 工具 + M2.4 决策信封）
  → DAG 就绪判定与派发（M2.5）
  → Artifact 归属与交接（M2.6）
  → Reviewer 结论（M2.7）
  → Evidence / Competency（既有 P6/T2 管道）
```

**两条正交的项目维度**（M2.1）：`work_mode`（guided | managed，产品行为，项目级快照）
与 `planning_fixture`（none | deterministic_template，基础设施，需显式请求 + 部署门控）。

---

## 2. 不变量

**105 条**，全部 `enforced=True`（M2.10 起不再有"冻结待锚点"的双态）。
真源：`apps/server/app/work/contracts.py::INVARIANTS`；人类可读表：`docs/m2-agent-work-runtime-design.md` §14。

| 家族 | 条数 | 主题 |
| --- | --- | --- |
| **W1–W42** | 42 | 工作/职位/权威的归属边界（谁决定什么、什么不能自动发生）|
| **T1–T12** | 12 | 管理工具面（Read Shared / Write Internal）|
| **DR1–DR10** | 10 | Decision Envelope（意图 / 执行事实 / 领域状态三层不混）|
| **R1–R12** | 12 | Canonical Task Graph Runtime（就绪 ≠ 可派发、系统不选人）|
| **H1–H8** | 8 | Artifact Handoff（归属 / 使用事实 / lineage / 只消费已完成产出）|
| **RV1–RV8** | 8 | 评审闭环（系统不产生结论、事实与结论分开存、ESCALATE 无目标）|
| **RD1–RD7** | 7 | Ready-to-Work（派生就绪、环境策略只配环境、失败不四舍五入）|
| **WO1–WO6** | 6 | WorkOrder ↔ Project 绑定边（只加边、引用受校验、桥不碰钱）|

**四类不变量各有机器守卫**（都在 `apps/server/tests/`）：

```text
契约一致性      test_m2_contract.py            锚点必须真实存在；文档与代码逐字一致
反例注入        5 套脚本（M2.5/6/7/8/9）       注入违规 → 对应测试转红 → 撤回 → 转绿（共 57 条）
AST 边界守卫    各测试模块内                   "某模块里不许出现某个调用/字段"
行为守卫        各测试模块内                   不选人 / 不自动通过 / 不自动 replan …
```

---

## 3. 唯一写入路径

**没有"第二套"。** 每个事实只有一个写入者（`同一个事实不留两个落点`）。

| 事实 | 唯一写入者 | 表/列 |
| --- | --- | --- |
| 项目（含 spec / work_mode 快照）| `services/projects.create_project()` | `projects` |
| 项目状态推进 | `Orchestrator._advance()`（系统事实）+ `reviews.replan_project()`（管理决定）| `projects.status` |
| Task 与依赖 | `services/tasks.create_task()` / `add_dependency()` | `tasks` / `task_dependencies` |
| Task 状态迁移 | `services/tasks.transition_task()`（唯一状态机入口）| `tasks.status` |
| 任务图校验 | `contracts.validate_task_graph()`（唯一校验器）| — |
| 就绪判定 | `contracts.resolve_ready_tasks()`（纯函数，唯一口径）| — |
| 可派发判定 | `app/work/dispatch.evaluate_dispatch()` | — |
| 输入声明 | `work/handoff.declare_inputs()` | `task_inputs` |
| 产出归属 | 产出时经 `services/artifacts.record_project_artifact(task_id=…)` | `drive_nodes.task_id` |
| 产物使用事实 | `work/handoff.record_consumed()` / `consume_artifact()` | `artifact_links` |
| 评审请求与结论 | `work/reviews.open_review_request()` / `submit_verdict()` | `review_requests` |
| 评审事实 | `work/review_facts.collect_facts()`（系统写）| `review_facts` |
| 运行时策略 | `work/readiness.set_company_runtime_defaults()` | `companies.settings["runtime_defaults"]` |
| 环境编排 | `work/readiness.orchestrate()`（步骤落 `provisioning_jobs/steps`）| 既有生命周期表 |
| WorkOrder 绑定边 | `work/work_order_bridge.py`（指针 + 历史同一事务）| `work_order_project_links` + `work_orders.project_id` |
| Authority | `work/authority.grant_authority()` / `revoke_authority()`（append-only + 时间窗）| `position_authority_grants` |
| 决策与执行事实 | `work/decisions.submit_envelope()` → `work/tool_executor.execute_tool()` | `decision_records` / `tool_audits` |

**事件集是封闭的**（M2.5 起）：`contracts.FACT_EVENTS`（事实通告）与
`contracts.DECISION_NEEDED_EVENTS`（需要管理层介入），编排器的发布口有**运行时断言**——
未登记的事件没有出路。

---

## 4. 模块边界

```text
app/work/             M2 契约与域逻辑（**不拥有**业务真相：写操作一律经 services/*）
  contracts.py        唯一契约源（枚举 re-export / 不变量 / 事件集 / 校验器）
  dispatch.py         "System schedules" 的唯一实现
  handoff.py          Artifact 交接（声明 / 解析 / 使用事实 / lineage）
  reviews.py          评审闭环（事实 / 结论 / 映射 / replan 权限）
  readiness.py        就绪事实 + 环境策略 + 编排（不 commit）
  work_order_bridge.py WorkOrder ↔ Project 绑定边
  tools.py            工具注册表与参数校验（含 json_safe）
  tool_reads.py       只读事实工具（复用既有查询服务）
  tool_writes.py      受校验的写工具（复用既有领域服务）
  tool_executor.py    执行五步（transport → args → authority → autonomy → apply+audit）
  planning_fixture.py 确定性规划替身（测试/教程/CI；**只建图**）
  review_fixture.py   确定性评审替身（同款门控；结论署在人头上）
app/api/v1/          HTTP 面（与工具**共用**同一 application service —— T2/T11）
app/services/*       领域服务（经济 / 项目 / 任务 / 驱动 / 人员 …）—— 真相所在
app/lifecycle/       开通与资源（provisioning 计划与执行）
app/models/*         表结构（**只加必要列/表**；M2 的 5 个迁移见 §5）
```

**依赖方向**（禁止反向）：

```text
api/v1  →  services  →  models/repositories
work/*  →  services / repositories（写操作**必须**经 services；可读直查 repo）
services/*  ✗→  work/*（例外：`services/projects.py` 只调用 fixture 与 work_defaults 的**纯函数**；
                      `services/artifacts.py` 提供中性引用解析给经济域复用）
```

**M2 的 5 个迁移**（全部 additive + 事实驱动回填）：

| 版本 | 内容 |
| --- | --- |
| v40 `a1c2e3f40517` | Canonical Project Spec（7 列 + 索引 + 回填）|
| v41 `b2d4f6a8c013` | `position_authority_grants` / `position_definition_resources` / `advisory_scope` |
| v42 `c3e5a7b9d124` | `decision_records` + `tool_audits` |
| v43 `43a70cd19cc7` | Artifact Handoff（`drive_nodes.task_id` / `tasks.produces_json` / `task_inputs` / `artifact_links`）|
| v44 `b3aee926425c` | `review_requests` / `review_facts` / `tasks.rework_count` |
| v45 `325887b7109a` | `work_order_project_links` + `ix_work_orders_project_id` |

---

## 5. 关键裁决

M2 的裁决记录见 `docs/m2-agent-work-runtime-design.md` §17（M2-ADR-1..35）与
`docs/m2-implementation-plan.md` §6（B1–B12，用户拍板）。冻结时特别强调六条：

1. **`Project` 是唯一执行根**（W22）；`WorkOrder` 只是商业包装 + 一条绑定边（W23）。
2. **系统永不选人**（W1/R1/R2）：就绪但没人负责 ⇒ 上报 `task.assignment_required`；
   负责人不可用 ⇒ 上报原因，不自动改派。
3. **系统永不做管理判断**（W2/W17/RV1）：不拆解、不通过评审、不自动 replan；
   失败 ⇒ `project.replan_required`，等管理层。
4. **Authority ≠ Autonomy**（T 系列）：`requires_confirmation` 的动作在 M2 **一律拒绝**
   （没有确认通道）；高影响动作刻意不在工具面里（T3/`HIGH_IMPACT_AUTHORITIES_RESERVED`）。
5. **事实与判断分开存**：`review_facts`（系统）vs `review_requests.verdict`（人）；
   绑定边里只放"投递事实"，交付意图是否吻合由管理层判断。
6. **fixture 只替**（D3/W33）：确定性规划与确定性评审都是**门控基础设施**，
   替掉的是 Agent 的决定，且**结论署名在人头上**；生产项目永不落到它们头上。

---

## 6. 留给 M3

**明确不在 M2 范围内**（设计 §15 + plan §2"不做"），M3 需要时**单独立项 + 单独 ADR**：

| # | 事项 | 现状 / 为什么留给 M3 |
| --- | --- | --- |
| 1 | 真实运行时拉起（docker/容器 + 健康检查闭环）| M2.8 只**登记**实例（`created`/`unknown`）；拉起归运行时管理器 |
| 2 | 版本级 Artifact lineage（B 修改 A 的文档）| M2.6 只追到"哪个 Task 的产物" |
| 3 | 评审升级通道（`ESCALATE` 之后谁接手）| M2.7 停在原地并叫醒管理层；具体流程未定义 |
| 4 | 绑定换绑 / 撤销（WorkOrder ↔ Project）| M2.9 是一次性绑定；换绑需要新语义 |
| 5 | 玩家/人类面的评审与绑定 UI | 只有 API 与工具面；前端未做面板 |
| 6 | Authority 门禁覆盖到 M1 面（绑定 / 验收端点）| 这些端点目前只做登录 + 公司隔离 |
| 7 | 能力缺口四选一的完整闭环（学 / 调 / 招 / 改方案）| M2.10 场景 B 验证了"系统不替它选 + 留下决定"；四条路径的**工具化**未做 |
| 8 | 历史脏值清理（`work_orders.project_id` 无效引用等）| M2 的纪律是"不猜、不美化"；清理要人工立项 |
| 9 | 经济/资产/复杂金融扩展（股权、买卖、融资…）| 用户明令不做（设计 §15）|
| 10 | 中央 Planner / 自动组队 / 自动辞退 / 个人化 prompt 注入 | 用户明令不做（W1/W2/W13/W26）|

**M3 开工前的三条纪律（沿用 M2）**：

```text
① 不变量先行：新概念先冻契约（含反例注入），再写实现
② 同一事实一个写入者：新表/新列要能回答"谁写、谁读、为什么不是别处"
③ 冻结面不破：碰 M1/T2/T1/R1 之前，先跑它们的锚点测试（见 plan §15 第 4 条）
```
