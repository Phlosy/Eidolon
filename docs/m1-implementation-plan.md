# M1 经济与合同系统 · 工程执行基线（Implementation Plan）

> 领域语义见 [m1-economy-design.md](m1-economy-design.md)（本文引用其 §编号与 E 编号不变量）。
> **本文件是 M1 的唯一执行基线**：阶段顺序、交付物、验收、迁移路线、门禁、commit 粒度、进度追踪。
> 新 Agent 只读本文件 + 设计文档即可恢复 M1 上下文，不依赖聊天历史。

---

## 1. Current State（audit 事实，HEAD `a9f6bae` / 迁移 head `d6e8f0a2b4c7` v31）

### 1.1 平台基线

| 项 | 事实 |
| --- | --- |
| 分支 / 数据 | `dev`；SQLite `apps/server/data/eidolon.db`（busy timeout 15s，未开 WAL） |
| 门禁 | 后端 775 passed / 6 deselected；前端 78 files / 328 passed；ruff / tsc / eslint / prettier / build / alembic check |
| 经济现状 | **全库无任何经济字段**（无 balance/wallet/ledger/price/fee/budget/salary/payment/amount）—— 干净起点，无历史包袱 |
| 配置 | `app/core/config.py`：pydantic-settings（`EIDOLON_` 前缀），按版本分段注释 |
| 事件 | `app/events/bus.py`（同步 publish + events 表 + WS）、`app/events/engine.py`（`EventEngine.register` 消费者注册表） |
| 权限 | `protected` router + `require_user`；公司作用域 `resolve_company_id` + **repo 层强制**（T2 教训） |

### 1.2 可复用的既有实体（经济 actor 与业务锚点）

| 能力 | 代码路径 | M1 用法 |
| --- | --- | --- |
| 个人 | `users`（`app/models/auth.py`） | `user` 经济 actor |
| 公司 | `companies` / `CompanyMembership`（`app/models/organization.py`） | `company` 经济 actor + 权限归属 |
| NPC / 系统发行方 | `market_participants(kind=npc_company/system_issuer)`（T2 D8：**不进 companies**） | `npc_company` actor + `system` 发行来源 |
| 人才市场 | `market_listings` / `MarketService` / `RecruitmentService`（T2，FROZEN） | M1.7 人才商业化的**唯一**招募入口 |
| 工作执行 | `projects` / `tasks` / `artifacts` / `employees` / runtime | WorkOrder 执行与交付物引用（不重造项目系统） |
| 教程进度 | `user_tutorial_progress` | TUTORIAL_COMPLETION 奖励的 eligibility 来源 |
| 评估体系 | `assessment` / `competency` / `evidence` | **不复制**；Evaluation（工作验收）与能力评估分离 |
| 幂等先例 | `drive_repo.create_node`（ON CONFLICT + 回查）、T2 listing CAS | 账本过账 / Escrow / Settlement 沿用同一纪律 |

## 2. 依赖与阶段顺序

```
T2 (FROZEN) ──┐
              ├─→ M1.0 ─→ M1.1 ─→ M1.2 ─→ M1.3 ─→ M1.4 ─→ M1.5 ─→ M1.6 ─→ M1.7 ─→ M1.8 ─→ M1.9 ─→ M1.10
T1/K1/R1  ───┘                     （M1.5/M1.7 依赖 M1.1–M1.3；M1.8/M1.9 依赖 M1.1–M1.7）
```

M1.0 只冻结契约，**不建表、不动任何既有实体**（与 T2.0 同纪律）。

## 3. 全局工程原则

1. **Domain first / Ledger first / Transaction first / UI later**（设计 §整体原则）；
2. **账本即事实**：先 Ledger → Account → Transaction → 业务 → Projection → UI；
3. **单一事实**：余额是派生；Escrow/reserved 是派生；观测指标全部可重算；
4. **最小演进**：新概念先指出唯一真相表；不建空壳经济表；不提前实现未来能力（设计 §40）；
5. **T2 不变量不动**：身份/知识/证据/历史 provenance 永不进入交易复制范围（E18/E19/E20）；
6. **三层 API 边界**：玩家/公司、内部领域、系统/管理（设计 §32）；mint/burn 不出玩家 router（E23）；
7. **每个阶段独立提交**：代码 + tests + migration（如需）+ docs，门禁全绿。

## 4. 阶段拆解

> 标注 `[migration vNN]` 的阶段创建/变更表；未标注即无迁移。

### M1.0 · Economic Domain Contract Freeze（本轮）

- **Goal**：冻结经济领域语义、状态机、不变量、服务边界、schema/API 路线。
- **Scope**：`docs/m1-economy-design.md` + `docs/m1-implementation-plan.md`；`app/economy/`
  契约代码（枚举/金额/账户引用/过账腿 + 守恒校验/政策 DTO + 状态机迁移表）；政策配置项
  （`Settings` + `.env.example`）；契约与不变量测试。
- **Non-goals**：任何表、任何 API、任何真实资金流动、任何 UI。
- **Schema**：无（M1.1 起建表，见 §5）。
- **Services / APIs / Events**：只冻结边界与名单，不实现。
- **Tests**：枚举值冻结、状态机迁移合法/非法、复式守恒（property-style）、金额整数与货币显式、
  政策版本与配置化、E1 AST 守卫（无 balance 列/无直接余额写入）、E23 守卫（玩家 API 无 mint/burn）、
  T2 边界守卫（talent/market 不 import 经济写入口）。
- **Migration**：无。
- **Acceptance**：全量 pytest/ruff/format/alembic + 前端门禁全绿；设计/计划文档落盘并含 Progress。
- **Risks**：契约过度设计（→ 只冻结"会被 M1.1–M1.7 真正用到"的部分）；政策默认值与 T2 现有
  经济无关（无冲突）。

### M1.1 · Accounts & Double-entry Ledger `[migration v32]`

- **Goal**：账本可过账、可查余额、可追溯。
- **Scope**：`economic_actors`（或直接以 `(kind, ref)` 表达、无独立表）→ 决定；
  `ledger_accounts`、`ledger_transactions`、`ledger_entries`、`wallet_projection`（缓存）；
  `LedgerService`（mint/transfer/burn/escrow 腿的组合原语）、`AccountService`（开户/冻结/查询）、
  余额查询与流水 API。
- **Schema**：4 张表（accounts / transactions / entries / wallet_projection）+
  唯一约束（`unique(actor_kind, actor_ref, currency, kind)`、`ledger_transactions.idempotency_key`）。
- **Services**：`LedgerService` / `AccountService`（其余服务在后续阶段）。
- **APIs**：`GET /economy/accounts`（本公司）、`GET /economy/transactions`（分页流水）、
  `GET /economy/balance`。**无**写端点（写只发生在业务服务内）。
- **Events**：`LedgerTransactionPosted`。
- **Tests**：守恒 property 测试（随机腿集合 → 只接受平衡交易）；余额=派生重算一致；
  幂等键重复过账不产生第二条；`available = balance − reserved`；行锁/CAS 竞争（并发扣款只有一次成功）；
  E1 守卫（全库模型无 `balance` 列、AST 无余额赋值）。
- **Acceptance**：mint/transfer/burn 三种腿组合的余额与守恒正确；缓存可从账本重建（重建脚本/测试）。
- **Risks**：投影与账本漂移（→ 重建校验 + 守卫测试）。

### M1.2 · Monetary Authority & Reward System `[migration v33]`

- **Goal**：唯一发行主体 + 统一奖励入口 + 首批 6 类奖励真实跑通。
- **Scope**：`MonetaryAuthority`（mint/burn/treasury）、`system_accounts` 初始化（ISSUANCE/TREASURY/BURN）、
  `reward_policies`（版本化）/`reward_grants`、`RewardService.ensure_eligible/claim`；
  首批：STARTER_GRANT、PROFILE_COMPLETION、COMPANY_PROFILE_COMPLETION、TUTORIAL_COMPLETION、
  DAILY_LOGIN、ACHIEVEMENT。
- **Schema**：`reward_grants`（含 `policy_version`、`reference_key`、`ledger_transaction_id`）+
  系统账户引导（seed/启动期幂等建）；`EconomicPolicy` 值来自 `Settings`。
- **Services**：`MonetaryAuthority` / `RewardService`（`EconomicPolicy` 由配置解析）。
- **APIs**：`GET /economy/rewards`（可用/已领）、`POST /economy/rewards/{type}/claim`（幂等）。
- **Events**：`RewardGranted`。
- **Tests**：启动资金只发一次（并发/重复请求幂等）；签到冷却与上限；政策版本落库；
  奖励过账可追溯（reference → ledger）；`total_supply` 统计 = Σissuance−Σburn。
- **Acceptance**：新公司自动/手动领取 starter grant 后余额=政策值；重复领取返回既有 grant 且 supply 不变。
- **Risks**：奖励滥用（→ 幂等约束 + 冷却 + 上限参数）。

### M1.3 · Official Work Market `[migration v34]`

- **Goal**：玩家通过"创造价值"赚新钱（主要发行渠道）。
- **Scope**：`work_orders` / `work_order_submissions` / `evaluations`；官方 Bounty 先跑通
  （OPEN → ACCEPTED → SUBMITTED → APPROVED → SETTLED），执行复用 `projects/tasks/artifacts`。
- **Schema**：3 张表（orders/submissions/evaluations）+ 状态与截止时间索引。
- **Services**：`WorkOrderService` / `EvaluationService` / `SettlementService`（首个使用者）。
- **APIs**：`GET /work-orders`（公开市场）、`POST /work-orders/{id}/accept`、
  `POST /work-orders/{id}/submit`、`POST /work-orders/{id}/evaluate`（系统/管理面）。
- **Events**：`WorkOrderAccepted` / `SubmissionApproved` / `SettlementCompleted`。
- **Tests**：完整 bounty 生命周期；结算 mint 到承接公司；重复结算幂等；拒绝/重提；
  **E8**（官方 mint / 玩家不 mint 的反面校验）；奖励金额 = base + bonus。
- **Acceptance**：一个真实官方 bounty 从发布到结算走通，公司余额增加且 `total_minted` 增加。
- **Risks**：验收主观性（→ evaluation_mode=auto 可跑；manual 只给内部/CLI）。

### M1.4 · Player Work Market `[migration v35]`

- **Goal**：玩家之间用既有货币交易（**不 mint**）。
- **Scope**：`player_escrow` 资金流 + WorkOrder 的 `player_bounty/player_contract`；
  发布前 fully funded（E11）；refund/expire。
- **Schema**：`escrows`（本阶段先服务 WorkOrder）。
- **Services**：`EscrowService`（fund/release/refund/expire）。
- **APIs**：`POST /work-orders`（玩家发布，需资金）、`POST /work-orders/{id}/cancel`。
- **Events**：`EscrowFunded` / `EscrowReleased` / `EscrowRefunded`。
- **Tests**：余额不足禁止发布；Escrow→Contractor 后 supply 不变（E6/E7/E8）；
  取消退款；release 与 refund 竞争只成功一个；**E24** 余额不可为负。
- **Acceptance**：A 发布 → B 完成 → A 资金到 B，双方余额变化与 world supply 恒定。
- **Risks**：Escrow 状态机与 WorkOrder 状态机耦合（→ 状态迁移表集中一处）。

### M1.5 · Company Operating Economy `[migration v36]`

- **Goal**：公司有真实成本（首批 3 项：培养 / 算力 / 市场与合同手续费）。
- **Scope**：`economic_categories`（枚举）+ 费用过账接入点；`compute_usage` 计量；
  手续费拆分（treasury/burn ratio）。
- **Schema**：`compute_usage`（计量表）+ 可选 `expense_entries`（若账本不足以直接表达分类，用账本 `reason/reference` 表达，不建同义表）。
- **Services**：`ComputeCostService` / `FeeService`（或并入 Settlement）。
- **Tests**：培养/算力扣款幂等；费用拆分比例正确（treasury vs burn）；公司余额不足时的语义（拒绝/欠费策略）。
- **Acceptance**：跑一次 Agent 任务产生可解释的 compute 成本；市场手续费按比例进入 treasury/burn。
- **Risks**：成本过高打击玩法（→ 政策参数 + 默认值偏低）。

### M1.6 · Contract / Offer / Escrow Core `[migration v37]`

- **Goal**：通用合同体系（工作/人才/服务/采购/科研共享）。
- **Scope**：`contracts` / `offers`；ContractService 状态机（设计 §37/§38）；
  SettlementService 扩展为通用终局（多腿：分账/手续费）。
- **Schema**：`contracts`（含 consideration 一等列）、`offers`。
- **APIs**：`GET /contracts`（本公司参与方）、`POST /contracts`、`POST /contracts/{id}/accept|fulfill|cancel`。
- **Tests**：合同全生命周期（含 fund/cancel/expire/settle/refund）；**E12/E13**（幂等、原子）；
  双结算、余额不足、Escrow 不足、状态非法。
- **Acceptance**：一个 work contract 与一个 service contract 各自走完并结算。
- **Risks**：状态爆炸（→ 状态机冻结在 M1.0，实现只允许表中迁移）。

### M1.7 · Talent Commercialization `[migration v38]`

- **Goal**：T2 人才市场接入经济（价格 + Escrow + 结算 + 招募）。
- **Scope**：`talent_commercial_terms`（listing 1:1 扩展）+ Offer（买方出价）+ Talent Contract +
  Escrow + 结算后调用 **T2 `RecruitmentService`**。
- **Schema**：`talent_commercial_terms`（price/currency/negotiable/sale_mode）。
- **Services**：`TalentTradeService`（编排：terms → offer → contract → escrow → settle → recruit）。
- **APIs**：`POST /market/listings/{id}/commercial-terms`（卖方）、`POST /market/listings/{id}/offers`（买方）、
  `POST /offers/{id}/accept`。
- **Events**：`TalentPurchased`。
- **Tests**（**T2 回归必跑**）：交易前后 `person_id`/`identity_id` 不变；knowledge/traits/evidence/
  assessment/education 零改写（快照对拍）；招募失败 → 整笔回滚（钱不动）；listing 关闭 + 新雇主
  可读（T2 语义）；**E18/E19/E20**。
- **Acceptance**：B 公司用收入买走 A 的人才：A 收款、B 扣款、Employee 创建、T2 不变量成立。
- **Risks**：与 T2 CAS 交互（→ 沿用 listing CAS + settlement 幂等键）。

### M1.8 · NPC Economy `[migration v39]`（可选调整）

- **Goal**：NPC 有预算并参与买卖（deterministic）。
- **Scope**：NPC actor 开户 + `npc_budget` 注入（计入 mint）；NPC 出手规则
  （`budget >= price and fit >= threshold and price <= max_price`）；NPC 收入记录。
- **Schema**：`npc_economic_profiles`（预算/偏好；或复用 `market_participants.profile_json`+政策表，M1.8 再裁定）。
- **Tests**：预算约束（不会买超）；NPC 不能 mint（只有系统预算注入可）；NPC 购买后人才离场（T2 `consumed`）。
- **Acceptance**：一轮 NPC 活动在预算内成交至少一单，账本可解释。
- **Risks**：NPC 与玩家争抢导致体验问题（→ 预算上限 + 节奏参数）。

### M1.9 · Economy UI & Analytics（无迁移）

- **Goal**：玩家看得懂钱（余额/流水/收入/支出/奖励/任务/合同），管理员看得懂经济（supply/mint/burn/treasury/volume）。
- **Scope**：`/economy`（钱包、流水、奖励）、`/work-orders`（市场 + 我的订单）、`/contracts`；
  admin/CLI 观测（`GET /economy/stats` 内部面或 `make economy-stats`）。
- **Tests**：前端组件测试（余额/流水/奖励领取/订单流转）、i18n 中英、admin 统计口径测试。
- **Acceptance**：玩家能回答"我这笔钱从哪来、花到哪去"；管理员能看到 mint/burn/supply。
- **Risks**：UI 成为业务真相（→ 前端只读、写全走 service）。

### M1.10 · Golden Path / Hardening / Freeze

- **Goal**：完整经济闭环 E2E + 失败注入 + 冻结。
- **Scope**：设计 §（prompt §四十三 M1.10 的 26 步）E2E；失败注入（余额不足/重复领取/重复结算/
  合同取消或过期/listing 被抢/escrow 不足/招募失败/中途异常）；并发（同余额并发消费、同合同并发结算）；
  文档收口 + M1 FROZEN。
- **Tests**：Golden Path 一条测试全链；失败注入矩阵；property/invariant 回归（E1–E25 全覆盖点名）。
- **Acceptance**：E2E 全绿；T2 Golden Path 回归全绿；E1–E25 每条都有测试锚点；门禁全绿。
- **Risks**：收尾阶段发现设计缺口（→ 记入 design 的偏差小节，不静默改语义）。

## 5. Schema Roadmap（迁移路线；M1.0 不建表）

| 迁移 | 阶段 | 表 |
| --- | --- | --- |
| v32 | M1.1 | `ledger_accounts`、`ledger_transactions`、`ledger_entries`、`wallet_projection` |
| v33 | M1.2 | `reward_grants`（+ 系统账户引导） |
| v34 | M1.3 | `work_orders`、`work_order_submissions`、`evaluations` |
| v35 | M1.4 | `escrows` |
| v36 | M1.5 | `compute_usage` |
| v37 | M1.6 | `contracts`、`offers` |
| v38 | M1.7 | `talent_commercial_terms` |
| v39 | M1.8 | `npc_economic_profiles`（或复用 participant profile_json，届时裁定） |

纪律：不改旧迁移；每迁移 `up/down/up` 实测 + `alembic check`；唯一/部分唯一索引必须写进模型。

## 6. API Roadmap（三层，设计 §32）

| 层 | 端点（汇总） | 阶段 |
| --- | --- | --- |
| 玩家/公司 | `GET /economy/balance`、`GET /economy/transactions`、`GET|POST /economy/rewards`、`GET|POST /work-orders*`、`GET /contracts*` | M1.1–M1.7 |
| 内部领域 | 不出 router：`LedgerService.post`、`MonetaryAuthority.*`、`EscrowService.*`、`SettlementService.settle` | M1.1+ |
| 系统/管理 | 发行统计、manual evaluation、NPC 预算注入（CLI / 内部面，**不进玩家 router**） | M1.3/M1.8/M1.9 |

职责约定：写端点只做 transport/校验/orchestration，资金不变量全在 service（T2 同纪律）；
玩家面一律公司作用域（`resolve_company_id` + repo 层强制）。

## 7. Event Plan

见设计 §35 的事件表（`RewardGranted` / `LedgerTransactionPosted` / `EscrowFunded|Released|Refunded` /
`WorkOrderAccepted` / `SubmissionApproved` / `ContractActivated|Settled` / `SettlementCompleted` / `TalentPurchased`）。
纪律：同步事务负责资金不变量；事件只承载已发生的事实；消费者幂等可重放；不为 CRUD 发事件。

## 8. Security / 权限模型

- 三层边界见设计 §32；**E23** 有 AST 守卫（玩家 router 不得 import `MonetaryAuthority` 写入口）；
- 公司作用域：账户/流水/订单/合同一律 `company_id` 过滤（repo 层）；
- 内部/系统面：v1 不建 admin 角色，管理动作走 CLI/脚本（`make economy-*`），并在文档标注
  "未来接 admin 角色时的入口位置"。

## 9. Concurrency / Idempotency

见设计 §33/§34。实现要点：
- 钱包扣款用 **CAS 条件更新**（`WHERE available >= :amt`，rowcount 判定）；
- 唯一约束兜底：`idempotency_key`（transaction）、`settlement_key`、`reward(unique key)`、
  `escrow 状态 CAS`；
- 不做分布式语义；SQLite 单写 + 事务 + 唯一约束即为正确性来源（与 T2 §9 同口径）。

## 10. Test Strategy

| 层 | 覆盖 |
| --- | --- |
| 契约/守卫（M1.0） | 枚举值、状态机迁移表、守恒纯函数、金额/货币、政策版本、E1/E23/T2 边界 |
| domain 纯函数 | 过账腿构造、守恒校验、余额聚合、手续费拆分、可用余额 |
| service | mint/transfer/burn/escrow/settlement/reward/workorder 全链路 + 幂等 + 回滚 |
| repository | 作用域、唯一约束收敛、CAS rowcount |
| API | 状态码语义（404/409/422）、公司作用域、三层边界 |
| property / invariant | 随机交易集合下守恒恒成立；余额 = 账本重算；supply 公式 |
| E2E | M1.10 Golden Path + T2 Golden Path 回归 |
| 失败注入 | 设计 §Failure Injection 矩阵 |

## 11. Migration / Rollback Strategy

- 每阶段独立迁移；`up/down/up` 实测；`alembic check` 无漂移；不改旧迁移；
- 回滚：账本/奖励/订单/合同表互不破坏既有域（T2/T1/R1 表不动），`downgrade` 只 DROP M1 表；
- 涉及 T2 的只有 M1.7（加一张 1:1 扩展表，不改 T2 表结构）。

## 12. Risks（M1 级）

| # | 风险 | 应对 |
| --- | --- | --- |
| 1 | 投影与账本漂移（余额缓存错） | 重建脚本 + 一致性测试 + 守卫 |
| 2 | 通胀（只发不收） | Sink 与 Source 同日设计；政策参数；观测 |
| 3 | 奖励套利（签到/兜底刷钱） | 冷却/上限/幂等 + 政策里明确"收益远小于经营" |
| 4 | 双花（并发扣款） | CAS 条件更新 + 唯一约束 + 并发测试 |
| 5 | 半完成状态（钱动业务没动） | 单事务 + Settlement 终局 + 失败注入测试 |
| 6 | 复制 T2 领域 | E18/E19/E20 + T2 回归网 |
| 7 | 契约过度设计 | 只冻结会被真正用到的部分；不建空壳表 |
| 8 | 三层边界被绕过（玩家调 mint） | E23 AST 守卫 + 内部 service 不出 router |
| 9 | 手续费/NPC 参数失衡 | 全部走政策配置 + 观测面板 |
| 10 | 性能（账本增长） | 索引（account+created_at、reference）+ 分页 + 投影缓存 |

## 13. M1 Golden Path（M1.10 必须真实执行）

```
新用户注册 → 创建公司 → STARTER_GRANT（Ledger 可追溯）
→ 完成官方任务 → 获得官方收入（Mint）
→ 发布玩家任务（Escrow 锁资）→ 第二家公司承接 → 完成 → Settlement（A → B，Supply 不变）
→ B 用收入买人才：Offer → Talent Contract → Escrow → Settlement
→ 调用 T2 Recruitment → Employee 创建 → Person.id / identity_id 不变
→ Seller 收款 / Buyer 扣款 / Ledger 平衡 / Contract SETTLED / Escrow 归零 / Listing 关闭
```

## 14. Acceptance Criteria（M1 级）

| | 内容 |
| --- | --- |
| **A** | 新公司拿到启动资金，账本可解释每一分钱（reference + reason + policy_version） |
| **B** | 玩家通过官方任务**创造价值→获得新发行货币**（唯一主要发行渠道） |
| **C** | 玩家之间交易**不改变 Total Supply**（Work + Talent 两条链都验证） |
| **D** | 人才交易调用 T2 招募，T2 不变量（身份/知识/证据/历史）零破坏 |
| **E** | 失败注入下：不凭空多钱、不凭空少钱、不留半完成状态 |
| **F** | E1–E25 每条不变量都有测试锚点；mint/burn 不出玩家 API |

## 15. Commit 粒度

```
M1.0  docs/architecture: freeze M1 economy domain
M1.1  feat(economy): double-entry ledger + accounts
M1.2  feat(economy): monetary authority + reward system
M1.3  feat(economy): official work market
M1.4  feat(economy): player work market + escrow
M1.5  feat(economy): company operating economy (compute/training/fees)
M1.6  feat(economy): contract / offer / settlement core
M1.7  feat(economy): talent commercialization (calls T2 recruitment)
M1.8  feat(economy): npc economy
M1.9  feat(web/economy): wallet, statements, work market UI + analytics
M1.10 test(m1): golden path e2e + failure injection + freeze
```

每阶段门禁（以 repository 实际脚本为准）：

```bash
cd apps/server && <conda eidolon python> -m pytest tests -q      # 775+ passed
<conda eidolon python> -m ruff check app tests
<conda eidolon python> -m ruff format --check app tests          # 只允许 5 个既有 WIP 红
cd apps/server && <conda eidolon python> -m alembic -c alembic.ini check
cd apps/web && npx tsc --noEmit && npx eslint src && npx prettier --check src
cd apps/web && npx vitest run                                    # 328+ passed
cd apps/web && npm run build
```

## 16. M1 Progress

> 每完成一个阶段更新本表；新 Agent 从这里恢复上下文。

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| M1.0 Economic Domain Contract Freeze | **DONE**（2026-09-11） | `见 Progress Log` | 设计 + 执行基线与契约代码；**无迁移**；pytest 798 / web 328 |
| M1.1 Accounts & Double-entry Ledger | **NEXT** | — | `[migration v32]`；入口：设计 §10–§12 + plan §4/M1.1 |
| M1.2 Monetary Authority & Reward System | PLANNED | — | `[migration v33]` |
| M1.3 Official Work Market | PLANNED | — | `[migration v34]` |
| M1.4 Player Work Market | PLANNED | — | `[migration v35]` |
| M1.5 Company Operating Economy | PLANNED | — | `[migration v36]` |
| M1.6 Contract / Offer / Settlement Core | PLANNED | — | `[migration v37]` |
| M1.7 Talent Commercialization | PLANNED | — | `[migration v38]`；必须跑 T2 回归 |
| M1.8 NPC Economy | PLANNED | — | `[migration v39]`（或复用 participant profile_json） |
| M1.9 Economy UI & Analytics | PLANNED | — | 无迁移 |
| M1.10 Golden Path / Hardening / Freeze | PLANNED | — | E1–E25 全覆盖 + 失败注入 |

### Progress Log

- **2026-09-11 · M1.0 DONE**：commit 哈希见紧随的 `docs(m1): M1.0 进度落盘` 提交（避免自引用哈希）。
  - 交付：`docs/m1-economy-design.md`（40 节：Vision/供给模型/主体/账户/账本/货币/奖励/救援经济/
    WorkOrder/官方与玩家工作市场/Evaluation/Contract/Offer/Escrow/Settlement/公司经营/算力成本/
    人才商业化/T2 集成/Ownership 裁定/NPC/政策/观测/安全/并发/幂等/事件/可审计/状态机/E1–E25/边界/未来）、
    `docs/m1-implementation-plan.md`（M1.0–M1.10 拆解 + 迁移与 API 路线 + 验收 + Progress）；
    `app/economy/`（枚举与值冻结、金额与货币、账户引用、过账腿 + 守恒校验、政策 DTO 与版本、
    状态机迁移表）、`Settings` 经济政策配置项 + `.env.example`；契约与不变量测试。
  - 迁移：**无**（M1.1 起建表；head 仍 `d6e8f0a2b4c7` / v31）。
  - 门禁：pytest **798 passed** / 6 deselected（+23 契约测试）；ruff 全绿、format 仅 5 个既有 WIP 红；
    alembic check 无漂移；web 328 passed + tsc/eslint/prettier/build 全绿（未改前端）。
  - 风险：M1.1 起需同时钉住"投影可重建"与"并发扣款 CAS"；T2 回归在 M1.7 必跑。
