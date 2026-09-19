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

**M3 开工前的三条纪律（沿用 M2）**：

```text
① 不变量先行：新概念先冻契约（含反例注入），再写实现
② 同一事实一个写入者：新表/新列要能回答"谁写、谁读、为什么不是别处"
③ 冻结面不破：碰 M1/T2/T1/R1 之前，先跑它们的锚点测试（见 plan §15 第 4 条）
```
