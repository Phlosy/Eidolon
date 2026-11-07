# Eidolon 内核化工作计划（Kernel Plan v1）

> 日期：2026-11-06　基线：分支 `dev`，HEAD `43a04d5`（2026-09-21），工作树含未提交的 U16 第十三段。
> 定位：这是 **M3.4 之后的新执行基线**，取代 `m3-implementation-plan.md` 中 U17–U31 的顺序；
> 不作废 M1/T2/M2 的冻结面，但允许在 K2 以显式理由触碰它们。
> 新会话从 [`v2-handoff.md`](v2-handoff.md) 进入，再到本文 §8 Progress 领取下一个未完成的 WP。
>
> **2026-11-06 更新**：§3–§4 的 K1–K6 已被 [`v2-architecture.md`](v2-architecture.md) §8 的 R0–R6 取代
> （自研智能体框架、Postgres、strangler 重构）。§1 本质陈述、§5 纪律、§7 旧包去向继续有效。

---

## 0. 一句话

> **Eidolon = 一本关于 AI 劳动的不可篡改账本 + 一条"谁来决定"的边界 + 全过程可追溯，
> 上面跑着可定制的公司制度。**

其余一切（经济、市场、履历、能力分、教程、Office）都是这四样东西的派生或配置。

---

## 1. 本质陈述（内核的四个支柱）

### 1.1 账本（Ledger of AI Labor）
- 只追加、不可改写的 **Fact** 流：谁、在什么授权下、做了什么、产出了什么、花了多少、谁判定了什么。
- 每条 Fact 带 `idempotency_key`（重放不重复）、`causation_id`（因果链）、`prev_hash/hash`（篡改可检）。
- **单一写入路径**：任何状态变化要么是 Fact，要么是对 Fact 的派生。派生不落库（沿用 ADR-12）。
- 现有的 `events` / `tool_audits` / `decision_records` 回执 / `runtime_attempts` 遥测 / `ledger_transactions`
  本质上是同一条流的五种写法，K2 收敛它们。

### 1.2 决定边界（Decision Boundary）
沿用 M2 的最高原则并把它做成一个引擎而不是散落的 if：

```
System  提供事实、校验、执行、记录      —— 永不做管理判断
Agent   在授权范围内做管理决定          —— 经 Decision 信封，带 authority 快照
Human   对高影响动作做最终确认          —— 经 ActionConfirmation，绑定具体动作
```

每一个"门"（gate：接单、拆解、派人、评审、放款、招聘、辞退、升级）都声明 **decider 类型**，
由制度模板决定，由内核统一解析（`gate.resolve()`），不再各域手写。

### 1.3 可追溯（Provenance）
任何派生量（一个能力分、一笔余额、一份产物、一条履历）都能在一次查询内回溯到：

```
派生量 → Fact 链 → Decision（为什么） → Authority（凭什么） → Runtime run（谁跑的） → Provider/Model/费用
```

并可导出为带哈希的 bundle 离线校验。这是"验货"能力的内生来源，市场和履历的可信度全靠它。

### 1.4 制度即配置（Institution-as-Config）
公司怎么运转——有哪些角色、谁对哪个门有权、审批链几级、评审几轮、超时怎么升级——
是 **数据而不是代码**。参考现实组织（初创扁平、职能制、矩阵制、项目制、体制内请示—审批—会签—批复—归档、
开源 PR/review/merge）提供内置模板，公司可 fork 并定制；定制变更本身也是 Fact。
内核只校验模板合法并按模板解析 gate，**不内置任何一种公司的做法**。

---

## 2. 现状映射：复用什么、折叠什么

| 现有 | 处置 |
|---|---|
| `runtimes/base.py` Adapter 接口、Hermes/OpenClaw 容器、探测、预算代理 | **复用**，内核的 Runtime 边界 |
| `DecisionRecord` + Decision Envelope + Authority grants（M2.2–M2.4） | **复用**，成为 Agent 类 decider 的实现 |
| `ActionConfirmation`（U10） | **复用**，成为 Human 类 decider 的实现 |
| `EventBus` + `events` 表 | **折叠进 FactLog**（K2），保留订阅接口 |
| `tool_audits`、runtime 遥测、动作回执 | **折叠**：主记录是 Fact，旧表变只读投影或保留兼容 |
| M1 `ledger_transactions`（复式账本） | **保持独立**（金额守恒逻辑不动），但每笔交易引用触发它的 Fact；不双写金额 |
| Evidence pipeline / Assessment / Competency | **保持**，接入 Projection 框架（可从 Fact 重建） |
| U16 的 13 段恢复逻辑 | **替换**为统一重放器（K2），原测试保留为回归 |
| Readiness 四层、Fit、Coverage、WorkExperience | 已是派生，接入 Projection 框架 |
| 教程 reconcile | 已是对事实的派生门禁，不动 |
| `work_orders` / 市场 / 合同 / NPC | **保持**，K5 之前不碰 |
| 2.5D Office、向量检索、多租户、新 Runtime 类型 | **推迟**到本计划之外 |

---

## 3. 总体路线

| 阶段 | 主题 | 目的 | 预计会话数 | 依赖 |
|---|---|---|---|---|
| **K0** | 止血与基线 | 让仓库回到可工作状态 | 1 | — |
| **K1** | 真实交付 Alpha | **验证核心假设**：真实 Runtime 跑通一个小项目 | 3–5 | K0 |
| **K2** | 内核抽取 | FactLog + Outbox + Projection + 重放恢复 + 哈希链 | 5–8 | K1 |
| **K3** | 决定边界与制度层 | Mission 入口、gate 引擎、制度模板库、定制 | 4–6 | K2 |
| **K4** | 可追溯 | Provenance 查询、"为什么"面板、导出校验 | 2–3 | K2 |
| **K5** | 积累与价值 | 履历/可靠性/AgentProfile/市场投影（折叠 M3.5–M3.8） | 3–4 | K2, K4 |
| **K6** | 三条真实路径与冻结 | A 交付 / B 招聘后工作 / C 故障重放；冻结文档机器检 | 3–4 | K3, K5 |

K1 结束时有一个 **go / no-go 裁决点**：如果真实 Runtime 连一个 3 任务的小项目都交付不了，
问题清单优先于内核重构。

---

## 4. 工作包明细

每个 WP 的约束：**一个会话内可完成**；超出就拆，不允许"第 N 段"。
每个 WP 交付 = 代码 + 测试 + Progress 表一行 + （仅真实模型调用时）费用记录。

### K0 止血与基线

**WP-00 SQLite WAL 与写锁**
- `core/database.py`：SQLite 连接事件里执行 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=15000`。
- 验收：全量 pytest 与 WAL 前基线持平；`test_*_locked` 类用例不劣化；文档登记"读不再阻塞写"。
- 不做：不改 Postgres 适配。

**WP-01 收口未提交 WIP**
- 决定 `work/recovery.py::reconcile_task_evidence` + `test_m3_evidence_recovery.py`：跑通则以 `fix(evidence): 重启后按原事实补齐任务 Evidence (U16)` 提交；跑不通则 `git stash` 并在 Progress 登记。
- 更新 `handover.md` 顶部、`m3-implementation-plan.md` §16，消除"U14 下一包"的漂移；写明 U14–U16 的真实状态（已落地、未关闭）。

**WP-02 门禁基线**
- 跑全套：pytest（默认序 + 一个随机 seed）、ruff、alembic check、前端 tsc/eslint/vitest/build。
- 把数字写进本文 §8 作为 K 系列基线；记录运行时长（后续 WP 的预算参考）。

### K1 真实交付 Alpha（验证假设）

**WP-10 真实环境固定**
- 选定一个 Runtime（裁决 D1，默认 Hermes）+ 一个 Provider + 预算上限（默认 ≤ $2/次运行）。
- 复用 U3/U4 的固定镜像与探测；`make real-env-check` 一条命令报告 ready/not-ready 与原因。
- 不做：不开第二种 Runtime。

**WP-11 最小纵向链（managed 模式，零 fixture）**
```
POST /projects {name, description}   → waiting_for_management
Manager Agent（真实）用现有工具读事实、提交 Decision 信封：建 2–3 个 Task + 依赖 + 派人
Worker Agent（真实）执行 → 产物经 U14 采集进 Drive（sha256）
Reviewer Agent（真实）用现有评审工具出 PASS
项目 delivered → Evidence 落库
```
- 允许的"假"：只有 Provider 费用代理与测试 SQLite；mock adapter **不得**出现在关键路径。
- 产出：`apps/server/tests/integration/test_k1_real_delivery.py`（环境门控，skip 原因明确）+ 一次真实运行记录（run ID、费用、产物哈希）。

**WP-12 失败清单与裁决**
- WP-11 每个卡点如实记录（协议、提示词、工具桥、产物、评审、状态推进）。
- 产出 `docs/k1-findings.md`：通过项 / 未通过项 / 根因 / 是否内核问题。
- **裁决点**：go（进入 K2）或 fix-first（把清单变成 K1.5 的 WP）。

### K2 内核抽取

**WP-20 FactLog 契约（ADR）**
- 新文件 `app/kernel/facts.py`（纯契约）：
  ```
  Fact(id, seq, company_id, actor{kind: system|agent|human, id}, kind, payload,
       idempotency_key, causation_id, correlation_id, decision_id,
       prev_hash, hash, created_at)
  ```
- kind 词表分层：`work.*` / `decision.*` / `runtime.*` / `money.*` / `talent.*` / `institution.*`；与现有事件名建立映射表（不改旧名，加别名）。
- AST 守卫：`app/kernel/` 不 import 任何领域写路径。
- 迁移：一张 `facts` 表 + `fact_consumers` 游标表（additive）。

**WP-21 Outbox 投递**
- `bus.publish(db=db)` 改为：事务内 INSERT fact → 提交后由同一事务 after_commit 回调投递；进程退出靠消费者游标补投（取代"事件必须在事务提交之后发"的手工纪律）。
- 消费者以 `(consumer_name, last_seq)` 幂等推进；启动即 sweep。
- 验收：M2.7 "database is locked 15s" 场景不再可能；U16 第一段（完成事件同事务）的测试原样通过。

**WP-22 写入者收敛（渐进）**
- 顺序：`events` → `tool_audits` → ActionConfirmation 回执 → runtime attempts 遥测。
- 每步：新写入先写 Fact，旧表由 Fact 投影（或双写一个阶段后关掉）；旧读面不变。
- 不做：不动 `ledger_transactions` 的金额逻辑；只在交易上加 `fact_id` 引用。

**WP-23 Projection 框架**
- `app/kernel/projections.py`：`Projection(name, handles: set[kind], apply(fact), rebuild())`。
- 先接三个：readiness、balance 投影（已有 rebuild）、competency 聚合。
- `make projections-rebuild NAME=...`；验收：删投影表 → 重建 → 与删前一致（已有 `economy-rebuild` 的同款纪律）。

**WP-24 统一重放恢复**
- `app/kernel/replay.py`：启动时按各消费者游标重放未消费 Fact；替代 `work/recovery.py` 里按子系统分别写的 13 段。
- U16 的全部测试（含真实子进程 `os._exit`）保留为回归；通过后删除被替代的分支。
- 验收：强退注入点集合（U16 已有）全部通过，且 `recovery.py` 行数显著下降。

**WP-25 哈希链与校验**
- 写入时 `hash = sha256(prev_hash || canonical_json(fact))`，公司作用域内链式。
- `make ledger-verify`：全链校验 + 报告首个断点；篡改一行 → 必红（反例注入一条即可）。
- 不做：不做签名、不做外部锚定。

### K3 决定边界与制度设定层

**WP-30 Mission / Intake**
- 填补 current-system-audit Top 1：用户输入 → 结构化 **Mission** Fact（目标、成功标准、约束、预算、期限）。
- Project 从 Mission 派生，`Project.requirements` 不再孤立；工单桥（M2.9）投递的也是 Mission。
- 不做：系统不替公司拆解 Mission（W1 不变）。

**WP-31 制度模板 Schema**
- `app/institution/schema.py`：
  ```
  InstitutionTemplate(id, version, roles[], gates{ gate_name: {decider: system|agent|human,
    role: ..., quorum: n, timeout: s, escalate_to: gate|role, require_reason: bool} },
    authority_defaults{role: grants[]}, review_policy, budget_policy)
  ```
- 校验器：每个内核 gate 必有 decider；human decider 必有可达的 Owner；无环升级链。
- 模板存为版本化 JSON（`companies.settings.institution` 指向版本），变更写 `institution.changed` Fact。

**WP-32 内置模板库**
- 六个模板（都只是配置，不含代码分支）：
  1. `startup-flat`：CEO 决一切，评审自评
  2. `functional`：部门负责人派活，QA 评审，CEO 终审
  3. `matrix`：项目负责人 + 职能负责人会签
  4. `agency`：客户（Owner）验收门为 human
  5. `institutional`：请示 → 审批 → 会签 → 批复 → 归档，大额三重一大（human quorum）
  6. `oss-maintainer`：PR → review ×2 → merge，无金额门
- 每个模板有一条测试：同一个 3 任务项目在该模板下走完，gate 决定者与预期一致。

**WP-33 定制与 fork**
- `POST /company/institution/fork` + `PATCH`；只能改 gate 的 decider/role/quorum/timeout 与授权默认值。
- 任何改动是 Fact（谁改、改了什么、基于哪个版本）；历史决策保留当时模板版本号（已有 `grants_hash` 同款思路）。

**WP-34 gate 引擎**
- `app/kernel/gates.py::resolve(gate, context) -> SystemCheck | AgentDecisionRequired | HumanConfirmationRequired`。
- 现有调用点（派发、评审、招聘、放款、取消）改为经 `resolve`，各自删除手写的"谁可以"判断。
- AST 守卫：领域包不再 import Authority 直接判权，只能经 gate。

### K4 可追溯

**WP-40 Provenance 查询**
- `GET /provenance/{entity_type}/{id}?depth=`：返回 Fact 链 + Decision + Authority 快照 + run + 模型/费用。
- 覆盖实体：artifact、task、competency、balance delta、career_event、listing。

**WP-41 "为什么"面板**
- 前端任一分数/状态/金额旁的溯源入口，渲染 WP-40 的链；不暴露 chain-of-thought（M3 P 守卫沿用）。

**WP-42 导出与离线校验**
- `GET /provenance/.../export` → JSON bundle（含链上哈希）；`scripts/verify_bundle.py` 无需数据库即可校验。

### K5 积累与价值（折叠 M3.5–M3.8）

**WP-50 履历 / 可靠性 / 专精 投影**：三者都是 Projection，可重建、可归因、禁"总战力"（P21）。
**WP-51 AgentProfile 与市场安全投影**：内部全貌 / 市场白名单字段两个投影，共用一份事实。
**WP-52 运营 UX（精简）**：员工页"在干什么 / 卡在哪 / 为什么"三块，数据全来自 readiness + provenance。

### K6 三条真实路径与冻结

**WP-60 A 真实交付**：K1 的链在 `functional` 模板下、含一次 REWORK。
**WP-61 B 招聘后工作**：缺口 → 招聘（M1/T2 真实扣款）→ 新人进入 A 链。
**WP-62 C 故障重放**：A 链中途 `os._exit` → 重启 → 重放恢复 → 继续完成；对比 U16 旧路径删除后仍通过。
**WP-63 冻结**：`docs/kernel-freeze.md`，机器检：Fact 词表、gate 清单、模板清单、投影清单、迁移集。

---

## 5. 工程纪律（本计划的过程规则）

1. **包粒度**：一个 WP 一个会话；超出即拆分，禁止"段"。
2. **证据**：测试 + Progress 一行文字；只有真实模型调用要求费用与 run ID 记录；不再为每包生成 JSON 证据文件。
3. **mock 边界**：mock 只允许在单元测试；集成/验收必须真实 Runtime，不可用时 skip 且写原因，**不计入通过**。
4. **表**：只允许新增 Fact 表、消费者游标、投影缓存；派生不落库；每张新表要回答"从哪些 Fact 重建"。
5. **不变量**：M1/T2/M2/M3.0 注册表继承；新增集中在 `app/kernel/contracts.py`；碰冻结面先跑对应锚点。
6. **决定归属**：任何新代码出现"系统替公司选人/拆任务/判通过"即违反 §1.2，守卫测试直接红。
7. **文档**：每个 WP 结束只更新本文 §8 与一行 handover；不写新的阶段设计文档，除非是 ADR。
8. **会话协议**：开始 → 读 §8 领取 WP → 做 → 跑该 WP 的门禁 → 更新 §8 → 提交（一个 WP 一次提交，Conventional Commits）。

---

## 6. 需要用户裁决的事项

| # | 裁决 | 建议 | 何时需要 |
|---|---|---|---|
| D1 | K1 用 Hermes 还是 OpenClaw | Hermes（U7 两轮稳定，profile 即员工的映射最直接） | WP-10 前 |
| D2 | K2 是否允许触碰 M1 账本表 | 不改金额逻辑，只加 `fact_id` 引用 | WP-22 前 |
| D3 | 默认制度模板 | 新公司默认 `startup-flat`，首次真实交付后提示可换 | WP-32 前 |
| D4 | SQLite 何时换 Postgres | K6 之前不换；WAL 解决当前痛点 | K6 后 |
| D5 | 推迟项确认 | 2.5D Office、向量检索、多租户、Codex/Claude Code 适配器全部推迟 | 现在 |
| D6 | K1 失败时的策略 | 先修清单再进 K2，不绕过 | WP-12 时 |

---

## 7. 与旧计划的对应

| 旧 M3 工作包 | 去向 |
|---|---|
| U14–U16（产物、重试取消、崩溃恢复） | K1 验证真实链；K2 WP-24 以重放取代分段恢复 |
| U17 费用归集 | 并入 K1（费用必须出现在真实运行记录里）+ K4 溯源 |
| U18–U21 履历/时间线/可靠性/专精 | K5 WP-50 |
| U22–U23 AgentProfile/市场投影 | K5 WP-51 |
| U24–U25 运营与成长 UX | K5 WP-52 + K4 WP-41 |
| U26–U27 权限加固/升级换绑 | K3 WP-34 gate 引擎统一处理 |
| U28–U31 三条路径与冻结 | K6 |

---

## 8. Progress

> 每完成一个 WP 更新一行。新会话从这里开始。

> 阶段按 `v2-architecture.md` §8 的 R0–R6 记录；WP 编号沿用 R 阶段前缀（R0 = WP-00…02，其余按阶段新开）。
> 裁决：D1–D6 已被 v2 §9 的 V1–V6 取代，V1–V6 于 2026-11-06 全部通过。

| WP | 状态 | Commit | 备注 |
|---|---|---|---|
| WP-00 WAL | DONE | （随 WP-02 提交） | `core/database.py` connect 事件：WAL / synchronous=NORMAL / busy_timeout；测试基座已处理 -wal/-shm |
| WP-01 收口 WIP | DONE | `16aa26f` | U16 第十三段（任务 Evidence 恢复）20 测试通过后提交；handover / m3 plan 漂移已修 |
| WP-02 基线 | DONE | `9719a7b` | 见下方基线数字；R0 完成 |
| R1-a 内核骨架 + PG 基座 | DONE | `d39e9a2` | `eidolon/kernel/{facts,hashchain,store,consumers,notify,contracts}` + `alembic-v2.ini`/`0001_kernel` + `tests_v2`（22 测试：并发不分叉、触发器不可改、篡改可检、重放/重建、NOTIFY 仅提交）+ `scripts/devpg.sh` + compose `postgres` + CI `backend-v2`。v2-ADR-1..4 |
| R1-b worker 进程 + CLI | DONE | `06fcf8b` | `eidolon/worker.py`（启动即从游标 drain = replay；LISTEN + self-pipe 唤醒；SIGTERM/SIGINT 完成当前批次后 exit 0；消费者失败隔离并重试）+ `python -m eidolon` migrate / verify-chain / rebuild / cursors / worker + `eidolon/logging.py` JSON 行日志（脱敏）+ `eidolon/registry.py` 唯一消费者注册表 + Makefile `v2-worker/v2-verify/v2-cursors`。tests_v2 22 → 38（含真实子进程 SIGTERM 退出码 0、CLI 篡改后 exit 1）。v2-ADR-5 |
| R1-c 溯源原语 | DONE | `517f1a0` | `kernel/provenance.py`：`trace`（causation 向上有界回溯 + 同 correlation 全集，环路/悬空 cause 安全）、`bundle`（整链 / correlation / fact_ids，含每条 hash、链头、整文件 seal）；`scripts/verify_bundle.py` 纯标准库、不连库、不 import 应用的独立校验器（exit 0/1/2）；CLI `export-bundle` / `trace`；K10 入 contracts。tests_v2 38 → 47（11 种篡改全部被检出、重封印仍失败、子集与空链、真实子进程端到端）。v2-ADR-6 |
| R2-a providers | DONE | `b783e5b` | `agent/providers/`：`base`（ChatRequest/ChatEvent 流式协议、ToolSpec/ToolCall、Usage、ProviderError、首事件前重试）、`openai_compatible`（httpx SSE，DeepSeek 默认，含 embeddings）、`anthropic`（官方 SDK，可选 server-side fallbacks）、`recorder`（record 写 fixture / replay 按请求指纹回放，缺 fixture 即失败）、`pricing`（版本化价目，计费币种微单位整数）、`accounting`（每次调用落 `agent.model.called` 事实，按 request_id 幂等）、`build_provider` 工厂；`provider_state`/`assistant_state` 承载 DeepSeek reasoning_content 与 Anthropic thinking 块的原样回传；`scripts/record_provider_fixtures.py` + 3 个 DeepSeek 真实录制 fixture；全层向下依赖守卫。tests_v2 47 → 68（67 passed + Anthropic 真实调用 1 skipped：无 key）。DeepSeek 真实调用验收通过。v2-ADR-7 |
| R2-b durable loop | DONE | `22a0b2d` | `agent/{run,loop,context,blobs}.py` + `agent/tools/`：run 的全部状态 = 对 `agent.*` 事实的折叠（`RunState.from_facts`）；每步 `agent.step.requested` 先提交再调模型，`agent.step.completed` / `agent.tool.requested|completed` / `agent.run.{started,completed,failed,cancelled,budget_exceeded,cancel_requested}`；幂等键是 (run, step, attempt, call) 的纯函数，崩溃重启即重放；完整上下文/响应/工具结果进内容寻址 BlobStore，事实只存 ref+摘要；取消是事实、循环在每步/每工具前检查；预算 max_steps / max_total_tokens。loop 场景对 DeepSeek 真实跑通并录成 fixture；测试含真实子进程 `os._exit` 两种崩溃点（模型应答前、工具执行中）后从日志续跑。tests_v2 68 → 78（77 passed + 1 skipped）。v2-ADR-8 |
| R2-c runner | DONE | （本提交） | `apps/runner/eidolon_runner/`：独立纯标准库包（不 import eidolon），JSON-RPC 2.0 按行走 stdio（`runner.hello` / `tool.execute` / `runner.shutdown`）；工具 read_file / write_file / list_dir / shell / run_tests，各自声明 side_effect 与 required_authority（M2.3 自描述）；路径解析后必须落在 workspace 内（符号链接、绝对路径、`..`、`.eidolon/` 全拒）；spawn 环境白名单 + runner 自身剔除 KEY/SECRET/TOKEN 类变量；超时杀进程组；输出截断；副作用工具按幂等键记账（`.eidolon/runner-ledger.jsonl`，重启后同键仍重放）。服务端 `agent/runner/`（RunnerClient + RunnerTools 实现 Tools 协议，runner 死掉自动重启重试一次）与 `agent/tools/registry.py`。tests_v2 78 → 91（90 passed + 1 skipped），含 loop 驱动 runner 端到端。v2-ADR-9 |
| R2-d memory | TODO | | episodic + semantic + 检索（先 FTS） |
| R2-e 真实任务 | TODO | | go/no-go 点：Engineer agent 沙箱完成"CLI 小工具 + 测试"，预算 ≤ $2 |
| R3 制度 + 工作域 | TODO | | |
| R4 身份 + 钱 + 溯源 | TODO | | |
| R5 人才投影 + 前端 | TODO | | |
| R6 上线加固 | TODO | | |

**基线数字（2026-11-06，Linux aarch64 VM，Python 3.12.16，WAL 已开）**：
ruff check 通过 / ruff format 557 files 已格式化 / alembic 干净库 up→down→up 通过、无漂移（head `64da10b82e39`，66 个迁移）/
tsc、eslint、prettier 通过 / vitest 89 files 392 passed（21.8s）/ build 通过（9.9s）/
**pytest 3228 passed, 0 failed**（`-m 'not integration'`，xdist 4 workers 分 4 组共约 270s；串行约 1.5s/测试）。
注意：`test_m3_worker_resume.py` 等子进程测试必须从仓库根目录运行（硬编码 `apps/server/tests` 相对路径）。
VM 工具链：`uv python install 3.12` + `uv venv`；前端在仓库外副本跑 pnpm（避免把 Linux node_modules 写进 macOS 仓库）。
