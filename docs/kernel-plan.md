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
- 模板存为版本化 JSON（`companies.settings.institution` 指向版本），变更写 `institution.template.changed` Fact。

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
4. **表**：只允许新增 Fact 表、消费者游标、投影缓存，以及 API 层的秘密（凭证、会话；v2-ADR-22）；派生不落库；每张新表要回答"从哪些 Fact 重建"，秘密表则回答"丢了为什么无所谓"。
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
| R2-c runner | DONE | `b07d66d` | `apps/runner/eidolon_runner/`：独立纯标准库包（不 import eidolon），JSON-RPC 2.0 按行走 stdio（`runner.hello` / `tool.execute` / `runner.shutdown`）；工具 read_file / write_file / list_dir / shell / run_tests，各自声明 side_effect 与 required_authority（M2.3 自描述）；路径解析后必须落在 workspace 内（符号链接、绝对路径、`..`、`.eidolon/` 全拒）；spawn 环境白名单 + runner 自身剔除 KEY/SECRET/TOKEN 类变量；超时杀进程组；输出截断；副作用工具按幂等键记账（`.eidolon/runner-ledger.jsonl`，重启后同键仍重放）。服务端 `agent/runner/`（RunnerClient + RunnerTools 实现 Tools 协议，runner 死掉自动重启重试一次）与 `agent/tools/registry.py`。tests_v2 78 → 91（90 passed + 1 skipped），含 loop 驱动 runner 端到端。v2-ADR-9 |
| R2-d memory | DONE | `caa1d0e` | `agent/memory/`：事实 `agent.memory.{recorded,promoted,forgotten}`（晋升必须带 decision_id 或由 human 发起；遗忘只降权重不删）；投影表 `memory_entries`（0002_memory，tsvector 生成列 + GIN，`rebuild memory_entries` 可重建；用户 2026-11-07 批准）；`MemoryRepository` 在仓库层强制可见性（private 本人 / department 同部门 / company 全员），排序 = FTS × weight × importance × episodic 时间衰减；`Reflector` 是 `agent.run.completed` 的 kernel consumer，一次模型调用产出 JSON 日记落 `agent.memory.recorded`（解析失败也落原文，不卡死）；`MemoryRecall` 把检索结果注入 ContextBuilder 的 memory 段，空则请求指纹不变。注册表首次有真实消费者（memory_entries + memory_reflector）。reflection 对 DeepSeek 真实录制。tests_v2 91 → 98（97 passed + 1 skipped）。v2-ADR-10 |
| R2-e 真实任务 | DONE | `afa427d` | **GO**。CLI `run-task`（build_provider → RunnerTools → AgentRunner → 可选内联反思）与 `show-run`；`agent/prompts.py` Engineer 角色；任务文本 `tests_v2/r2e_task.md`。真实 DeepSeek 运行两次都完成：第一次 run `a51c1b52`（12 步，测试失败两次自行修好，¥≈0.15）；第二次 run `c2c462a3`（4 步，12 个测试通过，26 条事实，5 次模型调用，¥0.0378），产物 `wc_lite.py` + `test_wc_lite.py`，`verify-chain` 0 断链，bundle 离线校验 OK，反思落账。第二次全程录制成 fixture（4 步 + 反思），`test_r2e` 以 replay 走真实 CLI/runner/loop 重跑：产物真能过 pytest、事实链完整、费用入账、溯源可验、记忆只对本人可见、两次回放逐字节相同。为此 runner 的工具输出去掉了耗时与宿主路径（v2-ADR-11）。tests_v2 98 → 102（100 passed + 2 skipped：Anthropic 真实调用、R2-e 真实任务需 `EIDOLON_V2_LIVE_TASK=1`） |
| R3-a 制度 schema + 模板 | DONE | `c65555d` | `institution/{schema,validate,adoption}.py` + `templates/`：InstitutionTemplate（roles(kind human/agent, reports_to) / 13 个内核 gate 的 GateRule(decider, role, quorum, separation, amount_tiers, timeout, escalate_to, require_reason, conditions) / authority_defaults / budget / review / retention），`as_dict/from_dict/fingerprint/with_changes`；校验 10 条规则；内置 `software-studio-v1`（默认，V6 的 project.deliver=Owner）/ `startup-flat` / `institutional`（quorum 3）全部通过校验；事实 `institution.template.adopted`（模板全文内联）/ `institution.template.changed`（fork + 校验，须 human 或 decision_id，causation 指向上一版本），`current(db, company)` 折叠。无新表。tests_v2 102 → 110（108 passed + 2 skipped）。v2-ADR-12 |
| R3-b gate 引擎 + 决策信封 | DONE | `649868e` | `institution/{gates,decisions,authority,confirmation}.py`：`resolve(template, ctx)` → Resolution（system/agent/human、role、quorum、会签角色、分离职责、理由、超时与升级、模板指纹；分支 rule / tier / escalate_if / company_scope）；`escalation_of`；决策信封事实 `institution.decision.{requested,signed,recorded,escalated}`（correlation = decision_id，`DecisionState` 折叠；签字校验 actor 类型、角色、分离、理由、不重复；quorum 满足即 approved，任一 reject/rework 立即结束；human 决策绑定动作哈希；system 决策一次落 requested+recorded 且拒绝判断类 gate；超时升级）；authority 快照入签字；`tool_gate` 接入 loop（越权调用记 refused 的错误结果，模型可见）。institution 不 import agent（Protocol 鸭子类型）。无新表。tests_v2 110 → 122（120 passed + 2 skipped）。v2-ADR-13 |
| R3-c 工作域核心 | DONE | `e2e8813` | `work/{facts,graph,state,projection,service}.py`：19 种 `work.*` 事实（mission created/accepted/rejected；project created/plan_approved/delivered/cancelled；task created/assigned/started/submitted/reviewed/blocked/unblocked/failed/reopened/cancelled；artifact produced/consumed），project 内全部事实 correlation = project_id；状态机写死在 `TASK_TRANSITIONS`；纯图函数 validate（自环/悬空/重复/Tarjan 环/H5 输入须为祖先）、resolve_ready / dispatchable / awaiting_assignment（RD1，不落库）、lineage（H7）；`ProjectState` / `MissionState` 按 correlation 折叠（强一致，服务校验用）；四张投影表 `work_missions/projects/tasks/artifacts`（0003_work，用户 2026-11-07 批准，`rebuild work_items`）；`WorkService` 的管理类转换（assign / review / cancel / reopen / plan_approve / deliver / mission 决定）必须带对应 gate 的 approved decision 且主体一致，系统只校验不选择。M2 不变量折成 W1–W10 进 `kernel/contracts.WORK_INVARIANTS`，守卫检查锚点。tests_v2 122 → 133（131 passed + 2 skipped）。v2-ADR-14 |
| R3-d 派发 + 评审 | DONE | `11f905c` | `work/dispatch.py`：`request_runs` 只对 READY 且已被决策指派、无在途 run 的任务发 `work.run.requested`（键含任务最后事实 id，幂等）；`agent/scheduler.py`：`RunLauncher` 消费者（requested → `task.started`，短事务，不跨 run 持有公司链锁）、`TaskRunExecutor` 作业（in_progress 任务启动/续跑 AgentRunner，完成后工作区文件→blob→`artifact.produced`、声明输入的消费→`artifact.consumed`、`task.submitted`；run 失败→`task.failed`，不重试 R5）、`ReviewExecutor` 作业（in_review 任务开 `task.review` 决策（id 由 task+run 派生）、跑 Reviewer agent（qa 角色、只读 tool_gate），其 JSON 结论由 reviewer **签字**，conclude 后 `task.reviewed`；无结论则等待 RV1/RV6）；`Worker` 加作业钩子（drain → jobs → 再 drain）、`registry.default_jobs`、CLI `worker --no-jobs`；工程师任务提示带输入产物内容（H6）。项目场景对 DeepSeek 真实跑通（工程师 run + 评审 run，评审自己跑了测试给 pass）并录制；回放发现 `list_dir` 暴露 `__pycache__` 的 pyc 大小随路径变化，runner 改为隐藏缓存与点目录。tests_v2 133 → 140（138 passed + 2 skipped）。v2-ADR-15 |
| R3-e 三角色真实项目 | DONE | `037aa9b` | **R3 完成**。`agent/managers.py`：MissionIntake（CEO agent 决定 mission.accept 并签字，接受后由 mission 派生项目）、Planner（PM agent 产出结构化计划 → 以 PM 名义建任务 → 开 plan.approve → Tech Lead agent 审批签字 → approve_plan）、Assigner（Tech Lead agent 按花名册选人 → 开 task.assign 并签字 → assign_task）、`Deciders.ask`（决策 run：id 由 key+attempt 派生，失败换下一次尝试，上限 3）；`Roster`（R4 前的人员/角色配置）。TaskRunExecutor 把声明输入的产物实体化进任务工作区（H6），工程师提示带上次评审意见；ReviewExecutor 同样按 attempt 换 run、max_tokens 4096。`Recording(reuse_existing)`：有 fixture 复用并 touch，只对缺的真调；回放键改为对话形状 `replay_key`（工具结果正文不入键，`ls -la` 之类输出不再打断回放）。`agent.model.called` 幂等键加 causation。真实运行（DeepSeek，开发库 company `ae51519b`，project `2fdbe87c`）：CEO 接单 → PM 拆 4 任务（含依赖与输入）→ TL 审批与指派（eng-1/eng-2）→ 4 个工程师 run 全部一次通过 QA → Owner 交付，6 个周期，全程由 agent 签字、系统只落账。全部录制回放。tests_v2 140 → 144（142 passed + 2 skipped）。v2-ADR-16 |
| R4-a 身份 | DONE | `dab51a1` | `identity/{facts,service,projection}.py`：事实 `identity.user.registered` / `identity.company.created` / `identity.person.created`（human 绑 user，agent 带 persona）/ `identity.employment.{started,ended}`（agent 招/解雇须 decision_id，human 可直接；owner 雇佣不可结束）；user 与 person 平台级（固定 PLATFORM 公司 id），company/employment 公司级 correlation；`CompanyState` 折叠、`IdentityService`；四张投影 `identity_users/companies/persons/employments`（0004_identity，用户 2026-11-07 批准）。角色合法性在 `institution/staffing.py`（institution 可 import identity，反向不行）：角色须在公司当前制度里且 kind 匹配。花名册改由雇佣关系派生：`agent/managers.IdentityRoster`（`RosterSource` 协议，`StaticRoster` 留给测试），管理作业按公司取花名册，人手不齐则等待。公司场景改为真实注册 → 建公司 → 采用制度 → 招六名 agent。顺手修了两处不确定性：执行器扫描按 created_at 排序；下游任务未改动的继承文件不再被当成自己的产物（H2），输入清单按 (path, 生产者) 排序。tests_v2 144 → 150（148 passed + 2 skipped）。v2-ADR-17 |
| R4-b 钱 | DONE | `641f6c4` | `money/{facts,ledger,costs,projection,budgets,verify,fx}.py`：事实 `money.account.opened` / `money.entry.posted`（≥2 行正整数、Σ借=Σ贷、只能记到已开立且币种一致的科目；builder、折叠、投影三处都拒）/ `money.cost.recorded`；`CostMeter` 消费者（`cost_meter`）把每条 `agent.model.called` 按版本化簿记汇率（`fx.py`，USD→CNY 7.10）换算成公司记账币种，live 且可估价的借 `compute`（费用）贷 `ap:<provider>`（负债），replay / 未知模型 / 无公司 / 零额只记量不过账（`posted=false` 带 reason）；科目首次用到时幂等开立；幂等键 = 调用事实 id，重放命中 K2。投影 `money_accounts`（科目 + 借贷累计）/ `money_journal`（一腿一行，带 run_id/task_id）（0005_money，用户 2026-11-07 批准）；项目费用 = journal.task_id JOIN work_tasks 查询时联，不落库。`ledger-verify` CLI：M1 折叠合法、M2 试算平衡、M3 投影与其游标内事实一致（落后是 lag 不是损坏）、M4 每条已过账 cost 恰有一笔分录；`balances` CLI。`money/budgets.py`：制度金额单位 = 记账币种最小单位（分），1 分 = 10 000 微，`project_status` / `run_status` / `spend_amount` 是 `spend.approve` 的金额来源。公司场景回放后以 `book_replays=True` 计量：每次调用进账本、按任务归属、项目费用可查、ledger-verify 绿。tests_v2 150 → 156（154 passed + 2 skipped）。v2-ADR-18 |
| R4-c 费用溯源 | DONE | `da1e257` | `money/trace.py`：`cost_trace(ref)` 从分录事实 / 分录 id / cost 事实 / 调用事实任一出发，沿 causation 走 `money.entry.posted` ← `money.cost.recorded` ← `agent.model.called` ← `agent.step.requested`，沿 correlation 取 `agent.run.started`，再沿它的 causation 取「是谁要这次 run」的事实（`work.task.started` / `work.task.submitted` / 项目或任务事实 / mission 事实），其 correlation 就是 project / mission / decision；走不通的环节列在 `missing` 而不是报错。为此 `RunSpec.causation_id` 落到 `agent.run.started`：执行器传任务的最后事实，评审器传提交事实，三个管理作业传 mission / 项目 / 任务事实（`Deciders.ask(causation_id=)`）。`project_costs(pid)`：按任务、按 run（含 person）汇总 journal，加上项目事实召唤但不带任务的管理 run（PM 规划、TL 审方案）；CEO 的 mission run 归 mission 不归项目。`ledger_fact_ids(pid)` 给 bundle：`export-bundle --with-ledger` 把项目花费背后的 money.* 事实并入同一 bundle，离线 verify_bundle 仍绿。CLI `cost-trace <ref> [--json]`、`project-costs --project [--json]`。公司场景回放：工程师分录追到任务与项目，PM/TL 管理 run 进项目汇总，CEO run 不进。契约 M5。tests_v2 156 → 158（156 passed + 2 skipped）。v2-ADR-19。**R4 完成**：费用从模型调用一路进账本并可溯 |
| R5 人才投影 + 前端 | 拆为 R5-a..e（2026-11-07） | | 范围（v2-architecture §8 R5 行 + V5）：evidence / competency / resume / profile 作为投影；API 层（薄路由，只做鉴权 + DTO）；前端按核心裁剪接新 API；旧 `app/` 删除，表 ≤ 45。上线门槛（§7：Passkey、凭证加密、compose、指标、限流、保留期）留给 R6 |
| R5-a 人才投影 | DONE | `1316360` | `talent/{evidence,competency,projection,profile}.py`：**不新增事实种类**。`EvidenceSource.derive(fact)` 把 `work.task.reviewed`（pass → 被评人 +1；rework −1；escalate 0；评审人 review_given 0）/ `work.task.failed`（−1）/ `work.project.delivered`（项目经理 +1）/ `work.artifact.produced`（0）/ `agent.run.{completed,failed,budget_exceeded}`（0 / −1 / −1）/ `institution.decision.signed`（签字人 0；`task.review` 门的签字不重复计，verdict 事实已算）变成证据 (person, company, area, kind, signal, weight, 来源事实, task/project/run, occurred_at)；area = 做这件事时的角色（任务 role → 签字时的 role → 公司雇佣 role → general）；human 不积累证据（Owner 没有）；所需查找全是事实折叠（ProjectState / CompanyState），重建结果一致。`Competency.absorb`：EWMA（α=0.25×weight）限 [−1,1]，显示 0–100，中性证据计数不动分。投影 `talent_evidence` / `talent_competencies`（0006_talent，用户 2026-11-07 批准），`rebuild talent` 精确复现。`profile(person)` / `resume(person)` 纯查询（雇佣 + 能力 + 证据 + 按证据所涉 run 的花费）；CLI `talent-profile <person> [--resume] [--json]`。契约 T1（talent 之下无人 import 它，AST 锚定）、T2。公司场景回放：工程师、QA、PM、TL、CEO 各有其角色域的证据，Owner 没有，PM 在交付后得 project_delivered。tests_v2 156 → 158（158 passed + 2 skipped）。v2-ADR-20 |
| R5-b API 读侧 + 鉴权 | DONE | `cda7876` | `api/`（FastAPI，前缀 `/api/v2`，`python -m eidolon api` / `make v2-api`，不跑 agent 循环）。鉴权：`api/auth.py` 的 `AuthService`——注册在同一事务内写 `identity.user.registered` + `identity.person.created`（human）事实和 `auth_credentials` 行（argon2id，哈希永不进事实）；登录发 HttpOnly `eidolon_session`（随机 token，库里只存 sha256）+ 可读 `eidolon_csrf`，改写请求须带 `X-CSRF-Token`（double-submit，注册/登录豁免）；会话可列、可单独吊销、可全部登出、改密码吊销其它会话。两张表 `auth_credentials` / `auth_sessions`（0007_auth，用户 2026-11-07 批准；它们既非事实也非投影，见 v2-ADR-22）。授权：所有公司范围的读都要求当前用户的某个 person 在该公司有活跃雇佣（identity 投影），否则 403；person 档案要求同公司。只读路由：auth（register/login/me/logout/logout-all/sessions/password）、companies（我的公司、公司 + 制度摘要、institution 全文、employees 含能力分、persons/{id} 含履历）、work（missions、projects、project 含任务/产物/花费、task、artifact + `/content` 从 blob 取内容）、runs（事实时间线：只给预览、用量、工具名、结局；`*_ref` / provider_state / reasoning 一律不出；公司 run 列表按 person 过滤）、ledger（balances、verify、project costs、cost-trace）、provenance（fact、trace、bundle 可带账本并离线校验）。tests_v2 用 TestClient 对真实 PostgreSQL：账号/会话/CSRF 全流程 + 陌生人处处 403 + 读侧全覆盖。tests_v2 161 → 163（161 passed + 2 skipped）。v2-ADR-22 |
| R5-c API 写侧（命令） | DONE | `56a9492` | `api/commands.py`（`Commands`：每个命令是领域服务的一层薄组合）+ 路由 `manage` / `decisions` / `work` / `runs` 的写侧。人的自有动作按制度 grant 放行（`has_authority(template, role, grant)`，Owner 的 `*` 全通）：建公司（owner = 当前用户的 person，默认采用 software-studio-v1）、采用/定制制度（`institution.customize`）、招人（建 agent person + `staffing.hire`；角色与 kind 由制度校验）/ 解雇（Owner 不可被解雇）、提 mission（`mission.propose`）、取消 run（`run.cancel`，已结束的 409）。管理动作走决策信封：`POST /companies/{id}/decisions` 开信封（proposer = 当前 person，human gate 绑定 action 哈希），`GET` 列出等人决定的，`POST /decisions/{id}/sign` 签字（角色、kind、会签、分权、理由、重复签字、动作一致全由 `institution.decisions.sign` 校验，API 只传递），签满即 conclude + record，再由 `Commands.apply` 按 gate 机械施行后果（mission.accept → decide_mission；plan.approve → approve_plan；project.deliver → deliver_project；task.assign / task.cancel；hire / fire）；施行失败整笔回滚（签字与后果同一事务）。`POST /projects/{id}/deliver` = V6 的 Owner 验收：开信封 + 签 + 交付一步到位，决策 id 由项目最后事实派生。`Idempotency-Key` 头派生实体 id（公司、mission、决策），重试命中 K2。顺手修：`create_company` 的 owner 雇佣 id 改为公司派生，重试不再给 Owner 两份雇佣。tests_v2 用 TestClient：建司→定制→招解雇→提 mission；交付闭环（未完工 422 且签字回滚、错误 action 422、签后施行、重复 409）；分权（Owner 提的 hire 自己不能批）；agent gate 人不能签；取消 run。tests_v2 163 → 166（164 passed + 2 skipped）。v2-ADR-23 |
| R5-d 前端裁剪 | DONE | `36cc52d` | V5 落地：`apps/web/src` 从 41.9k 行减到 4.7k 行（含测试），只剩 Auth（登录/注册，无邮箱验证与 Passkey）/ Onboarding（无公司时先建司）/ Overview（等你签字的决定 + 使命 + 四个数字 + 提使命）/ Projects（列表；详情含任务、评审、依赖、产物、费用、Owner 一键验收）/ Employees（含能力分；招聘/结束雇佣；人的档案含履历、运行、能力条）/ Runs（事实时间线 + 任一事实的因果追溯弹窗 + Owner 取消）/ Drive（全公司产物表 + 内容预览）/ Economy（科目余额、账本核验、项目费用、费用溯源）/ Settings（个人、会话与改密码、制度角色与闸门、Owner 定制、关于）。`src/api/` 重写到 `/api/v2`（client：双提交 CSRF、Idempotency-Key、ApiError；index：按层分组的端点；types：DTO）；`hooks/queries.ts` 用 react-query 轮询（run 5s、项目/决定 10s），不再有 WebSocket/事件流；`features/company` 持有当前公司（多家时可切换）。删除 market / contracts / work-orders / cultivation / positions / talent-roster / knowledge / office 游戏 / tutorial / runtime / git / lifecycle 等页面组件与 23 个 hooks、2.4k 行旧类型、19 个旧 locale 命名空间；去掉 phaser / docx / jspdf / html2canvas / mammoth / pdfjs / dompurify / react-markdown / remark-gfm / @xyflow / @floating-ui 共 11 个依赖（lockfile −176 包）。i18n 收成 common / nav / auth / app 四个命名空间，中英各一份。vitest 392 → 30（client、format、login、dashboard 签字、project 验收、dialog、language、password）；tsc / eslint / prettier / build 全绿；主包 455 kB。v2-ADR-24 |
| R5-e 旧应用删除 + cutover | DONE | `fb288d3` | 删除 `apps/server/app/`（旧应用）、`apps/server/tests/`（3228 条）、`apps/server/migrations/` + `alembic.ini`（66 个迁移）、12 个旧脚本；历史归档点 `232ceff`，`eidolon/extensions/README.md` 指向它（V4）。150 个旧文档 + 50 份证据 JSON `git mv` 到 `docs/archive/`（带 README），`docs/` 只剩 v2 三篇 + ports + deployment（后两篇重写为 v2）。`pyproject.toml` 只打包 eidolon、依赖裁到 11 个；`requirements.lock` 从 69 裁到 52 个包并在干净 venv 里装好、import api/worker/registry 验证。Makefile：`make dev` 起 api + worker + web（健康检查 `/api/v2/health`），`make test` = test-v2 + test-web，去掉约 30 个旧目标，新增 `v2-ledger-verify`；`scripts/devctl.sh` 按 `python -m eidolon api/worker` 识别进程。CI：旧 backend 作业删除，镜像冒烟改为 v2 镜像对 PostgreSQL 服务起 `/api/v2/health`。compose：postgres + api（先 migrate 再 serve）+ worker（同镜像换 command，唯一持有模型 key 的进程）+ web；nginx 去掉 `/ws`。`.env.example` 全换 `EIDOLON_V2_*`；README / CONTRIBUTING / apps/server/README 按 v2 重写。表数核对：20 张（≤ 45）。cutover 实测：`make dev` 真实起三进程，经 Vite 反代走通注册 → 建司 → 招人 → 员工列表 → 账本 → 登出。实测抓到一个设计缺口：首次对有历史的开发库起 worker，`memory_reflector` 游标不存在，从 0 把历史 run 逐条拿去调模型反思（停掉前花了 3 次调用）——改为 `kernel.consumers.starts_at_head`：声明 `start_at_head = True` 的会花钱反应器首次注册把游标钉到 head，投影永远从 0（handoff §4.1）。tests_v2 166 → 168（166 passed + 2 skipped）。v2-ADR-25。**R5 完成**：旧 `app/` 不复存在 |
| R6 上线加固 | 拆为 R6-a..e（2026-11-07） | | 范围 = v2-architecture §7 的上线门槛（限流/配额、超时升级、观测、密钥与 Passkey、备份与保留）+ §4.7 预算与费用的落地。每包一次提交；R6-d 需新表先报批 |
| R6-a 预算与配额 | DONE | `1dd4c00` | 制度的 `budget_policy` 从账本取数生效，三级：**run 级** `RunBudget.max_cost_micro` + `cost_currency`（执行器/评审器从模板 `run_default` × `MICRO_PER_UNIT`、公司币种得出），循环把每步 `pricing.estimate` 写进 `agent.step.completed.cost` 并在 `RunState.cost_total` 累计，超过 → `agent.run.budget_exceeded(reason=max_cost)`；供应商币种与公司币种不同则不比（项目级仍拦）。**项目级** `money/guard.py` 的 `BudgetGuard`（实现 `agent.scheduler.BudgetGuard` 协议，registry 注入，agent 不 import money）：已花 + run_default ≤ warn_ratio × 预算直接放行；否则该 tranche（k = 已花 // run_default，决策 id 由 (project, k) 派生）需要 `spend.approve`，amount = 已花 + run_default，按模板分层——系统层 `system_record` 机械记录、agent 层由新管理作业 `SpendApprover` 让 PM 看项目花费后签字、human 层进前端决定收件箱；pending / rejected 时执行器与评审器不起新 run（`task.run_held` / `review.run_held` 日志，下一轮再问），`hard_stop=false` 只记决定不拦。**公司级** `BudgetPolicy` 新增 `max_concurrent_runs`（默认 4）与 `daily_cap`（分，默认关），超则等待，无需决定；`MICRO_PER_UNIT` 单一来源移到 `institution/schema.py`。契约 B1–B3。公司场景回放不受影响（花费远低于阈值）。tests_v2 168 → 171（169 passed + 2 skipped）。v2-ADR-26 |
| R6-b 超时与升级 | DONE | `8e04f97` | `agent/escalation.py` 两个 worker 作业：`Escalator` 扫全部 pending 决定，过了 `timeout_s`（时钟可注入）就 `decisions.escalate` 写 `institution.decision.escalated`（新 resolution、from、原因、第几次），升级后的 resolution 没有 timeout 所以只升一次；`EscalatedDecider` 对 `branch=escalated` 且 decider=agent 的决定，从花名册里找被允许角色、未签过、非提出人/作者的人，用 gate + subject + 项目/任务/使命上下文提问，答案经同一信封 `sign_and_conclude`（rework 只在 plan.approve / task.review 有效，否则按 reject）；human 目标留给前端收件箱。管理作业改为"已决定优先"：MissionIntake / Planner 以前在 decider 不是自己时直接返回，升级后永远不施行——现在先看 status，approved 就施行；ReviewExecutor 施行评审时 reviewer 取最后签字人而不是自己的 qa（RV4 否则拒绝 Tech Lead 的结论）。API `POST /decisions/{id}/escalate`（`escalation.resolve` grant，Owner）强制升级，无处可升 409。取消：`agent.run.cancel_requested` 在循环第一个检查点生效，执行器把任务置 failed（原因含 cancelled），只能经决定 reopen（W3），重复取消命中 K2。契约 G1–G2。tests_v2 171 → 175（172 passed + 2 skipped）。v2-ADR-27 |
| R6-c 可观测性 | DONE | `c94040d` | `eidolon/observability.py`（prometheus_client，新依赖进 lock）：**进程指标**——API 中间件按 (method, 路由模板, status) 计数与时延直方图（路由模板经 FastAPI 的懒加载 router 递归匹配，依赖抛 401/403 也有正确标签），worker 每批记 `eidolon_worker_batches_total` / `facts_handled_total` / `consumer_lag`（head − cursor）/ `fact_head_seq`，每次作业 tick 记次数、动作数、失败数、时长直方图；**事实派生指标**——`MetricsConsumer`（`start_at_head`，是本进程生命的计数器不是投影）按 kind 计事实数，run 开始/结束（按 status）、步数、模型调用（provider/model/mode）与 token、已入账成本（按币种）与未入账原因、决定数（gate × 结论）、升级数、`gate_wait_seconds` 直方图（requested → recorded）。API `GET /metrics`；worker 在 `EIDOLON_V2_METRICS_PORT`（默认 0 关；.env.example 与 compose 开 26890，ports.md 登记）起 `start_http_server`。`/api/v2/health` 回答运维最先问的两件事：DB 可达与各消费者落后多少（`head_seq`、`cursors`、`max_lag`），DB 不可达 503 degraded。`export-bundle --since/--until`（API `bundle?since=&until=`）按 created_at 窗口裁选择，窗口进封印。tests_v2 175 → 180（177 passed + 2 skipped）。v2-ADR-28 |
| R6-d 密钥与 Passkey | DONE | `dbfa107` | 三张 API 层秘密表（0008_secrets，用户 2026-11-07 批准，同 ADR-22）：`provider_credentials`（每公司一行，Fernet 加密，密钥由 `EIDOLON_V2_SECRET_KEY` sha256 派生，占位值直接拒绝存储；只回尾 4 位）、`auth_passkeys`、`auth_webauthn_challenges`（单次使用、过期即删、支持 discoverable 登录）。`agent/providers/routing.RoutedProvider` 实现 Provider 协议，按 `CallContext.company_id` 经注入的 resolver 选公司自己的 provider（按 (updated_at, kind, 尾号) 代际缓存），没配置回退 .env 全局；registry 的 `routed_provider` 在 live 模式装配（replay 模式一套 fixture 不路由）。API：`GET/PUT/DELETE /companies/{id}/provider`（grant `provider.manage`，Owner）；Passkey 注册/登录（options + verify，登录 ceremony 免 CSRF、受限流）、列表/改名/删除；`AuthService.open_session` 供 Passkey 登录开会话。登录限流 `api/ratelimit.LoginLimiter`：进程内滑动窗口，账号 + 客户端地址各 5 次/15 分钟，429 带 Retry-After，成功即清。前端：登录页 Passkey 按钮（`features/auth/webauthn.ts` 从 56a9492 移植，JSON↔二进制编码有测试）、设置页 Passkeys 面板与 Provider 分区（只显尾 4 位）。依赖回加 cryptography / webauthn（lock +6 包）。**未做**：真实 authenticator 的完整 ceremony 没有自动化验证（库负责验签，测试覆盖 options、单次挑战、过期、错误路径、管理）；邮件验证不做。tests_v2 180 → 186（183 passed + 2 skipped）；vitest 32。v2-ADR-29 |
| R6-e 备份、保留与恢复 | DONE | `37cb18e` | **保留**：`agent/retention.py` 的 `TranscriptRetention` worker 作业（每小时一次）：按公司制度 `retention.transcript_days` 找结束超期的 run，收集其事实里的全部 blob 引用（system/prompt/context/response/result/final/arguments），减去仍被未过期 run 或任何产物引用的（内容寻址 blob 跨 run 共享），其余 `BlobStore.delete`；每次落 `agent.transcripts.purged` 事实；无制度的公司不动。**备份**：`scripts/backup.sh`（pg_dump custom + blob tar + manifest 含 git commit 与 sha256；本机无 pg_dump 时在 PG 容器里跑；按键读 .env 不 source）。**恢复自证**：`scripts/restore-check.sh`（只接受 `*_restore` 库：drop/create → pg_restore → 解 blob → `python -m eidolon restore-check`）；新 CLI `restore-check`：每公司 verify-chain、每账本 ledger-verify、每投影 rebuild 后行数与 dump 一致，任一不符 exit 1；memory 投影补 `TABLES`。**实测**：对开发库真实备份（db.dump 762 KB + blobs 2.4 MB）→ 恢复到 `eidolon_restore` → 自证全绿（3 条链、17 本账、5 个投影行数不变）后删除。runbook 写进 docs/deployment.md。tests_v2 186 → 188（185 passed + 2 skipped）。v2-ADR-30。**R6 完成**：compose 一键起（R5-e）、指标可见（R6-c）、预算与超时可拦（R6-a/b）、备份可恢复并自证（R6-e） |
| R7 决策者体验 | 拆为 R7-a..e（2026-11-07） | | 用户裁决（2026-11-07）：产品的另一半是"用户只判断、不思考怎么做"——系统自主协同把需求做到交付，关键决策来问人时带着公司决策层的意见，用大白话讲清前因后果，用户随时能看懂。地基不变：建议是信封的一部分、叙事是事实的投影、同意仍走闸门 |
| R7-a 带建议的决策信封 | DONE | `44423f2` | 人要决定的每个闸门都带 **brief**（公司自己的意见）。`institution/decisions.py`：新事实种类 `institution.decision.advised`（payload：decision_id、gate、brief{recommendation∈approve/reject/rework、summary ≤ 300 字、rationale、risks ≤ 5、alternatives ≤ 5、cost、advisor person/role}），`Brief` 折进 `DecisionState.brief`；`request(..., brief=)` 开信封时可直接附建议（advisor = 提出人）；`advise(state, actor, role, brief)` 对未决定的信封落建议事实（系统不能有意见；同一顾问同一轮幂等键，后到的覆盖折叠）；`validate_brief` 校验/截断。`GateRule.advisor`（角色码，序列化进 as_dict/from_dict），`InstitutionTemplate.advisor_for(gate, decider_role)`：规则指定 → 该人类角色的直接下属 agent 角色 → 直属人类的最高 agent 角色（studio 里是 ceo）。`agent/briefing.py` 两个管理作业：`Advisor` 对 decider=human、pending 且无 brief 的决定，让 advisor 角色的 agent 读 `describe_decision`（从 `EscalatedDecider._prompt` 抽出共用：闸门、主体、项目/任务/使命上下文）后给建议；`DeliveryProposer` 对所有任务 done 的 active 项目，由项目经理打开 `project.deliver` 信封（绑定动作、决定 id = uuid5(project, attempt)，被拒后 attempt+1），附自己的 brief——取代测试里手工 `owner_delivers`，公司场景现在是 PM 提交付、Owner 只判断（`owner_signs_delivery`）。注册进 `registry.default_jobs`。API：`decision_dict` 带 brief；`Commands.deliver_project` 复用 PM 提案的决定 id，所以 Owner 的一键交付签的是带 brief 的那个信封而不是另开一个。前端：`DecisionCard` 先显示 "{谁}（{角色}）建议 · 批准/拒绝/返工" 徽章 + 大白话 summary + 为什么/风险/其他选择，提出时间/提出人/绑定动作折进 `<details>` 详情；i18n 中英。契约 B1（`agent/briefing.py` 不 import sign/conclude/system_record，AST 锚定：出主意的人没有笔）。公司场景只新录 1 个 fixture（PM 的交付提案，DeepSeek，< 0.1 CNY），其余复用。tests_v2 185 → 189（189 passed + 2 skipped）；vitest 30 → 32。v2-ADR-31 |
| R7-b 叙事报告 | DONE | `663ff32` | 把事实折叠成人能读的故事。`api/narrative.py`：`project_report(db, pid, lang)` / `mission_report(db, mid, lang)` 六段固定结构 asked（使命：谁何时提了什么、验收标准、预算；立项；方案通过及签字人）/ done（每个任务：谁完成、谁评审通过、返工几次、产出什么；交付/取消）/ decided（相关的每个决定：闸门、状态、签字人与裁决、升级次数、brief 建议）/ cost（总花费与预算占比、每任务、管理运行；挂账本事实 id）/ stuck（失败或阻塞的任务及原因、返工 ≥ 2、超时升级、等 agent 决定的闸门）/ next（等人决定的闸门 + 顾问建议大白话；全部完成等 PM 提交付；已交付则没有了）。每行 `Line(text, facts[], link{kind,id})`：facts 是这句话由哪些事实折叠而来（可下钻），link 指向 decision/task/run/mission。确定性模板，zh/en 双语（`lang` 参数），同一事实日志两次生成完全相同；**报告不落库**（是读投影，不是事实）。`Report.as_dict/to_text/fact_ids`。使命报告 = 使命段 + 使命级决定 + 每个项目的六段（标题前缀项目名）。LLM 润色为可选层：`polish(report, provider, model)` 一次调用改写 `to_text()`，CallContext 带公司与项目（记账），只能放在源文本旁边不能替代；CLI `report --project|--mission [--lang] [--json] [--facts] [--polish]`。API：`GET /projects/{id}/report?lang=` 与 `GET /missions/{id}/report?lang=`（成员可读，lang 非 zh/en → 422）。前端：项目页 "任务 | 报告" 两个 tab，报告每行下方列事实 id 短码，点开 TraceDialog 追溯，run 链接可跳；总览页"今天需要你的 N 件事"摘要（每件一行：闸门 + 建议大白话 + 推荐）。公司场景回放断言完整故事（使命→接受→立项→任务→PM 建议→Owner 验收→花费占预算）且每个事实 id 属于本公司。tests_v2 189 → 194（194 passed + 2 skipped）；vitest 32 → 33。v2-ADR-32 |
| R7-c 管理 agent 有手有眼 | DONE | `b2cf6e5` | 决策 run 从两步 JSON 升级为可用只读工具的多步 run。`agent/project_tools.py`：`ProjectTools`（进程内 `Tools`，每个工具带 side_effect / required_authority 描述符）——`project_status`（任务状态、执行人、评审、返工、验收标准；任务用稳定句柄 t1/t2… 按事实顺序，**不暴露 id**）、`list_artifacts`、`read_artifact(path)`（blob 内容，16k 字上限）、`list_dir(task, path, depth)`、`read_file(task, path, offset, limit)`（任务工作区内，路径越界拒绝，根目录 resolve 一次以兼容 macOS 符号链接 tmp）——均 READ / `workspace.read`；`run_tests(task, path)` 经沙箱 runner 跑 pytest，WRITE / `workspace.execute`（与 runner 同级）。没有 write_file / shell。`Deciders(workspaces=, svc=)`：`ask(project_id=, template=)` 有项目可看时挂 `ProjectTools`，系统提示加 `TOOLS_HINT`，`tool_gate(template, role, registry)` 按角色授权（PM 可读不可跑测试；公司可通过 customize 收回 `workspace.read`，TL 就回到只看提示词），`RunBudget(max_steps=8, max_cost_micro=run_default×MICRO_PER_UNIT, cost_currency=公司币种)`；没配 workspaces 或没有项目时仍是原来的两步 run。调用点全部带上项目：Planner 的 TL 审方案、Assigner、SpendApprover、EscalatedDecider、Advisor、DeliveryProposer（`decision_project(db, d)` 从 subject 的 project_id / task_id 反查）。制度：software-studio-v1 的 ceo / pm / tech_lead、startup-flat 的 lead、institutional 的 dept_head 增加 `workspace.read`（看是管理的一部分，写不是）。回放约束：工具调用参数进 replay key，所以工具输出里不能有运行间会变的 id（契约：带 TOOLS_HINT 的 fixture 中所有 tool_call 参数不含 uuid）。公司场景重录 3 次（第 1 次输出含 task_id 被模型回传、第 2 次 macOS /var 符号链接导致 list_dir 录制时报错回放时成功，均为回放键不一致；第 3 次通过），共约 0.75 CNY；最终 24 个新 fixture（TL 17、PM 7：project_status 19 次、list_artifacts 19、read_artifact 26、list_dir 28、read_file 7、run_tests 10），删除 5 个旧的无工具管理 fixture。回放断言 TL 与 PM 都用过工具且只用过只读工具集。tests_v2 194 → 197（197 passed + 2 skipped）。v2-ADR-33 |
| R7-d 失败处置提案 | DONE | `92bad33` | 失败不再是一个红色状态，而是一个带建议的决定。内核闸门清单新增 **`task.triage`**（failed / blocked / 超预算任务的处置：reopen / reassign / cancel / split），三套内置制度各有规则：studio = TL 提议（`conditions.proposer`）、PM 批准（agent，proposer≠approver，8h 超时升级 Owner）；startup-flat = lead 提议、Owner 批准（human）；institutional = dept_head 提议、deputy 批准（human）。`work/triage.py`：`validate_disposition`（reassign 须换人且在花名册内；split 须 2–4 个新任务且无人依赖它；必须有 reason）、`apply_triage`（reopen → `reopen_task`；reassign → reopen + `assign_task`；cancel → `cancel_task`；split → cancel + `create_task`×N 继承原任务的 depends_on/inputs，新任务走正常 Assigner；幂等）、`triage_decision_id(task, failure_fact, attempt)`。`WorkService.decision()`：批准的 triage 决定按其 disposition 代行 `task.assign`（reopen/reassign）或 `task.cancel`（cancel/split）的授权，其他组合拒绝。`agent/triage.py` `Triage` 作业：对 active 项目里 failed/blocked 的任务，提议人（R7-c 工具在手，看得到失败原因、评审、依赖、花名册负载）给出处置 + brief → `request(task.triage, brief)`（绑定动作，人类决策者可签）→ 决策者是 agent 则同一 tick 内由 PM 读提案批/否并 `sign_and_conclude` → 批准即 `apply_triage` 施行；否决最多 `MAX_ATTEMPTS=2` 次（第二次提示上次被否），之后任务等人。API `Commands.apply` 新增 `task.triage` 分支（Owner 在收件箱签字即施行）。叙事报告 stuck 段在失败任务下一行写"{谁} 建议{重开/改派给 X/取消/拆成 N 个}：{大白话}（待定/已批准/已拒绝）"。未采用新规则的旧制度公司：作业跳过，不臆造决策者。tests_v2 197 → 202（202 passed + 2 skipped）。v2-ADR-34 |
| R7-e 文档/设计任务与决策节奏 | DONE | `929aa74` | **任务种类**：`work.facts.TASK_KINDS = build / document / design / research`（`task_created` 校验）；`agent/prompts.py` 新增 WRITER / DESIGNER / RESEARCHER 工作提示词与 DOCUMENT_REVIEWER 评审提示词，`system_for(kind, role)`（书面类按 kind，build 按角色）、`reviewer_for(kind)`；TaskRunExecutor / ReviewExecutor 按 kind 选提示词，书面任务的评审 brief 注明"交付物是下面的 Markdown，按内容判，没有测试可跑"；PM_SYSTEM 让 PM 给每个任务标 kind（只有使命要求时才加文档/设计任务），`_create_tasks` 透传。**决策节奏**：`Commands.follow_recommendations(decision_ids=None)` 把所有待人决定、带 brief 的信封按 brief.recommendation 签字（理由 = brief.summary、动作 = 信封自己绑定的那个）并逐个施行，没 brief 的跳过并说明；`POST /companies/{id}/decisions/follow`。前端总览收件箱按闸门分组（"{gate} · N 件"）、每件一行摘要、"全部按建议处理"按钮两步确认（无弹窗）后显示"已签 N 件，跳过 M 件"。ReviewExecutor.max_steps 8 → 12：重录时 QA 的真实评审（读两个文件、跑测试、再独立探测、再查 import）第 8 步被掐断三次。公司场景因 PM 提示词变化全量重录（删 74 个旧 fixture，新录 83 个，约 1.7 CNY 含一次因评审步数不够的失败尝试；之后用一次性 pytest 插件记录回放实际读到的文件：104 个全部在用，没有孤儿——前两次步数不够的评审尝试在回放里同样重演后由第三次通过）；新方案仍是 3 个 build 任务，PM 没有加文档任务（使命没要求）。tests_v2 202 → 205（205 passed + 2 skipped）；vitest 33 → 34。v2-ADR-35。**R7 完成** |
| R8 场景验收 | 拆为 R8-a..b（2026-11-08） | | 用户裁决（2026-11-08）：开 R8，用一个真实的"文档 + 代码"使命从头跑一遍 R7 的体验做场景验收。验收标准先于运行写死（见 R8-a），运行只回答过没过 |
| R8-a 文档+代码使命的真实场景 | DONE | `R8A_HASH` | 新场景 `company-docs`（`company_support.Scenario` 参数化使命，NOTES / DOCS；recorder `--only company-docs`；`tests_v2/test_company_docs.py` 回放，验收标准 A1–A6 先写后跑）。**真实录制结果（DeepSeek，2026-11-08）**：第 1 次录制 BAD——PM 方案在 2048 输出 token 处被掐断（思考型模型先花 token 思考），三次重试请求相同只会回放同一次掐断，项目停在 planning（花费 0.02 CNY）；修复见 R8-b 后第 2 次录制 ok：CEO 接受 → PM 3 个任务（notes 模块 + 测试 / argparse CLI + 集成测试 / **《Write end-user README.md guide》kind=document**，依赖并输入 CLI 任务）→ TL 审方案与指派（用了 project_status / list_artifacts / read_artifact / list_dir）→ eng-1 三个任务全部 done（README 由 WRITER_SYSTEM 写、DOCUMENT_REVIEWER_SYSTEM 评审通过）→ PM 带 brief 提交付 → Owner `follow_recommendations` 一键签字 → delivered，8 个周期。**验收**：A1 过（3 任务，文档任务 depends_on + inputs CLI 任务）；A2 过（README.md 产物提到 add / list / delete，QA pass）；A3 过（按建议一键签字即交付）；A4 过（六段齐全，含文档任务与 PM 建议）；A5 过（管理者只用只读工具；198 个 fixture 的工具参数无 uuid；链校验 ok）；A6 N/A（无失败/阻塞任务）。花费约 1.92 CNY（≤ 3 CNY 预算），94 个新 fixture（工程师 36、QA 17、TL 15、写手 10、文档评审 7、PM 提案 5、PM 规划 3 次尝试、CEO 1）。一次性插件核对 198 个 fixture 全部被回放读到。tests_v2 205 → 208（208 passed + 2 skipped）|
| R8-b 场景暴露的缺口 | DONE | `R8A_HASH`（与 R8-a 同一提交） | 一条：**PM 方案被输出 token 上限掐断** → 现象：`decision.run_failed`×3、项目停在 planning；根因：`Planner` 给 PM 的 `max_tokens=2048`，deepseek-flash 先思考再输出，5 条验收标准的使命让方案 JSON 放不下；重试的三次 attempt 请求逐字相同，录制/回放都只能得到同一次掐断。修法：`Deciders.ask` 的第 n 次 attempt 给 `max_tokens × 2^(n-1)`（上限 `MAX_TOKENS_CAP=16384`），第一次尝试的请求不变（既有 fixture 全部有效），被掐断的决定在下个周期以双倍空间重来；回归测试 `tests_v2/test_deciders_retry.py`（2048 → 4096 → 8192，前两次 failed 第三次 completed）。另有一条已在 R7-e 修：QA 评审 8 步上限掐断真实评审 → 12 步。未修的观察：文档评审人仍是 qa-1（制度里没有单独的编辑/技术写作评审角色），三个任务都派给了 eng-1（TL 看了负载仍选同一人——eng-2 空闲，值得在 R9 看指派提示词）|

**基线数字（2026-11-06，Linux aarch64 VM，Python 3.12.16，WAL 已开）**：
ruff check 通过 / ruff format 557 files 已格式化 / alembic 干净库 up→down→up 通过、无漂移（head `64da10b82e39`，66 个迁移）/
tsc、eslint、prettier 通过 / vitest 89 files 392 passed（21.8s）/ build 通过（9.9s）/
**pytest 3228 passed, 0 failed**（`-m 'not integration'`，xdist 4 workers 分 4 组共约 270s；串行约 1.5s/测试）。
注意：`test_m3_worker_resume.py` 等子进程测试必须从仓库根目录运行（硬编码 `apps/server/tests` 相对路径）。
VM 工具链：`uv python install 3.12` + `uv venv`；前端在仓库外副本跑 pnpm（避免把 Linux node_modules 写进 macOS 仓库）。
