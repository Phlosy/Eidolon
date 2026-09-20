# M3 — Agent Production & Growth（Agent 生产运行、适应与长期资产成长）

> **阶段**：M3.0 Production Agent Contract Freeze（本文是该阶段的冻结设计）
> **前置**：M2 已冻结（`docs/m2-freeze.md`，105 条不变量全部 enforced）；M1/T2 已冻结。
> **代码化**：`apps/server/app/work/production.py`（纯契约层）+ `apps/server/tests/test_m3_contract.py`
> **执行基线**：`docs/m3-implementation-plan.md`（M3.0–M3.10 拆解 / 验收矩阵 / Progress / 风险）

---

## 0. 一句话目标

M2 解决的是「公司怎么组织工作」；M3 解决的是：

> **这些 Agent 能不能真正进入真实 Runtime 工作，在真实任务中积累经验、知识、能力与职业价值，
> 并最终成为玩家长期拥有、可以解释、可以交易的数字人才。**

M3 的六个动词：**真正启动 · 真正执行 · 真正失败 · 真正恢复 · 真正产出 · 真正成长。**

M3 **不做**的（属于 M2，已冻结）：Planner / Team Decision / DAG Decision / Review 归属 / 决策信封。
M3 只允许**消费**它们。

---

## 1. 最高架构原则（沿用 M2，不重新谈判）

```text
System provides facts.      Agent makes decisions.
System validates.           System executes.        System records.
```

M3 把这句话延伸到**执行层**：

| 问题 | 谁回答 | 系统**不**回答 |
| --- | --- | --- |
| Runtime 是否健康？ | **系统**（事实） | — |
| Provider 是否可用？ | **系统**（事实） | — |
| Workspace 是否存在？ | **系统**（事实） | — |
| 权限是否合法？ | **系统**（校验） | — |
| Task 是否 ready？ | **系统**（事实） | — |
| 资金是否足够？ | **系统**（事实） | — |
| 能力缺口是什么？ | **系统**（事实） | — |
| 该等还是换人？ | — | **Manager Agent** |
| 该学习还是招聘？ | — | **Manager Agent** |
| 该买哪个 Agent？ | — | **Manager Agent / Owner** |
| 这个 Agent 值不值得培养？ | — | **Manager Agent / Owner** |
| 失败后改哪个方案？ | — | **Manager Agent** |

Runtime failure 是**事实**，不是管理决策（P29）。一次 `exit=0` 不是任务成功（P17）。

---

## 2. M3 的最终产品循环

```text
Person
 ↓
Recruit / Existing Employee
 ↓
Employee
 ↓
Position Assignment
 ↓
RoleContext
 ↓
Provisioning（workspace → runtime → provider）
 ↓
RuntimeInstance
 ↓
Provider / ModelBinding
 ↓
Health / Capability Probe
 ↓
READY_TO_WORK（派生事实，不是一行 bool）

        ↓  Manager Agent 分配 Task（M2 决策信封）

真实 Runtime 执行（WorkSession）
 ↓
Tool / Files / Knowledge / Artifacts
 ↓
Artifact Handoff（M2）
 ↓
Reviewer Agent（M2 评审闭环）
 ↓
PASS / REWORK（M2）
 ↓
Final Delivery（M2）
 ↓
Work Experience（M3.5）
 ↓
Evidence（既有 P6/T2 管道）
 ↓
Competency / Skill / Knowledge / Career
 ↓
Agent 长期资产价值增加（可解释，不是神秘战力）
```

能力缺口分支（与上面的循环**正交**）：

```text
Capability Gap（系统只给事实：fit / load / 缺口 / 市场候选）
      ↓
Manager Agent 自己选：学习 / 调人 / 招聘 / 改方案 / 拆任务 / 委派
      ↓
DecisionRecord（署名）+ 结果（M2.4）
```

---

## 3. Agent 的正确语义：Product Aggregate，不是新 SoT

### 3.1 正式定义（M3 冻结）

```text
Person            = 长期身份与资产主体
Employee          = 这个 Person 在某家公司中的任职身份
PositionAssignment= 当前职责与 Authority（表：employments）
RuntimeInstance   = 当前执行环境
Provider/ModelBinding = 当前模型能力供应
Knowledge / Skill / Evidence / Competency / Experience
                  = Person 长期成长资产
```

产品上可以叫 **Agent**；数据上**不存在** Agent 实体。`AgentProfile`（§16）是**读模型**，不是身份真相。

### 3.2 Person-owned vs Employment-owned（M3 冻结）

| 长期随人走（Person-owned） | 当前公司/任职相关（Employment-owned） |
| --- | --- |
| identity / traits / competency | employee 行（任职身份） |
| evidence / knowledge / skill | position assignment / authority |
| experience / career / specialization | runtime / workspace / provider binding |
|  | resource entitlement / 当前任务 |

换公司的**正确行为**：

```text
带走：Knowledge / Skill / Evidence / Competency / Experience / Specialization
重建：Employee / Position / Authority / Runtime / Workspace / Provider
```

**禁止复制**：一次招聘（`recruit`）不得复制 Person-owned 资产 —— 资产属于 Person，
招聘只是让同一个 Person 进入另一家公司（P12）。

### 3.3 禁止成为第二真相（机器守卫）

禁止新建的表（整表名，`Base.metadata` 级守卫）：

```text
agents / agent / agent_profiles / agent_assets / agent_records
agent_identities / agent_skills / agent_competencies / agent_experiences
```

禁止的表级信号：任何表出现 `agent_id` 列（等于把 Agent 变成外键主体）。

**如果确实发现必须新增 Agent SoT**：先写 ADR → 暂停阶段 → 等 Review。不允许"顺手加一张表"。

---

## 4. Production Agent State Model：四层就绪，派生而非断言

不要用一行 `runtime_status` 表达所有问题，也不要新增 `employee.ready = true`（第二真相）。

M3 冻结**四层**语义（每层都派生自现有事实）：

| 层 | 含义 | 事实来源（现有） | 谁消费 |
| --- | --- | --- | --- |
| **Employment Readiness** | 这个人在这家公司是否有效在册、有职责面 | `employees.lifecycle_status` / `employments`（effective primary）/ `position_definitions` | 招募、编排、派发 |
| **Provisioning Readiness** | 环境是否已开通（工作区 / 实例 / 供应商） | `employees.workspace_path` + 目录存在 + `runtime_instances` + `model_bindings`/`providers` | `work/readiness.orchestrate()` |
| **Runtime Readiness** | 实例是否活着且健康、能力是否匹配 | `runtime_instances.status/health_status` + adapter `detect()`/`capabilities()` | 派发门禁（M2） |
| **Execution Readiness** | 现在能不能真的开工 | 上面三层 + `READINESS_REQUIRED_FOR_EXECUTION` 的必答项 | `work/dispatch.evaluate_dispatch()`（M2，不改） |

状态链（**每一跳都是事实，不是开关**）：

```text
employee active
 → resources provisioned      （工作区目录 + resource_accounts）
 → runtime provisioned        （runtime_instances 行存在）
 → runtime healthy            （status ∈ OK 且 health ≠ unhealthy）
 → provider/model valid       （providers.enabled + is_primary binding）
 → workspace ready            （路径存在且是目录）
 → READY_TO_WORK
```

M3.0 把这条链**冻结成契约**（`production.py` 的 `PRODUCTION_STAGES` + `READINESS_ITEM_STAGE`），
并要求：`READINESS_FACT_SOURCES` 的每个 item 都必须映射到一个 stage，反之每个需要事实的 stage
都必须有 item —— 两边不一致即红（守卫：`test_readiness_items_map_onto_production_stages`）。

### 4.1 为什么必须是派生

| 反模式 | 为什么禁止 |
| --- | --- |
| `employee.ready = True` | 第二真相：容器死了它还是 True；没人负责撤销 |
| `agent_ready` 表 | 同上，且会长出写路径（谁写？什么时候写？） |
| 只读 `runtime_status` | 把"在册 / 环境 / 健康 / 可派发"四件事压成一个值，无法解释原因 |
| 就绪 = 布尔 | 阻塞原因不可解释 ⇒ Manager 无法决策 |

**就绪永远由事实算出来**：`ReadinessReport(ready_to_work, items[…], gaps[…])`（M2.8 已实现）。
M3.0 只冻结它的**形状**（item 词表 / stage 映射 / 必答项 / 解释字段），**不新建载体**。

---

## 5. AgentReadiness 契约（M3 冻结）

### 5.1 形状

```text
AgentReadiness = ReadinessReport（派生读模型，M2.8）

  employee_id / company_id
  runtime_type / deployment_mode
  policy            （RuntimePolicy：值 + 来源，可解释"谁配的"）
  items[]           （每项：item / ready / detail / source / facts）
  gaps[]            （**全部**未就绪项：面向人，告诉你差什么）
  ready_to_work     （= 四项全就绪：面向人的"完全就绪"口径）
  执行门禁            （= `READINESS_REQUIRED_FOR_EXECUTION` 的必答项；`position` 不在其中 —— W5）
```

两个口径**刻意不同**（M2.8 冻结，M3 不合并）：人需要知道"差什么"，派发只需要知道"能不能开工"。
例如一个新员工没有职位（软契约允许）⇒ 严格口径有 gap，但执行门禁里没有 `position`。

### 5.2 item 词表（冻结，四条）

| item | stage | 是否必答 | 事实来源 |
| --- | --- | --- | --- |
| `position` | employment | 否（软契约 W5） | `employments`(effective) + `position_definitions` |
| `workspace` | provisioning | real 必答 | `employees.workspace_path` + 目录存在 |
| `runtime` | runtime | real 必答（mock 不需要实例） | `runtime_instances.status/health_status` |
| `provider` | runtime | real 必答（mock 不需要） | `model_bindings(is_primary)` + `providers.enabled` |

必答项集合来自 `READINESS_REQUIRED_FOR_EXECUTION`（M2 冻结）：`mock: ()`、`real: (workspace, runtime, provider)`。

### 5.3 就绪的**解释**要求

每个 `ReadinessItem` 必须同时给出：`ready`（判定）、`detail`（一句人话）、`source`（从哪读的）、
`facts`（具体值）。**禁止只有布尔**：`NOT_READY` 必须能回答"为什么"，例如：

```text
NOT_READY
- provider unavailable（providers.enabled = false / 无 is_primary binding）
- workspace missing（employees.workspace_path = "/…/x"，目录不存在）
```

### 5.4 Readiness 不替 Manager 决策

```text
Bob: NOT_READY（runtime unhealthy）
系统只能说：Bob 现在不能执行。
系统不能：自动改派 Alice（P8）
```

正确路径：事实 → 事件（`task.runtime_unavailable`，M2 事件集）→ Manager Agent → 决策信封。

### 5.5 与 Dispatcher 的集成（不改 Dispatcher）

```text
M2 Dispatchability（evaluate_dispatch）
      ↓
AgentReadiness Query（readiness_report / required_for_execution）
      ↓
ready?  → 派发
不 ready → 发布原因（DECISION_NEEDED 事件），等管理层
```

纪律：**Manager chooses. System schedules. Worker executes.**（M2 冻结，M3 不重写。）

---

## 6. Provisioning 契约（M3.0 冻结形状，M3.1 落地真实执行）

### 6.1 正式流程

```text
Employee
 ↓ ① workspace     ensure_workspace()      （目录 + 资源账户事实）
 ↓ ② runtime       ensure_runtime_instance()（RuntimeInstance 行 + 容器 create/start）
 ↓ ③ provider      ensure_provider_binding()（primary binding + provider enabled）
 ↓ ④ health        health check（status/health_status 刷新）
 ↓ ⑤ provider probe（provider 可用性）
 ↓ ⑥ model binding validation（is_primary 的 model 存在）
 ↓ ⑦ capability probe（adapter.get_capabilities() vs Task 需求）
 ↓ READY
```

M3.0 冻结的**步骤顺序**：`PROVISIONING_STEP_ORDER = ("workspace", "runtime", "provider")`
（与 `READINESS_REQUIRED_FOR_EXECUTION["real"]` 必须逐字一致；守卫钉住）。
④–⑦ 属于 M3.1 的探测层，M3.0 只冻结**失败原因词表**，不假装已经探测。

### 6.2 失败必须可解释（枚举，按现有模型裁剪）

```text
PROVISIONING_FAILED     编排整体失败（事务/未知错误）
WORKSPACE_UNAVAILABLE   工作区不可用（路径缺失/无权限）
START_FAILED            实例创建或启动失败（容器/进程）
UNHEALTHY               实例起来了但不健康
PROVIDER_UNAVAILABLE    供应商未启用/无绑定
MODEL_UNAVAILABLE       binding 的模型不存在/不可用
CAPABILITY_MISMATCH     Runtime 能力不满足任务需求（探测层，M3.1）
RUNTIME_UNAVAILABLE     运行时类型没有可用实现（adapter.detect() = False）
```

纪律：**不要巨大状态机**。只要求"每个失败都落在一个**能解释**的原因上"，并写进
`provisioning_jobs/steps`（现有载体）+ 员工就绪回报（`partial`）。

### 6.3 幂等（P6）

```text
ensure_runtime_ready(employee) × N  ==  ensure_runtime_ready(employee) × 1
```

不得重复创建：容器 / 工作区 / RuntimeInstance 行。要覆盖的现实场景：

| 场景 | 期望 |
| --- | --- |
| 容器已存在，DB 行存在且健康 | no-op（不重建、不重启） |
| DB 行存在，容器不存在 | 修复：按 DB 行重建容器 |
| 容器存在，DB 行不存在（孤儿） | **登记**（不静默删除）→ 见 §6.4 |
| 容器 unhealthy | 尝试恢复；失败则记原因，不反复重启风暴 |
| 进程崩溃（编排中途） | 下次 ensure 从事实重新收敛，不依赖内存状态 |

### 6.4 孤儿 Runtime 的收敛策略（P7）

```text
Database  ⇄  Runtime backend（Docker / 本地进程）对账

detect orphan（外部有、DB 无）      → 标记 + 上报，等管理层决定（**不删**）
detect missing（DB 有、外部无）     → 重建或标记 UNHEALTHY（可自动重建：幂等）
detect stale status（状态与事实不符）→ 刷新为事实
repair                               → 只做**可逆、可解释**的动作
```

**禁止**：`docker rm $(docker ps -a | grep 未知)` 这类"清理未知容器"的危险策略（P7）。
对账的默认动作是**上报事实**，删除必须由人/管理层显式发起。

---

## 7. Real vs Mock 契约（P24 / P25）

### 7.1 判定链（唯一口径）

```text
employees.runtime_type = "mock"
   → RuntimePolicy.is_mock（work/readiness.py）
   → RuntimeInstance.runtime_type（mock 不需要实例）
   → WorkSession.runtime_type（写入时快照）
   → evidence.normalize：任何 mock 会话 ⇒ environment = "mock"
   → evidence.policy.MOCK_RELIABILITY_FACTOR（打折）
```

M3 冻结的三条：

1. **mock 与 real 必须一直可区分**：`execution_mode(runtime_type) -> "mock" | "real"`（纯函数，
   契约层可判定，不允许"看起来像真的"）。
2. **mock 证据继续打折**：`MOCK_RELIABILITY_FACTOR` 的语义不动（M1/T2 已实现）；
   M3.0 的契约常量必须与 `app/evidence/policy.py` **逐字一致**（守卫断言）。
3. **mock 不是"次等真实"，而是另一种事实**：mock 会话可以推进任务、产出 Artifact、进入评审；
   唯一差别是**证据环境**与可靠性乘数。禁止把 mock 结果写成 real。

### 7.2 Mock 的诚实边界

```text
允许：mock 跑通 DAG / 评审 / 交接 / 经济（教程、CI、冷启动）
禁止：mock 结果伪装 real（P24）
禁止：mock 会话产生"真实世界证据"（P25）
```

M3.10 Scenario A **必须**至少一个真实 Runtime（Hermes 或 OpenClaw），禁止全 mock。

---

## 8. Runtime Provenance 契约（P14）

### 8.1 必须快照的事实

一次 WorkSession 结束以后，"这次是谁在什么环境用什么模型干的"必须**不可改写**：

| 事实 | 现有承载 | 状态 |
| --- | --- | --- |
| `runtime_type` | `work_sessions.runtime_type` | ✅ 已有 |
| `runtime_instance_id` | `work_sessions.runtime_instance_id` | ✅ 已有 |
| `provider_id` | `work_sessions.provider_id` | ✅ 已有 |
| `model` | `work_sessions.model` | ✅ 已有 |
| `deployment_mode` | `work_sessions.deployment_mode` | ✅ U5，旧值 unknown |
| `runtime_session_ref` | `work_sessions.runtime_session_ref` | ✅ 已有 |

M3.0 冻结两件事：

1. **写一次**：WorkSession 的 provenance 列在 `ended_at` 之后**不得改写**（换绑/换绑定/换模型
   只能影响**未来**会话）。
2. **欠账显式登记**：未实现的字段必须带 owner stage；偿还后以写入、读取和不可变行为证据关闭，
   不保留虚假欠账。U5 已保存 deployment_mode、Provider 名称、Person / Employee / Company、
   任职、任务要求与名称快照。既有缺失历史保持 unknown，不能从当前资料反推。

U5 的创建路径为 `repositories/project.create_work_session`，历史读者为 `work/review_facts`。
v50 SQLite 守卫从创建起保护出处字段；外部 runtime_session_ref 分配后只允许补记一次，
ended_at 非空后不能更改或清空。全局 mock 覆盖按实际适配器记录，real 表示所选执行通道，
不单独证明模型已成功调用或业务已交付。普通执行的 task / employee 关联约束不变。

### 8.2 Binding 变化的历史语义（M3.9 落地）

```text
Provider Binding / Runtime Binding / Manager Binding / Reviewer Binding
        ↓ 变化
只影响未来（新会话 / 新任务 / 新决策）
       ✗ 不改写历史 WorkSession / DecisionRecord / ToolAudit
```

---

## 9. Capability Gap Action Model（M3.3 落地，M3.0 冻结模型）

### 9.1 缺口是事实，动作是决定

```text
Gap detected（事实：Task 需求 vs 人/团队的 competency/负载/可派发性）
 ↓
Manager notified（DECISION_NEEDED 事件）
 ↓
Manager calls facts：inspect_gap / list_company_people / calculate_task_fit /
                     get_current_load / inspect_readiness / search_market
 ↓
Manager decides（DecisionRecord，署名）
 ↓
Manager calls action tool → System validates（Authority/Budget/Market）→ System executes
```

**禁止**：`if fit < 60: recruit_best_candidate()`（P9/P10）。契约层不得出现按阈值自动选动作的函数。

### 9.2 动作词表（冻结；每条登记"现在有没有工具"）

| action | 语义 | M3.0 现状 | M3.3 实施结果 |
| --- | --- | --- | --- |
| `learn` | 让这个人补能力（学习优先级 / 训练请求） | ⏳ M3.3（学习工具面未建） | `create_learning_priority` / `request_training` |
| `use_internal_employee` | 换本公司的人 | ✅ `assign_task`（M2 工具面） | `assign_task` |
| `reassign` | 改派已分配的任务 | ✅ `assign_task` / `update_task` | `assign_task` / `update_task` |
| `recruit` | 从人才市场招 | ⏳ M3.3（市场工具面未建；T2 市场本体已冻结） | `recruit_from_market`（固定条款经 Owner 确认） |
| `revise_task` | 改方案/改任务定义 | ✅ `update_task` / `replan_project` | `update_task` / `replan_project` |
| `split_task` | 拆小任务 | ✅ `create_task` + `create_dependency`（组合，无单名工具） | `create_task` + `create_dependency` |
| `delegate` | 委派项目 | ✅ `delegate_project` | `delegate_project` |

M3.0 守卫允许登记 `tool` 或欠账 `owner_stage`。U12 已收紧为七类全部具有实际工具，
Runtime 目录从同一映射派生；不再以本阶段占位通过验收。[M3.3 阶段证据](m3-3-capability-actions.md)。

### 9.3 Learning 的硬边界（P11）

```text
允许：创建 LearningPriority / LearningTask / Training Request（学习事实）
禁止：创建学习任务 ⇒ 直接增加 competency score
```

能力只能经：**真实学习结果 → Evidence → Aggregator**（P6/T2 既有管道）。职位不会赋予 Skill（W7），
Runtime 也不会（P3）。

### 9.4 Recruit 的硬边界（P12 / P13）

```text
允许：search_market → inspect_candidate → calculate_fit → 决定招 → 走 T2 交易（Authority/Budget 校验）
禁止：招聘复制 Person-owned 资产（Knowledge/Skill/Evidence/Competency/Experience）—— 资产本来就是同一个 Person 的
禁止：招聘完成 = 可工作。**必须** 过 M3.1 provisioning + M3.2 readiness 才可能 READY_TO_WORK（P13）
```

---

## 10. Execution Telemetry vs Domain Event（P16）

### 10.1 两个问题，两套事实

```text
Runtime Telemetry  回答：执行环境发生了什么（session / tool / tokens / errors）
Domain Event       回答：项目/任务领域发生了什么（ready / started / completed / review…）
```

**Runtime 事件不得直接成为领域真相**：

```text
✗ Hermes emitted "task.completed"  ⇒  Task.status = done
✓ Runtime Adapter → WorkSession → 领域迁移（服务层校验）→ FACT_EVENT / DECISION_NEEDED
```

M3.0 冻结遥测词表（结构化事件，非 token 流）：

```text
session.started / session.completed / session.failed
message.completed
tool.called / tool.completed / tool.failed
artifact.created
runtime.warning / runtime.error
```

守卫：遥测词表与 `FACT_EVENTS` / `DECISION_NEEDED_EVENTS` **不得重叠**（同一个集合存在的意义就是
回答"这个事件是不是领域事实"）。详细 token stream 只进日志/短期缓冲，**不永久落库**（M3.4 决策）。

### 10.2 事件边界守卫

- `app/runtimes/**` 不得 import 领域写路径（`app.services.tasks` / `app.models.project` 等）——
  adapter 只能返回结构化事件与产物，由 `work`/`services` 转译。
- 新增遥测事件**不得**直接加进 `FACT_EVENTS`：那要过 M2 契约（P28）。

---

## 11. Technical Retry vs Management Replan（P15）

两套语义，**不共用一个计数器**：

| | Technical Retry | Management Replan |
| --- | --- | --- |
| 触发 | 网络抖动 / provider 5xx / 容器重启 / 超时 | Agent 做错了 / 方案不可行 / 反复失败 |
| 归属 | **系统**（可自动，有限次，退避） | **Manager Agent**（DecisionRecord 署名） |
| 事实落点 | `runtime.warning`/`runtime.error` 遥测 + WorkSession 重试元数据 | `task.failed` → `project.replan_required`（M2 事件）+ `replan_project` 工具 |
| 次数语义 | `technical_retry_count`（可重置，属于会话/环境） | `rework_count`（M2.7 已存在，属于任务/管理事实） |
| 越界 | 重试耗尽 ⇒ 上报事实，**不自动改方案** | 不允许系统代替 Manager replan |

守卫：`TECHNICAL_RETRY_OWNER = "system"` 与 `MANAGEMENT_REPLAN_OWNER = "manager_agent"` 是两个不同常量，
且契约层不得出现 `decide_retry_policy` 之类函数。

---

## 12. Crash Recovery（M3.4 落地，M3.0 冻结原则）

必须能收敛（而不是永远留下 `RUNNING` 僵尸）：

```text
server restart            → 启动时对账 WorkSession（running 但进程已死）→ 标记 + 上报
runtime container restart → 刷新实例状态与健康
WorkSession stuck running → 依据 runtime 事实判定（超时/无会话）→ 结束或标记失败
runtime session lost      → 记 `session.failed`，任务回到可管理状态
provider timeout          → technical retry（有限次）
artifact partially written→ 产物不完整 ⇒ 不进入 handoff（宁缺勿假）
```

原则：**启动对账只做事实修正与上报**；业务响应（重试/换人/重规划）永远是 Manager 的决定（P29）。
现有 `app/main.py` lifespan 已有启动清扫先例（`workforce_access.sweep_on_startup()`），M3.4 沿用该模式。

---

## 13. Work Experience（M3.5 落地；M3.0 先裁决载体）

### 13.1 先评估现有 CareerEvent（M3.0 的裁决）

`career_events` 现有列：`company_id / employee_id / event_type / position_definition_id /
position_slot_id / from_position_id / to_position_id / effective_at / actor_user_id / reason /
source_id / metadata_json`。

**结论：CareerEvent 是"职位履历审计"，不足以表达 Work Experience。** 它没有 project/task/
artifact/review/session 的口径列，硬塞只能进 `metadata_json`（不可查询、不可守卫、会变成垃圾抽屉）。

因此 M3.5 的形态（M3.0 只冻结契约与字段，**不建表**）：

```text
WorkExperience = 派生读模型（首选）
  来源：tasks（真实完成的）+ work_sessions（谁在哪次会话）+ projects + artifacts +
        review_requests/review_facts（结论）+ decision_records（决策角色）
  宿主：读模型（M3.5 评估是否需要物化投影；若需要 → 单独立项 + 迁移 + ADR）
```

### 13.2 字段契约（冻结）

```text
person_id / employee_id（当时的）
company_id / company_name（当时的）
position_definition_id / position_code（当时的）     ← 历史快照，不从当前 Position 反推
project_id / project_title
task_id / task_title / task_kind
role_in_work           （执行者 / 评审者 / 决策者）
contribution           （产出：artifact 引用）
requirement_snapshot   （当时的难度/需求快照）
result                 （done / rejected / abandoned）
review                 （结论 + 返工次数）
started_at / completed_at
```

**历史不由当前状态反推**（P23）：换公司、换职位以后，"当时在哪家公司、什么职位"必须来自
**当次事实的快照**，不是 join 当前 `employees` / `position_definitions`。

---

## 14. Unified Person Timeline（M3.5）

现在 `EducationEvent`（教育）与 `CareerEvent`（职业）分开；M3 提供**统一读模型**：

```text
Person Timeline
  Education / Cultivation / Employment / Project & Task Experience /
  Assessment / Major Decision / Achievement
```

来源是**多个表**，不是把历史塞进一个表（P23 的同款纪律：历史事实的宿主 = 它发生的地方）。

---

## 15. Reliability / Specialization / Collaboration / Management Experience（M3.6）

### 15.1 可靠性：事实维度 + Evidence，不做神秘总战力（P19 / P21）

派生指标（全部可解释、可回溯到事实）：

```text
tasks_completed / success_rate / first_pass_review_rate / rework_rate
on_time_rate / high_difficulty_tasks / runtime_failure_rate
review_score / delivery_quality / management_decision_outcomes
```

**禁止**单一 `power = 9532` 类字段成为 Agent 真相（P21）。
每个指标必须能回答"从哪些事实算出来的"（算子 + 来源表）。

### 15.2 Specialization：派生（P20）

从真实任务、能力证据、知识、SkillUsage 推导（Kubernetes / Distributed Systems / Quant Research /
Frontend / Technical Writing / Review / Management…）。
禁止"玩家点一下就成为专家"：标签只能从证据收敛出来。

### 15.3 Collaboration：先事实，后解释

先保留可解释事实：`handoff_history / review_relationship / co_task_history /
dependency_relationship / rework_relationship`。
**禁止**先造 `chemistry = 92` 这类黑盒数值。

### 15.4 Management Experience：Decision + Outcome

管理 Agent（CEO/CTO/PM/Reviewer）的成长来自 `DecisionRecord` + `ToolAudit` + `Project Outcome` +
`Review Outcome`。**禁止**中央系统主观评价"这是一个好决定"（P22）：只有"决策 + 后续事实"。

---

## 16. Agent Asset Read Model（M3.7）

`AgentProfile` = **读模型**（不是身份真相，不是表）。必须**分开**两组字段：

| 长期资产 | 当前运行态 |
| --- | --- |
| identity | company / role |
| traits | load / runtime / provider |
| competencies（score + confidence） | current task / readiness |
| validated skills / knowledge domains | work session（进行中） |
| work experience / representative projects | blocked reason / review status |
| reliability metrics / specializations | provider binding |
| collaboration facts / management experience | — |
| representative artifacts | — |

纪律：**当前运行态不得覆盖历史**（P23）；T2 市场继续复用该读模型做 **Market Read Projection**，
不重开 T2 的 Source of Truth（P27）。

---

## 17. Authority / Autonomy / Escalation / Rebinding（M3.9）

### 17.1 Authority ≠ Autonomy（P30）

```text
Authority（组织权力）      ：CEO has economy.spend
Autonomy（自动执行程度）    ：AI CEO 能否无人确认花无限的钱？—— 由 Autonomy Policy 回答
```

两者**独立**：有权限 ≠ 可以自动执行。M2 已冻结 `AUTONOMY_BY_SIDE_EFFECT`（read/write = auto，
high_impact = requires_confirmation），M3.9 把 M1 面的高影响端点（合同验收 / 工单验收 / 大宗采购 /
招聘 / 辞退）逐步接入 Authority + Autonomy 双门禁。

### 17.2 ESCALATE：系统只路由

触发（事实）：`Agent cannot decide / Authority insufficient / Risk too high / Repeated failure /
No valid candidate / Budget insufficient`。

```text
ESCALATE → parent manager → higher authority → human owner
系统只负责路由（谁是父级/谁有权限），不做判断（M2 的 ReviewVerdict.escalated 已冻结"无自动状态目标"）
```

### 17.3 Rebinding 的历史语义

见 §8.2：Binding 变化只影响未来。

---

## 18. Agent Operations UX（M3.8）

玩家需要看到"这个 Agent 正在做什么"：

```text
Agent Detail：Readiness / Runtime Health / Provider·Model / Current Project / Current Task /
WorkSession / Execution Timeline / Tool Activity / Artifacts / Blocked Reason /
Review Status / Rework / Experience Gain
```

**禁止**把 Runtime 的隐藏思考/完整 chain-of-thought 当产品功能（§37）。
UI 呈现结构化 `activity / decision / result`（动作、工具、产物、错误、进度、决策摘要）。

---

## 19. M3 不变量（P1–P36）

编号即承诺。M3.0 的纪律（沿用 M2.10 的教训）：**不允许"冻结待锚点"的双态** ——
每条 P 在 M3.0 就必须有**现存**测试锚点（`enforced=True`），
尚未实现的语义用"契约 + 欠账登记（owner_stage）"表达，而不是假装不变量已落地。

| # | 不变量 | 当前锚点（保留 M3.0 契约） |
| --- | --- | --- |
| **P1** | Agent has no new identity source of truth. | `Base.metadata` 无禁止表 + AST 守卫 |
| **P2** | Person remains the long-term asset owner. | 资产表 person 口径列断言（M2 A6 同源）+ 契约 |
| **P3** | Runtime configuration is not Person competency. | `FORBIDDEN_RUNTIME_POLICY_KEYS` 含能力类键 + AST |
| **P4** | Runtime readiness is derived, not manually asserted. | 无 `employee.ready` 列/字段 + readiness 派生断言 |
| **P5** | A RuntimeInstance DB row alone does not imply READY_TO_WORK. | 就绪项断言：行存在但 unhealthy ⇒ NOT_READY |
| **P6** | Provisioning is idempotent. | U3：顺序/并发 ensure 只保留同一环境，真实容器创建后进程退出可找回原资源；[证据](m3-u3-runtime-ensure.md) |
| **P7** | Runtime reconciliation must not silently delete unknown external resources. | 契约动作表只含 detect/mark/repair；AST 无 rm/remove/delete 调用 |
| **P8** | System never reassigns work because an Agent is not ready. | AST：无 readiness ⇒ assign 的自动路径；派发只发事件 |
| **P9** | Capability Gap does not trigger automatic recruitment. | U9/U12：缺口查询无写副作用，动作来自显式决定；无按阈值自动招聘路径 |
| **P10** | Capability Gap actions originate from Manager/User decisions. | U9–U12：真实工具目录、DecisionRecord / ToolAudit、组合部分执行与重放锚点 |
| **P11** | Learning never directly writes competency score. | U9：学习优先级与训练请求不写 competency；Person 归属及失败训练行为检查 |
| **P12** | Recruitment does not copy Person-owned assets. | U11：实际 M1/T2 招聘前后 Person 资产不复制，并发成交与回滚锚点 |
| **P13** | Recruitment must eventually pass through provisioning before real execution. | U11：真实 Manager 招聘→Owner 确认→成交→真实开通与就绪；partial 仍不可工作 |
| **P14** | Historical runtime/provider/model facts are immutable snapshots. | U5：创建快照、历史读取、直接 SQL / ORM / 批量改写拒绝（test_m3_provenance） |
| **P15** | Technical retry and management replan are separate semantics. | U13：持久请求/尝试计数与管理 rework 分离；自动重试及取消仍属 U15 |
| **P16** | Runtime events do not directly become domain truth. | U13：有界遥测、原远端引用、主机/远端结果分离；重复及晚到事件不改领域状态 |
| **P17** | Task success requires domain completion/review, not merely runtime exit=0. | 契约：status 目标仅来自域（M2 锚点） |
| **P18** | Work Experience must originate from real domain facts. | 字段契约 + 来源表映射（全部真实存在） |
| **P19** | Reliability metrics are derived. | 指标清单 + 来源映射 + 无缓存列 |
| **P20** | Specialization is derived from evidence/history. | 契约：无 specialization 写入路径 |
| **P21** | No single opaque power score becomes Agent truth. | AST + metadata：无 power/overall_score 列 |
| **P22** | Management experience must come from DecisionRecord + outcome facts. | 来源映射（decision_records + outcome 事实） |
| **P23** | Current employment/runtime state must not overwrite historical career facts. | U5：改名/调岗/改派/换环境后历史不变；旧记录 unknown；完整履历投影仍属 U18 |
| **P24** | Mock execution must remain distinguishable from real execution. | `execution_mode()` + 判定链守卫 |
| **P25** | Mock evidence must continue respecting existing reliability discount. | 契约常量 == `evidence.policy.MOCK_RELIABILITY_FACTOR` |
| **P26** | AgentProfile is a read model. | AST：无 AgentProfile 表/写路径；字段集与来源映射一致 |
| **P27** | T2 Market remains the talent transaction source of truth. | U9/U11：复用 T2 市场公开投影与 M1 商业条款，工具/API 同源；交易锚点通过 |
| **P28** | M2 Project/Task/Decision/DAG contracts remain frozen. | M2 冻结锚点（`test_m2_*`） |
| **P29** | System detects execution problems; Agents decide business responses. | AST：检测函数与决策函数分离（无 auto_replan） |
| **P30** | High-impact actions must respect Authority and Autonomy independently. | U10/U11：固定动作 Owner 确认与当前双 Authority 重查；存储失败保留批准；其他入口覆盖由 U26 补齐 |

**审计补充（M3.0 新增，来自 repository re-audit）**：

| # | 不变量 | 为什么补 |
| --- | --- | --- |
| **P31** | Provisioning steps and readiness items must describe the same environment facts. | 步骤与就绪项不一致 ⇒ "配了但没检"或"检了但没配"（漂移口） |
| **P32** | Every declared readiness item must name a real table/column source. | 来源可以解析到真实表列（M2 A4 同款纪律） |
| **P33** | A declared capability-gap action must have either a live tool or an owner stage. | U12：七类动作全部有真实工具；Runtime 目录、非法输入/权限及部分成功行为锚点 |
| **P34** | Provenance gaps are explicitly owned; repaid gaps require behavioral evidence. | U5：字段、创建写者、历史读者和数据库不可变守卫；旧值 unknown |
| **P35** | Telemetry vocabulary must not collide with frozen domain event vocabulary. | P16 的词汇层守卫 |
| **P36** | M3 must not introduce a second project/task/DAG container. | M2 冻结面的 M3 侧守卫（防止执行层另造图） |

---

## 20. 与 M2 冻结面的关系（P28）

M3 **依赖** M2，不重构 M2：

| M2 冻结物 | M3 的态度 |
| --- | --- |
| Project / Task / Decision / DAG / Review / Handoff / WorkOrder bridge | **只读消费**；碰之前先跑 M2 冻结锚点 |
| `evaluate_dispatch()` / `resolve_ready_tasks()` | 不改；M3.2 在**外面**加就绪查询 |
| `DecisionIntent` / `ToolAudit` | 不改；M3 的动作工具复用同一信封 |
| `FACT_EVENTS` / `DECISION_NEEDED_EVENTS` | 封闭；新增 = 过 M2 契约评审 |
| 105 条 M2 不变量 | 全部保留 enforced；M3 不删除、不弱化 |

**触发条件（必须跑锚点）**：任何 M3 阶段碰 Project / Task / Decision / Tool Executor / DAG /
Review / Handoff / WorkOrder bridge ⇒ 先跑 M2 freeze anchors；碰 Recruitment / Talent Market /
Contract / Escrow / Settlement ⇒ 先跑 T2 / M1 锚点。

---

## 21. M3 明确不做

```text
公司股权 / 融资 / 公司交易 / 贷款 / 分红 / 并购 / 多币种 / 复杂仲裁
公开多人服务器 / 真正世界宏观经济
新的中央 Planner / 系统自动 Team Formation / 系统自动招聘 / 系统自动辞退
完整 Agent 社交系统
Codex / Claude Code / OpenCode / Custom 的新 Runtime 类型（保持 placeholder）
```

M4 才做 Agent & Task Ecosystem（Official/Player Task Supply、Agent Market、Reputation、
Task Difficulty、Market Discovery、NPC Supply/Demand、Social/Ranking/Achievements）；
M5 才做 Autonomous Company。Eidolon 始终是 **Agent Society OS**。

---

## 22. 阶段职责划分

| 阶段 | 主题 | 主要交付 | 迁移 |
| --- | --- | --- | --- |
| **M3.0** | Production Agent Contract Freeze | 本文 + 实施计划 + `work/production.py` + 守卫测试（P1–P36） | **零** |
| **M3.1** | Real Runtime Provisioning | Hermes/OpenClaw 真实拉起、健康探测、provider/model 校验、幂等、对账 | 可能 1（provenance 快照列） |
| **M3.2** | Ready-to-Work & Runtime Readiness | AgentReadiness 读面 + 派发集成（外挂，不改 M2 内核） | 无 |
| **M3.3** | Capability Gap Actions | learn / recruit / reassign 等工具化（复用决策信封） | 可能 1（学习事实） |
| **M3.4** | Execution Telemetry & Recovery | 遥测落点、重试语义、崩溃对账 | 1（遥测事实） |
| **M3.5** | Work Experience & Unified Resume | WorkExperience 读模型 + Person Timeline | 视裁决（可能 1 投影表） |
| **M3.6** | Reliability / Specialization / Collaboration | 派生指标 + 专精 + 协作事实 + 管理履历 | 无（派生） |
| **M3.7** | Agent Asset Read Model | AgentProfile 读模型 + 市场投影复用 | 无 |
| **M3.8** | Agent Operations UX | Agent Detail 面板（结构化 activity） | 无 |
| **M3.9** | Authority / Escalation / Binding Hardening | M1 面 Authority+Autonomy、ESCALATE 路由、换绑历史 | 视需要 |
| **M3.10** | Real Runtime Golden Path & Freeze | 3 条真实场景 + `docs/m3-freeze.md` | 无 |

---

## 23. 技术债登记（M3.0 复审计发现，带 owner）

| # | 债 | 现状 | owner |
| --- | --- | --- | --- |
| 1 | **测试无套件级隔离** | U1 已关闭：逐测试数据库/目录/应用生命周期与全局状态隔离，默认序 + 5 个实际随机序均通过；历史为 6 次 5 红 | M3.1 U1，[证据](m3-u1-test-isolation.md) |
| 2 | `deployment_mode` 未进 WorkSession 快照 | 只有实例上的当前值 | M3.1 |
| 3 | Provider/Model 无名称快照 | 只有 id + model 字符串 | M3.5 |
| 4 | Token/成本遥测缺失 | 无 runtime token 事实（`learning.py` 的 `tokens_used` 是学习预算，不是执行） | M3.4 |
| 5 | 无 WorkExperience 载体 | CareerEvent 是职位审计，不含 task/project/artifact | M3.5 |
| 6 | 学习/市场工具面缺失 | 30 个工具里没有 learn / search_market / inspect_candidate | M3.3 |
| 7 | 无 provider/model 可用性主动探测 | 只读 `providers.enabled` 与 binding 存在性 | M3.1 |
| 8 | 无 runtime 对账 | 只有 `refresh_status()` 单实例刷新，没有 DB⇄backend 全面对账 | M3.1 |
| 9 | 既有 flake | U1 隔离后，`test_m1_hardening::test_races_have_exactly_one_winner` 在默认序及 5 个随机序均通过；保留真实独立连接、屏障及唯一赢家断言 | M3.1 U1，[证据](m3-u1-test-isolation.md) |
| 10 | 历史脏值 | `work_orders.project_id` 无效引用等（M2 纪律：不猜、不美化） | 人工立项 |
| 11 | 技术重试计数器无承载 | 契约声明 `technical_retry_count`，但 `work_sessions` 没有该列（管理侧 `tasks.rework_count` 已有） | M3.4 |
| 12 | `career_events` 缺 Person 口径列 | 只有 `employee_id`；换公司后历史归因会断链（已登记 `PERSON_SCOPED_COLUMN_OWED`） | M3.5 |
| 13 | 工具面缺学习/市场动作 | 30 个工具里没有 learn / search_market / inspect_candidate（能力缺口四选一只走通一条路） | M3.3 |

---

## 24. 关键裁决记录（M3 ADR）

| ADR | 裁决 | 理由 |
| --- | --- | --- |
| **M3-ADR-1** | Agent 不新增 SoT；`AgentProfile` 是读模型 | 第二条身份真相会让"人级资产随人走"失效（P1/P2/P26） |
| **M3-ADR-2** | 就绪是四层派生事实，不新增 bool 列 | 一行 bool 无法解释阻塞原因，且会长出写路径（P4/P5） |
| **M3-ADR-3** | Provisioning 步骤与就绪项共用同一份环境事实词表 | 防止"配了不检 / 检了不配"（P31） |
| **M3-ADR-4** | mock 与 real 的判定链唯一（runtime_type → evidence environment） | 教程/CI 不能刷真实能力（P24/P25） |
| **M3-ADR-5** | 遥测事件与领域事件是两套封闭词表 | 执行环境的噪音不得变成业务真相（P16/P35） |
| **M3-ADR-6** | 技术重试归系统、管理 replan 归 Manager，分开计数 | 混在一起等于系统替 Manager 放弃方案（P15） |
| **M3-ADR-7** | Work Experience 首选派生读模型；物化投影单独立项 | CareerEvent 不足，但"先建表"会造第二套履历（P18/P23） |
| **M3-ADR-8** | Reliability/Specialization 必须是可解释派生指标，禁止总战力 | 神秘数值无法归因，也无法与 Evidence 对齐（P19/P20/P21） |
| **M3-ADR-9** | Provenance 快照写一次，Binding 变化只影响未来 | 历史不可改写是履历可信的前提（P14/P23） |
| **M3-ADR-10** | 孤儿 runtime 只能登记/修复，不得静默删除 | 删除外部资源是不可逆动作，必须由人发起（P7） |
| **M3-ADR-11** | 能力缺口动作全部来自 Manager/User 决策，系统只给事实与校验 | 最高原则在执行层的展开（P9/P10） |
| **M3-ADR-12** | M3.0 零迁移；确需迁移则暂停并解释 | 契约冻结不应顺手改库（Migration Discipline） |

---

## 25. 术语表（M3 冻结）

| 术语 | 含义 | 不是什么 |
| --- | --- | --- |
| **Agent** | 产品语义：Person + 任职 + Runtime + 长期资产的整体 | 不是数据库实体 |
| **AgentProfile** | 读模型（长期资产 + 当前运行态分开） | 不是身份真相 |
| **AgentReadiness** | 派生就绪事实（四层 + 必答项 + 解释） | 不是 bool 列 |
| **Provisioning** | 环境编排（工作区/实例/供应商），幂等 | 不是"建个容器"的单动作 |
| **RuntimeInstance** | 当前执行环境的持久记录行 | 不是 Agent；行存在 ≠ READY |
| **WorkExperience** | 真实工作事实的履历投影 | 不是第二套能力系统 |
| **Telemetry** | 执行环境的结构化事件 | 不是领域事件 |
| **Technical Retry** | 系统对可恢复故障的重试 | 不是管理 replan |
| **Reliability Metric** | 从事实派生的可解释指标 | 不是总战力 |
| **Specialization** | 从证据/历史收敛出的专精 | 不是玩家点的标签 |
| **ESCALATE** | 把决定路由到更有权限的人/Agent | 不是系统自己判断该怎么办 |

---

## 26. 与 M4 / M5 的边界

```text
M3  负责：Agent 怎么真正工作，并因为工作而成长
M4  负责：Agent & Task Ecosystem（任务供给、市场、声誉、难度、发现、排行榜）
M5  负责：Autonomous Company（自主发现机会 → 接单 → 组织资源 → 扩张）
```

M4/M5 都**不重新发明执行层** —— 那正是 M3 冻结的东西。

---

## 27. 收口

> 一个 RuntimeInstance 行不是 Agent。
> 一个容器 Running 也不代表 Agent Ready。
> 一个模型调用成功不代表 Task 成功。
> 一个 Task 成功不代表 Agent 自动获得能力。
> 一个职位不会赋予 Skill。
> 一次招聘不会复制 Person 资产。
> 一个 Capability Gap 不允许系统替 Manager 做选择。
> 一个 Runtime Failure 是事实，不是管理决策。
> Experience 来自真实工作事实。
> Competency 来自 Evidence。
> Specialization 来自长期事实。
> Agent 的价值应该可以解释，而不是由神秘战力值决定。
