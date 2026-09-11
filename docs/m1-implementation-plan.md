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

- **Goal**：账本可过账、可查余额、可追溯、**可重建** —— 建立 M1 全部后续阶段的金融底座。
- **内部四小阶段**（同一阶段，实现顺序不可合并成一坨）：
  - **M1.1a Schema + Accounting Contract**：迁移 v32（4 张表 + 唯一约束）、`app/models/economy.py`、
    `normal_side` 派生与 `balance_delta` 统一入口、`SYSTEM_ACCOUNT_KINDS` 定义顺序清理；
  - **M1.1b Ledger Posting Engine + CAS**：`AccountService`（开户/冻结/查询/系统账户 bootstrap）、
    `LedgerService.post()`（唯一 Posting Core）、`MonetaryAuthority`（内部令牌）、CAS 扣款；
  - **M1.1c Projection + Rebuild + Reconciliation**：`wallet_projection` 同事务维护、
    `rebuild_wallet_projection`（只依赖 Ledger）、`verify_wallet_projection`（只报告不改）、CLI + make target；
  - **M1.1d Read API + Hardening**：`GET /economy/balance`、`/economy/accounts`、`/economy/transactions`；
    property / 并发 / 失败注入 / append-only 测试。
- **Schema**：`ledger_accounts`、`ledger_transactions`、`ledger_entries`、`wallet_projection`；
  唯一约束：`ledger_accounts(actor_kind, actor_ref, currency, kind, subject_ref)`、
  `ledger_transactions.idempotency_key`（部分唯一，允许 NULL）。
- **Services**：`AccountService` / `LedgerService` / `MonetaryAuthority` / 投影重建与对账
  （`app/services/economy/`）；仓储 `app/repositories/economy.py`。
- **APIs**：`GET /economy/balance`（公司作用域）、`GET /economy/accounts`、`GET /economy/transactions`
  （分页 + 过滤）。**无写端点** —— 写只发生在业务服务内部（mint/burn/transfer 不对外）。
- **Events**：`ledger.transaction_posted`（跨域有价值时才发；M1.1 只发这一条）。
- **Tests**：开户幂等 / 系统账户 bootstrap / 冻结账户拒绝过账；复式守恒（含 property 随机交易序列）；
  幂等键重复不产生第二条；`available = posted − reserved`；CAS 并发扣款只有一个成功；
  并发同幂等键只落一笔；失败注入（CAS 后 / entries 后 / projection 后异常 → 全回滚）；
  投影重建逐账户逐字段一致；对账能发现人为 drift；append-only（无 UPDATE/DELETE 入口）；
  E1 守卫（财务字段只住经济模型）；Supply = minted − burned 且 transfer 不改变它。
- **Migration**：v32（`down_revision = d6e8f0a2b4c7`）；up/down/up 实测 + `alembic check` 无漂移。
- **Acceptance**：满足 §14b 的 **A1–A20** 全部条目。
- **Risks**：投影与账本漂移（→ 重建校验 + 对账 + 守卫测试）；SQLite 并发语义
  （→ CAS 条件更新 + rowcount 判定，不假装多节点共识）；性能（→ projection 是快路径，
  账本是事实，必要时后续加索引）。

#### M1.1 Acceptance Criteria（A1–A20，完成判定）

| # | 判据 |
| --- | --- |
| **A1** | Ledger 是资金事实来源（余额是派生/缓存） |
| **A2** | `wallet_projection` 可从 Ledger 完整重建（清空后重算一致） |
| **A3** | 普通 Wallet（actor，非 system）不允许负 `available_balance` |
| **A4** | 并发扣款通过 CAS 防止 double spend（100 两笔 80 → 恰好一笔成功） |
| **A5** | CAS + Ledger 写入 + Projection 更新在同一事务 |
| **A6** | 所有普通 transaction 复式守恒 Σdebit = Σcredit |
| **A7** | 金额不使用 float（整数最小单位） |
| **A8** | LedgerEntry append-only（无 UPDATE/DELETE 入口） |
| **A9** | 所有便利原语最终走统一 `post()` 核心 |
| **A10** | Idempotency 阻止重复记账（同 key 不产生第二条 transaction） |
| **A11** | Transfer 不改变 Total Supply |
| **A12** | Mint 增加 Supply |
| **A13** | Burn 减少 Supply |
| **A14** | 系统账户权限正确（mint/burn/treasury 仅 MonetaryAuthority 令牌） |
| **A15** | Projection drift 可被检测（对账只报告不改） |
| **A16** | Projection 可被重建 |
| **A17** | Read API 公司作用域正确（跨公司看不到余额/流水） |
| **A18** | 没有新增公开 Mint/Burn 写接口 |
| **A19** | M1.1 没有越界实现 M1.2+（无 Starter/Reward/WorkOrder/Contract/Talent 价格） |
| **A20** | 完整 gates 通过或仅剩明确记录的既有 WIP |

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

## 11b. Known Technical Debt（记录，不在 M1.1 处理）

| 项 | 现状 | 处理时机 |
| --- | --- | --- |
| 政策快照缓存 | `economic_policy()` 是进程内 `lru_cache`；改配置需**重启**或显式 `cache_clear()` | **M1.9**（在线调参 / 政策中心），M1.1 不提前设计 Admin |
| T2 回归专项门禁 | `test_t2_golden_path` / `test_recruitment` 目前随全量 pytest 一起跑 | **M1.7**（人才商业化触碰招募路径）列为必跑专项 |
| 既有 ruff-format WIP（5 个文件） | 历史遗留，与本阶段无关 | 独立清理，不顺手扩大范围 |
| 内容数据 i18n / R1 镜像列 | 已知，评估结论：暂不拆 | M1/M2 之后按域分批 |

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
>
> **状态：M1（经济 / 金融 / 合同）已 FROZEN**（2026-09-11，M1.0–M1.10 全部 DONE）。
> 冻结面见设计 §39b；不变量锚点见 `tests/test_m1_invariants.py`；
> 下一步是 M2（联网市场 / 政策中心 / 股权等），不在本文件范围。

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| M1.0 Economic Domain Contract Freeze | **DONE**（2026-09-11） | `2f75590` | 设计 + 执行基线与契约代码；**无迁移**；pytest 798 / web 328 |
| M1.1 Accounts & Double-entry Ledger | **DONE**（2026-09-11） | `12fb9a7` / `2d37938` / `9cb1e06` | `[migration v32]` `8f1abef8410f`；四小阶段 M1.1a–d 全部落地；**A1–A20 全部满足**；pytest 851 / web 328 |
| M1.2 Monetary Authority & Reward System | **DONE**（2026-09-11） | `81eed9a` / `6e35963` / `7cc5541` | `[migration v33]` `691816bccb53`；7 类自助奖励全部落地（含救援经济）；pytest 874 / web 328 |
| M1.3 Official Work Market | **DONE**（2026-09-11） | `03f47a6` / `6a479e9` / `e2f505b` / `ee958cf` | `[migration v34]` `328fbe9f3034`；官方 bounty 全生命周期 + 预算内发行；pytest 898 / web 328 |
| M1.4 Player Work Market | **DONE**（2026-09-11） | `1f715f2` / `6f5eaeb` / `14db721` / `54ac474` | `[migration v35]` `d2a4926a21d4`；Escrow 锁资 + 玩家间转移（绝不 mint）；pytest 916 / web 328 |
| M1.5 Company Operating Economy | **DONE**（2026-09-11） | `73e07ca` / `bef7e72` / `aaee26f` / `eb7a1a5` / `f038ed5` | `[migration v36]` `d9545a745166`；算力/培养/手续费三项 Sink + 经营报表；pytest 933 / web 328 |
| M1.6 Contract / Offer / Settlement Core | **DONE**（2026-09-11） | `044825d` / `03938de` / `0281dd0` / `43ab6c2` | `[migration v37]` `0425abecc96e`；合同全生命周期 + 多腿结算（净额 + Treasury/Burn）；pytest 950 / web 328 |
| M1.7 Talent Commercialization | **DONE**（2026-09-11） | `aca9f1e` / `b4dd1e5` / `92fa279` / `71a1a5b` / `cfca5a3` | `[migration v38]` `862e2d3d7d8e`；T2 人才接入经济（价格 + Escrow + 招募 + 结算）；pytest 961 / web 328 |
| M1.8 NPC Economy | **DONE**（2026-09-11） | `c31ace6` / `3aca802` / `27ea43f` / `ec670fd` | `[migration v39]` `64fec2d13d9b`；NPC 预算（注入=mint，受封顶）+ deterministic 出手；pytest 972 / web 328 |
| M1.9 Economy UI & Analytics | **DONE**（2026-09-11） | `b3869b2` / `49cd4f9` | 无迁移；个人钱包读面 + 三个经济页面 + admin 观测/巡检/政策刷新；pytest 972 / web 351 |
| M1.10 Golden Path / Hardening / Freeze | **DONE**（2026-09-11） | `8297871` | 闭环 E2E + 失败注入矩阵 + E1–E31 锚点表；**M1 FROZEN**；pytest 1018 / web 351 |

### Progress Log

- **2026-09-11 · M1.10 DONE —— M1 FROZEN**：commit **`8297871`**（`test(m1): golden path e2e +
  failure injection + invariant anchors (M1.10)`，无迁移）。
  - **Golden Path E2E**（`test_m1_golden_path.py`）：注册 → 启动资金 → 官方任务（mint）→
    玩家任务（Escrow）→ 人才交易（Offer → Contract → Escrow → 多腿放款 → **T2 招募** → Employee）→
    终局对账；证据 = 账本复式平衡 + 状态终态 + 双方余额可精确复算 + 身份/历史零破坏；
  - **失败注入矩阵**（`test_m1_hardening.py`，11 项）：余额不足（4 条路）/ 重复领取·结算·释放 /
    取消与过期退款 / listing 被抢（订单与挂牌各一）/ Escrow 状态竞争 / 招募失败整笔回滚 /
    中途异常（写腿与投影两处）/ 成本扣款 SAVEPOINT 隔离 / NPC 注入封顶 / 并发消费与并发结算 /
    收尾无孤儿实体 —— 每个场景都断言"钱逐字段不动 + 状态一致"；
  - **不变量锚点表**（`test_m1_invariants.py`，33 项）：E1–E31 → 真实测试锚点，并与设计 §38 编号
    做集合相等断言（锚点删了就红）；
  - 测试：pytest **1018 passed / 6 deselected**（972 → **+46**）；T2 回归 61 项全绿；
  - 冻结：设计新增 **§39b M1 冻结面**（Schema/不变量/钱的口径/唯一写入路径/三层 API/T2 接入缝/
    模块边界/政策/关键裁决 + 明确留给 M2 的清单）；
  - 未发现设计缺口（无需偏差小节）；唯一顺带修正是 M1.1 测试数据补 `reason`（与生产一致）。

- **2026-09-11 · M1.9 DONE**（无迁移）：commits **`b3869b2`**（后端读面/观测/政策刷新）、
  **`49cd4f9`**（三个经济页面 + i18n + 测试）。
  - 交付：`GET /economy/wallet/me`（个人钱包，补 M1.2 缺口）、`EconomyStatsService` 经济快照 +
  一致性巡检（`make economy-stats|economy-check`）、`reload_policy()` + `make economy-policy-reload`
  + gated admin 端点；前端 `/economy`、`/work-orders`、`/contracts` 三页 + 导航 + 中英 i18n；
  - 口径裁定：**前端只读**（数字全来自后端读面）；个人钱包与公司账户分开；admin 面默认关
  （v1 无 admin 角色体系）；政策在线刷新是最小入口（政策中心属 M2）；
  - 测试：后端 **972 passed / 6 deselected**（含 T2 回归）；前端 **351 passed / 83 files**（+23 用例）：
  API 契约 5、经济页 4、工作订单页 3、合同页 4、i18n 契约 7；
  - **修掉一个假阳性 bug**：托管余额聚合必须按方向带符号（escrow 是 debit-normal），
  否则"已释放/已退款"的托管会被巡检误报（首版报 4 条假阳性，修完 `--check` 立即 OK）；
  - 门禁：ruff 全绿、format 仅 5 个既有 WIP 红、`alembic check` 无漂移（无迁移）、
  web tsc/eslint/prettier/build 全绿。

- **2026-09-11 · M1.8 DONE**：`[migration v39]` `64fec2d13d9b`（`npc_economic_profiles`）；
  commits **`c31ace6`**（schema + 政策 + 枚举）、**`3aca802`**（T2 成交原语 seam）、
  **`27ea43f`**（NpcEconomyService + CLI）、**`ec670fd`**（测试硬化）。
  - 交付：NPC 经济档案（参数在档案、钱在账本）、系统预算注入（**唯一 mint 入口**：受 `budget_cap`
    封顶 + `category=NPC_BUDGET`）、§29 判定规则（deterministic）、成交（判定 → T2 `take_candidate`
    → **立刻付款转移** → 提交后发事件）、`run_round`（一轮可解释）、收入记录（账本按类别）、
    CLI `make npc-economy-*`（没有玩家路由）；
  - 口径裁定：**NPC 出手是转移不是发行**（E7/E8，实机 minted 不变）；成交走 T2 的**同一个**
    `take_candidate`（E20，不重写"谁被拿走"）；`fit_threshold_bps` 用基点避免浮点阈值；
    系统/发行方卖家 ⇒ 成交款进 Treasury；
  - T2 接入缝（第二个）：`NpcMarketService.take_candidate` / `publish_candidate_taken` /
    `ensure_system_definition` —— 行为与事件完全不变（`test_market_npc` 等回归全绿）；
  - 测试：**+11**（972 passed / 6 deselected）：注入=mint 且封顶、判定规则逐条、不会买超、
    人才离场（T2 consumed）、一轮至少一单（acceptance）、dry-run 不动钱、收入记录、
    边界守卫（无玩家路由 + AST：mint 只在 inject_budget）；
  - 迁移：v39 up/down/up 实测 + 两个 dev 库 `alembic check` 无漂移。

- **2026-09-11 · M1.7 DONE**：`[migration v38]` `862e2d3d7d8e`（`talent_commercial_terms`）；
  commits **`aca9f1e`**（schema）、**`b4dd1e5`**（招募事务 seam）、**`92fa279`**（TalentTradeService）、
  **`71a1a5b`**（API）、**`cfca5a3`**（测试硬化）。
  - 交付：`TalentTradeService`（条款 → 出价 → 合同 → 锁资 → **T2 招募** → 放款，同一事务）、
    `talent_commercial_terms`（listing 1:1 扩展，价格一等列 + 一口价/议价模式）、
    `/market` 前缀下的 M1 交易 API（T2 market.py 一行不动）、`talent.purchased` 事件；
  - T2 接入缝：`RecruitmentService.recruit_existing_person(commit=True 新增)` —— 默认行为不变，
    `commit=False` 时事务/事件归调用方；**T2 回归 61 项全绿**（硬门禁）；
  - 口径裁定：一口价出价即成交 / 议价需卖方接受；系统挂牌价格由系统侧设置、成交款进 Treasury；
    `sale_mode` 取代 `negotiable`（一个字段说一件事）；
  - 测试：**+11**（961 passed / 6 deselected）：资金三腿与 E8、E18/E19/E20 快照对拍、
    招募失败整笔回滚、余额不足不留痕、二次购买被拒、系统卖方进 Treasury、API 语义；
  - 迁移：v38 up/down/up 实测 + 两个 dev 库 `alembic check` 无漂移；
  - 顺带修 M1.6 两个缺口：合同允许 system 承接方（仅 talent，Treasury 收款）、
    结算受益账户解析支持系统主体 + `actor_ref=0` 不再被真值判断误判。

- **2026-09-11 · M1.6 DONE**：`[migration v37]` `0425abecc96e`（`contracts` / `offers` /
  `escrows.contract_id`）；commits **`044825d`**（schema + 手续费档位）、**`03938de`**
  （多腿放款 + 合同/Offer 服务）、**`0281dd0`**（合同 API）、**`43ab6c2`**（测试硬化）。
  - 交付：`ContractService`（创建即锁资 → 接受 → 交付即结算 → 取消/失败/过期退款，全状态 CAS）、
    `OfferService`（接受报价生成已锁资合同）、`EscrowService.release_legs`（一次 CAS + 多腿拨付）、
    `SettlementService` 的放款多腿/退款分支（三腿之和 = 对价）、`contract_fee_bps` 手续费档位、
    合同 API（当事人作用域）。
  - 口径裁定：**托管权威指针只在 `escrows.contract_id`**（不双指针）；**创建即锁资**（E11 的合同形态）；
    `FUNDED` 之后不能取消（冻结状态机）；手续费从对价里扣（结算不依赖任何人的额外余额）；
    退款不抽手续费。
  - 测试：**+17**（950 passed / 6 deselected）：锁资/无钱不留合同、work + service 全生命周期、
    多腿守恒与 E8（不 mint、只有 burn 腿回收）、取消/过期/失败退款、重复结算幂等、并发接受唯一赢家、
    状态机非法迁移、Offer 幂等生成合同、API 当事人作用域与 404/409 语义。
  - 迁移：v37 up/down/up 实测（含 SQLite batch FK 与部分唯一索引）+ 两个 dev 库 `alembic check` 无漂移。
  - **踩坑记录**：autogenerate 的 v37 用 `create_foreign_key` 在 SQLite 直接失败，而 SQLite DDL 非事务
    ⇒ 两个 dev 库被部分写入（`contracts`/`offers`/`contracts_id` 半成品）。已手工回滚到 v36 一致状态
    后改写为 `batch_alter_table` 版本（具名 FK 与模型侧同名，`alembic check` 才稳定）。
    教训：**带 FK 的新列在 SQLite 上必须用 batch 模式，先写迁移再升级**。

- **2026-09-11 · M1.5 DONE**：`[migration v36]` `d9545a745166`（`compute_usage` +
  `ledger_transactions.category`）；commits **`73e07ca`**（schema + 政策）、**`bef7e72`**
  （成本服务 + 类别 plumbing）、**`aaee26f`**（三个触发点接线）、**`eb7a1a5`**（经营报表 API/CLI）、
  **`f038ed5`**（测试硬化）。
  - 交付：`CompanyCostService`（统一 Sink 扣款，treasury+burn 守恒、**SAVEPOINT 保护**）、
    `ComputeCostService`（计量 + 计价 + 欠费语义）、`FeeService`（bps 报价 + 拆分 + 挂牌/合同费）、
    培养成本事件消费者（`cultivation.completed`，T2 零改动）、`orchestrator._finalize` 算力计量、
    挂牌手续费、经营报表读面（overview / compute-usage）+ CLI。
  - 口径裁定：**余额不足 = 欠费**（`unpaid` 计量照记、扣款尽力而为、绝不产生负余额）；
    手续费在**发布时**收（不是结算时）；`category` 作为账本一等列（不建同义表），
    存量行为 NULL 不编造；1 compute unit = 1 分钟运行时。
  - 测试：**+17**（933 passed / 6 deselected）：算力 paid/unpaid/幂等/类别落库、
    手续费守恒与 bps 数学、挂牌费入 treasury+burn、只够锁资的欠费路径、同 key 不同类别拒绝、
    培养成本消费者（含重放/欠费/无付款方）、报表与用量 API 的公司作用域。
  - **修掉两个真实 bug**：(1) 成本扣款失败会残留未提交账本腿，被调用方 commit 后破坏复式守恒
    （现用 SAVEPOINT 隔离）；(2) `derive_wallets(account_ids=[...])` 丢掉托管归因 ⇒
    受限查询的 `reserved` 恒为 0（`GET /economy/overview` 上暴露）。
  - 迁移：v36 up/down/up 实测 + `alembic check` 无漂移；dev 库已升到 v36。
  - M1.4 的余额断言同步改为按政策算挂牌手续费（不写死数字）。

- **2026-09-11 · M1.4 DONE**：`[migration v35]` `d2a4926a21d4`（`escrows`）；commits
  **`1f715f2`**（schema + 玩家订单护栏）、**`6f5eaeb`**（EscrowService + 玩家市场生命周期）、
  **`14db721`**（发布/取消 API）、**`54ac474`**（测试硬化）。
  - 交付：`EscrowService`（fund/release/refund/expire，CAS 裁定 release-vs-refund 竞争）；
    `SettlementService` 的 `player_escrow` 分支（放款而非发行）；
    `WorkOrderService.publish_player_order`（**E11 发布前锁资**）/`cancel`（退款）/
    `settle`（玩家订单走托管放款、**不写 reward_grants**）/`expire_overdue`（过期退款）；
    玩家 API `POST /work-orders` + `POST /work-orders/{id}/cancel`；订单载荷带 `escrow` 与 `is_issuer`。
  - 口径裁定：玩家订单**不受官方预算约束**（花自己的钱）但受 `player_order_max_reward` 护栏；
    **玩家之间的转移不写 `reward_grants`**（`reward_grants` 只表达"发行/奖励"，E8）；
    Escrow 行 + 账本交易承担全部来源追溯（E16）。
  - 测试：**+18**（916 passed / 6 deselected）：锁资语义（posted 不变 / reserved 增加）、
    E11 余额不足不留订单、A→B 全流程 supply 恒定、取消与过期退款、release-vs-refund 竞争唯一赢家、
    幂等重放、E25/E30 归零与归因重建、玩家订单无 grant/无 mint、官方 kind 经玩家通道被拒。
  - 迁移：v35 up/down/up 实测 + `alembic check` 无漂移；dev 库已升到 v35。
  - 实现中注意到的坑：并发竞争必须落在**已提交**的 CAS 上（测试里用 `commit=False` 会让两边
    都"成功"然后各自回滚 —— 那是测试假象，不是竞态）；`ruff format tests` 会连带格式化 3 个
    既有 WIP 文件（已回滚，未扩大改动面）。

- **2026-09-11 · M1.3 DONE**：`[migration v34]` `328fbe9f3034`（`work_orders` /
  `work_order_submissions` / `evaluations`）；commits **`03f47a6`**（schema + 预算政策）、
  **`6a479e9`**（Settlement/Evaluation/WorkOrder 服务）、**`e2f505b`**（读/写 API + 管理面 CLI）、
  **`ee958cf`**（测试硬化）。
  - 交付：官方 bounty 全生命周期（OPEN→ACCEPTED→IN_PROGRESS→SUBMITTED→REVIEWING→APPROVED→SETTLED）；
    `SettlementService`（资金终局唯一入口，`settlement_key` = ledger 幂等键）；
    `EvaluationService`（auto 确定性规则 / manual 管理面，奖励 = base + Σbonus）；
    `WorkOrderService`（发布/领取/提交/验收/结算/过期，全部 `assert_transition` + CAS）；
    玩家 API（`GET /work-orders`、详情、`accept`、`submit`）+ 管理面 CLI
    （`make work-order-publish/list/evaluate/settle/expire`）。
  - 口径裁定：官方发行**预算内**（单笔封顶 + 未结算承诺额封顶，新增两个政策参数）；
    auto 模式**不给 bonus**（bonus 属人工语义）；玩家类 kind 在 M1.4（Escrow）前不可发布；
    发布/验收/结算**不进玩家 router**（§32），落 CLI + 功能/AST 双守卫。
  - 测试：**+24**（898 passed / 6 deselected）：生命周期与 mint 去向、奖励=base+bonus、
    重复结算幂等、并发领取唯一赢家、预算封顶与释放、非法迁移/过期/空提交/非整数 bonus、
    E8 反面校验、跨公司不可替提交与不可读交付物、无发布/验收/结算端点。
  - 迁移：v34 up/down/up 实测 + `alembic check` 无漂移；dev 库已升到 v34。
  - 实现中修掉的 bug：REJECTED 重提漏做真实状态迁移；`expire_overdue` 漏传 session；
    auto 判定的"缺件诊断"被当成 bonuses 解析；**订单过期比较 naive/aware datetime 混用**；
    `self.evaluations` 属性遮蔽同名方法；`SettlementService.settle(commit=True)` 未真正提交。

- **2026-09-11 · M1.2 DONE**：`[migration v33]` `691816bccb53`（`reward_grants`，只建表）；commits
  **`81eed9a`**（schema + 政策快照）、**`6e35963`**（RewardService + 读/领 API）、**`7cc5541`**（测试硬化）。
  - 交付：`RewardGrant` 模型 + 仓储原语；`RewardService`（判定/领取，**唯一自助发放入口**）：
    7 类自助奖励（Starter / Profile / Company Profile / Tutorial / Daily / Achievement / Recovery）、
    幂等（唯一约束 + 重放 + IntegrityError 兜底）、状态机 ELIGIBLE→CLAIMED→POSTED、
    政策快照（`amount` + `policy_version`）、`ledger_transaction_id` 可追溯；
    `GET /economy/rewards` + `POST /economy/rewards/{type}/claim`（金额无入参）；
    事件 `reward.granted`；政策新增 `achievement_reward` 并加入救援金硬约束。
  - 口径裁定：**政策不建表**（真相在 `Settings` + `policy_version` 快照，plan §5 v33 只有 `reward_grants`）；
    自助可领类型白名单（官方类必须走各自业务流，M1.3+）；资格全部读既有业务事实。
  - 测试：**+22**（874 passed / 6 deselected）；奖励 16（启动资金幂等/并发一次、资料与教程资格、
    每日按 UTC 日、成就按 code、公司编制、救援阈值+冷却+政策约束、官方类拒绝、金额不可被调用方影响、
    混合奖励后 supply 恒等与投影一致），API 6（目录、幂等领取、409/404 语义、金额来自政策、
    公司作用域、事件只发一次）。
  - 迁移：v33 up/down/up 实测 + `alembic check` 无漂移；dev 库已升到 v33。
  - 实现中修掉的 bug：教程奖励的 reference_key 丢失"未领过"过滤（第二次领取会命中同一 key）。
  - 后续收紧（同批提交）：救援金必须**先领过启动资金**才可领（没进入经济不算破产，`starter_not_claimed`）。
  - 已知读面缺口（→ M1.9）：个人类奖励进 user 钱包，公司作用域的 `GET /economy/balance` 看不到；
    个人钱包读面（我的钱包）列入 M1.9 Economy UI；不并入公司钱包（会污染公司 P&L）。

- **2026-09-11 · M1.1 DONE**：`[migration v32]` `8f1abef8410f`；commits
  **`12fb9a7`**（M1.1a schema + accounting contracts）、**`2d37938`**（M1.1b/c posting core + projection）、
  **`9cb1e06`**（M1.1d read API + CLI）。
  - 交付：4 张表（accounts / transactions / entries / wallet_projection）+ 唯一约束；
    `AccountService`（开户/冻结/bootstrap 幂等）、`LedgerService.post()`（唯一 Posting Core：
    幂等 + 守恒 + 权限令牌 + CAS + 投影同事务）、`MonetaryAuthority`（唯一 mint/burn/treasury）、
    `derive_wallets`（余额唯一口径）、`rebuild_wallet_projection` / `verify_wallet_projection`、
    只读 API（balance/accounts/transactions，公司作用域）、CLI + make 目标。
  - 验收：**A1–A20 全部满足**（A1 账本即事实、A2 投影可重建、A3 余额非负、A4 CAS 防双花、
    A5 同事务、A6 守恒、A7 整数金额、A8 append-only、A9 单入口、A10 幂等、A11–A13 供给语义、
    A14 系统账户权限、A15 drift 可检、A16 可重建、A17 作用域、A18 无写端点、A19 未越界、A20 gates）。
  - 测试：**+53**（851 passed / 6 deselected）；其中账本契约 25、投影与 property 8、
    并发与失败注入 8、读 API 8，另有 M1.0 契约测试扩充 4。
  - 迁移：v32 up/down/up 实测 + `alembic check` 无漂移；dev 库已升到 v32。
  - 门禁：ruff 全绿、format 仅 5 个既有 WIP 红（**未顺手改**）；web 328 passed +
    tsc/eslint/prettier/build 全绿（未改前端）。
  - 实现中修掉的真实 bug（写测试发现）：ORM `direction` 字符串用 `is` 比较导致余额增量反号；
    escrow release/refund 出资人 `posted` 方向写反；第二出资人未在写账前拦下；
    流水 count 查询 cartesian product 导致 total 被放大。

- **2026-09-11 · M1.0 DONE**：commit **`2f75590`**（`docs/architecture: freeze M1 economy domain (M1.0)`，10 files / +2389）。
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
