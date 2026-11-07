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

fork → 改 gate 的 decider/role/quorum/tiers/timeout 与授权默认 → 校验 → 新版本。变更是 `institution.template.changed` 事实；历史决策引用当时版本。

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

**v2-ADR-10（2026-11-07）记忆 = 事实 + 一张投影表；可见性在仓库层；反思是消费者。**
记忆只有三种事实：`recorded`（新条目）、`promoted`（semantic 的 scope 变宽——这是管理判断，事实必须带 `decision_id`
或由 human 写入，系统自己永远不晋升）、`forgotten`（只降检索权重，永不删除）。`memory_entries` 是唯一的新表，
`python -m eidolon rebuild memory_entries` 从零重建（用户 2026-11-07 批准新增）。可见性不是查询参数而是仓库的硬规则：
private 只有本人，department 同部门，company 全员；换哪个调用方都绕不过。排序 = 全文相关度 × 权重 × 重要度因子 ×
episodic 的时间衰减（半衰期 30 天，semantic 不衰减）；检索默认"任一词命中、多命中靠前"，因为召回的输入是整段任务描述。
反思不在循环里做：`Reflector` 消费 `agent.run.completed`，由 worker 异步跑，一次模型调用、同一事务落事实并推进游标（K6），
请求只由 transcript 构造（不含 id/时间），所以回放的 run 得到同样的 fixture；模型不给 JSON 也落原文，消费者不会卡在一条 run 上。
全文检索先用 PostgreSQL `english` 配置；中文分词与向量检索（pgvector）在接口上留了位置（排序再乘一个因子）。

**v2-ADR-11（2026-11-07）R2 go；验收 = 真实一次 + 回放永远；工具输出必须可复现。**
R2-e 的真实任务在 DeepSeek 上两次完成（见 kernel-plan §8），自研循环 + runner + 记忆可以稳定完成真实交付，R2 判定 **GO**，进入 R3。
验收口径从此固定：每个真实场景先对真实模型跑一次并录制，之后的回归用回放走**同一条代码路径**（真实 CLI、真实 runner 子进程、
真实 PostgreSQL），只有模型应答来自 fixture。回放要逐字节可复现，前提是工具输出不含任何随运行而变的东西：runner 把耗时移到
RPC 元数据、把工作区绝对路径统一显示为 `/workspace`（顺带不向模型泄露宿主路径）、把 shell 里的 `python` 固定为 runner 自己的解释器。
真实调用的开关：`EIDOLON_V2_LIVE_TASK=1` 跑 R2-e 真实任务，有 key 自动跑 provider 的 ping；没有 key 一律 skip 并写明原因，不计入通过。

**v2-ADR-12（2026-11-07）制度模板是代码里的数据，被采用时全文进事实；改制度也要有授权。**
内核 gate 清单固定为 13 个（§5.2），模板只决定"谁决定、怎么决定"；一个模板要被接受，必须通过 `validate`（每个 gate 有 decider、
human decider 可达 Owner、组织图是树、升级不成环、system 不带 quorum/分离、金额分级单调且末档无界）。内置三个模板是 Python 数据
而不是 YAML：类型检查与 IDE 可见，且不需要新的加载/迁移机制；公司采用时把模板**全文**写进 `institution.template.adopted`，
定制写进 `institution.template.changed`（上一版本的指纹与 causation 一起带上），所以任何历史决策都能按当时的制度解释，模板代码改了也不影响已采用的公司。
采用与定制都是管理行为：`actor` 必须是 human，或带 `decision_id`（gate `escalation.resolve` / 将来的 `institution.change`）。

**v2-ADR-13（2026-11-07）决策信封：谁决定由 resolve 算出，怎么决定由四种事实记录，系统永不签字。**
`resolve` 是纯函数：模板 + 情境（提案人、作者、金额、规模等事实）→ Resolution，分支固定为 rule / tier / escalate_if / company_scope，
结果带模板指纹，所以一条旧决策永远能按当时的制度解释。信封只有四种事实：requested（含 Resolution 与主体）、signed（每位决定者一条，
带 authority 快照）、recorded（quorum 满足或任一否决时由系统**机械地**落账，理由是签字的汇总）、escalated（超时或强制，换下一位决定者）。
系统的角色只有两种：对 decider=system 的 gate 把执行过的机械检查写成 recorded 的理由；对其他 gate 只做校验（actor 类型、角色、
分离职责、理由、重复签字、human 的动作哈希）并在签字凑齐时落 recorded——永远不替任何人签字（§1.2）。
工具调用的越权在循环里是**结果**不是异常：`tool_gate` 拒绝时写 `tool.completed{refused:true,is_error:true}`，模型看到原因后自行调整。

**v2-ADR-14（2026-11-07）工作域 = 事实 + 按 project 折叠的强一致状态 + 四张可重建投影；就绪永不落库。**
一个 project 的所有事实（project、task、review、artifact）共享 `correlation_id = project_id`，`ProjectState.from_facts(by_correlation)`
给出强一致视图，服务函数据此校验转换与决策；四张 `work_*` 表只是读模型（worker 异步推进，`rebuild work_items` 可从零重建），
不参与任何判定。READY 是 `graph.is_ready` 的派生（todo 且依赖与输入生产者全部 done），没有列、没有枚举值（RD1）；
"可派发" = READY 且已有被决策指定的 assignee（R1/R3），READY 但无人 = 管理问题（R4），系统永不填人（R2/R12）。
M2 的 R/H/RV/RD 不变量折成 W1–W10 放进 `kernel/contracts.WORK_INVARIANTS`，每条有测试锚且被守卫机器检查。
评审映射一向：decision approved → pass → done，rework → rework → todo（计数），rejected → escalate → blocked（等人）；系统从不产生评审结论（RV1）。

**v2-ADR-15（2026-11-07）派发与评审：请求是事实，启动是短事务，执行是作业；评审结论永远由 reviewer 签字。**
派发分三段：`request_runs`（work 层，纯事实，不 import agent）→ `RunLauncher` 消费者把请求变成 `task.started`（几毫秒的事务；
绝不能在消费者事务里跑 run，否则 `fact_chains` 的行锁会把同公司的所有追加堵死）→ `TaskRunExecutor` 作业在事务之外跑/续跑 AgentRunner，
每一步（started / artifact / submitted / failed）都是独立提交的幂等事实，进程死在任何位置下一轮都能接上。
Worker 的循环因此变成 drain → jobs → drain：作业产生的事实立刻被投影消化。
评审同样不是系统判断：`ReviewExecutor` 只负责开决策（id 由 task+run 派生，幂等）、跑 Reviewer agent、把 agent 的 JSON 结论作为
该 reviewer 的签字写进决策信封，`conclude` 落账后才写 `task.reviewed`；agent 没给出可解析的结论就什么都不写，任务留在 in_review 等待（RV1/RV6）。
身份层未到前，reviewer 的 person/role 来自配置（`EIDOLON_V2_REVIEWER_PERSON/ROLE`）。

**v2-ADR-16（2026-11-07）管理 agent 走同一循环：决策 run 产出结构化答案，由该角色签字；项目由 mission 派生。**
CEO / PM / Tech Lead 没有特殊通道：`Deciders.ask` 用普通 AgentRunner（无工具、两步预算）跑一次，答案是 JSON，解析后作为**该角色的签字**
写进决策信封；解析失败或 run 失败则什么都不写，下一轮换一个 attempt 的 run id 再试（上限 3），系统从不代签（§1.2）。
PM 的计划同样是 agent 的产物：任务以 PM 名义创建（task id 由 project+key 派生，幂等），然后走 plan.approve；"接受的 mission 生出一个 project"
是派生不是判断，所以由系统写 `project.created`。R3-e 的真实运行证明了三条真实路径里的第一条（交付）：一个 mission 在 DeepSeek 上
由 CEO→PM→TL→Engineer×2→QA 六个 agent 用同一循环、同一制度、同一账本跑到 Owner 交付。
录制模式新增"复用已有 fixture"：长场景迭代时只对缺的请求真调，fixture 的 mtime 标记本轮用到了哪些，未触碰者即陈旧。
回放匹配键从精确指纹改为**对话形状**（`ChatRequest.replay_key`：system、模型、每条文本、工具调用名与参数、工具结果只取 id 与 is_error）：
工程师一句 `ls -la` 就让工具输出永远不可能逐字节重现，而回放时工具本来就真实执行；事实里的 `context_hash` 仍是精确指纹，不受影响。

**v2-ADR-17（2026-11-07）人与账号是平台级事实，雇佣是公司级事实；花名册只是雇佣关系的投影。**
`identity.user.registered` 与 `identity.person.created` 挂在固定的 PLATFORM 公司 id 下（人随人走，§4.1），`identity.company.created` 与
`identity.employment.*` 以公司为 correlation。谁在公司里扮演什么角色，唯一来源是活跃雇佣：管理作业、派发、评审都通过 `RosterSource.for_company`
现取，R3 的配置花名册只保留给测试（`StaticRoster`）。identity 在 institution 之下，所以"这个角色在不在制度里、kind 对不对"由
`institution/staffing.hire` 补上规则再调 identity；agent 招人/解雇必须带 `hire`/`fire` gate 的 decision_id，human 可以直接做，Owner 的雇佣不可结束。
密码、会话、Passkey 属于 API 层（R5），不进 identity 事实。

**v2-ADR-18（2026-11-07）钱是复式账本事实；每次模型调用由系统记量并过账，制度用"分"，账本用"微"。**
`money.entry.posted` 一条事实就是一笔平衡分录（≥2 行、Σ借=Σ贷、只记到已开立且币种一致的科目），没有"分录修改"事实，错账只能反向冲销；
`money.account.opened` 开科目。`CostMeter` 是反应器而非投影：它把 `agent.model.called` 的供应商币种估价按**版本化簿记汇率**换算成公司记账币种，
先落 `money.cost.recorded`（总是记：replay、未知模型、无公司、零额都记，`posted=false` 带 reason），live 且可换算的再落分录：借 `compute`（费用）贷 `ap:<provider>`（负债）；
两条事实都以调用事实 id 为幂等键，崩溃重放命中 K2。记量是系统的机械行为，不是管理判断，所以 actor 是 system。
投影 `money_accounts` / `money_journal` 只是 `money.*` 事实的读模型；项目费用汇总是 `journal.task_id` 与 `work_tasks` 的查询时连接，不落库，
所以账本不依赖消费者先后顺序；`ledger-verify` 把投影与**其游标之内**的事实比对，落后是 lag 不是损坏。
单位：制度模板里的预算与 `spend.approve` 金额是记账币种的最小单位（分），账本是微单位（1 分 = 10 000 微，单次调用只有零点几分），
只在 `money/budgets.py` 换算，模板数字与模型看到的提示词都不变（不必重录 fixture）。
回放的调用默认只记量不过账（不花钱）；驱动回放 fixture 的测试用 `CostMeter(book_replays=True)` 走完整链路。记账政策是部署常量：同一日志上跑两个政策不同的 meter 会在 K2 冲突。

**v2-ADR-19（2026-11-07）费用归属是因果的，不是存储的：每条 run 必须说出是哪条事实要了它。**
`agent.run.started` 带 causation：执行器指向 `work.task.started`，评审器指向 `work.task.submitted`，管理作业指向 mission / 项目 / 任务事实。
于是一笔分录只靠 causation 与 correlation 就能走回去：分录 ← 成本 ← 调用 ← step，调用的 correlation 是 run，run 的 causation 是「谁要的」，那条事实的
correlation 就是 project / mission / decision。`cost_trace` 走不通的环节记在 `missing` 里而不报错（run 外的调用、老数据没有 causation 都是合法状态）。
项目费用 = 任务 run 的分录（按 journal.task_id）+ 项目事实召唤的无任务管理 run（按 run.started 的 causation 落在项目 correlation 内）；
CEO 的 mission run 归 mission，不归项目。这些都是查询，不建新表、不在事实里存 project_id——否则换一种归属规则就得重写事实。
bundle 不另设「账本段」：`--with-ledger` 只是把项目花费背后的 `money.*` 事实并入同一个事实选择，封印与离线校验不变。

**v2-ADR-20（2026-11-07）人才层不写事实、不被引用：证据是其它层事实的读法，能力只给人看。**
`talent/` 不新增任何事实种类——评审结论、失败、交付、产物、run 结局、签字本来就都是事实，证据只是把「这条事实说了谁的什么」读出来，
每条证据带来源事实 id，所以「为什么这个人是这个分」永远一跳可查。能力是证据的 EWMA（粗信号 ±1/0 × 权重），是给人看的摘要，不是模型；
契约 T1 用 AST 锚定「talent 之下没有模块 import 它」，于是选人、指派、判通过都不可能把分数当输入——它们仍只能是 gate 上的决定（W1/W7）。
human 不积累证据：Owner 的行为是决定，不是被计量的劳动。`task.review` 门的签字不单独计证据，因为 verdict 事实已经记了同一件事。
履历与档案是纯查询，形状可以随前端随时改。

**v2-ADR-21（2026-11-07）重建投影不能把读者挡在门外：reset 用 DELETE，rebuild 带 lock_timeout。**
投影是给 API 和人随时读的；重建是运维动作。TRUNCATE 的 ACCESS EXCLUSIVE 锁让两者互斥：一个开着的读事务就能让 rebuild 无限等待，
rebuild 反过来又让所有新读者排队——这在测试里表现为整套 pytest 卡死，在生产里就是停机。于是 `reset` 一律经 `kernel.consumers.clear_tables`
（DELETE，MVCC 下与读者共存，读者在重置提交前仍看到旧行），`rebuild` 的重置段设 `lock_timeout`，真被别的会话锁住时抛 `RebuildBlocked`
而不是挂起：等不到锁是一种明确的失败，不该伪装成"worker 死了"。代价是大表 DELETE 比 TRUNCATE 慢且产生死元组，接受；
投影按设计都能从事实重建，表不该大到让这点成为问题。

**v2-ADR-22（2026-11-07）持久状态的第三种：API 层的秘密。凭证与会话既不是事实也不是投影。**
密码哈希不能进不可篡改、永久保留、可导出的账本；会话是有寿命、可吊销的秘密。它们是 API 层自己的两张表（`auth_credentials`、`auth_sessions`），
不是任何事实的派生，也不能重建——丢了只是重新登录或重设密码，公司的历史一字不变，这就是允许它们存在的全部理由。
注册仍然是事实（`identity.user.registered` + 人的 `identity.person.created`），凭证行与事实同一事务落库；登录态只在 cookie 与会话表之间流转。
由此规则改写为：持久状态只有 Fact、投影，和 API 层的秘密；新表要么回答"从哪些 Fact 重建"，要么回答"丢了为什么无所谓"。
API 本身保持薄：鉴权 + DTO + 调服务；授权只看雇佣投影（你在这家公司有活跃雇佣才能读它），不做任何管理判断。
对外只给事实里有的东西：预览、用量、工具名、结局；reasoning / provider_state / 完整上下文只在 blob，永不经 API 出去（P 守卫），`*_ref` 也不出。

**v2-ADR-23（2026-11-07）API 写侧只有两种动作：人的自有动作按 grant 放行，管理动作走信封；结论的后果由系统机械施行。**
人在 API 上能做的事分两类。第一类是制度里直接授予的自有动作（建公司、定制制度、招人解雇、提 mission、取消 run），
API 只查 `has_authority(template, role, grant)`，然后调领域服务——服务层本来就允许 human 直接做这些，API 不再发明第二套规则。
第二类是 gate 上的决定：API 只做"开信封、签字、读信封"的搬运，谁能签、要不要理由、分权、会签、动作是否与绑定的哈希一致，
全部由 `institution.decisions` 校验——所以 API 不可能比制度更宽松。决定一旦 conclude，后果由 `Commands.apply` 按 gate 机械施行
（交付、批方案、指派、取消、入职、离职），这和管理作业施行 agent 决定是同一回事：施行不是判断，判断已经在签字里。
签字与施行同一事务：后果施行不了（例如项目未完工）就连签字一起回滚，不留下一个"批准了但没发生"的信封。
`Idempotency-Key` 派生实体 id 而不是另存请求表：重试天然命中 K2，不需要新的持久状态。

**v2-ADR-24（2026-11-07）前端是 `/api/v2` 的薄客户端：只读投影、只发命令、轮询而不推送。**
旧前端 42k 行里绝大部分是扩展页面和与旧数据模型（整数 id、生命周期、运行时适配器、教程、办公室游戏）耦合的组件，没有可"接到新 API"的东西，
于是按 V5 重写成 4.7k 行：六组核心页面各自只做一件事，所有数据来自 R5-b 的投影读路由，所有改动经 R5-c 的命令路由。
不再有 WebSocket 事件流：事实日志本身就是状态，react-query 对 run / 项目 / 决定做 5–10 秒轮询，省掉一整套连接管理与"代际"失效逻辑；
真需要推送时再加 SSE，而不是先把复杂度放进去。签字在前端只是"把服务端绑定的 action 原样回传"，前端不构造也不解释动作，
所以它不可能批准一个和服务端记录不同的东西。公司是显式上下文（当前公司存在 localStorage，多家时可切换），没有公司先 onboarding 建司。
邮箱验证与 Passkey 随 R6 的 API 一起回来；前端不为尚不存在的端点留壳。

**v2-ADR-25（2026-11-07）cutover 是删除，不是并存：旧应用整体归档到一个 commit，仓库里只剩 v2。**
strangler 的终点是把被缠绕的树砍掉。旧应用没有被"迁移"——它的 116 张表、3228 条测试和 66 个迁移整体停在 `232ceff`，
`extensions/README.md` 与 `docs/archive/README.md` 指向那里；要接回某个扩展（R7）只能对着事实日志与投影重写，不能复活旧表。
一个镜像两种进程：`api` 先 migrate 再 serve，`worker` 同镜像换 command，是唯一持有模型 key 的进程；runner 随镜像装入但零密钥。
依赖锁按"v2 真正 import 的包"手工裁剪并在干净 venv 里验证，而不是 `pip freeze` 把旧应用的残留一起锁进去。

**v2-ADR-26（2026-11-07）预算不是循环里的一个数，而是制度在账本上的三道闸：run 的零花钱、项目的分期审批、公司的并发与日限。**
run 级：每步按价目表估价累计，超过模板的 `run_default` 就停——这是机械上限，不是判断；币种不同就不比，因为换算属于 money 层。
项目级：没有"预算超了就停"的全局开关，而是把花费切成 `run_default` 大小的 tranche，过了 `warn_ratio` 之后每一份都要经 `spend.approve`，
金额分层决定谁来签：系统层机械记录，中层由 PM agent 看着项目进度签，上层由 Owner 在收件箱签；pending 就等，rejected 就停。
守卫只提问、不决定，它用注入的方式进 agent 层（agent 不 import money），所以预算规则改在制度和 money 里，循环和执行器不动。
公司级：并发与日限是配额不是判断，超了只是等待，不开决定。

**v2-ADR-27（2026-11-07）没人能靠不回答拖住公司：超时把问题挪给下一个人，不替任何人回答。**
升级是两件事分开做：`Escalator` 只搬问题（过了 `timeout_s` 就写一条 `escalated`，新 resolution 由模板的 `escalate_to` 决定，
升级后的 resolution 不再有 timeout，所以每个问题最多走到模板写明的终点），`EscalatedDecider` 只把问题交给制度点名的人（agent 角色当场问、
human 角色进收件箱），答案仍然走同一个信封校验。于是所有管理作业都得遵守一条纪律：**先看决定有没有结论，再看自己是不是该决定的人**——
以前 MissionIntake / Planner 看到决定者不是自己就返回，升级后永远没人施行结论。评审的 reviewer of record 是签字的人，不是跑评审的 QA agent。
取消同理：`cancel_requested` 是一条事实，循环在检查点看到就停，任务按失败处理，回来仍要一个决定；系统从不替人决定"再试一次"。

**v2-ADR-28（2026-11-07）指标是事实流的另一种读法：计数器从 head 起算，历史在事实里，不在内存里。**
业务指标（run、步数、模型调用、成本、决定、升级、闸门等待）不是在业务代码里埋点，而是一个消费者 `MetricsConsumer` 读事实流得出——
事实已经是审计日志，指标只是它的实时聚合；它声明 `start_at_head`，因为计数器描述的是这个进程活着的这段时间，重放历史既没意义也会把
启动变成一次假的流量高峰。进程指标（请求、批次、游标落后、作业时长）在 API 中间件和 worker 循环里直接记。`/health` 不只回 ok：
它回 DB 可达与否和每个消费者落后多少，因为"投影过期"是这套架构唯一会静默退化的地方。导出 bundle 加时间窗，窗口进封印，
取证时才能说清"这份证据覆盖哪段时间"。

**v2-ADR-29（2026-11-07）密钥按公司路由而不是按进程配置；秘密表遵守 ADR-22 的尺子：丢了只需重新配置。**
一家公司用谁的模型、花谁的钱，是这家公司的事，所以 provider 凭证按公司存、按 run 的 `company_id` 路由（`RoutedProvider` 只是 Provider 协议的
另一个实现，循环与执行器不知道有路由）；没有自己的凭证就用进程的全局配置。凭证 Fernet 加密，占位密钥直接拒绝写入——宁可报错也不能
把明文假装成加密。Passkey 只存公钥与计数，挑战单次使用且过期即删。登录限流留在进程内存：它保护的是口令比对，不是审计记录，
重启清零无所谓，正好是 ADR-22 判定"什么可以不进事实"的尺子。

**v2-ADR-30（2026-11-07）"备份可恢复"的定义是恢复后链、账、投影三者自证一致；保留期只删 transcript，而且只删没人再引用的。**
一份 dump 存在不等于能恢复。`restore-check` 把恢复变成一个可判定的命题：每条公司链从 GENESIS 验到头，每本账折叠平衡且与投影一致，
每个投影从事实重建后与 dump 里的行数相同——三者有一个不成立就不叫恢复成功。保留期遵守 ADR-8：事实永久，blob 是被引用的内容；
内容寻址意味着一个 blob 可能被多个 run 共享（同一个系统提示），所以只有当引用它的每个 run 都已过期、且没有产物指向它时才删，
删除本身也落一条事实。失去的只是对过期 run 的重放能力，账本和溯源一字不少。
