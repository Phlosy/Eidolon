# Eidolon v2：底层框架设计与重构思路

> 日期：2026-11-06　基线：`dev` @ `43a04d5`。
> 本文**取代** `kernel-plan.md` 的 §3–§4（阶段与工作包），保留其 §1 本质陈述与 §5 工程纪律。
> 三个前提来自用户裁决：(1) 自研带记忆的智能体框架，不依赖 Hermes / OpenClaw；
> (2) 制度层有一个可上线的默认实现，不是玩具；(3) 允许从底层框架开始重构，含语言选择。

---

## 0. 决定摘要（先给结论）

| 议题 | 决定 | 一句话理由 |
|---|---|---|
| 后端语言 | **Python 3.12 保留** | 瓶颈是架构不是语言；agent 循环是 I/O 密集；8 万行与 2400 条测试可选择性移植；LLM 生态最厚 |
| 可替换为非 Python 的部件 | **Runner（沙箱执行器）** 单独进程、窄协议 | 唯一真正受益于 Go/Rust 的部件；先 Python 实现，接口不变即可换 |
| 前端 | TypeScript / React 保留 | 无争议 |
| 数据库 | **PostgreSQL 唯一目标**（含测试） | 多进程、行锁、`SKIP LOCKED`、LISTEN/NOTIFY、pgvector；避免 SQLite/PG 双方言债务 |
| 进程模型 | `api` / `worker` / `runner` 三类进程 | API 不跑 agent；worker 领任务；runner 隔离工具执行 |
| 第三方 Runtime | **移出核心**；Adapter 接口保留为扩展点 | 自研 agent 是核心；协议胶水（~5k 行）不再是产品的一部分 |
| 代码策略 | **同仓库 strangler 重构**：新建 `eidolon/` 包，按模块移植，`app/` 逐步清空 | 保留 git 历史与可复用的测试；避免"大爆炸重写" |
| 测试中的模型 | **录制/回放 Provider** 取代 Mock Runtime 模板 | 确定性 + 真实分布；Mock 模板永久退役 |

> 关于 Hermes / OpenClaw 的后门传闻：我没有核实这一点，也不以它为设计依据。
> 自研的架构理由本身足够：核心能力不能依赖我们无法控制的协议、提示词和记忆实现。

---

## 1. 代码审阅结论：核心瘦身清单

现状后端 82k 行，按包审阅后的处置（行数为当前实测）：

| 包 | 行数 | 处置 | 去向 / 说明 |
|---|---|---|---|
| `work/` | 19.3k | **拆分重写** | `contracts.py`(98KB) 与 `production.py`(38KB) 是不变量注册表，压缩为 `kernel/contracts`；`tool_reads/tool_writes/tool_executor`(115KB) → `agent/tools`；`readiness/dispatch/reviews/handoff/decisions/authority` → `work/`、`institution/`；`runtime_bridge/runtime_conversation/telemetry/recovery/controlled_resume/management_runner/management_dispatch`(~170KB) **删除**，由自研 agent 的 durable loop 取代 |
| `services/` | 20.3k | **按域拆回领域包**，编排层消失 | `project_delivery`(54KB)、`position_service`(38KB)、`lifecycle`(39KB)、`career`(32KB)、`learning`(30KB) 各自瘦身 40–60% |
| `runtimes/` | 4.9k | **核心删除** | 仅保留 `base.py` 的 Adapter 接口作为 `agent/runtime_adapter.py` 扩展点；hermes/openclaw/mock/docker-manager 移到 `extensions/`（不维护） |
| `models/` | 6.5k | 116 表 → **≤ 45 表** | 事实表 + 投影表 + 必要的实体表；派生不落库 |
| `api/` | 6.8k | 路由瘦身 | 删除 economy 扩展、cultivation、market、contracts、talent_trade、work_orders、tutorial 路由（随扩展走） |
| `lifecycle/` | 2.2k | **折入 agent 开通** | 员工 = Person + Employment + AgentBinding；开通就是建 workspace + 绑定模型 |
| `brain/` | 1.5k | **折入 agent.persona** | traits → 检索/反思额度的解析器保留（~300 行） |
| `tutorials/` | 0.9k | **延期** | 上线后再做，作为投影 |
| `talent/`、`assessment/`、`competency/`、`evidence/`、`learning/`、`knowledge/` | 6.5k | **保留为投影** | 证据管线是溯源的一部分；能力/履历是投影 |
| `economy/` + economy services | ~6k | **瘦为 money/** | 保留复式账本、账户、成本计量；奖励/工单/托管/合同/人才交易/NPC → `extensions/market` |
| `providers/`、`core/`、`git/` | 1.4k | 保留 | provider 凭证加密保留；git 延期 |
| 前端 | 42k | 保留骨架，页面按核心裁剪 | 删除 cultivation/market/contracts/work-orders/economy 页面与 tutorial 组件（随扩展走） |

**v2 核心目标规模**：后端 **30–35k 行**（含新写的 agent 框架 ~8k），表 ≤ 45，迁移从 v1 重新编号。

---

## 2. 目标架构

### 2.1 分层与依赖方向（import-linter 强制）

```
                 ┌────────────── api (FastAPI, 薄路由, 只做鉴权+DTO) ──────────────┐
                 │                                                               │
  extensions/*  ─┤  market · cultivation · tutorial · third-party runtime adapters│
                 │                                                               │
  talent        ─┤  competency / evidence / resume / profile   （全部是投影）      │
  money         ─┤  ledger(复式) · accounts · cost metering                        │
  agent         ─┤  loop · memory · context · tools · runner · providers · budget │
  work          ─┤  mission · project · task graph · dispatch · review · artifacts│
  institution   ─┤  templates · roles · gates · authority · confirmation          │
  identity      ─┤  user · company · person · employment · position(slim)         │
  kernel        ─┤  facts · outbox · projections · hashchain · ids · clock        │
                 └───────────────────────────────────────────────────────────────┘
依赖只能向下。kernel 不 import 任何上层。extensions 可以 import 一切，但一切不得 import extensions。
```

### 2.2 进程模型

```
eidolon-api      FastAPI；读投影、写 Fact（经领域服务）；不跑 agent 循环
eidolon-worker   调度器 + agent 循环；用 SELECT … FOR UPDATE SKIP LOCKED 领取 run；可水平扩展
eidolon-runner   每个 workspace 一个沙箱进程/容器；只执行工具；没有 provider 密钥；JSON-RPC over stdio/unix socket
PostgreSQL       facts / 投影 / 任务队列（LISTEN/NOTIFY 唤醒 worker）/ pgvector 记忆索引
```
不引入 Redis/消息队列；Postgres 足够到相当规模，且减少上线运维面。

### 2.3 目录

```
apps/server/eidolon/
  kernel/        facts.py outbox.py projections.py hashchain.py contracts.py replay.py
  identity/      models.py services.py
  institution/   schema.py templates/ gates.py authority.py confirmation.py
  work/          mission.py project.py graph.py dispatch.py review.py artifacts.py
  agent/         model.py loop.py memory/ context.py tools/ runner/ providers/ budget.py persona.py
  money/         ledger.py accounts.py costs.py
  talent/        evidence.py competency.py resume.py profile.py
  api/           routers/ deps.py
  extensions/    market/ cultivation/ tutorial/ runtime_adapters/
apps/runner/     独立可执行（先 Python；接口固定后可换 Go）
apps/web/
```

---

## 3. 内核（kernel）

与 `kernel-plan.md` §1 一致，这里只定接口：

```python
class Fact(BaseModel):
    id: UUID; seq: int; company_id: UUID
    actor: Actor            # kind: system|agent|human, id
    kind: str               # 'work.task.created' …（分层词表）
    payload: dict
    idempotency_key: str; causation_id: UUID|None; correlation_id: UUID; decision_id: UUID|None
    prev_hash: str; hash: str; created_at: datetime

class FactStore(Protocol):
    def append(self, db, fact: NewFact) -> Fact          # 同事务；幂等键冲突返回已有
    def stream(self, after_seq, kinds=None, company_id=None) -> Iterator[Fact]

class Projection(Protocol):
    name: str; handles: frozenset[str]
    def apply(self, db, fact: Fact) -> None
    def rebuild(self, db) -> None

Outbox: after_commit → NOTIFY；worker 侧按 (consumer, last_seq) 游标消费；启动 sweep。
Replay: 唯一的恢复机制；无按子系统的恢复代码。
```

---

## 4. 自研智能体框架（agent）

### 4.1 Agent 模型

```
Agent = Identity(Person)            谁：随人走的命名空间
      + Persona                     性格/目标/traits → 只影响工作方式（检索额度、反思额度），不影响判定
      + Memory                      四层，见 4.3
      + ToolSet                     按职位/授权解析出的工具子集
      + ModelBinding                provider + model + 参数 + 预算
      + Policy                      超时 / 最大步数 / 允许的副作用级别 / 升级规则（来自制度）
```
没有"AgentProfile 表"：Agent 是上述对象在运行时的聚合，所有持久状态是 Fact 或投影。

### 4.2 Durable 执行循环（取代 U13–U16 全部机制）

```
AgentRun(run_id, person_id, task_id, budget)
  loop:
    ctx   = ContextBuilder.build(run)            # 4.4
    fact  agent.step.requested {ctx_hash, step_no}          ← 先落事实
    resp  = Provider.complete(ctx, tools)        # 流式；usage 计量
    fact  agent.step.completed {resp_hash, usage, tool_calls}
    for call in tool_calls:
        gate  = institution.gates.resolve(call)  # system / agent-authority / human-confirm
        fact  agent.tool.requested {idempotency_key}
        res   = Runner.exec(call) | Service.exec(call)      # 副作用必带幂等键
        fact  agent.tool.completed {res_hash}
    if resp.final: fact agent.run.completed → 触发 reflection
崩溃恢复 = 从最后一条 agent.* 事实继续：
  · step.requested 无 completed → 重新调用模型（记一次 attempt）
  · tool.requested 无 completed → 按幂等键查询副作用是否已发生，再决定重放或跳过
取消 = 写 agent.run.cancel_requested 事实；循环每步检查；runner 收 SIGTERM。
```
每步事实只存哈希与结构化摘要，完整 transcript 存对象存储（本地文件/S3），按保留策略清理（R6 遥测爆炸风险的解法）。

### 4.3 记忆系统（四层，全部按 person 隔离，仓库层强制）

| 层 | 内容 | 写入 | 读取 |
|---|---|---|---|
| Working | 本步上下文 | 不持久（只存哈希） | — |
| Episodic | 会话/任务级经历：做了什么、结果、花费 | run 结束时 reflection 生成 `MemoryEntry`（事实） | 向量 + 时间衰减 + 任务相似度 |
| Semantic | 知识条目（private / dept / company 三级，晋升需评审） | 沿用现有 KnowledgeItem 与晋升循环 | 混合检索（pgvector + FTS），受 traits 额度 |
| Procedural | 技能（SKILL.md 形式，candidate → validated） | 沿用现有 Skill 生命周期 | 按任务类型匹配注入 |

- 嵌入模型是 Provider 的一种能力（`embed()`），凭证同样加密。
- 压缩：长会话按步数/令牌阈值做摘要链；摘要本身是 `agent.memory.compacted` 事实。
- 遗忘：不删除事实；只降低检索权重（投影层）。
- 公司知识与个人记忆的边界不变（Private Memory ≠ Company Knowledge）。

### 4.4 上下文装配（ContextBuilder）

固定顺序、固定预算分配（可按制度调）：
```
system:   身份 + 职位 RoleContext（责任/授权/期望）+ 制度摘要 + 工具清单 + 输出契约
task:     Mission 摘要 + Task 描述/验收标准 + 输入产物（有界摘要 + 引用）
memory:   procedural(技能) → semantic(知识) → episodic(经历)，各有 token 上限
history:  本 run 的步骤（压缩后）
```
输出契约：结构化（JSON schema）——最终答复 / 工具调用 / 需要决策 / 阻塞原因。自由文本永不直接变成领域动作（沿用 U7 纪律）。

### 4.5 工具系统

- Registry 沿用 M2.3 的自描述：`side_effect(READ|WRITE|HIGH_IMPACT)`、`required_authority`、schema、handler、`decision_semantics`。
- 两类执行面：
  - **Service tools**（领域写/读）：在 worker 进程内经领域服务执行，受 gate 校验。
  - **Runner tools**（shell / 文件 / git / http / 测试）：在 runner 沙箱执行；路径限定在 workspace；网络按公司允许列表；无密钥。
- 工具结果进入事实前做大小与敏感信息裁剪（沿用 `core/redaction`）。
- Prompt injection 边界：工具返回内容与外部文档一律标记为不可信数据；只有结构化输出能触发动作，且动作仍过 gate。

### 4.6 模型层（providers）

```python
class Provider(Protocol):
    async def complete(self, req: ChatRequest) -> AsyncIterator[ChatEvent]   # 流式；含 tool_calls、usage
    async def embed(self, texts) -> list[Vector]
```
- 实现：`openai_compatible`（覆盖 DeepSeek / Qwen / vLLM / Ollama 等）、`anthropic`；两者即可上线。
- 重试/退避/超时/速率限制在此层；每次调用产生 `agent.model.called` 事实（usage、估价、request_id）→ money 成本计量。
- **录制/回放 Provider**：`record` 模式把真实响应写入 fixture；`replay` 模式按请求哈希回放。测试不再有"模板式 mock"。

### 4.7 预算与费用

- 每个 run 有预算（来自制度 / 项目）；超过 → `agent.run.budget_exceeded` 事实 → gate 决定升级。
- 成本计量：model usage × 价格表（版本化）→ `money.cost.recorded` → 账本借贷（公司 → 算力成本科目）。

### 4.8 多 Agent 协作

只有两种渠道，都是事实：**Task**（含产物交接）与 **Message**（定向消息，带线程）。没有 agent 直接调用 agent；Manager 通过决策信封建任务，Worker 通过产物与消息回应。

### 4.9 Agent 的"员工开通"

招聘 = Person + Employment + `AgentBinding(provider, model, workspace, persona)` + workspace 目录创建；没有容器编排。
Readiness 四层保留：Employment / Binding / Workspace / Budget。

---

## 5. 制度层（institution）：默认实现必须可上线

### 5.1 Schema

```
InstitutionTemplate
  id, version, name
  roles[]:            code, title, reports_to, decides[]  (gate 列表)
  gates{name}:        decider: system | agent | human
                      role:      谁（agent/human 时）
                      quorum:    n（会签/三重一大）
                      separation: [author≠reviewer, proposer≠approver]
                      amount_tiers: [{max, decider, role}]   (金额分级)
                      timeout:   秒；escalate_to: gate|role
                      require_reason: bool
  authority_defaults{role}: grants[]
  budget_policy:      project_default, run_default, warn_ratio, hard_stop
  review_policy:      rework_limit, escalate_after
  retention:          transcript_days, fact_forever=true
```
校验：每个内核 gate 必有 decider；human decider 必可达 Owner；升级链无环；分离职责不自相矛盾。

### 5.2 内核 gate 清单（固定，模板只能改"谁决定"）

`mission.accept` · `plan.approve` · `task.assign` · `task.review` · `task.rework` · `task.cancel` · `project.deliver` · `spend.approve` · `hire` · `fire` · `purchase` · `knowledge.promote` · `escalation.resolve`

### 5.3 默认制度 `software-studio-v1`（可上线的那一个）

| 项 | 规则 |
|---|---|
| 角色 | Owner（人）、CEO、PM、Tech Lead、Engineer、QA、Reviewer（QA 兼） |
| mission.accept | CEO（agent）；预算 > 项目默认 2× → Owner 确认 |
| plan.approve | PM 提出，Tech Lead 会签（quorum 2）；任务数 > 20 或含采购 → CEO |
| task.assign | 所属负责人（PM/Tech Lead）；系统只校验就绪与授权，不选人 |
| task.review | Reviewer ≠ author（硬分离）；REWORK 上限 2，之后升级到 Tech Lead；再失败 → Owner |
| project.deliver | Owner 验收（human）；可在定制里改为 CEO |
| spend.approve | 分级：≤ 单次默认预算 → 系统放行；≤ 项目预算 80% → PM；以上 → Owner |
| hire / fire / purchase | Owner 确认（human），提案人 ≠ 批准人 |
| knowledge.promote | 部门级：Tech Lead；公司级：CEO + 任一其他管理者 |
| SLA | 任务超期 → 升级到负责人；负责人 24h 无响应 → Owner |
| 审计 | 全部事实永久；transcript 90 天 |

另提供 `startup-flat`（Owner 兼 CEO、单评审）与 `institutional`（请示—审批—会签—批复—归档、三重一大 quorum 3）两个模板，但只有 `software-studio-v1` 作为上线默认并被 K6 三条真实路径验证。

### 5.4 定制

fork → 改 gate 的 decider/role/quorum/tiers/timeout 与授权默认 → 校验 → 新版本。变更是 `institution.changed` 事实；历史决策引用当时版本。

---

## 6. 工作域（work）瘦身后的形态

- **Mission**（新）：目标 / 成功标准 / 约束 / 预算 / 期限；Project 从 Mission 派生。
- Project / Task / Dependency / Review / Artifact 引用沿用 M2 语义（R1–R12、H1–H8、RV1–RV8 作为 `kernel/contracts` 的一部分保留）。
- Dispatch：`resolve_ready` → gate `task.assign` → 创建 AgentRun（事实）→ worker 领取。
- Review：Worker 完成 → `in_review` → Reviewer Agent run → 结论经 `task.review` gate 落地。
- Artifact：Drive 瘦为"内容寻址存储 + 归属 + 使用"三件事；四区目录、上传文件夹等 UI 便利延期。

---

## 7. 上线所需的横切能力（不是功能，是门槛）

| 项 | 决定 |
|---|---|
| 认证 | 沿用现有邮箱/密码/会话/CSRF；Passkey 保留（已生产级） |
| 密钥 | Provider 凭证 Fernet 加密沿用；runner 进程零密钥 |
| 部署 | docker compose：postgres + api + worker + runner-pool + web；单机可上线，worker 可横向扩 |
| 观测 | 结构化日志（沿用）、Prometheus 指标（run 数、步数、费用、gate 等待）、事实流即审计 |
| 备份 | Postgres 逻辑备份 + transcript 对象存储；`ledger-verify` 作为恢复后校验 |
| 限流/配额 | 每公司并发 run 上限、每日费用上限（制度 budget_policy） |
| 数据保留 | 事实永久；transcript 按制度；投影可重建 |

---

## 8. 重构路线（strangler，替代 kernel-plan v1 的 K1–K6）

每个阶段都以"旧 `app/` 中对应模块被删除"为完成标志，而不是"新代码写完"。

| 阶段 | 内容 | 完成标志 | 预计会话 |
|---|---|---|---|
| **R0 止血** | WAL、收口 WIP、基线数字（= v1 K0） | 门禁绿、Progress 有基线 | 1 |
| **R1 内核 + Postgres** | `eidolon/kernel`；docker compose 起 PG；测试基座切 PG；FactStore/Outbox/Projection/Replay/Hashchain | 旧 `events`/`tool_audits` 写入改经 Fact；`ledger-verify` 可跑 | 4–6 |
| **R2 智能体框架 MVP** | `eidolon/agent`：loop、provider(openai_compatible)、runner(Python)、ContextBuilder、Episodic+Semantic 记忆、录制/回放；一个 Engineer agent 在沙箱完成"写一个 CLI 小工具 + 测试"真实任务 | 真实模型一次交付；崩溃重放通过；`runtimes/hermes|openclaw|mock` 移出核心 | 6–8 |
| **R3 制度 + 工作域** | `institution`（schema、gates、`software-studio-v1`）；`work` 移植（Mission、图、派发、评审、产物）；Manager/Reviewer agent 走同一循环 | CEO→PM→Engineer→QA 真实跑完一个 3 任务项目；旧 `work/runtime_*`、`management_*`、`recovery.py` 删除 | 6–8 |
| **R4 身份 + 钱 + 溯源** | `identity` 瘦身移植；`money` 只留账本+成本；Provenance 查询 + 导出校验 | 费用从模型调用一路进账本可溯；旧 services 层清空过半 | 4–5 |
| **R5 人才投影 + 前端** | evidence/competency/resume/profile 作为投影；前端按核心裁剪并接新 API | 旧 `app/` 删除；116 表 → ≤45 | 5–7 |
| **R6 上线加固** | §7 全部；三条真实路径（交付 / 招聘后工作 / 崩溃重放）；冻结文档机器检 | 可在一台服务器上对外提供服务 | 4–5 |
| R7 扩展（可选） | market / cultivation / tutorial 从 `extensions/` 逐个接回 | 按需 | — |

总计约 **30–40 个会话**。R2 结束是第一个 go/no-go：自研循环 + 记忆能否稳定完成真实任务。

---

## 9. 用户裁决（**2026-11-06 全部按建议通过，已生效**）

| # | 议题 | 建议 = 决定 |
|---|---|---|
| V1 | Postgres 唯一（含测试） vs 保留 SQLite 开发 | 唯一；测试用本地 PG（docker），牺牲一点速度换零方言债 |
| V2 | 首批 Provider | `openai_compatible`（DeepSeek 作为默认验证）+ `anthropic` |
| V3 | Runner 首版用 Python 子进程还是 Docker 容器 | 子进程 + 工作区 chroot 式路径限制先上；接口固定后换容器/Go |
| V4 | 旧 T2/M1 扩展（市场、培养、合同、NPC）是否保留到 R7 还是直接归档 | 归档到 `extensions/`（不删历史），上线范围不含 |
| V5 | 前端是否同步大裁剪 | 是；核心页面：Projects / Employees(含运行态+溯源) / Drive(最简) / Settings(provider/institution) / Economy(只读余额与费用) |
| V6 | 制度默认 `software-studio-v1` 中 `project.deliver` 是否由 Owner 人工验收 | 是（上线初期人保底），可定制为 CEO |

---

## 10. 与 kernel-plan v1 的关系

- v1 §1（本质）、§5（纪律）、§7（旧 U 包去向）继续有效。
- v1 §3–§4 的 K1–K6 由本文 §8 的 R0–R6 取代；v1 §8 Progress 表改为按 R 阶段 / WP 记录。
- 下一步：用户确认 V1–V6 后，R0 开工。

---

## 11. ADR（实施中的裁决，按时间追加）

**v2-ADR-1（2026-11-06）v2 是新应用，不与旧 `app/` 共库、不双写。**
仓库没有生产数据，只有本地开发库。因此 v2 在 `apps/server/eidolon/` 用独立的 PostgreSQL schema、
独立的 Alembic 链（`alembic-v2.ini`，版本表 `alembic_version_v2`，从 `0001` 重新编号）从零建起；
旧 `app/` 只作移植参考，R5 整体删除后 cutover。§8 R1 中"旧 `events`/`tool_audits` 写入改经 Fact"一项
作废——v2 里只有 Fact。好处：不必修 66 个 SQLite 迁移，不存在跨库事务。

**v2-ADR-2（2026-11-06）事实日志即 outbox；NOTIFY 只是唤醒。**
`FactStore.append` 在写事务内执行 `NOTIFY`，PostgreSQL 保证只有提交才送达（K9）。消费者按
`fact_cursors` 游标推进，处理与推进同一事务（K6）；错过通知无害，下一次唤醒或超时轮询补上。
没有独立的 outbox 表、没有"事务提交后再发事件"的手工纪律、没有按子系统的恢复代码——
`replay_all` 是唯一恢复机制。

**v2-ADR-3（2026-11-06）测试基座 = 真实 PostgreSQL，不可用即失败而非跳过。**
`tests_v2/conftest.py` 连不上库直接 `pytest.exit`。两种等价的本地库：`make pg-up`（compose，
postgres:16）或 `scripts/devpg.sh start`（用户态集群，任何 PG ≥ 14 二进制，无需 Docker/root）。
两者都暴露 `127.0.0.1:5433`、`eidolon/eidolon`、库 `eidolon` + `eidolon_test`。CI 用 service 容器。
最低版本 PostgreSQL 14（`Identity(always=True)`、`ON CONFLICT`、JSONB 均可用）；pgvector 到 R2 再要求。

**v2-ADR-4（2026-11-06）不可篡改 = 触发器 + 哈希链两层。**
`facts` 上的 BEFORE UPDATE/DELETE 触发器拒绝改写（K1）；每公司一条 sha256 链 + `fact_chains`
行锁串行化同公司追加（K3）。触发器挡住应用层，哈希链让拥有数据库权限的篡改可被 `verify_chain`
发现。生产部署应撤销应用角色对 `facts` 的 UPDATE/DELETE/TRUNCATE 权限（R6 加固项）。

**v2-ADR-5（2026-11-07）worker 的失败隔离与停止语义。**
worker 启动与每次唤醒做的是同一件事：对注册表里的每个消费者从游标 drain 到底（启动时的"replay"不是另一套代码）。
一个消费者的批次抛异常时，该批次回滚、游标不动、记一次失败并写日志，**其余消费者照常推进**；失败者在下一次唤醒重试，
永不跳过事实、永不把游标推过失败点。理由：一条"毒事实"不应让整个进程反复崩溃并拖垮无关投影，而重试又保证修好处理器后无需人工干预即可恢复。
停止（SIGTERM / SIGINT / `stop()`）只在批次边界生效：当前批次与其游标在同一事务提交后退出，退出码 0；等待中的 worker 由 self-pipe 立即唤醒。
消费者注册表只有一个入口 `eidolon/registry.py:default_registry`，worker 与 CLI（`rebuild`、`cursors`）共用，`--registry module:callable` 仅用于测试与调试。

**v2-ADR-6（2026-11-07）离线校验器是独立重实现；bundle 带整文件 seal。**
`scripts/verify_bundle.py` 只用标准库、不连库、**不 import `eidolon`**：审计方拿到 bundle 和这一个文件就能验，
不必信任应用代码。哈希规则因此在脚本里重述一遍，两套实现由 `test_provenance` 用真实导出互证（K10），漂移会红。
bundle 除每条事实的 `prev_hash/hash` 与公司链头外，还带 `bundle_hash = sha256(canonical_json(其余全部))`：
事实哈希证明"内容与账本一致"，seal 证明"文件未被改动"（含导出时间、选择条件、顺序）；重封印一个伪造 bundle 仍过不了事实哈希。
子集导出（按 correlation / fact_ids）只校验相邻 chain_seq 之间的链接，并在子集含链尾时与链头比对；整链导出要求从 1 连续到链头。

**v2-ADR-7（2026-11-07）Provider 协议与录制回放的三条约定。**
(1) 协议是自有的 `ChatRequest → ChatEvent*`（TextDelta / ToolCallEvent / Completed），两家实现各自翻译；Anthropic 走官方 SDK，
openai_compatible 走 httpx + SSE。模型侧必须原样回传的东西（DeepSeek thinking 模式的 `reasoning_content`、Anthropic 带签名的 thinking 块）
由 `Completed.assistant_state` 带出、`Message.provider_state` 带回，协议层不解释其内容；循环（R2-b）把它存进步骤事实，续跑才不会被 400 拒绝。
(2) 录制/回放按**请求指纹**（模型所见内容的 canonical JSON 的 sha256，不含 CallContext）命中 fixture；回放缺 fixture 直接失败，不允许即兴应答。
fixture 由 `scripts/record_provider_fixtures.py` 对真实模型录制并提交，测试因此既是真实分布又确定。重试只发生在首个事件之前：半截流不静默重启。
(3) 估价用版本化价目表，金额为**计费币种的微单位整数**（DeepSeek CNY、Anthropic USD，DeepSeek 按峰值价作上界），未知模型不估价而非乱估；
每次调用落 `agent.model.called`，幂等键 = provider + request_id，回放的调用标 `mode: replay` 但同样入账（成本计量在 money 层区分）。

**v2-ADR-8（2026-11-07）run 的状态只是事实的折叠；transcript 进内容寻址 blob 存储。**
`RunState.from_facts` 把一条 run 的 `agent.*` 事实按 seq 折成状态（待应答的 step、未完成的工具调用、历史引用、用量、终态），
循环不持有任何别的状态；恢复 = 折叠 + 继续，启动与恢复走同一段 `continue_`。每条事实各自提交，`agent.step.requested` 在模型调用**之前**
落库；幂等键是 (run_id, step_no, attempt, call_id) 的纯函数，所以重启后的进程把同样的事实再写一遍只会命中 K2 的幂等返回。
模型应答前崩溃 → 同一步 attempt+1；工具执行中崩溃 → 把**同一个**幂等键再交给执行器，由执行器（R2-c 的 runner）决定副作用是否已发生。
事实只存哈希、引用和 ≤200 字摘要（K4 上限 64KB，DeepSeek 一步的 reasoning 就能超）；完整上下文、assistant 回合（含 provider_state）
与工具结果存 `BlobStore`（`sha256:<hex>`，写一次不可改、读时校验），这是 §4.2/§7 所说的"transcript 存对象存储"的本地实现。
blob 不是第二种持久状态：它是被事实引用的不可变内容，按制度保留期清理后事实依然自洽（引用悬空只影响重放，不影响账本）。

**v2-ADR-9（2026-11-07）runner 是独立进程、独立包、零密钥，幂等账本在 runner 这一侧。**
`apps/runner/eidolon_runner` 只用标准库、不 import `eidolon`，协议是 JSON-RPC 2.0 按行走 stdio；服务端只认协议不认实现，
接口固定后可换容器或 Go（V3）。密钥隔离做两道锁：客户端 spawn 时只传白名单环境变量（PATH/HOME/LANG/TMPDIR/TZ + PYTHONPATH），
runner 启动后再剔除名字含 KEY/SECRET/TOKEN/PASSWORD/CREDENTIAL 的变量，测试用父进程故意注入的假 key 验证 shell 里看不到。
路径限定是"解析后必须落在 workspace 内"（跟随符号链接、拒绝绝对路径与 `..`、保护 `.eidolon/`），不是字符串前缀匹配。
幂等账本放在 runner 侧（`.eidolon/runner-ledger.jsonl`）而不是服务端：副作用发生在哪里，"是否已发生"的记录就在哪里；
循环崩溃后把同一个键再交过来，runner 返回存储的结果并标 `replayed`，读类工具永不重放。工具失败是带 `is_error` 的结果
（模型应当看到），JSON-RPC error 只用于请求本身畸形。超时按进程组 kill（`start_new_session`），输出按字节截断并标注。
