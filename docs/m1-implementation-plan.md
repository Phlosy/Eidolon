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

| 阶段 | 状态 | Commit | 备注 |
| --- | --- | --- | --- |
| M1.0 Economic Domain Contract Freeze | **DONE**（2026-09-11） | `2f75590` | 设计 + 执行基线与契约代码；**无迁移**；pytest 798 / web 328 |
| M1.1 Accounts & Double-entry Ledger | **DONE**（2026-09-11） | `12fb9a7` / `2d37938` / `9cb1e06` | `[migration v32]` `8f1abef8410f`；四小阶段 M1.1a–d 全部落地；**A1–A20 全部满足**；pytest 851 / web 328 |
| M1.2 Monetary Authority & Reward System | **DONE**（2026-09-11） | `81eed9a` / `6e35963` / `7cc5541` | `[migration v33]` `691816bccb53`；7 类自助奖励全部落地（含救援经济）；pytest 874 / web 328 |
| M1.3 Official Work Market | **DONE**（2026-09-11） | `03f47a6` / `6a479e9` / `e2f505b` / `ee958cf` | `[migration v34]` `328fbe9f3034`；官方 bounty 全生命周期 + 预算内发行；pytest 898 / web 328 |
| M1.4 Player Work Market | **DONE**（2026-09-11） | `1f715f2` / `6f5eaeb` / `14db721` / `54ac474` | `[migration v35]` `d2a4926a21d4`；Escrow 锁资 + 玩家间转移（绝不 mint）；pytest 916 / web 328 |
| M1.5 Company Operating Economy | **NEXT** | — | `[migration v36]` |
| M1.6 Contract / Offer / Settlement Core | PLANNED | — | `[migration v37]` |
| M1.7 Talent Commercialization | PLANNED | — | `[migration v38]`；必须跑 T2 回归 |
| M1.8 NPC Economy | PLANNED | — | `[migration v39]`（或复用 participant profile_json） |
| M1.9 Economy UI & Analytics | PLANNED | — | 无迁移 |
| M1.10 Golden Path / Hardening / Freeze | PLANNED | — | E1–E31 全覆盖 + 失败注入 |

### Progress Log

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
