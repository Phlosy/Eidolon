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
app/models/*         表结构（**只加必要列/表**；M2 的 6 个迁移见下表）
```

**依赖方向**（禁止反向）：

```text
api/v1  →  services  →  models/repositories
work/*  →  services / repositories（写操作**必须**经 services；可读直查 repo）
services/*  ✗→  work/*（例外：`services/projects.py` 只调用 fixture 与 work_defaults 的**纯函数**；
                      `services/artifacts.py` 提供中性引用解析给经济域复用）
```

**M2 的 6 个迁移**（全部 additive + 事实驱动回填）：

| 版本 | 内容 |
| --- | --- |
| v40 `a1c2e3f40517` | Canonical Project Spec（7 列 + 索引 + 回填）|
| v41 `b2d4f6a8c013` | `position_authority_grants` / `position_definition_resources` / `advisory_scope` |
| v42 `c3e5a7b9d124` | `decision_records` + `tool_audits` |
| v43 `43a70cd19cc7` | Artifact Handoff（`drive_nodes.task_id` / `tasks.produces_json` / `task_inputs` / `artifact_links`）|
| v44 `b3aee926425c` | `review_requests` / `review_facts` / `tasks.rework_count` |
| v45 `325887b7109a` | `work_order_project_links` + `ix_work_orders_project_id` |

M3 可以在 v45 之后追加迁移。冻结守卫继续校验上述六个迁移存在、父子顺序不变、
v45 是 M2 最后一版且处于当前唯一 head 的祖先链中；不再要求历史 v45 永远等于仓库 head。

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
| 11 | **测试隔离（真随机序不稳定）** | 历史：M2.10 打乱文件序 **6 次里 5 次转红**，冻结态 `42a9d0f` 同样复现，原因为 session 级 `client` 和共享 SQLite 写入累积。**M3.1 U1 已关闭**：每例应用生命周期、数据库/目录基线与全局状态隔离；保留真实独立连接及 M1 冻结锚点，默认序 + 5 个实际随机序全部通过。单纯外层事务回滚不能覆盖独立 SessionLocal 提交。[验收证据](m3-u1-test-isolation.md) |

## 7. M3 兼容增补

### U2：招聘提交与环境开通分离（2026-09-20）

依据 M3 实施计划 KTD2，`work/readiness.orchestrate()` 保留环境意图的唯一入口，
在调用方事务内只创建 `ProvisioningJob/Step`，不创建磁盘目录、容器或外部 Provider 资源。
提交前、事务回滚后的意图均不可被后台执行者领取。招聘成功的响应可以为 `pending`，
其 `ok` 只有在开通 job 完成时才为 true；失败以持久 `partial` 和分步原因查询，不撤销招聘。

新 Runtime 执行者只领取 `owner=runtime` 的已提交意图，旧生命周期引擎和启动补偿只处理
`owner=lifecycle`。来源、领取标识与时间、分步结果随 v46 一起落库；启动扫描补偿提交后
未执行的意图，运行中的过期领取可恢复，正常关闭释放本执行者的领取。不会自动重试 partial。
现有 Job 查询/重试入口按 owner 路由，显式重试只重置失败步骤。

RD1–RD7 的派生就绪、环境策略、人级资产不变及失败不等于招聘失败仍保留。
`test_m2_readiness` 的同步完成断言改为“先 pending、提交后执行、再查询真实分步结果”；
事务守卫增加磁盘与外部调用断言，原测试名称保留。新增 `test_m3_provisioning` 覆盖提交、
回滚、重启扫描、领取竞争、失败/取消、owner 路由及迁移回填。

### U3：真实实例开通与复用（2026-09-20）

在 U2 的提交后执行边界增加实例级领取和稳定受管标识；同一员工的多个开通任务及
手动创建共用底层 ensure。v47 为既有 RuntimeInstance 增加资源标识及领取字段，
旧行初始为空，首次实际开通时填写；迁移本身不启动或删除容器。
已有空实例行可以补齐，创建后回写中断可按持久标识找到同一资源。失败保留资源事实，
由明确重试恢复。既有 create API 对已存在实例的拒绝语义保留。
旧入职向导由 lifecycle 服务显式提交员工与绑定后调用 manager；底层开通器不再替调用方
提交尚未完成的业务修改，独立连接可在 Docker 操作前读到完整入职记录。

工作区以 Employee.workspace_path 为源，RuntimeInstance 和真实挂载指向同一目录。
M2 readiness 的占位 Runtime 测试改用已实现 Runtime 的 Docker 替身，保留冻结锚点名称
及未就绪不派发、失败保留员工、人级资产不变的断言。替身证据与真实 Docker 验收分别记录。

### U4：运行探测与本地控制面（实施登记，2026-09-20）

真实 Runtime 的 runtime/provider 就绪项将读取有时间、原因及有效期的主动探测事实。
实例行、进程运行、模型列表可读均不能单独证明 ready；旧记录无探测时保留 unknown。
四项就绪词表、mock 必答项、任职软契约及“不就绪不自动改派”保持不变。
Provider/模型配置变化使旧探测失效；付费模型探测需显式调用和有限预算，读面与派发不发起付费探测。

本地 conda 后端不处于 Docker DNS 网络，因此增加 loopback 控制连接模式，在已登记的
Runtime 端口段内持久预留端口，仅绑定 127.0.0.1，并保留每实例控制凭证。容器化后端可选
docker 模式，经同一私有网络连接，不发布 Runtime 端口。旧容器不会被后台自动重建或删除；
缺少连接条件时明确报告，显式重建才应用新连接模式。端口与网络规则同步登记在 ports.md。

### U5：执行历史快照（实施登记，2026-09-20）

会话创建路径一次保存 Person、Employee、Company、任职、任务要求及执行环境的历史快照。
普通执行的 task_id / employee_id 仍必填；快照只保存当时可证实的引用和展示字段，不复制
凭证、Provider 配置或运行目录。旧行不回填当前资料，缺失历史明确为 unknown。
创建时的出处字段由数据库禁止改写；外部 runtime_session_ref 在分配后允许一次补记，
会话结束后不能补写或改写，也不能清空 ended_at 绕过保护。费用和结果仍按既有收尾路径保存。
全局 mock 模式按实际适配器记录 mock，已配置的真实 Runtime / Provider 不冒充实际执行环境。

`test_review_task_lifecycle` 保留收尾、改派及重复回调的冻结锚点；新增
`test_m3_provenance` 覆盖历史变化、直接 SQL 改写拒绝、未知历史和派发前快照。
v50 采用 nullable 增量迁移；升降往返保留旧会话与原有归属约束。

### U6：就绪解释与派发通知周期（实施登记，2026-09-20）

就绪继续由现有事实派生，四项词表及 mock / real 必答项保持不变。读面增加四层解释、
观察时间、原因及执行缺口，API、内部工具和派发共用同一计算入口。有效主职由任职仓储
解析；在册状态与任职缺口分别解释，缺职位仍是软契约。实际开始会话前再次调用既有
evaluate_dispatch，防止排队期间的暂停、改派或环境变化绕过门禁，不重写 DAG 或 Task 状态机。

派发阻断的发生和结束保存为通知周期事实；每个新周期与既有 Event 行在同一事务写入。
该记录只回答“何时观察到、是否尝试广播”，不作为就绪开关或任务真相。故障持续时不重复
创建通知；原因消失、作用域失效或改派后结束该周期，再次发生时创建新通知。
使用原有事件词表，不自动改派、招聘或解除 Manager 暂停。广播记录不代表消费者已完成；
持久来源 Event ID 留给 U8/U16 的幂等消费和恢复，不能声称内存总线支持崩溃重放。

原 DAG、Handoff 和 Review 锚点保留原业务断言，调度通知由捕获 `bus.publish` 改为核对
已提交 Event 及重复扫描不新增记录。U5 的快照时序测试显式固定就绪前提；新门禁行为由
`test_m3_readiness` 验证，不放宽生产派发条件。

### U7：受控 Runtime 工具调用与持久回执（实施登记，2026-09-20）

依照 M3 计划 KTD3/KTD8，真实 Runtime 的完整结构化回复经宿主内部桥进入既有
`decisions.submit_envelope()` / `tool_executor.execute_tool()`。普通遥测、自由文本和模型
自报身份均不能发起业务写入；不新增玩家通用写工具端点。Actor 从 WorkSession 及其创建时
的身份归属取得，每次调用重新检查会话、当前任职、公司、目标范围及 Authority / Autonomy。
内部只读工具显式传入公司范围，不依赖玩家 HTTP 请求上下文来完成隔离。

单个动作的业务效果与 ToolAudit 在同一事务提交，工具处理函数不提前提交业务修改。
`execute_tool(commit=False)` 将效果和审计交给调用方事务；默认入口提交一次完整动作。
DecisionEnvelope 仍先保存意图、再逐动作提交，某一动作失败不撤销此前已提交的动作；
DR6 的 PARTIALLY_APPLIED 继续按真实结果聚合，不引入整份信封的全有或全无事务。
成功审计写入失败时，该动作的业务效果也必须回滚，不能返回无回执的成功。

相关事实事件使用现有 Event 表，与业务效果、审计同事务保存；广播和调度唤醒仅在最外层
事务提交后进行，回滚时不广播、不唤醒。提交后的通知失败保留已提交事实，不能把已生效
动作改报为未执行。恢复仍由事实消费者按来源 ID 扫描、去重，不新增通用 outbox 平台。
既有事件词表、唯一领域写入者及 Task / Project / Review 状态语义保持不变。

受控请求另存会话内的持久操作标识与参数摘要，并关联 DecisionRecord 和逐动作 ToolAudit。
它只承载请求身份与恢复关联，不复制业务状态或建立第二份动作执行真相。同 key 不同参数
拒绝；重复请求按已有审计返回结果，只继续有明确未执行证据的动作，不盲目重放已生效动作。
协议层的进程内回复缓存不能代替这些持久回执。

任职授权按真实时间窗 `effective_from <= at < effective_to`（无结束时间则开放）校验，
未到生效时间的主职不授予权限。授权查询重新读取当前任职、编制、职位及 grant，避免长会话
沿用已关闭授权或旧额度；任职及授权历史仍按请求时刻解释，不修改历史行。职位与编制的
公司归属必须与 actor 一致。既有“无职位为软就绪项、公司内读事实无需管理权限”保持不变。

改动前 M2 工具、决策、评审与黄金路径 166 项已通过。保留原不变量与锚点名称；直接调用
私有工具处理函数的测试改为由调用方明确提交或经执行器调用，原业务断言不降低。
新增反例覆盖效果与审计原子性、真实 SQLite 外层回滚、嵌套事务通知、部分成功、公司隔离、
过期身份、参数冲突和响应丢失后的回执恢复。U8 的会话用途扩展另行登记。

### U8：管理与评审会话用途（实施前登记，2026-09-20）

依据 M3 实施计划 KTD4，WorkSession 增加 `worker`、`management`、`review` 用途和目标引用。
旧会话统一解释为 worker，仍必须关联真实 Task；旧历史快照不从当前资料回填。管理会话绑定
既有 Project，评审会话绑定既有 ReviewRequest，并通过该请求指向被评任务。两种非 worker
会话不伪造业务 Task，也不进入 Worker 的产物收集和任务收尾路径。

用途与目标属于创建时的出处，沿用不可变守卫。放宽 task_id 空值仅适用于具有合法目标的
management/review；数据库约束和创建入口共同拒绝缺目标、用途冲突及跨公司关联。
管理/评审会话结束只保存其执行事实，不能因 Runtime 正常退出或自然语言“通过”推进目标状态。
目标仍只由既有 DecisionEnvelope、ReviewVerdict 和领域服务改变。

DECISION_NEEDED 和 `task.review_named` 消费已有持久 Event/ReviewRequest；接收人来自已指定
的管理者或评审人。消费保存来源引用、去重与等待原因，不自动选团队、改派或改变领域事件词表。
接收人缺失、失效或环境未就绪时保留等待事实；每次执行仍重查身份、当前目标和权限。

新管理会话的现有项目写工具限定在其绑定项目内，评审会话的写动作限定为向所绑定请求
提交结论。非 Worker 的 DecisionEnvelope 作用域也必须指向绑定项目或其适用任务，防止
业务效果与 DecisionRecord 记到不同项目；公司内其他可见事实仍可作为读取上下文。
既有 Worker 和内部入口继续沿用原 Authority / Autonomy 边界。本人在该会话
提交的结论生效后，可凭关联的持久回执完成读取、重传和结束确认，但不能继续新增写动作。
其他调用者已决定、请求已被替代、人员失效或项目取消时，不得借回执阶段恢复旧权限。

Worker 与非 Worker 的会话领取在 SQLite 上共用短写事务：先取得写锁，再读取当前人员占用、
目标与就绪事实，并在同一事务创建会话。锁不跨 Runtime 调用；既有 DAG 判定和 Worker 收尾不变。

实施前 M2 契约、Project/RoleContext/Decision/DAG/Handoff/Review/WorkOrder 黄金路径及
Worker 收尾、M3 契约与出处迁移锚点 **356 passed**。`test_review_task_lifecycle`、
`test_m2_dag_runtime`、`test_m2_review`、`test_m3_provenance` 和相关迁移测试保留原业务断言；
新增用途、独立结束与事件唤醒反例归入 `test_m3_management_sessions`，迁移另测旧行兼容、
up/down/up 及模型/迁移约束一致。U8 的实现及真实验收完成后再记录结果，不将本登记算作通过。

迁移锚点保留 v50 自身的 `task_id NOT NULL` 验证；升到当前 head 后，改验 worker 的
条件性非空约束、合法用途/目标和既有历史不可变守卫。v52 回执迁移测试保留其父版本断言，
head 断言随唯一新版本推进。上述调整仅反映已登记的用途增补，不降低旧 worker 归属约束。

U8 真实评审发现引用命名空间不一致：既有查询返回当前项目 DriveNode ID，U7 桥却查已弃用的
Artifact 表。桥的 `artifact_refs` 统一指向本公司项目区文档的 DriveNode ID；同编号的旧表行
不能代替当前文档授权，也不回退混用两套编号。公司、项目归属和文档种类均须有效；保留
公司内其他项目作为上下文事实的能力。旧记录不重写，引用仍只保存 ID、不复制产物内容。

U8 审阅补充：事件发现进度只记录已检查的来源 ID，不承载执行状态；与来源观察同事务推进，
按全局或当前公司分别保存。旧事件分批补读，直接领取较新的来源不能跳过更早的事件。
SQLite 新插入或改为适用类型的低编号事件会使对应扫描进度回退；已有来源处理历史仍不可改写。
该进度可重建，迁移回退可删除进度，不能删除 ManagementDispatch 去重历史。

管理 Agent 将项目实际委派给另一人时，当前指针变更与新的 `project.replan_required` 事实
同事务保存，沿用既有事件词表。新来源唤醒当前指定接收人；原会话和原来源保留原归属，
不转移或重放。重复信封和委派给同一人不能再造唤醒；项目状态仍由已有领域动作决定。

真实管理复验另发现 `create_task.produces` 的工具 schema 只声明字符串，未公开已有
ArtifactType 值域。目录补齐该既有枚举，未知类型在请求校验阶段拒绝，不再先保存失败意图；
合法类型和领域服务校验保持不变。受控信封说明补充嵌套 decision 与简单 operation_key
示例，不放宽解析或执行约束。

### U9：缺口读面与学习请求（实施前登记，2026-09-20）

内部工具新增 inspect_gap、search_market、inspect_candidate，复用当前任务要求、能力、
负载、就绪事实和 T2 市场白名单。与相同权限的领域 API 共享投影，保留未知值，不按分数
替 Manager 选择人或动作；不公开旧公司私有产物、配置或 Provider 信息。

create_learning_priority 与 request_training 通过已有 DecisionEnvelope、Authority、ToolAudit
和 U7 持久操作回执执行。管理会话必须关联其绑定项目，目标员工须在同公司有效在册；
评审会话仍仅能提交指定请求的结论。新增目标规则只服务这两个明确工具，不泛化为公司级
任意写入许可。学习优先级继续归 Person，不能覆盖不同来源或人工意图。

训练请求只创建计划中的学习事实，不能调用现有模拟学习执行器、占用或扣除执行预算、
生成完成结果或增加 competency。重复信封复用原效果与审计；训练未完成或失败时保留
真实状态。未来实际执行仍经过预算与适用确认要求，成果沿用 Evidence 管道；本包不能
绕过 U10 的高影响确认。相关 M2 工具/决策、T2 市场及 Person 归属锚点保留原业务断言，
新增真实桥、拒绝、重放、原子回滚和 API 一致性反例记录于 test_m3_gap_actions。
目录与语义清单的精确集合分别在 test_m2_tools 和 test_m2_decisions 增补两个学习工具；
它们均为 required，原有 optional 集合及缺失决策时的拒绝断言不变。

### U13：执行尝试与结构化遥测（实施前登记，2026-09-20）

WorkSession.runtime_session_ref 继续表示原有本地会话引用，快照、身份、用途与目标保护
保持不变。runtime_attempts 另存请求键、尝试次序、实际幂等键、原实例指纹、远端 run /
session 引用、进展和终态出处；请求身份先于外部副作用持久化，远端接受后保存引用再等待。
管理对话的正常轮次是不同请求，不增加 technical_retry_count；仅同一请求的显式技术
重试增加计数。旧会话未采集的重试历史保持 NULL，新会话才初始化为 0。

app/work/telemetry.py 是执行事实的唯一写者。adapter 通过不依赖领域模型的观察接口
回报结构化事实；宿主接入普通执行及受控管理 / 评审会话。remote 引用写入一次，原请求
身份和已确认终态不可重写。未知结果、断流和主机传输错误保留不确定性，可由以后查得的
远端事实收敛，不能伪装成功或提前封死为已确认远端失败。主机结果与远端结果明确区分。

runtime_telemetry 只保存冻结词表中的有界结构化观察，保留 7 天，分页最多 100 条并有界
清理；不保存原始 token、完整提示词、工具参数、隐藏推理或凭证。未知 / 非法事件只记
通用诊断，重复、乱序和晚到消息不直接修改 Task、Review 或 Decision，不扩大领域事件集。
重建读取使用已存执行引用，并核对原实例指纹；不能在换绑后查询替换实例来解释旧执行。

test_m3_contract 的欠账守卫同步改为核验已落地的 runtime_attempts / runtime_telemetry
及可空 technical_retry_count，不再强制它们缺失。重试语义的事实来源指向新载体，
系统技术重试与 Manager 重新规划的所有者、计数器和自动执行边界保持独立；U15 的
重试策略仍待实施。未观测到的 token 用量不再在 WorkSession.cost 中补造为 0。

v56 增量迁移须通过临时库 up/down/up、单 head、无漂移及原有归属 / 快照守卫；新增
test_m3_telemetry 与 test_m3_execution_migration 验证真实宿主接缝、引用持久顺序、历史
未知值和独立进程读取。本包提供 U15–U17 所需执行事实，不代替重试、远端停止、崩溃
接管或费用归集的后续验收。

### U10：固定动作的人工确认（实施前登记，2026-09-20）

`high_impact` 只允许登记具有明确 ConfirmationPolicy 的内部工具；策略必须声明
DecisionKind、版本、有限有效期和服务器端条款查询。仍要求 Authority、额度入参及
required 决策语义，Autonomy 继续为 requires_confirmation。无策略的高影响动作、
普通写工具和调试入口保持原守卫。本包不登记虚构招聘业务；真实 T2 招聘归 U11。

action_confirmations 由 work/action_confirmations.py 唯一写入。提案绑定原 Person、
Employee、公司、管理 WorkSession、项目、DecisionRecord、RuntimeOperation/action_index、
工具及策略版本、固定入参及摘要、目标版本、服务器金额和到期时间。只接收已绑定管理信封
的提案，Agent 只能提案和查询。原等待 ToolAudit 保持 U7 不可变；执行另存最终 ToolAudit，
由确认记录关联两份回执。领域效果、最终审计、确认终态同事务，重发不得重做已完成效果。

正式 /action-confirmations 资源 API 使用真实 UserSession，即使开发模式未强制登录也
不例外。批准、拒绝、执行要求当前本公司 OWNER，且校验 cookie/header/会话中 CSRF。
授权依赖在业务事务之前解析，事务内重新读取 User、会话和成员身份。人工决定另记
UserAuditEvent，与确认审阅事实同事务；批准事实独立提交，崩溃后可重新执行。执行还须
确认原批准人及会话仍有效，并重新核对原提案人的任职、当前指定 Manager、Authority、
固定参数、条款版本、金额及领域资源状态。不可把旧批准移给另一动作或另一人。

允许经过确认服务验证的延迟动作在原管理会话已结束后执行；它仅复用不可变出处，重查
当前身份及范围，不重新打开会话。普通 Runtime 入口仍要求 running，模型参数不能提供
确认凭据或跳过会话校验。批准/拒绝/过期/结果均持久化；过期扫描有界，读查询不改写事实。

等待确认继续使用 EXECUTING、resolved_at=NULL，不能算作成功或失败。确认结果聚合按
逻辑动作计算，不将初始与最终两份回执算成两个动作；完整审计历史仍可查询。RuntimeOperation
尚有未提交的原动作回执时也保持 EXECUTING，防止首项动作提前获批导致后续动作不能执行。
既有普通批次的 APPLIED/PARTIALLY_APPLIED/FAILED 语义不变。读目录增补
inspect_action_confirmation / list_action_confirmations；精确目录守卫随之增补。

实施前相关 M2/M3 工具、决策、契约、桥、管理及学习锚点 411 passed；新行为测试
1 passed、2 failed（缺 ConfirmationPolicy、缺正式路由）。原始日志保存在本包独立证据目录。
新增并发、丢回执、跨进程、撤权/改价/资源变化、审计失败回滚和提前批准反例；v57 接在
56b215ea43f0 后，临时库 up/down/up、无漂移及历史不可变守卫必须通过。旧迁移自身版本
断言保留，仅当前 head 断言推进；有确认历史的降级须在 DDL 前拒绝。

### U11：确认招聘与既有交易接入（实施登记，2026-09-20）

实施前招聘、T2/M1 黄金路径、不变量和 M2/M3 相关锚点 251 passed。新增测试已观察到
候选人商业条款缺失、recruit_from_market 未注册、外层交易未保存成交来源事件三项失败。
首次测试夹具未提交导致的锁冲突已修正；它不作为业务缺陷证据。

新增 recruit_from_market 高影响工具，绑定 recruit 决策及 U10 确认策略，同时要求
approve_hiring 和带金额上限的 spend_credits；附加授权为交集，普通写工具的门禁不变。
执行前重查固定挂牌、商业条款、结算政策及金额。只适配 M1 一口价成交；议价明确要求卖方
接受，未设条款不推定为免费。人类既有 T2/议价入口保持其原领域语义，后续统一入口加固归 U26。

复用 TalentTradeService → RecruitmentService → U2 开通意图，不另写账本、招聘或定价。
招聘、扣款、成交来源事件和最终 ToolAudit 同事务；既有 Event 行在外层提交后才分发，
回滚不发通知。Person 及其资产不复制，Agent 发起者留在真实 Employee 审计中，不伪装成 User。
结果分开返回招聘、交易和开通；待开通或 partial 不冒充 ready。候选人详情增补 M1 既有
price/currency/sale_mode 白名单投影，API 与工具共用，无新增商业事实表。

目录守卫由“尚无 high_impact 工具”改为“仅有具备确认策略的真实招聘工具”；无确认策略的
动作仍拒绝。P12/P13、资金原子性、并发抢人、重放、撤权、条款变化和开通失败都有行为检查。

### U12：七类动作与终态指派守卫（实施登记，2026-09-20）

实施前相关工具、决策、管理和真实请求桥锚点 361 passed。先观察到三项失败：学习动作仍为
阶段占位、Runtime 目录缺少能力动作映射、failed 任务仍能被改派。done / cancelled 拒绝保持通过。

七类动作全部映射实际工具，Runtime 目录从同一契约派生 capability_actions；它只说明可用
入口，不替 Manager 选人、选动作或拆图。replan_project 仍属于 revise_task。P33 守卫收紧为
全部具有真实工具，不再接受 M3.3 owner_stage；保留原测试锚点名称供冻结表引用。
原欠账总表的非空断言相应允许 capability_gap_action 清空，其余未完成欠账仍强制非空。

assign_task 对 failed 补齐既有终态拒绝，保留原负责人和来源事件；done / cancelled 边界不变。
已有任务终态不通过 force 重开，后续技术恢复继续遵守 KTD6。组合动作仍逐项保存真实效果和
ToolAudit，部分失败保持 PARTIALLY_APPLIED；U7 回执用于重放及恢复，不新造事务或任务图。
已结束评审、返工次数、Authority、公司隔离及 P9–P13 锚点继续适用。

**M3 开工前的三条纪律（沿用 M2）**：

```text
① 不变量先行：新概念先冻契约（含反例注入），再写实现
② 同一事实一个写入者：新表/新列要能回答"谁写、谁读、为什么不是别处"
③ 冻结面不破：碰 M1/T2/T1/R1 之前，先跑它们的锚点测试（见 plan §15 第 4 条）
```
