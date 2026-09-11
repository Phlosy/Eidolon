# T2 人才市场 · 工程执行基线（Implementation Plan）

> 领域语义见 [t2-talent-market-design.md](t2-talent-market-design.md)（本文引用其 §编号）。
> **本文件是 T2 的唯一执行基线**：阶段顺序、交付物、验收、门禁、commit 粒度、进度追踪。
> 新的 Agent 只需读本文件 + 设计文档，即可在不依赖聊天历史的情况下恢复 T2 上下文。

---

## 1. Current State（audit 事实，HEAD `68ce65a`）

### 1.1 平台基线

| 项 | 事实 |
| --- | --- |
| 分支 / 迁移 | `dev` @ `68ce65a`；alembic head `a3b5c7d9e1f4`（v28），29 个迁移脚本，`alembic check` 无漂移 |
| 数据 | SQLite（`apps/server/data/eidolon.db`）；`check_same_thread=False` + busy timeout 15s；**未启用 WAL** |
| 门禁 | 后端 683 passed / 6 deselected；前端 73 files / 303 passed；ruff / tsc / eslint / prettier / build / alembic check |

### 1.2 可直接复用的既有实现（含代码路径）

| 能力 | 代码路径 | T2 用法 |
| --- | --- | --- |
| Person 聚合根 | `app/models/person.py`，`app/repositories/persons.py`（`resolve_person_id` / `write_person_id` / `read_criterion` / `matches_owner`） | 身份与归属换算唯一入口 |
| 候选人实体 | `persons` + `employees.person_id`（部分唯一索引 `uq_employees_person_id`） | 候选人 = 无 employee 行的 Person |
| 角色档案 | `character_profiles`（`identity_id` unique、`owner_company_id` nullable、`lifecycle`） | 培养态 + 培养期持有方 |
| 培养履历 | `education_events`、`training_programs` | 履历=证据链 |
| 培养引擎 | `app/talent/cultivation/engine.py`（`advance_program` / `run_free_session`）、`templates.py` | T2.4 发行方复用 |
| 能力读面 | `app/services/competency.py`（`person_capabilities_out` / `assess_person_competencies`） | 市场画像 |
| 人格读面 | `app/services/traits.py`（`person_traits_out`） | 市场档案 |
| 证据模型 | `competency_evidence`（**无 company_id**，有 `environment` 快照）、`assessment_runs.company_id`（公司上下文快照） | provenance 不可改写 |
| 任职真相 | `employments` = `PositionAssignment`（`app/models/position.py`）+ 两个部分唯一索引 | 招募后如需挂职走既有服务 |
| 编制 | `position_definitions` / `position_slots` / `OccupancyStatus`（派生，ADR-2） | Fit 目标 |
| 任职服务 | `app/services/position_service.py`（`assign_position` / `release_position`，且会发 bus 事件） | T2.6 编排复用 |
| Fit 引擎 | `app/talent/fit/{engine,service,models,policy,serializer}.py`（**employee_id 入参**） | T2.5 person 化 |
| P9 职位候选 | `app/services/candidate_analysis.py`、`app/api/v1/position_candidates.py` | **不改**（术语冲突见设计 §2.2） |
| 知识 owner 口径 | `app/repositories/knowledge.py::list_knowledge_items`（private 走 `read_criterion`；共享 scope 经 Employee/Department 公司 join） | 验收 B |
| 事件总线 | `app/events/bus.py`（同步 `publish`；events 表 + WS；`actor_employee_id` → person 镜像由 bus 统一解析） | 领域事件 |
| 公司作用域 | `app/api/scope.py::resolve_company_id` + repo 层强制 | 普通 API 不动 |
| 履历审计 | `career_events`（`employee_id` FK、`CareerEventType.joined` 等） | 招募审计（D7） |
| 入职流程 | `app/services/lifecycle.py::onboard`（**总是新建 Person**，全链 runtime/provider/provisioning） | T2.6 不复用其建人路径 |

### 1.3 audit 结论：与原规划的差异（已在设计文档中裁定）

| # | 发现 | 裁定 |
| --- | --- | --- |
| 1 | `Candidate` 已被 P9 占用（Employee×Position） | 市场语境改用 `listing` / `listed talent`；API 用 `/market/*`（设计 §2.2） |
| 2 | `character_profiles.lifecycle` 预留了 `listed`/`hired` | **废弃这两个值**，市场/任职态各自表达（设计 §4） |
| 3 | `PositionAssignment` 的物理表就叫 `employments` | 复用，T2 不新建 Employment 表（设计 §12） |
| 4 | `competency_evidence` 无 company_id；公司快照只在 `assessment_runs` | provenance 口径按此描述（设计 §5） |
| 5 | `onboard` 只支持"新人"，无"既有 Person 入职" | T2.6 新建 `RecruitmentService`，`onboard` 不动 |
| 6 | Fit 引擎全部 `employee_id` 入参 | T2.5 增加 person 入口，保留 employee 入口（不迁调用方） |
| 7 | `companies` 无 kind 列，且被大量外键引用 | NPC 用独立 `market_participants` 表（D8） |
| 8 | `career_events` 已具备招募审计所需的 `joined` 语义 | 不建 `recruitment_events`（D7） |
| 9 | 私有知识 owner 口径已是 person 优先 | 验收 B 预期"已天然成立"，T2.6/T2.8 用真实检索验证 |
| 10 | 项目无 Protocol 先例（`app/runtimes/base.py` 用 ABC） | MarketAdapter 用 `typing.Protocol`（结构式，便于本地/远端替换），并在文档说明差异 |

## 2. 依赖与前置

```
T1 (✅) ─→ T2.0 ─→ T2.1 ─→ T2.2 ─→ T2.3 ─→ T2.4 ─→ T2.5 ─→ T2.6 ─→ T2.7 ─→ T2.8
                          └─ T2.5/T2.6 仅依赖 T2.1/T2.2/T2.3 的读面与挂牌语义
M1 依赖 T2；M2 依赖 M1（T2 不得反向依赖）
```

## 3. 工程原则（贯穿 T2）

1. **单一事实**：新增概念先指出唯一真相表；派生不落库（概念架构 §4 规则 1/ADR-12）。
2. **最小演进**：能用既有实体表达就不建新表；需要新表先写 schema plan 再迁移。
3. **兼容优先**：`新增正确抽象 → 迁移调用方 → 验证 → 最后清理兼容层`。T2 期内不动 R1 镜像列、不动 P9、不动 `/employees/*`。
4. **不建空壳**：不提前创建 M1 的经济实体；不提交没有实现者的架构装饰（Protocol 例外：T2.3 立即实现）。
5. **守卫化**：T2 的边界（无经济概念、能力分只有聚合器能写、市场不改写 provenance）尽量落成**测试**而不是口头约定。
6. **每个小阶段独立提交**：代码 + tests + docs（必要时 migration），门禁全绿后提交。

## 4. 阶段拆解

> 每阶段的"验收"均可执行；标注 `[migration]` 的才需要迁移。

### 4.1 T2.0 · Domain Contract Freeze（本轮）

- **目标**：冻结术语、状态模型、不变量、适配器契约、可见性 policy；不改行为。
- **交付**：
  - `docs/t2-talent-market-design.md`（领域设计）
  - `docs/t2-implementation-plan.md`（本文件）
  - 代码：`CultivationState` / `TalentOrigin` 枚举 + 既有 magic string 替换（`character_profiles.lifecycle`、`origin` 写入点）
  - 代码：`app/talent/market/`（`contracts.py`：`MarketState`、`MarketListingStatus`、`MarketParticipantKind`、`MarketListingView`、`MarketSearchQuery`；`adapter.py`：`MarketAdapter` Protocol）
  - 前端：`CharacterLifecycle` 收窄为 `cultivating`/`ready`，i18n 同步移除废弃值
  - 测试：`tests/test_market_contract.py`（枚举值冻结、Protocol 表面冻结、DTO 字段冻结、M1 词汇守卫）
- **验收**：全量 pytest / ruff / alembic check / 前端门禁全绿；**无迁移**；`character_profiles.lifecycle` 值域仍为 `cultivating`/`ready`。
- **禁止**：任何 market 表、market API、market UI、issuer、recruit 实现。

### 4.2 T2.1 · Person Read Model / Person API

- **目标**：Person 成为独立可读资源（不再必须经 Employee）。
- **交付**：`app/talent/person/`（`PersonReadService` + 投影 DTO）；API `GET /api/v1/persons/{person_id}`（自有 person，company scope）+
  `GET /api/v1/persons/{person_id}/timeline|competencies|traits|knowledge-summary`（**避免 chatty**：优先一个聚合端点 + 可选 include）；
  前端类型与 `PersonProfile` 组件骨架（复用 T1.3 的培养档案组件）。
- **验收**：读模型与既有员工读面**语义一致**（对拍：同 person 的 traits/capabilities 与 `/employees/{id}/traits|competencies` 等值）；
  未评估 = `null`；`/persons/*` 对无权限 person 404（不泄露存在性）。
- **依赖**：T2.0。**注意**：不复制聚合逻辑（复用 `person_traits_out` / `person_capabilities_out`）。

### 4.3 T2.2 · Cultivation Completion & Market Eligibility

- **目标**：自由养成可结业；eligibility 集中判定。
- **交付**：
  - `POST /api/v1/cultivation/characters/{profile_id}/complete`（自由养成显式结业 → `ready`；模板线已自动）
  - `app/talent/market/eligibility.py::MarketEligibility`（`can_list` / `can_recruit` 纯判定，输入三轴状态）
  - 事件 `cultivation.completed`
  - 前端：培养详情页"结业"入口 + 状态展示（延伸 T1.3 UI）
- **验收**：自由角色结业后 `ready` 且 `can_list=true`；`cultivating` 角色 `can_list=false`；已入职 person `can_list=false`；
  重复 complete 幂等（第二次 409/无副作用）；**不得**以能力阈值作为结业条件（测试断言无 score 参与判定）。
- **依赖**：T2.1（读面）。

### 4.4 T2.3 · Market Core & MarketAdapter `[migration v29]`

- **目标**：本地市场核心（挂牌/下架/查询）。
- **交付**：
  - 迁移 v29：`market_participants`、`market_listings`（含 `uq_market_listing_active_person` 部分唯一索引）
  - `LocalMarketAdapter`（实现 T2.0 Protocol）、`MarketService`（orchestration + eligibility + bus 事件 `market.listed`/`market.delisted`）
  - `MarketReadService`（公开投影，设计 §6.1 白名单）
  - API：`POST /market/listings`、`DELETE /market/listings/{id}`、`GET /market/listings`（搜索）、`GET /market/listings/{id}`
- **验收**：I6/I7/I9 成立；重复挂牌被唯一约束/幂等逻辑收敛（不 500）；下架后不可再被发现；
  跨公司可读**仅**公开投影（测试断言私有字段不出现：credential/memory/messages/drive）。
- **依赖**：T2.2。**禁止**引入 M1 概念。

### 4.5 T2.4 · Issuer & Market Supply

- **目标**：系统发行方投放成品角色（复用真实培养链）。
- **交付**：`app/talent/market/issuer.py`（`IssuerService`：建 Character → `advance_program` 跑满 → 评估 → 挂牌，participant kind=`system_issuer`）；
  品质档 `normal|fine|rare` 作为**参数**（模板选择/阶段强度/际遇权重/额外阶段），CLI/内部端点触发（不先做 UI）。
- **验收**：I11 + D11：发行角色能力分**全部**来自聚合器（守卫测试：issuer 代码不出现 score 赋值）；
  同 seed 确定性；三档产出分布可区分（统计断言）；`origin=issued` 成为合法来源。
- **依赖**：T2.3。

### 4.6 T2.5 · Person-scoped Fit Engine

- **目标**：Fit 直接作用于 Person（市场里的人不是 Employee）。
- **交付**：`calculate_person_fit(person_id, position_definition_id, company_id)`（复用 `talent/fit/engine.py` 核心；employee 入口**不动**，改为委托）；
  API：`GET /api/v1/persons/{person_id}/fit?position_definition_id=` + 市场侧复用（`GET /market/listings/{id}/fit?position_definition_id=`）。
- **验收**：同一人的 person 入口与 employee 入口结果一致（对拍测试）；`score` 与 `confidence` 保持分列；
  缺证据维度按既有 `NOT_EVALUABLE`/`NEEDS_EVIDENCE` 语义（不伪造 0）。
- **依赖**：T2.1。

### 4.7 T2.6 · Recruitment / Existing Person Onboarding

- **目标**：把既有 Person 变成某公司 Employee（T2 最关键事务）。
- **交付**：`app/services/recruitment.py::RecruitmentService.recruit_existing_person(...)`：
  `BEGIN → 校验 listing active + eligibility → 校验目标公司/编制 → 关闭 listing（条件更新）→ 创建 Employee(person_id=既有) →
  （可选）`position_service.assign_position` → `career_events(joined)` → 发 `person.recruited` → COMMIT`；
  API：`POST /api/v1/market/listings/{id}/recruit`。
- **验收**：I1–I5、I7、I8；重复招募/并发招募被拒（唯一约束 + 条件更新）；
  入职后真实检索能召回培养期私有知识（**走 retrieval pipeline**，非只查表）；
  `identity_id`/traits/evidence/assessment/knowledge 行数与内容不变（前后对拍）。
- **依赖**：T2.3（+ 可选 T2.5）。**不做** runtime/provider 全链开通（沿用既有员工 runtime 流程；招募只建人与任职）。

### 4.8 T2.7 · Market Experience & NPC Participants

- **目标**：市场 UI（Resume + Evidence Browser）+ NPC 对手方雏形。
- **交付**：
  - UI：`/market`（列表：候选卡片 = 履历摘要 + score/confidence + Fit 提示）、`/market/:listingId`（档案：时间线/能力/证据下钻/Fit/招募）；
    强制复用 `PersonProfile`/`ResumeTimeline`/`CompetencyEvidence`/`FitPanel`，中英 i18n；
  - NPC：`MarketParticipant(kind=npc_company)` + 简单策略（发现 → Fit → 选择 → 招募），产生玩家可感知的"已被其他公司招募"。
- **验收**：空/加载/错误/陈旧状态齐备；无"战力/SSR/总评分大字"；NPC 只做对手方（无财务/项目/经营）。
- **依赖**：T2.6。

### 4.9 T2.8 · E2E / Hardening / T2 Freeze

- **交付**：Golden Path E2E（见 §13，含真实检索断言）；并发/幂等测试补全；文档收口（T2 状态、handover 基线、R1 镜像列拆除评估）；
  「T2 稳定后」条件的技术债评估（`docs/person-core-migration.md` §7）。
- **验收**：Acceptance A–D（§14）；全量门禁；`tmp/` 不入库。

## 5. API 规划（draft，逐阶段实装）

| 方法 | 路径 | 阶段 | 作用域 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/cultivation/characters/{profile_id}/complete` | T2.2 | company（持有方） |
| `GET` | `/api/v1/persons/{person_id}` | T2.1 | company（自有 person） |
| `GET` | `/api/v1/persons/{person_id}/timeline` | T2.1 | 同上 |
| `GET` | `/api/v1/persons/{person_id}/competencies` | T2.1 | 同上 |
| `GET` | `/api/v1/persons/{person_id}/traits` | T2.1 | 同上 |
| `GET` | `/api/v1/persons/{person_id}/knowledge-summary` | T2.1 | 同上 |
| `GET` | `/api/v1/persons/{person_id}/fit` | T2.5 | company |
| `POST` | `/api/v1/market/listings` | T2.3 | company（挂自己持有的 person） |
| `DELETE` | `/api/v1/market/listings/{listing_id}` | T2.3 | company（挂牌方） |
| `GET` | `/api/v1/market/listings` | T2.3 | **market public**（仅 active listing 的公开投影） |
| `GET` | `/api/v1/market/listings/{listing_id}` | T2.3 | market public |
| `GET` | `/api/v1/market/listings/{listing_id}/fit` | T2.5 | market public |
| `POST` | `/api/v1/market/listings/{listing_id}/recruit` | T2.6 | company（招募方） |

约定：市场侧一律 `/market/*`（不与 P9 `/candidates` 冲突）；`/persons/*` 只服务自有 person（company scope），
市场读取**只**走 `/market/*`，避免"一个端点两种作用域"。

## 6. Schema 规划（T2.3 实装，T2.0 只冻结形状）

```sql
-- market_participants：市场参与者（玩家公司 / NPC / 系统发行方）
id, kind(player_company|npc_company|system_issuer), company_id NULL, display_name,
profile_json, active, created_at, updated_at

-- market_listings：可发现性（一次"在市"= 一行 active）
id, person_id, status(active|closed), quality_tier NULL,
listed_by_participant_id, listed_at, closed_at NULL, close_reason,
recruited_company_id NULL, recruited_employee_id NULL, metadata_json,
created_at, updated_at
-- 关键约束：同一 person 至多一条 active
CREATE UNIQUE INDEX uq_market_listing_active_person ON market_listings(person_id) WHERE status='active';
```

**不建**：`recruitment_events`（D7，复用 `career_events` + bus）、`TradeContract`/`Wallet`/`LedgerEntry`（M1）、
`market_orders`/`quotes`（D2：T2 无交易）。

**迁移纪律**：`up → down → up` 实测；`alembic check` 无漂移；不改已发布迁移；部分唯一索引必须写进模型（否则 autogen 要求删除）。

## 7. Event Plan

| 事件 | 阶段 | 载荷 | 同步消费方 | 后续价值 |
| --- | --- | --- | --- | --- |
| `cultivation.completed` | T2.2 | `person_id`, `profile_id`, `template`, `reason(template|free)` | — | 市场供给统计、UI 刷新 |
| `market.listed` | T2.3 | `listing_id`, `person_id`, `participant_id`, `quality_tier` | — | NPC 发现循环（T2.7） |
| `market.delisted` | T2.3 | `listing_id`, `person_id`, `reason` | — | 统计 |
| `person.recruited` | T2.6 | `person_id`, `employee_id`, `company_id`, `listing_id`, `position_slot_id` | 无（同步事务已落库） | knowledge scope refresh、NPC 反应、analytics |

**边界**：领域事务的**不变量**全部在同步事务内保证（不依赖消费者）；事件只承载"已经发生"的事实，
消费者必须幂等且可重放（概念架构 §4 规则 6）。不为"用了事件系统"而事件化所有代码。

## 8. Company Scope 与 Market Scope 的实现边界

```
普通读/写：resolve_company_id / repo 层 _scope_filter  →  公司隔离（不动）
市场读：  MarketReadService → 专用查询（按 active listing 的 person_id 取数，白名单投影）
市场写：  MarketService / RecruitmentService → 显式校验（挂牌方 / 招募方 / 目标公司）
```

- market 模块**禁止**调用公司作用域 `list_*` repo 去读别的公司数据；
- market 读路径的 SQL 必须**显式列出投影字段**（不用 `SELECT *` / ORM 全字段序列化）；
- 新增守卫（T2.3）：market 包不得 import `app/api/scope`（防止误用请求公司上下文充当市场边界）。

## 9. Concurrency / Idempotency

| 场景 | 手段 |
| --- | --- |
| 重复挂牌（同 person） | `uq_market_listing_active_person` 部分唯一索引 + ON CONFLICT 回查赢家（概念架构规则 7，`drive_repo.create_node` 先例） |
| 重复/并发招募 | 事务 + **条件更新**：`UPDATE market_listings SET status='closed' … WHERE id=? AND status='active'`，rowcount=0 ⇒ 已被招募（409）；`uq_employees_person_id` 兜底"一人一 employee" |
| 重复下架 | 幂等（返回 false/204，不报错） |
| 陈旧页面 | 客户端携带 `listing_id`；服务端以 DB 当前状态为准，返回 409 + 明确 detail |
| 双重点击 | 前端 pending 禁用 + 服务端幂等（同上） |

不引入分布式锁/消息队列；SQLite 写串行 + 15s busy timeout + 唯一约束已足够覆盖单机本地市场。

## 10. Test Strategy

| 层级 | 覆盖 |
| --- | --- |
| 契约/守卫（T2.0） | 枚举值冻结、Protocol 表面冻结（含"无 recruit"）、DTO 字段冻结、M1 词汇守卫 |
| domain 纯函数 | eligibility 判定矩阵、market state 派生（含非法组合）、person Fit 计算 |
| service | 挂牌/下架/招募事务（含失败回滚）、发行参数分布 |
| repository | 作用域（跨公司不可见私有字段）、部分唯一索引幂等收敛 |
| API | 状态码（404/409/422）、公开投影字段白名单、作用域（自有 person vs market） |
| E2E | Golden Path（§13），**第 26 步必须走真实检索** |
| 前端（T2.7） | list/detail/evidence 下钻/fit/recruit/stale/empty/loading/error/i18n 中英 |
| 回归网 | 既有 683 后端 + 303 前端全程保持全绿 |

**核心不变量必须写成测试**（I1–I12），不允许只靠人工验证。

## 11. Migration & Rollback Strategy

- **T2.0**：无迁移（纯契约 + 枚举重构）。
- **T2.3**：v29（两张新表 + 1 个部分唯一索引 + 1 个唯一索引）；`up/down/up` 实测；down 必须 DROP 两表与索引（无数据依赖）；
  模型侧同步声明索引（否则 `alembic check` 报漂移）。
- **回滚策略**：T2.3 之前的所有阶段不改变既有表 → 直接 revert 代码即可；T2.3 之后若需回滚，
  `alembic downgrade` 丢弃 market 两表（不影响 persons/employees/employments/evidence）。
- **兼容层**：T2 全程不删 R1 镜像列、不改 P9、不改 `/employees/*`。

## 12. Risks（audit 发现 + 规划风险）

| # | 风险 | 影响 | 应对 |
| --- | --- | --- | --- |
| R1 | **自由养成结业条件若被做成"阈值"** → 与 D1 冲突，市场变成筛分器 | 产品语义走偏 | D1 + T2.2 测试断言"无 score/confidence 参与判定" |
| R2 | 跨公司读取若复用公司作用域读面 → 泄露私有数据（credential/文档/私信） | 安全事故 | 设计 §6 白名单 + T2.3 投影测试（断言禁止字段不出现） |
| R3 | Fit 引擎 person 化时"另写一套" → 两套口径漂移 | 语义分叉 | T2.5 对拍测试（person vs employee 同值） |
| R4 | T2.6 若复用 `onboard` 会新建 Person（复制身份） | 破坏 I1/I2 | T2.6 使用 `RecruitmentService`，测试断言 `create_person` 未调用 |
| R5 | 培养期私有知识（`owner_employee_id IS NULL`）在入职前无 company 归属 | 验收 B 失败 | ✅ **T2.6 已修复并实测**：`knowledge.list_knowledge_items` 的公司过滤补 person 口径分支（`owner_person_id → 当前在职行`）；真实 retrieval pipeline 能召回培养期知识，且跨公司/同公司他人仍不可见（两条测试钉住） |
| R6 | 招聘/挂牌若直接从 UI 拼语义（前端拼 lifecycle 判断） | UI 成为真相 | eligibility 集中在 T2.2 policy；前端只读状态 |
| R7 | NPC 若用 `companies` 行承载 → 污染公司作用域读面 | 数据噪声 | D8：独立 `market_participants` 表 |
| R8 | 事件消费者不幂等/无启动兜底 | 状态漂移 | 同步事务负责不变量；消费者（T2.7 起）幂等 + sweep 模式（概念架构规则 6） |
| R9 | `AssessmentRun.company_id` 被"招募后刷新" | 历史被改写（I5） | 设计 §5 + T2.6 前后对拍测试 |
| R10 | 市场 search 的文本检索范围若扫全库 | 性能/越权 | T2.3 只按 active listing 的 person_id 集合检索；分页 limit ≤ 200 |
| R11 | 前端 T2.7 若绕过 PersonReadModel 自行聚合 | 三套逻辑 | 复用组件 + i18n 检查清单 |
| R12 | `origin`/rarity 被当作"战力"直出 | 违背愿景 §2.1 | T2.4 品质只影响参数；UI 仅展示履历（T2.7 验收） |

## 13. Golden Path（T2.8 必须真实执行）

```
1  创建自由培养 Character
2  运行多个 free session
3  产生真实履历 / knowledge / evidence
4  玩家 finalize cultivation
5  Person/Character READY
6  listing
7  Market 跨 company 可以发现
8  打开 listing 详情（候选档案）
9  查看 timeline
10 查看 competency score
11 查看 confidence
12 下钻 underlying evidence
13 选择 PositionDefinition
14 Person-scoped Fit
15 Recruit
16 listing 关闭
17 Employee 创建成功
18 Employee.person_id == 原 person.id
19 identity_id 完全不变
20 traits 不变
21 evidence 不变
22 assessment 不变
23 knowledge 不变
24 Employee 立即携带这些知识工作
25 使用公司知识检索真实查询一次
26 能召回该 Person 培养时期已经拥有的知识   ← 必须走 retrieval pipeline
```

## 14. Acceptance Criteria

| | 内容 | 验证方式 |
| --- | --- | --- |
| **A** | 市场履历可完整追溯：`competency → score + confidence → underlying evidence → EducationEvent/Assessment` | API 测试 + T2.7 证据下钻 E2E |
| **B** | 招募后 Employee **无需复制数据**即可使用培养期 Knowledge（走真实检索） | T2.6 测试 + Golden Path 第 24–26 步 |
| **C** | `issued` 与 `trained` 角色进入**完全相同**的 Evidence/Assessment/Knowledge/Fit/Recruitment 体系 | T2.4 测试：同模板同 seed 的产出结构一致（除参数分布） |
| **D** | T2 不引入任何真实经济依赖 | T2.0 词汇守卫 + 全阶段复查 |

## 15. Commit 粒度与门禁

```
T2.0  docs/architecture: freeze T2 talent market domain
T2.1  feat(person): Person read model + person APIs
T2.2  feat(cultivation): explicit completion + market eligibility
T2.3  feat(market): local market core (listings + adapter + read model)
T2.4  feat(market): issuer & market supply
T2.5  feat(fit): person-scoped fit engine
T2.6  feat(recruitment): recruit existing person (no asset copying)
T2.7a feat(web/market): market browse & listing detail
T2.7b feat(web/market): fit & recruit flows
T2.7c feat(market): NPC market participants
T2.8  test(t2): golden path e2e + docs + hardening
```

每阶段门禁（以 repository 实际脚本为准）：

```bash
cd apps/server && <conda eidolon python> -m pytest tests -q          # 期望 683+ passed
<conda eidolon python> -m ruff check app tests
<conda eidolon python> -m ruff format --check <改动文件>
cd apps/server && <conda eidolon python> -m alembic -c alembic.ini check
cd apps/web && npx tsc --noEmit && npx eslint src && npx prettier --check src
cd apps/web && npx vitest run                                        # 期望 303+ passed
cd apps/web && npm run build
```

提交前 `git status` / `git diff`；**不得**提交 `tmp/`、构建产物、数据库临时文件、日志。

## 16. T2 Progress

> 每完成一个阶段更新本表；新 Agent 从这里恢复上下文。

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| T2.0 Domain Contract Freeze | **DONE**（2026-09-10） | `9465947` | 设计 + 执行基线落盘；枚举/契约代码 + 守卫测试；**无迁移**；pytest 690 / web 303 |
| T2.1 Person Read Model / API | **DONE**（2026-09-10） | `f4165d4` | `app/talent/person/` + `/api/v1/persons/*`；对拍/404/null 语义全锁；**无迁移**；pytest 701 / web 312 |
| T2.2 Cultivation Completion & Eligibility | **DONE**（2026-09-10） | `6a79102` | 自由养成显式结业 + `cultivation.completed` + 三轴资格判定集中一处；附带修复 roster person-only 行缺陷（I13）；**无迁移**；pytest 712 / web 314 |
| T2.3 Market Core & MarketAdapter | **DONE**（2026-09-10） | `18e1bcb` | 迁移 v29（两张表 + 部分唯一索引）+ LocalMarketAdapter + MarketService + 公开投影读面；pytest 723 |
| T2.4 Issuer & Market Supply | **DONE**（2026-09-10） | `2c6236f` | 迁移 v30（training_programs.metadata_json）+ IssuerService（三档参数）+ CLI；`origin=issued` 走真实培养链；pytest 736 |
| T2.5 Person-scoped Fit | **DONE**（2026-09-10） | `524868c` | 一套引擎两个入口（owner 口径 person 优先，hash 相等）+ 市场 Fit 读面 + 搜索标注排序；无迁移；pytest 745 |
| T2.6 Recruitment | **DONE**（2026-09-10） | `748f0f1` | 招募事务（CAS + 同事务建人/任职）+ R5 知识读路径修复 + 验收 B 实测；无迁移；pytest 754 |
| T2.7 Market Experience & NPC | **NEXT** | — | 入口：plan §4.8（市场 UI + NPC 参与者） |
| T2.8 E2E / Hardening / Freeze | PLANNED | — | 本文件 §13/§14 |

### Progress Log

- **2026-09-10 · T2.0 DONE**：commit **`9465947`**（`docs/architecture: freeze T2 talent market domain (T2.0)`，19 files / +1165）。
  - 产出：`docs/t2-talent-market-design.md`、`docs/t2-implementation-plan.md`、`app/talent/market/contracts.py`、`tests/test_market_contract.py`；
    枚举 `CultivationState` / `TalentOrigin` 入 `app/models/enums.py`，培养域 magic string 替换为枚举（行为不变）。
  - 门禁：pytest **690 passed** / 6 deselected（+7 契约测试）；ruff check 全绿、改动文件 format 干净（5 个既有 WIP 红不变）；
    alembic check 无漂移；web 303 passed / tsc / eslint / prettier / build 全绿（仅类型收窄与 i18n 清理）。
  - 迁移：**无**（T2.0 不需要 schema 变化）；alembic head 仍为 `a3b5c7d9e1f4`（v28）。
  - 守卫已做“反例注入”验证：向 `app/` 注入 `lifecycle = "listed"` 与向市场模块注入 `price` 均能使对应守卫转红，
    撤回后全绿（守卫不是声明式装饰）。
  - 风险：R5（培养期知识检索可见性）仍为 T2.6/T2.8 需实测的最大不确定点；R2（市场投影越权）在 T2.3 用白名单测试兜住。

- **2026-09-10 · T2.1 DONE**：commit **`f4165d4`**（`feat(person): T2.1 Person read model + person APIs`，28 files / +1511）。
  - 交付：`app/talent/person/{__init__,access,read_model}.py`、`app/api/v1/persons.py`、`app/schemas/person.py`；
    新增 person 读出口复用（`competency_service.evidence_payload`/`person_evidence_rows`、
    `knowledge_summary_by_person`、`list_education_events(newest_first/limit/offset)`）；
    员工证据端点改为共用同一份 payload 构造（响应逐字段不变，既有测试保护）。
  - 前端：`api/persons.ts` + `hooks/usePersons.ts` + `components/person/{traits-list,competency-list,person-profile}.tsx`；
    T1.3 `character-profile` 改为复用共享列表组件，人格词表统一到 `person:traits.*`（`cultivation:traits.*` 已移除）。
  - 测试：后端 +11（`tests/test_person_read_model.py`，含两条读面只读守卫，已做反例注入验证）；
    前端 +9（person-profile 5 + persons API 4）。
  - 门禁：pytest **701 passed** / 6 deselected；ruff check 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head 仍 `a3b5c7d9e1f4` / v28）；web 312 passed + tsc/eslint/prettier/build 全绿。
  - 迁移：**无**（T2.1 不落新表；知识摘要与证据均为读）。
  - 风险：`/persons/*` 当前以 person 持有所属公司为主口径；招募后（T2.6）原持有方与新雇主都可读 ——
    这是设计 §5/§6 的有意行为，但需在 T2.6 测试中用对拍固定下来。

- **2026-09-10 · T2.2 DONE**：commit **`6a79102`**（`feat(cultivation): T2.2 explicit completion + market eligibility`，17 files / +729）。
  - 交付：`app/talent/market/eligibility.py`（三轴读面 + `can_list`/`can_recruit` 纯矩阵与 DB 包装，唯一判定处）、
    `EmploymentState` 入 `app/models/enums.py`、
    `services/cultivation.complete_cultivation` + `POST /cultivation/characters/{id}/complete`（幂等；模板进行中 409）、
    事件 `cultivation.completed`（payload: person/profile/identity/template/reason + company 快照）；
    前端 `components/cultivation/complete-cultivation.tsx` + 详情页入口 + i18n 中英。
  - **发现并修复跨阶段缺陷**：`services/talent_roster.py` 的批量属主解析假设“行都双写两列”，
    遇到 **person-only 行（`employee_id IS NULL`）+ 同 person 的 employee 行** 时 `derived[None]` → KeyError，
    `/talent-roster` 直接 500 —— 这正是 T2.6 招募后必然出现的形态。修复：新增 `_row_owner_id()`
    按 person 口径还原员工（对不上再回落镜像列），runtimes/bindings/brains/competency 四处统一使用；
    回归测试进 `tests/test_roster_api.py`。已登记为不变量 **I13**（设计 §10）。
  - 测试：后端 +11（`tests/test_market_eligibility.py` 10：纯矩阵 / DB 三轴 / 零证据结业 / 幂等 / 模板 409 /
    跨公司 404 / 事件 / D1 无阈值守卫；roster 回归 1）；前端 +2（结业入口出现与消失 + 点击传参）。
  - 门禁：pytest **712 passed** / 6 deselected；ruff check 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head 仍 `a3b5c7d9e1f4` / v28）；web 314 passed + tsc/eslint/prettier/build 全绿。
  - 迁移：**无**（三轴均为派生或既有列）。
  - 实机：空白角色 complete 200/ready → 重复 complete 200（幂等）→ `/persons/{id}` ready；
    模板角色 complete → 409；dev 库三轴抽查（person 3/5 `ready+unemployed+unlisted ⇒ can_list=ok`、
    person 4 `cultivating ⇒ not_ready`）与 `cultivation.completed`（reason=free）均正确；`/talent-roster` 200。

- **2026-09-10 · T2.3 DONE**：commit **`18e1bcb`**（`feat(market): local market core — listings + adapter + public read model (T2.3)`，19 files / +1602）。
  - 迁移 **v29**（`b4c6d8e0f2a3`，down_revision `a3b5c7d9e1f4`）：`market_participants`
    （部分唯一 `uq_market_participant_company(kind, company_id) WHERE company_id IS NOT NULL`）+
    `market_listings`（部分唯一 `uq_market_listing_active_person(person_id) WHERE status='active'`）；
    up/down/up 实测；dev 库已 upgrade 到 v29，`alembic check` 无漂移。**无经济列**（M1 边界）。
  - 后端：`models/market.py`、`repositories/market.py`（幂等创建 ON CONFLICT + 回查；关闭走
    条件 UPDATE rowcount）、`talent/market/local_adapter.py`（实现 T2.0 Protocol）、
    `services/market.py`（挂牌/下架编排 + `close_listing_for_recruitment` 供 T2.6 同事务复用）、
    `talent/market/read_model.py`（公开投影白名单 + outcome/evidence 显式键选择）、
    `api/v1/market.py`（POST/DELETE/GET/GET detail）、`schemas/market.py`；
    枚举 `MarketListingStatus`/`MarketParticipantKind` 归位 `app/models/enums.py`（contracts re-export）；
    `eligibility.market_state` 接入 active listing（T2.2 预留的单点）。
  - 测试：后端 +11（`tests/test_market_core.py`）：v29 up/down/up + 两个部分唯一索引；
    挂牌/下架往返（I6/I7）；重复挂牌幂等（201→200、单行、不重发事件）；资格门禁 409
    （not_ready / employed）；跨公司挂牌 404；跨公司下架 404；跨公司**公开投影**可读且
    禁止键（person_id/owner_company_id/credential/memory/messages/drive/content/inputs_hash）
    不出现；outcome 去 session_ids、证据保留 source_ref；知识摘要无正文；closed/unknown → 404；
    市场 API 经济词汇守卫（D10）。
  - 门禁：pytest **723 passed** / 6 deselected；ruff check 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head `b4c6d8e0f2a3` / v29）；web 314 passed + tsc/eslint/prettier/build 全绿
    （本阶段未改前端）。
  - 实机：建角色 → 自由学习 → 结业 → 挂牌 201 → 重复挂牌 200（同 listing）→ 搜索命中
    （listed_by=TestCo）→ 详情 200（traits 8 / general 10 / timeline 1 / evidence 1 / market_state=listed，
    无 person_id、owner_company_id、session_ids 泄露）→ 下架 204（重复 204）→ 详情 404、
    搜索 0 → 重新挂牌 201；events = listed/delisted/listed 各一次；participants 单行（幂等）。

- **2026-09-10 · T2.4 DONE**：commit **`2c6236f`**（`feat(market): issuer & market supply — 三档参数 + 真实培养链 (T2.4)`，12 files / +781）。
  - 迁移 **v30**（`c5d7e9f1b3a6` ← `b4c6d8e0f2a3`）：`training_programs.metadata_json`
    （JSON NOT NULL + server_default `'{}'`；刻意不塞 `resource_used`、不建参数表）；
    up/down/up 实测；dev 库已 upgrade；`alembic check` 无漂移。
  - 后端：`talent/market/issuer.py`（`IssuerService` + 三档 `TIERS` + `CultivationParams`），
    `engine.advance_program(assessment_company_id=...)`（发行上下文覆盖，玩家路径不变）、
    采样应用参数（signal_bonus / fortune_weight / intensity_bonus，默认值与 T1 逐值一致）、
    `market_service.list_for_participant`（供给予路径共用事件）、`create_program(metadata_json=)`、
    `count_profiles_by_origin`；CLI `scripts/issue_talent.py` + `make market-issue`。
  - 决策：**D13**（发行方上下文：owner NULL + 部署默认公司作评估历史快照）；
    `origin=issued` 由发行方产出，**玩家端点仍拒绝**。
  - 测试：后端 +13（`tests/test_market_issuer.py`）：真实培养链（履历/证据/评估 run/能力行有证据背书）、
    在市场挂牌（system_issuer）+ 公开投影可读 + `/persons/*` 404、只产出不挂牌、
    同 seed 确定性、三档分布单调（证据条数与平均 signal；固定模板隔离参数）、
    rare 双模板、未知档位拒绝、玩家端点拒 issued、D11 两条守卫（AST 无 score/confidence、
    不构造 EmployeeCompetency/AssessmentRun）、档位契约冻结、owner NULL。
  - 门禁：pytest **736 passed** / 6 deselected；ruff check 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head `c5d7e9f1b3a6` / v30）；web 314 passed + tsc/eslint/prettier 全绿（未改前端）。
  - 实机：`make market-issue ISSUE_ARGS="--tier rare --count 2 --seed live-demo"` → 两名 issued 角色
    （CH-TBW2BR8T5AC8 / CH-3MTW07X6AQ6H），各 22–23 条教育证据、mean_signal 74.3/78.6、
    listings 3/4；DB 校验：programs 双模板 completed + metadata.issuer.tier=rare、
    assessment company=1（快照）、能力行得分有证据背书（77@18 / 99@4）、无 employee 行；
    市场 API：detail 200（traits 8 / general 10 / timeline 7 / evidence 20 / market_state=listed）、
    `origin=issued` 与 `quality_tier=rare` 过滤各命中 2、无内部 id 泄露。

- **2026-09-10 · T2.5 DONE**：commit **`524868c`**（`feat(fit): person-scoped fit engine + market fit read (T2.5)`，15 files / +934）。
  - 后端：`talent/fit/engine.py` 抽出 `_calculate(owner=FitOwner)` 共享核心 +
    `calculate_for_person`；`FitOwner.hash_payload`（person 优先）→ 同一人两条路径 **hash 相等**；
    `PositionFitResult` 增加 `person_id`、owner 字段可空；`hashing.inputs_hash` 改 owner 口径；
    `service.calculate_person_fit`（职位须属本公司或全局模板，否则 404）；
    API `GET /persons/{id}/fit`（自有 person）与 `GET /market/listings/{id}/fit`（公开投影）；
    市场搜索 `position_definition_id` 落地：附 Fit 摘要 + 排序（**不筛人**）；
    `MarketSearchQuery.limit=None` = 不分页；`MarketListingItemOut`（列表项带 fit，
    POST 挂牌响应形状不变 —— T2.3 契约冻结）。
  - 测试：后端 +9（`tests/test_person_fit.py`）：person/employee 对拍（含 inputs_hash）、
    person-only 候选可算、缺证据 UNRATED/INSUFFICIENT_DATA（不伪造 0）、persons Fit 公司边界、
    市场 Fit 公开投影（无 inputs_hash/owner id/内部 id）、closed/unknown/别家公司职位 404、
    搜索标注+排序且未评估仍在列、Fit 只读。
  - 门禁：pytest **745 passed** / 6 deselected；ruff 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head 仍 `c5d7e9f1b3a6` / v30，**本阶段无迁移**）；
    web 314 passed + tsc/eslint/prettier 全绿（未改前端）。
  - 实机：市场搜索带 engineer 职位 → 3 名在市候选人全部附 Fit 摘要并按 score 降序
    （0.976 → 0.944 → 未评估最后）；市场 Fit 详情 200（12 条逐项评估、无 inputs_hash/owner id）；
    **对拍**：`/persons/1/fit` 与 `/employees/1/position-fit/4` 八项字段全等、hash 相等
    （owner 分别为 person-only 与 person+employee）；未知 listing/职位 → 404。

- **2026-09-10 · T2.6 DONE**：commit **`748f0f1`**（`feat(recruitment): recruit existing person without copying assets (T2.6)`，10 files / +922）。
  - 后端：`services/recruitment.py`（RecruitmentService：CAS 关闭 listing → 建 Employee(person_id=既有)
    → 回填 recruited_* → 可选 `assign_position(commit=False)` → career_events(joined) + audit →
    单次 COMMIT → 提交后发 `person.recruited` 与 `employee.position_assigned`）；
    `position_service.assign_position` 增加 `commit: bool = True`（不改默认行为）；
    API `POST /market/listings/{id}/recruit`（未知 404 / 已关闭与重复 409 / 别家公司部门·编制 404）；
    架构守卫登记：`employees.role` 镜像写入点加入 `services/recruitment.py`（与 lifecycle/seed 同口径）。
  - **R5 修复（验收 B 的关键）**：`repositories/knowledge.list_knowledge_items` 的公司过滤补 person 口径
    （`owner_person_id → 当前在职行`）—— 原先人级行会被 `owner_employee_id` join 整行过滤掉。
  - 测试：后端 +9（`tests/test_recruitment.py` 8 条：golden path（I1–I5 快照对拍）/验收 B 真实检索/
    发行角色招募后可读/重复招募 409 且只一人一 employee/未知 404/带编制同事务任职/跨公司编制与部门 404 且回滚/
    D12 守卫；`test_knowledge_retrieval_scopes.py` +1：人级知识只对当前在职公司可见、scope 未放宽）。
  - 门禁：pytest **754 passed** / 6 deselected；ruff 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移（head 仍 `c5d7e9f1b3a6` / v30，**本阶段无迁移**）；
    web 314 passed + tsc/eslint/prettier 全绿（未改前端）。
  - 实机：真实链路 建角色→自由学习（主题「T2.6 招募实机检索标记」）→结业→挂牌→招募 200
    （person 10 → employee 3，identity_id 不变）；重复招募 409 `listing_not_active`；
    DB：employee.person_id=10 / lifecycle=active / runtime=mock；知识行仍 `owner_person_id=10,
    owner_employee_id=NULL`（无复制）；**真实 retrieval 召回该主题**；同公司他人不可见；
    listing closed/reason=recruited/recruited_employee_id=3；career joined=1。
