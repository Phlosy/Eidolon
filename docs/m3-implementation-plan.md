# M3 — Agent Production & Growth 实施计划

> **执行基线**：本文（`docs/m3-implementation-plan.md`）。
> **设计**：`docs/m3-agent-production-growth-design.md`（M3.0 冻结）。
> **前置冻结面**：M2（`docs/m2-freeze.md`，105/105 enforced）、M1、T2、T1、R1。
> **代码化契约**：`apps/server/app/work/production.py` + `apps/server/tests/test_m3_contract.py`。

**最高原则（不重新谈判）**：

```text
System provides facts.   Agent makes decisions.
System validates.        System executes.        System records.
```

**M3 的边界一句话**：M2 负责"公司怎么组织工作"；M3 负责"Agent 怎么真正工作，并因为工作而成长"。

---

## 1. M3 总览

### 1.1 M3 做

```text
真实 Runtime 拉起（Hermes / OpenClaw）与健康/能力探测
AgentReadiness（派生就绪事实，四层，不新增 bool/表）
Capability Gap 的动作工具化（学习 / 换人 / 招聘 / 改方案 / 拆任务 / 委派）
执行遥测（结构化事件）、技术重试与管理 replan 分离、崩溃对账
Work Experience（履历投影）与 Unified Person Timeline
可靠性指标 / 专精 / 协作事实 / 管理履历（全部派生、可解释）
AgentProfile（读模型）与市场投影复用
Agent Operations UX（结构化活动，不暴露 chain-of-thought）
Authority / Autonomy / ESCALATE / Rebinding 在 M1 面的收口
真实 Runtime Golden Path × 3 + M3 冻结
```

### 1.2 M3 不做（写进设计 §21）

```text
公司股权/融资/公司交易/贷款/分红/并购/多币种/复杂仲裁
公开多人服务器 / 真正世界宏观经济
新的中央 Planner / 系统自动 Team Formation / 自动招聘 / 自动辞退
完整 Agent 社交系统
Codex / Claude Code / OpenCode / Custom 新 Runtime（保持 placeholder）
```

### 1.3 阶段表

| 阶段 | 主题 | 迁移 | 状态 |
| --- | --- | --- | --- |
| M3.0 | Production Agent Contract Freeze | 零 | **IN_PROGRESS** |
| M3.1 | Real Runtime Provisioning | 可能 1 | PLANNED |
| M3.2 | Ready-to-Work & Runtime Readiness | 无 | PLANNED |
| M3.3 | Capability Gap Actions | 可能 1 | PLANNED |
| M3.4 | Execution Telemetry & Recovery | 1 | PLANNED |
| M3.5 | Work Experience & Unified Resume | 视裁决 | PLANNED |
| M3.6 | Reliability / Specialization / Collaboration | 无 | PLANNED |
| M3.7 | Agent Asset Read Model | 无 | PLANNED |
| M3.8 | Agent Operations UX | 无 | PLANNED |
| M3.9 | Authority / Escalation / Binding Hardening | 视需要 | PLANNED |
| M3.10 | Real Runtime Golden Path & Freeze | 无 | PLANNED |

---

## 2. 横切要求（每个阶段都要过）

1. **不变量先行**：新概念先在 `app/work/production.py` 冻契约（含锚点与反例注入），再写实现。
2. **门禁全绿**：`pytest`（固定序）/ `ruff check` / `ruff format --check` / `alembic check` /
   前端 `tsc` / `eslint` / `prettier` / `vitest` / `build`（碰前端才跑）。
3. **反例注入验证**：每个新守卫必须"注入违规 → 转红 → 撤回 → 转绿"，禁止声明式装饰。
4. **冻结锚点回归**：
   - 碰 Project / Task / Decision / Tool Executor / DAG / Review / Handoff / WorkOrder bridge
     ⇒ 先跑 M2 freeze anchors；
   - 碰 Recruitment / Talent Market / Contract / Escrow / Settlement
     ⇒ 先跑 T2 / M1 锚点。
5. **唯一写入路径**：新表/新列必须能回答"谁写、谁读、为什么不是别处"；同一事实不留两个落点。
6. **迁移纪律**：additive first；`up → down → up`；`alembic check` 无漂移；不为"未来可能要的表"建表。
7. **真实 Runtime 安全**（M3.1 起）：
   - 真实 Runtime **不是默认可信**：Container / Workspace / Tool 权限由系统控制；
   - Runtime **不能伪造 actor identity**，**不能自行声明 Authority**；
   - Tool Executor 继续走 M2 Authority 边界；
   - **Provider Secret 不得暴露给 Agent**（只进 Runtime 环境，不进 prompt/产物/审计）。
8. **Git 纪律**：动手前 `git status` / `git log`；不碰 `tmp/`、历史未跟踪文件、既有 WIP format 文件；
   完成后 `git diff` + `git status` 再提交；每个阶段独立 commit。
9. **文档同步**：阶段收尾更新本文 §16 Progress + `docs/handover.md` + 设计文档不变量表。
10. **测试隔离**（技术债 1）：M3.1 之前先修套件级 per-test 隔离（真随机序当前 6 次 5 红）。

---

## 3. M3.0 Production Agent Contract Freeze

### Goal

在进入真实 Runtime 之前，把"什么叫 Agent 可工作、环境事实怎么表达、真实与模拟怎么区分、
履历属于谁、什么事实能变成能力证据"**冻结成契约与守卫**，并让后续每个 M3 阶段都有可回归的锚点。

### 交付物

```text
docs/m3-agent-production-growth-design.md      设计（形态 / 边界 / 36 条不变量 / 12 条 ADR）
docs/m3-implementation-plan.md                 本文（M3.0–M3.10 / 验收 / 风险 / Progress）
apps/server/app/work/production.py             M3 契约层（纯契约，不建表、不发事件、不写业务）
apps/server/tests/test_m3_contract.py          契约 + 守卫测试（P1–P36 的现存锚点）
docs/{architecture,handover}.md                指向 M3 冻结面
```

契约内容（`production.py`）：

| 契约 | 内容 |
| --- | --- |
| 生产阶段 | `ProductionStage`（employment / provisioning / runtime / execution） |
| 就绪映射 | `READINESS_ITEM_STAGE`（item → stage，与 `READINESS_FACT_SOURCES` 双向一致） |
| 编排步骤 | `PROVISIONING_STEP_ORDER` == `READINESS_REQUIRED_FOR_EXECUTION["real"]` |
| 失败原因 | `ProvisioningFailureReason`（8 个可解释原因） |
| 幂等契约 | `IDEMPOTENT_ENSURE_OPERATIONS` + 覆盖场景表 |
| 对账契约 | `RECONCILIATION_ACTIONS`（detect/mark/repair；**无删除**） |
| 真实/模拟 | `execution_mode()` + `EVIDENCE_ENVIRONMENT_*`（与 `evidence.policy` 逐字一致） |
| Provenance | `PROVENANCE_REQUIRED_FIELDS` / `PROVENANCE_PRESENT` / `PROVENANCE_OWED_FIELDS` |
| 能力缺口动作 | `CAPABILITY_GAP_ACTIONS`（7 条，每条 tool 或 owner_stage） |
| 重试语义 | `TECHNICAL_RETRY_OWNER` / `MANAGEMENT_REPLAN_OWNER` + 分属字段 |
| 遥测词表 | `RUNTIME_TELEMETRY_KINDS`（∩ 领域事件 = ∅） |
| 履历契约 | `WORK_EXPERIENCE_FIELDS` + `WORK_EXPERIENCE_SOURCES`（全部真实表列） |
| 资产读模型 | `AGENT_PROFILE_LONG_TERM` / `AGENT_PROFILE_CURRENT_STATE`（两轴分离） |
| 可靠性 | `RELIABILITY_METRICS`（全部带来源，无总战力） |
| Agent SoT 禁区 | `FORBIDDEN_AGENT_SOT_TABLES` |
| 不变量 | `PRODUCTION_INVARIANTS`（P1–P36，全部 enforced） |

### 明确不做（本阶段边界）

```text
✗ 真的启动 Hermes / OpenClaw 容器
✗ 新建 WorkExperience / telemetry 迁移
✗ 重做 UI / 改 Talent Market
✗ 实现全部 capability gap actions
✗ 实现 Authority hardening
✗ 任何 alembic 迁移（零迁移）
```

### Acceptance

| # | 判据 |
| --- | --- |
| A1 | `production.py` 是**纯契约层**：不 import 领域写路径、不建表、不发事件（AST 守卫） |
| A2 | P1–P36 全部 `enforced=True` 且锚点**真实存在**（跨 M3 测试模块解析） |
| A3 | 就绪与 stage 双向一致；就绪项来源可解析到真实表列 |
| A4 | 无 `employee.ready` 列 / 无 `agent_*` 表 / 无 `power` 列（metadata + AST） |
| A5 | mock 判定链与 `evidence.policy` 常量逐字一致 |
| A6 | 遥测词表与 `FACT_EVENTS` / `DECISION_NEEDED_EVENTS` 不重叠 |
| A7 | 每条能力缺口动作都有 tool 或 owner_stage |
| A8 | 欠账（provenance 缺列）显式登记且非空 |
| A9 | 设计文档 ↔ 代码：不变量表逐条一致（文本 + 集合） |
| A10 | 反例注入：每条守卫至少 1 次"注入转红"（本阶段实测） |

### Risks

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 契约层长出"替 Manager 决策"的函数 | 命名守卫（`pick_/choose_/auto_/should_`）+ AST（沿用 M2 A11 同款） |
| 2 | 就绪被写成 bool 第二真相 | metadata 无 `ready` 列 + 派生断言 + 反例注入 |
| 3 | 幽灵工具（词表写了但没人做） | `CAPABILITY_GAP_ACTIONS` 强制 tool 或 owner_stage |
| 4 | 文档漂移 | 设计表 ↔ 代码逐条对拍（M2.10 教训：声明性条数必须机器检） |
| 5 | 新契约模块绕过 M2 冻结面 | P28 + M2 锚点回归 |

---

## 4. M3.1 Real Runtime Provisioning

### Goal

把 `RuntimeInstance row exists` 升级为 `Runtime truly exists and is healthy`：真实支持 Hermes 与
OpenClaw（已有 adapter），补齐健康探测、provider/model 校验、幂等 ensure 与 DB⇄backend 对账。

### 交付物

```text
provisioning 编排加固（work/readiness.orchestrate 的幂等与分步事实）
health / provider / model / capability probe（4 个探测，落在 Reason）
reconciliation service（detect orphan / missing / stale → mark / repair，**不删除**）
provenance 快照列（deployment_mode 等，additive 迁移）
套件级测试隔离（技术债 1）+ 既有 flake 处理（技术债 9）
```

### 明确不做

```text
✗ 新 Runtime 类型（Codex / Claude Code / OpenCode / Custom 继续 placeholder）
✗ 复杂容器编排（K8s / 多机调度）
✗ 自动删除未知容器
```

### Acceptance

| # | 判据 |
| --- | --- |
| B1 | `ensure_runtime_ready(employee)` 幂等：连续 N 次不新增容器/工作区/实例行（P6） |
| B2 | 四类现实场景（行无容器 / 容器无行 / unhealthy / 崩溃后重启）都有确定行为 |
| B3 | 对账输出只有 detect/mark/repair，无删除调用（P7） |
| B4 | 失败原因落在 `ProvisioningFailureReason`（无"未知错误"兜底文案） |
| B5 | 真实 Hermes 与 OpenClaw 各一条集成路径（可跳过：环境门控 + 明确标记 skipped 原因） |

### Risks

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | CI/本地无 Docker 或镜像缺失 ⇒ 门禁不可重复 | 环境门控 + `detect()` 前置 + 明确 skip 原因，不假装跑过 |
| 2 | 真实模型调用产生费用 | 默认 mock；真实调用需显式开关 + 上限 |
| 3 | 容器/网络资源泄漏 | 幂等 + 对账 + 显式清理入口（人发起） |
| 4 | 启动探测拖慢服务启动 | 探测异步化 + 超时 + 状态回报，不阻塞 lifespan |

---

## 5. M3.2 Ready-to-Work & Runtime Readiness

### Goal

把 §5 的 AgentReadiness 从"契约形状"变成**可查询、可解释、可被派发消费**的就绪读面，
且**不修改 M2 的 Dispatcher 内核**。

### 交付物

```text
AgentReadiness 读面（复用 work/readiness.readiness_report）
派发集成：evaluate_dispatch 之前查就绪 → 不 ready 时发 DECISION_NEEDED（不自动改派）
API + 工具面（readiness / runtime status）补齐解释字段
```

### 明确不做

```text
✗ 改写 M2 dispatcher / DAG 内核
✗ 就绪 ⇒ 自动改派 / 自动换人（P8）
✗ 新增 ready bool 列
```

### Acceptance

| # | 判据 |
| --- | --- |
| C1 | 就绪项覆盖四层；`NOT_READY` 必带原因（P4/P5） |
| C2 | 派发路径只**读**就绪；不 ready ⇒ 事件 + 等待（P8） |
| C3 | mock 与 real 的必答项不同且可解释（P24） |
| C4 | 招募后未完成 provisioning ⇒ 不 READY（P13） |

### Risks

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 就绪检查变成"先有鸡还是先有蛋"（就绪要求实例，实例要求就绪） | 依赖方向固定：employment → provisioning → runtime → execution |
| 2 | 就绪计算过重（每次派发全量算） | 事实读缓存（读模型层），不落第二真相 |

---

## 6. M3.3 Capability Gap Actions

### Goal

把 Manager 面对能力缺口时的动作**完整工具化**：学习、换人、招聘、改方案、拆任务、委派。
系统只给事实 + 校验 + 执行 + 记录；**不替 Manager 选**。

### 交付物

```text
读工具：inspect_gap（缺口事实）、search_market / inspect_candidate（市场事实，复用 T2 读面）
写工具：create_learning_priority / request_training（学习事实）、recruit_from_market（经 Authority+Budget）
组合：split_task（create_task + create_dependency 的显式语义）
DecisionRecord 归属：每个动作都过决策信封（署名）
```

### 明确不做

```text
✗ 按阈值自动选动作（P9）
✗ 学习 ⇒ 直接加 competency（P11）
✗ 招聘复制 Person 资产（P12）
✗ 重开 T2 的 Source of Truth（P27）
```

### Acceptance

| # | 判据 |
| --- | --- |
| D1 | 7 条动作全部有工具（或明确 owner_stage），无幽灵词条（P33） |
| D2 | 学习类动作不产生任何 competency 写（AST + 行为）（P11） |
| D3 | 招募动作经 Authority + Budget 校验，且完成后必须过 provisioning（P13） |
| D4 | 市场读面复用 T2 投影，无新决策/定价真相（P27） |

---

## 7. M3.4 Execution Telemetry & Recovery

### Goal

真实执行进入生产后的观测与恢复：结构化遥测、技术重试与管理 replan 分离、崩溃对账。

### 交付物

```text
RUNTIME_TELEMETRY_KINDS 落点（结构化事件；token 流只进日志/短期）
technical retry（有限次 + 退避 + 可解释）与 rework/replan 分离（P15）
启动对账（stuck WorkSession / lost session / partial artifact）
成本与 token 事实（替换 tokens = 0 占位）
```

### 明确不做

```text
✗ 永久保存全部 token stream
✗ 让 runtime 事件直接改领域状态（P16）
✗ 系统自动 replan（P29）
```

### Acceptance

| # | 判据 |
| --- | --- |
| E1 | 遥测事件不改领域状态（AST + 行为）（P16） |
| E2 | 重试次数与技术重试语义绑定；管理 replan 走 DecisionRecord（P15） |
| E3 | 崩溃后重启不留下永久 `running` 僵尸（对账有明确结果） |
| E4 | 部分写入的产物不进入 handoff |

---

## 8. M3.5 Work Experience & Unified Resume

### Goal

建立"这个 Person 真正干过什么"的履历投影：来自真实域事实、带历史快照、可查询、可解释。

### 交付物

```text
WorkExperience 读模型（字段契约见设计 §13.2）
Person Timeline 统一读模型（教育 / 培养 / 任职 / 项目与任务 / 评估 / 重大决策 / 成就）
（若需物化）投影表 + 单独立项 ADR + 迁移
```

### 明确不做

```text
✗ 新建第二套能力/证据系统（P18）
✗ 用当前 Position 反推历史（P23）
✗ 把所有历史事件塞进一张表
```

### Acceptance

| # | 判据 |
| --- | --- |
| F1 | WorkExperience 字段全部映射到真实表列（P18/P32 同款） |
| F2 | 换公司/换职位后历史不变（快照断言）（P23） |
| F3 | Timeline 只读聚合，无写路径 |

---

## 9. M3.6 Reliability / Specialization / Collaboration

### Goal

把 Agent 价值变成**可解释的派生事实**：可靠性指标、专精、协作关系、管理履历。

### 交付物

```text
RELIABILITY_METRICS 的读面实现（每项带来源与算子）
Specialization 推导（证据 / 知识 / SkillUsage / 任务历史）
Collaboration 事实（handoff / review / co-task / dependency / rework 关系）
Management Experience（DecisionRecord + 结果事实）
```

### 明确不做

```text
✗ 单一"总战力"数值（P21）
✗ 黑盒 chemistry 分数
✗ 中央系统主观评价"好决定"（P22）
```

### Acceptance

| # | 判据 |
| --- | --- |
| G1 | 每个指标都能回答"从哪些事实算出来"（P19） |
| G2 | 专精只从证据/历史收敛（P20） |
| G3 | 管理履历来自决策 + 结果事实（P22） |
| G4 | 元数据与 AST 无 power/overall_score 字段（P21） |

---

## 10. M3.7 Agent Asset Read Model

### Goal

产品层 `AgentProfile`：长期资产与当前运行态**分开**，供 UI / CLI / 市场投影复用。

### 交付物

```text
AgentProfile 读模型（两轴字段表见设计 §16）
API / 工具面（读）+ i18n
T2 Market Read Projection 增强（复用，不改 T2 SoT）
```

### 明确不做

```text
✗ AgentProfile 落表 / 新增身份真相（P1/P26）
✗ 当前状态覆盖历史（P23）
✗ 重开 T2 Source of Truth（P27）
```

### Acceptance

| # | 判据 |
| --- | --- |
| H1 | AgentProfile 是读模型（无表、无写路径）（P26） |
| H2 | 两轴字段不混（长期资产 vs 当前运行态） |
| H3 | 市场投影只读复用（P27） |

---

## 11. M3.8 Agent Operations UX

### Goal

让玩家看见"这个 Agent 正在做什么"：就绪、运行健康、当前任务、会话、工具活动、产物、阻塞原因、
评审与返工、成长。

### 交付物

```text
Agent Detail 面板（Readiness / Runtime / Provider / Task / WorkSession / Timeline / Artifacts /
                   Blocked / Review / Rework / Experience）
i18n（中英逐键）+ types 同步
```

### 明确不做

```text
✗ 暴露完整 chain-of-thought（只给结构化 activity / decision / result）
✗ 前端造假数据（缺事实就显示"未知/无"）
```

### Acceptance

| # | 判据 |
| --- | --- |
| I1 | 面板字段全部来自读面事实（无前端推算） |
| I2 | 无 CoT 字段 |
| I3 | 前端门禁全绿（tsc/eslint/prettier/vitest/build） |

---

## 12. M3.9 Authority / Escalation / Binding Hardening

### Goal

把 M1 面的高影响端点接入 Authority + Autonomy 双门禁，补齐 ESCALATE 路由与换绑历史语义。

### 交付物

```text
M1 端点门禁：合同验收 / 工单验收 / 大宗采购 / 招聘 / 辞退
ESCALATE 路由（parent manager → higher authority → human owner）
Rebinding 语义：只影响未来（P14/P23）
Autonomy Policy 的必要部分（high_impact ⇒ 确认通道）
```

### 明确不做

```text
✗ 通用 ABAC/策略引擎（M2-ADR-20 同款纪律）
✗ 系统自己判断升级到谁（只按路由表）
```

### Acceptance

| # | 判据 |
| --- | --- |
| J1 | 高影响动作同时过 Authority 与 Autonomy（P30） |
| J2 | ESCALATE 无自动状态目标（M2 契约）+ 路由可解释 |
| J3 | 换绑不改写历史会话/决策（P14） |
| J4 | 触碰 M1 面 ⇒ M1/T2 锚点全绿 |

---

## 13. M3.10 Real Runtime Golden Path & Freeze

### Goal

三条**真实**场景 + M3 冻结面。

### 场景（必须至少一个真实 Runtime，禁止全 mock）

```text
A 真实 Agent 工作：Project → Manager 决策 → DAG → 真实 Worker Runtime → 工具执行 → Artifact
                  → Handoff → Reviewer Runtime → PASS/REWORK → 交付 → Work Experience → Evidence
B 缺口 → 招聘 → 工作：缺口事实 → 查内部人（无合适）→ 市场搜索 → Manager 选人 → 招聘
                  → Provisioning → READY_TO_WORK → 派任务 → 真实执行 → Review PASS
C Runtime 失败/恢复：unhealthy 或 provider 不可用 → 系统检测 → 任务不可派发/会话失败
                  → Manager 收到事实 → Manager 决定 retry/wait/rebind/reassign
                  → 系统校验 → 恢复执行（证明"系统检测、Manager 决定"没被破坏）
```

### 交付物

```text
apps/server/tests/test_m3_golden_path.py（A/B/C + 冻结面自检）
docs/m3-freeze.md（机器可检：形态 / 不变量 / 唯一写入路径 / 模块边界 / 关键裁决 / 技术债 / 留给 M4）
```

### Acceptance

| # | 判据 |
| --- | --- |
| K1 | 三条场景各一条真实调用链（真实 Runtime，非"接口 200"） |
| K2 | P1–P36 全部 enforced 且锚点现存 |
| K3 | 反例注入验证（M3 全部守卫 + 历史 64 条复跑） |
| K4 | M2 / M1 / T2 冻结锚点全绿 |
| K5 | 完整门禁全绿（含前端 build） |
| K6 | `docs/m3-freeze.md` 落盘且机器可检（章节 / 条数 / 迁移表 / 三条场景锚点） |

---

## 14. 验收矩阵（K1–K10）

| # | 判据 | 阶段 |
| --- | --- | --- |
| K1 | 三条真实 Golden Path（≥1 真实 Runtime） | M3.10 |
| K2 | P1–P36 全部 enforced + 锚点现存 | M3.0 起，M3.10 收口 |
| K3 | 反例注入：每条守卫"注入转红 → 还原转绿" | 每阶段 |
| K4 | M2 / M1 / T2 冻结锚点全绿 | 每阶段（触碰即跑） |
| K5 | 完整门禁（pytest / ruff ×2 / alembic / 前端四件套 + build） | 每阶段 |
| K6 | `docs/m3-freeze.md` 机器可检 | M3.10 |
| K7 | 就绪不可伪造（无 bool 第二真相 / 无 agent_* 表） | M3.0 |
| K8 | mock / real 可区分且 mock 证据继续打折 | M3.0 |
| K9 | 遥测不成为领域真相 | M3.0（词汇）+ M3.4（行为） |
| K10 | 履历历史不可改写（快照 + 只影响未来） | M3.0（契约）+ M3.5/M3.9（行为） |

---

## 15. 阶段 × 不变量矩阵

| 不变量 | M3.0 | M3.1 | M3.2 | M3.3 | M3.4 | M3.5 | M3.6 | M3.7 | M3.8 | M3.9 | M3.10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1 Agent 无新 SoT | ● | | | | | | | ✓ | | | ✓ |
| P2 Person 是资产主体 | ● | | | | | ✓ | | ✓ | | | ✓ |
| P3 Runtime 配置 ≠ 能力 | ● | | | | | | | | | | ✓ |
| P4 就绪是派生 | ● | | ✓ | | | | | | | | ✓ |
| P5 实例行 ≠ READY | ● | ✓ | ✓ | | | | | | | | ✓ |
| P6 Provisioning 幂等 | ● | ✓ | | | | | | | | | ✓ |
| P7 对账不静默删除 | ● | ✓ | | | | | | | | | ✓ |
| P8 不就绪不自动改派 | ● | | ✓ | | | | | | | | ✓ |
| P9 缺口不自动招聘 | ● | | | ✓ | | | | | | | ✓ |
| P10 动作来自 Manager | ● | | | ✓ | | | | | | | ✓ |
| P11 学习不直接加能力 | ● | | | ✓ | | | | | | | ✓ |
| P12 招聘不复制资产 | ● | | | ✓ | | | | | | | ✓ |
| P13 招聘须过 provisioning | ● | ✓ | ✓ | ✓ | | | | | | | ✓ |
| P14 历史 provenance 不可变 | ● | ✓ | | | ✓ | | | | | ✓ | ✓ |
| P15 技术重试 ≠ 管理 replan | ● | | | | ✓ | | | | | | ✓ |
| P16 遥测 ≠ 领域真相 | ● | | | | ✓ | | | | | | ✓ |
| P17 exit=0 ≠ 任务成功 | ● | | ✓ | | ✓ | | | | | | ✓ |
| P18 履历来自真实事实 | ● | | | | | ✓ | | | | | ✓ |
| P19 可靠性派生 | ● | | | | | | ✓ | | | | ✓ |
| P20 专精派生 | ● | | | | | | ✓ | | | | ✓ |
| P21 无神秘总战力 | ● | | | | | | ✓ | ✓ | | | ✓ |
| P22 管理履历来自决策+结果 | ● | | | | | | ✓ | | | | ✓ |
| P23 当前状态不覆盖历史 | ● | | | | | ✓ | | ✓ | | ✓ | ✓ |
| P24 mock/real 可区分 | ● | | ✓ | | | | | | ✓ | | ✓ |
| P25 mock 证据打折 | ● | | | | | | | | | | ✓ |
| P26 AgentProfile 是读模型 | ● | | | | | | | ✓ | ✓ | | ✓ |
| P27 T2 是交易真相 | ● | | | ✓ | | | | ✓ | | | ✓ |
| P28 M2 契约冻结 | ● | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| P29 系统检测 / Agent 决定 | ● | ✓ | ✓ | ✓ | ✓ | | | | | ✓ | ✓ |
| P30 Authority ≠ Autonomy | ● | | | | | | | | | ✓ | ✓ |
| P31 步骤与就绪同源 | ● | ✓ | ✓ | | | | | | | | ✓ |
| P32 就绪来源可解析 | ● | | ✓ | | | ✓ | | | | | ✓ |
| P33 动作有工具或归属 | ● | | | ✓ | | | | | | | ✓ |
| P34 欠账显式登记 | ● | ✓ | | | | ✓ | | | | | ✓ |
| P35 遥测词表不撞领域 | ● | | | | ✓ | | | | | | ✓ |
| P36 无第二套图容器 | ● | | | | | | | | | | ✓ |

（● = M3.0 冻契约 + 锚点；✓ = 该阶段落地行为锚点。）

---

## 16. M3 Progress

> 每完成一个阶段更新本表；新 Agent 从这里恢复上下文。

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| M3.0 | **IN_PROGRESS** | — | 设计 + 实施计划 + `work/production.py` + `test_m3_contract.py`；**零迁移** |
| M3.1 | PLANNED | — | 真实 provisioning（Hermes / OpenClaw）+ 幂等 + 对账；先修测试隔离 |
| M3.2 | PLANNED | — | |
| M3.3 | PLANNED | — | |
| M3.4 | PLANNED | — | |
| M3.5 | PLANNED | — | |
| M3.6 | PLANNED | — | |
| M3.7 | PLANNED | — | |
| M3.8 | PLANNED | — | |
| M3.9 | PLANNED | — | |
| M3.10 | PLANNED | — | |

---

## 17. 风险登记（M3 全阶段）

| # | 风险 | 影响 | 缓解 / owner |
| --- | --- | --- | --- |
| R1 | **测试无套件级隔离**（真随机序 6 次 5 红） | 所有阶段的"绿"只在字典序下成立 | M3.1 前修（碰 M1 测试基建需显式理由） |
| R2 | 真实 Runtime 依赖环境（Docker/镜像/网络） | 门禁不可重复、假绿 | 环境门控 + 明确 skip 原因；禁止 mock 冒充（M3.1/M3.10） |
| R3 | 真实模型调用成本失控 | 费用 | 默认 mock；真实调用显式开关 + 上限（M3.1） |
| R4 | Runtime 不可信（伪造身份/越权/注入） | 安全 | Tool Executor 边界 + 不暴露 Secret + 不信任 runtime 声明（M3.1/M3.9） |
| R5 | 孤儿容器/实例累积 | 资源泄漏 | 对账 detect/mark/repair（不删除）+ 人发起清理（M3.1） |
| R6 | 遥测数据量爆炸 | 存储/性能 | 只存结构化事件，token 流不永久落库（M3.4） |
| R7 | WorkExperience 物化 → 第二真相 | 履历不可信 | 首选派生；物化需 ADR（M3.5） |
| R8 | "总战力"诱惑 | 产品失信 | P21 守卫 + 指标必须可归因（M3.6） |
| R9 | 迁移爆炸（为未来建表） | 债务 | 零迁移优先；additive + up/down/up（全阶段） |
| R10 | 真实 Runtime 绕过 M2 冻结面直接写领域 | 契约破坏 | AST 守卫：runtimes 不 import 领域写路径（M3.0/M3.4） |
| R11 | Authority 接入破坏既有 M1 验收流程 | 回归 | 触碰即跑 M1/T2 锚点；灰度：先只读门禁（M3.9） |
| R12 | M3.0 契约层长出决策函数 | 原则破坏 | 命名守卫 + AST（M3.0 起，每阶段） |

---

## 18. 交付证据（M3.0，完成后填）

```text
pytest（固定序）          待填
pytest（真随机序）         待填（当前既有缺陷见 R1）
M2 freeze anchors         待填
M1 / T2 anchors           待填（若触碰）
ruff check / format       待填
alembic check / heads     待填（期望：无新操作；head 不变）
前端四件套 + build          待填（未改前端 ⇒ 仅确认）
反例注入                   待填（M3.0 守卫逐条）
commit                    待填
```
