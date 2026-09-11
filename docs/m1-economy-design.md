# M1 经济与合同系统 · 领域设计（Economy, Finance & Contract System）

> 状态：**M1.0 Economic Domain Contract Freeze 已冻结**（2026-09-11）。
> 本文是 M1 的**领域语义唯一基线**；工程执行基线见 [m1-implementation-plan.md](m1-implementation-plan.md)。
> 上游：T2（人才流通，已 FROZEN）见 [t2-talent-market-design.md](t2-talent-market-design.md)；
> 概念架构 §2.2（资产的统一生命周期词汇）/ §4（十条扩展规则）见 [concept-architecture.md](concept-architecture.md)。
> 本文所有代码路径以 audit 时的 repository 现状为准（HEAD `a9f6bae`，迁移 head `d6e8f0a2b4c7` / v31）。

---

## 1. Vision

Eidolon 目前是「公司 + Agent + 人才管理系统」，产出与流通都有，唯独**没有价格与价值交换**。
M1 建立一套可持续演进的经济层，使游戏第一次拥有完整的宏观循环：

```
Mint → Earn → Trade → Spend → Sink → Mint
（发行 → 创造价值 → 收入 → 流通 → 消费 → 回收 → 再发行）
```

**M1 不是"给人才市场加个价格"**：它是账户、复式账本、货币发行/回收、奖励、统一工作市场、
合同与 Escrow、公司经营收支、人才商业化、NPC 经济与调控观测的整体体系。

## 2. Economic Goals

1. 让「玩家创造价值」成为新货币的主要来源（官方工作订单/合同/资助/采购）；
2. 让玩家之间形成真实的价值交换（工作、服务、人才），且**不增发**；
3. 让公司拥有可读的 Revenue / Cost / Expense 账（经营决策有依据）；
4. 让货币有回收（Sink：算力、培养、手续费、Burn），形成闭环而非单边通胀；
5. 让经济可观测（Total Minted / Burned / Supply / Treasury / Volume / Fees）；
6. 让经济政策可配置、可版本化（解释"为什么当年给 100k，现在给 50k"）；
7. **账务正确性优先于玩法便利**：先 Ledger，再业务，最后 UI。

## 3. Non-goals（M1 明确不做）

真实法币 / 区块链 / 加密货币 / 外部支付 / 银行接口 / 真钱交易 / 证券与股票交易所 /
复杂债券 / 复杂贷款 / 复杂税法 / AML·KYC / 全套真实会计准则。

也**不**在底层未稳定时做：NPC 的 LLM 经济决策、多币种兑换、玩家间二级市场（股权/债权）。

## 4. Money Supply Model

- **Total Supply 只在两种操作下变化**：
  `mint`（发行，+）与 `burn`（销毁，−）；
- `Total Supply = Σ(ISSUANCE 账户的 credit) − Σ(BURN 账户的 debit)`；
  `Circulating Supply = Total Supply − Treasury 余额 − Escrow 中未释放资金`
  （后两者是"已发行但不在玩家手上"的部分）；
- **Transfer / Escrow 永不改变 Total Supply**（E6/E7）；
- 金额一律 **整数**（最小单位），货币符号与精度由 `Currency` 表达（v1 只有 `CREDIT`，
  `minor_unit_scale = 1`）；**禁止 float**（E-Amount）。

## 5. Money Sources（发行）

| 来源 | 说明 | 发行者 |
| --- | --- | --- |
| `STARTER_GRANT` | 新公司启动资金（一次性） | MonetaryAuthority |
| `PROFILE_COMPLETION` / `COMPANY_PROFILE_COMPLETION` | 完善资料（一次性，小额） | MonetaryAuthority |
| `TUTORIAL_COMPLETION` | 教程完成（一次性，小额） | MonetaryAuthority |
| `DAILY_LOGIN` / `WEEKLY_ACTIVITY` | 活跃激励（非常小，且有上限） | MonetaryAuthority |
| `ACHIEVEMENT` | 成就（小～中，一次性） | MonetaryAuthority |
| `MILESTONE_REWARD` | 公司里程碑（一次性） | MonetaryAuthority |
| `OFFICIAL_BOUNTY` / `OFFICIAL_CONTRACT` | 官方工作订单（中～大，主要来源） | MonetaryAuthority |
| `RESEARCH_GRANT` | 科研资助（大，高阶来源） | MonetaryAuthority |
| `SYSTEM_PROCUREMENT` | 系统采购数字资产（中～大） | MonetaryAuthority |
| `EVENT_REWARD` | 活动奖励（可调） | MonetaryAuthority |
| `RECOVERY_GRANT` | 破产兜底（非常小，有冷却与上限） | MonetaryAuthority |

**规模原则**：`签到/资料/教程 << 官方任务/合同 < 高阶合同/资助`。
任何情况下"靠签到比靠经营赚得多"都是设计错误（有政策参数与测试约束）。

## 6. Money Transfers（流通，不增发）

玩家任务赏金、玩家合同、服务交易、人才交易、系统费用划转（treasury）、销毁（burn）、
Escrow 资金进出 —— 全部是账户间转移或对系统账户的划转。

## 7. Money Sinks（回收）

| Sink | 去向 | 说明 |
| --- | --- | --- |
| 算力 / Agent 运行时（ComputeCost） | Treasury（或按比例 Burn） | 未来接真实 provider 成本映射 |
| 培养 / 认证（TrainingCost） | Treasury | T1 培养资源消耗的货币化 |
| 市场手续费（Listing/Settlement Fee） | 按 `treasury_ratio` / `burn_ratio` 拆分 | 默认 3% Treasury / 2% Burn |
| 合同手续费 | 同上 | |
| 系统服务 / 基础设施 | Treasury | |
| 公司升级 | Treasury | 未来 |

**Sink 必须与 Source 同日设计**：只有 Source 的系统必然通胀。

## 8. Monetary Authority

`MonetaryAuthority` 是**唯一拥有发行/销毁权限的主体**（E4/E5）：

```
MonetaryAuthority
  ├── mint(actor, amount, reason, reference)        → 发行（Starter/Reward/Official 结算）
  ├── burn(actor, amount, reason, reference)        → 销毁（Sink 的 Burn 部分）
  └── treasury_transfer(from, amount, reference)    → 流入 Treasury（Sink 的财政部分）
```

- 普通 `User` / `Company` / `NPC Company` **不得**调用（M1 §32 Security）；
- 三个系统账户是**固定的系统 Actor**（`SYSTEM_ISSUANCE` / `SYSTEM_TREASURY` / `SYSTEM_BURN`），
  不是公司行、不需要 `companies` 记录（沿用 T2 D8 的"系统角色不进 companies"）。

## 9. Economic Actors

```
EconomicActor
  kind ∈ { system, user, company, npc_company }
  ref  = 对应实体的 id（system 时指向 SystemAccountKind）
```

| kind | 载体 | 说明 |
| --- | --- | --- |
| `system` | `SystemAccountKind`（固定枚举） | 发行/财政/销毁 |
| `user` | `users.id`（`app/models/auth.py`） | 个人钱包（签到/成就/未来投资），v1 业务以公司为主 |
| `company` | `companies.id` | 公司钱包（M1 主战场） |
| `npc_company` | `market_participants.id`（kind=npc_company） | NPC 经济，**不进 companies**（T2 D8 延续） |

**冻结**：账户表**不写死** `company_id NOT NULL` —— 用 `(actor_kind, actor_ref)` 表达，
未来 `user` / `npc_company` / 新 actor 类型不需要改表。

## 10. Accounts

`LedgerAccount` 是记账主体在**某货币**下的账户：

```
LedgerAccount
  actor_kind / actor_ref      — 归属（见 §9）
  currency = CREDIT
  kind ∈ { actor, escrow, issuance, treasury, burn }
  normal_side                 — 由 kind 派生：actor/escrow/treasury=debit；issuance=credit；burn=debit
  status ∈ { active, frozen, closed }
  unique(actor_kind, actor_ref, currency, kind)
```

- 每个 company 有且只有一个 `actor` 账户（v1）；一个 Escrow 一个 `escrow` 账户（§23）；
- 系统账户固定三条（§8）；
- **账户不可删除**，只能 `closed`（历史账本要能解释每一分钱）。

**Escrow 账户与唯一性（M1.1 冻结）**：Escrow 账户归 `system` actor 所有，用 `subject_ref`
（= escrow id）区分 —— `unique(actor_kind, actor_ref, currency, kind, subject_ref)`，
普通账户 `subject_ref = 0`。**不设"全局 SYSTEM_ESCROW"账户**：托管必须逐笔可归属（§23），
一个全局池子会让"这笔钱是谁锁的"退化成账本外的数字。

**normal_side 与余额语义（M1.1 冻结）**：余额只有一个解释入口 —— 由 `kind` 派生的 `normal_side`：

| AccountKind | normal_side | 余额含义 |
| --- | --- | --- |
| `actor` | debit | 主体可花余额（Company / User / NPC Wallet） |
| `escrow` | debit | 该笔托管中、待释放或退回的资金 |
| `treasury` | debit | 财政池余额 |
| `burn` | debit | 累计销毁量（只增） |
| `issuance` | credit | 累计发行量（只增） |

```
balance_delta(account, direction, amount) = +amount if direction == account.normal_side else −amount
balance(account)                          = Σ balance_delta(entries)
```

- `normal_side` 开户时由 kind 派生并落库（**派生值**，不是可配置业务字段）；
- **资金充足性与 AccountKind 绑定**（不是全局硬编码）：`actor` 与 `escrow` 是"真实持有资金"的账户，
  debit 时必须 `available >= amount`；`issuance` / `treasury` / `burn` 是系统账务侧，
  不受余额不足约束（否则 mint/burn 的第一腿在数字上无法成立）；
- 业务代码不得自己解释借贷方向（禁止散落 `if direction == DEBIT: balance += amount`）——
  统一走 `balance_delta`；
- 未来引入 `revenue` / `expense` / `liability` / `equity`（完整会计）时只扩展该映射表：
  余额语义入口不变，业务代码零改动（**留边界，不提前实现**）；
- 账户状态：`active | frozen | closed`；`frozen` 不可过账，`closed` 终态；**不可删除**。

## 11. Wallet Projection

`Wallet` 不是账务真相。真相是 Ledger Entries 聚合（E2）：

```
LedgerEntry[] ──(聚合)──> balance          （派生）
                        ├── reserved        （Escrow 中由该 actor 出资的未释放额，派生）
                        └── available = balance - reserved   （派生，消费判定的口径）
```

- `WalletProjection`（或 `wallet_accounts` 缓存）**只是 Cache**：
  一个事务内更新，任何时刻都能由账本重算；重建脚本是 M1 的常规运维能力；
- 余额永远不允许直接 `+= / -=`（E1）；所有变化经 Ledger 过账。

**正式定义（M1.1 冻结）**

```
Ledger（accounts / transactions / entries）  =  Financial Source of Truth
WalletProjection                             =  Rebuildable Operational Materialized Projection
```

`wallet_projection` 一行 = 一个 `ledger_accounts` 行（`account_id` 做主键）：

| 列 | 语义 |
| --- | --- |
| `posted_balance` | 该主体的**总资产**（含被 Escrow 锁定但仍属本主体的份额）；对 escrow/系统账户即账户自身余额 |
| `reserved_balance` | 本主体**锁定在 Escrow 中的份额**（归因到本主体的 escrow 账户余额之和）；非 actor 账户恒为 0 |
| `available_balance` | `posted_balance − reserved_balance` = **可花余额**（消费判定与 CAS 的唯一口径） |
| `version` | CAS 乐观并发计数器（每次投影更新 +1） |
| `last_entry_id` | 最后一条导致变化的 entry（重建校验 / 追查用） |
| `updated_at` | 投影更新时间 |

- 均匀恒等式（所有 kind 都成立）：`available_balance = posted_balance − reserved_balance`；
- 公司钱包示例：actor 账户 90,000 + 该主体出资的 Escrow 10,000 ⇒
  `posted = 100,000`、`reserved = 10,000`、`available = 90,000`
  （"锁资不改变净资产，只改变可花额度"）；
- **Projection 不是财务事实来源**：清空 `wallet_projection` 后必须能仅由
  `ledger_accounts + ledger_transactions + ledger_entries` 完整重算
  （`rebuild_wallet_projection`，运维常规能力）；
- `reserved` 的唯一事实来源是 **escrow 账户的 Ledger 余额 + `escrow_fund` 的出资腿归因**：
  出资关系写在 entry 里，重建时可重新推导 —— 禁止把 reserved 设计成账本之外的独立数字；
- 普通 Wallet（`actor` kind 且非 system）不得出现 `available_balance < 0`（E24）；
  CAS 条件更新只落在这一列上（§33）。

## 12. Double-entry Ledger

```
LedgerTransaction
  id / kind(TransactionKind) / currency
  occurred_at / posted_at
  idempotency_key (unique, nullable)
  reference_type / reference_id        — 业务锚点（E16）
  reason / metadata_json
  posted: bool                          — 只有 posted=true 的交易计入余额

LedgerEntry
  transaction_id / account_id
  direction ∈ { debit, credit }
  amount > 0（整数最小单位）
  created_at
```

**守恒不变量**（M1.1 实现，M1.0 以纯函数 + property 测试冻结）：

```
∀ posted transaction T: Σ debit(T) = Σ credit(T)       （E3）
∀ entry: amount > 0                                     （禁止负金额）
```

- 过账方向（v1 固定组合）：
  - mint：Debit actor/escrow，Credit ISSUANCE
  - transfer：Debit 收款账户，Credit 付款账户
  - burn：Debit BURN，Credit 付款账户
  - treasury：Debit TREASURY，Credit 付款账户
  - escrow_fund：Debit escrow，Credit 出资人
  - escrow_release：Debit 收款 actor，Credit escrow
  - escrow_refund：Debit 出资 actor，Credit escrow
- **单边账在结构上不可表达**（Transaction 必须 ≥2 条 Entry 且守恒）。

### 12b. 唯一 Posting Core（M1.1 冻结，E27）

所有资金变化只有一条路径：**便利原语 → 构造 posting → `LedgerService.post()`**。

```
mint() / transfer() / burn() / treasury_transfer() / escrow_fund() / escrow_release() / escrow_refund()
        ↓ 构造 posting（腿取自 LEG_BLUEPRINTS，不手写方向）
LedgerService.post()        ← 唯一入口
        ├── 金额/币种/账户校验（存在、active、非自指、币种一致）
        ├── 权限校验（mint / burn / treasury 需 MonetaryAuthority 内部令牌）
        ├── 复式守恒 Σdebit == Σcredit（E3）
        ├── 幂等（`idempotency_key` 命中 → 返回既有 transaction，绝不二次过账，E12 同族）
        ├── 资金校验 + CAS（`available_balance >= amount`，rowcount 判定，E24）
        ├── INSERT ledger_transactions + ledger_entries（append-only，E17）
        └── 更新 wallet_projection（同事务，E28）
```

- 便利原语**不得各自写 Ledger**：禁止在其它模块出现 `db.add(LedgerEntry(...))`；
- 上述步骤全部在**同一个数据库事务**内完成（CAS → Ledger → Projection）：
  任何一步失败整体回滚。禁止"先 CAS 提交、再记账"，也禁止"先记账、再更新投影"（E28）；
- 幂等语义统一为**返回既有结果**（200 + 既有交易），而不是报错；
- 权限：`mint` / `burn` / `treasury_transfer` 只能由 `MonetaryAuthority` 携带内部令牌调用；
  玩家/公司 API 永远不暴露（E23，§32 三层边界）；
- LedgerEntry 一旦写入不得 UPDATE / DELETE（E17）：不提供修改入口，纠错只能追加 reversal 交易。

## 13. Currency

- 概念先行：`Currency` 枚举 + `currency` 列；v1 只有 `CREDIT`；
- **不做**汇率/兑换/多币种账本（未来 `K3/M2` 视需要再议，表结构预留 `currency` 列即可）；
- 显示名与精度由 Currency 元数据给出（`minor_unit_scale=1`），不散落硬编码。

## 14. Rewards

```
RewardPolicy     — 政策（可版本化）：reward_type → amount/上限/冷却/条件
RewardGrant      — 一次可领取资格（eligibility）或已领取记录
RewardClaim      — 领取动作的结果（可并入 RewardGrant 状态）
```

`RewardGrant` 至少记录：`reward_type / actor_kind / actor_ref / amount / currency / reason /
reference_type / reference_id / policy_version / status / created_at / claimed_at /
ledger_transaction_id`。

- **全部奖励统一走 `RewardService`**（注册奖励/启动资金/签到/教程/成就/悬赏/资助/活动），
  业务代码不得自己操作钱包（E1）；
- **幂等**：`unique(reward_type, actor_kind, actor_ref, reference_key)` 兜底重复领取（E10）；
- 状态机：`ELIGIBLE → CLAIMED → POSTED`（失败可 `VOID`，见 §37）；
- 结算：`RewardService.claim` → `MonetaryAuthority.mint`（官方类）或 `Treasury` 划转（财政类）→ Ledger。

## 15. Starter Economy

注册 → 创建第一家公司 → `STARTER_GRANT`（默认 100,000 CREDIT，**配置化**）：

```
RewardService.ensure_eligible(STARTER_GRANT, company)
  ↓ 条件：公司存在 + 未领取过（唯一约束）
RewardService.claim(...)
  ↓ MonetaryAuthority.mint
Ledger（Debit company.actor 100000 / Credit ISSUANCE 100000）
```

- 一个公司**只能领取一次**；重复请求幂等返回既有 grant（不重复 mint）；
- 金额来自 `EconomicPolicy.starter_grant`，带 `policy_version` 落库（可解释历史差异）。

## 16. Recovery Economy（破产保护）

0 余额不能死档，但也不能成为套利：

- `RECOVERY_GRANT`：当 `available < policy.recovery_threshold` 时可领取**非常小**的额度；
- 约束：冷却期（`recovery_cooldown_hours`）、期间上限、**必须低于任意官方任务收益**；
- 定位：`破产兜底 + 新手引导`，不是收入来源（政策参数 + 测试约束，见 §30/§43）。

## 17. WorkOrder（统一工作市场）

**一个模型承载官方/玩家/NPC 的任务**，靠 `issuer_type` / `funding_mode` / `evaluation_mode` 区分：

```
WorkOrder
  issuer_actor_kind / issuer_actor_ref      — 发布方（system / company / npc_company）
  issuer_type ∈ WorkOrderKind (§16 of prompt)
  title / description / requirements(JSON) / deliverables(JSON)
  reward_amount / currency
  funding_mode ∈ { system_mint, player_escrow, npc_treasury }
  evaluation_mode ∈ { auto, manual, none }
  status(WorkOrderStatus)
  deadline_at / created_at / accepted_at / submitted_at / completed_at
  assignee_actor_kind / assignee_actor_ref   — 承接方（公司）
  project_id                                — 承接后关联的 T2/现有项目（可选）
  policy_version
```

**纪律**：核心领域字段（金额、状态、双方、期限、资助模式）**不得塞进 JSON**；
JSON 只放"需求/交付物描述"这类自由结构（沿用项目既有风格）。

## 18. Official Work Market（官方工作市场，主要发行渠道）

```
系统发布（official_bounty / official_contract / research_grant / system_procurement）
  ↓ 玩家领取 / 申请
执行（现有 Project/Task/Artifact/Employee 体系）
  ↓ 提交（Submission + deliverables 引用）
Evaluation（auto/manual，见 §20）
  ↓ APPROVED
Settlement（MonetaryAuthority.mint → 承接公司）
```

- **Bounty vs Contract 的差别在"分配方式"**：bounty 先到先得（或 Top N），contract 需申请+选中；
- 高级任务逐步以 Contract 为主（M1.6 起）；
- 结算必须走 Reward/Ledger，不得直接改余额（E4/E9）。

## 19. Player Work Market

```
Company A 发布 WorkOrder（player_bounty / player_contract）
  ↓ 校验 available 余额
Escrow 锁资（Debit escrow / Credit A）        ← 发布前必须 fully funded（E11）
  ↓ OPEN → ACCEPTED → SUBMITTED → APPROVED
Escrow 释放（Debit B / Credit escrow）        ← 玩家之间转移，**不 mint**（E8）
```

- 失败/取消/过期：`Escrow → Refund → Issuer`；
- **玩家 WorkOrder 绝不 mint**（E8）；资金不足时**不能发布**（不是"先发布后补钱"）。

## 20. Evaluation

```
Evaluation
  order_id / submission_id
  mode ∈ { auto, manual }
  criteria(JSON)          — 公开/隐藏用例、质量门、性能门（自由结构）
  score / verdict ∈ { approved, rejected, revise }
  bonuses(JSON)           — 例如 score>=90 +2000 / early delivery +1000
  evaluated_by_actor_kind / ref
  created_at
```

- 与项目既有 `assessment`/`evidence` 体系**不冲突**：Evaluation 是"工作订单验收"，
  assessment 是"员工能力评估"；M1.3 不复制 assessment，只引用 `project_id` 与产出物；
- 奖励 = `base + Σbonus`，最终仍走 Reward/Ledger（不得在 Evaluation 里改钱）。

## 21. Contract

通用商业合同（工作/人才/服务/采购/科研共用一个核心）：

```
Contract
  contract_type ∈ { work, talent, service, procurement, research }
  parties: [ { actor_kind, actor_ref, role(issuer/contractor/payee) } ]
  subject / terms(JSON)
  consideration_amount / currency        — 对价（金额是一等字段，不塞 terms）
  escrow_id
  status(ContractStatus)
  effective_at / expires_at / fulfilled_at / settled_at
  policy_version / reference(order/offer/…)
```

**纪律**：`terms` 只放条款细节；金额、双方、状态、期限是一等列（可查、可约束）。

## 22. Offer

```
Offer
  listing_id（T2）/ work_order_id / contract_type
  from_actor / to_actor
  amount / currency
  status ∈ { OPEN, ACCEPTED, REJECTED, WITHDRAWN, EXPIRED }
  expires_at / created_at
```

Offer 是"出价/申请"的通用表达：人才出价、合同申请、报价都属于它；
被接受后**生成 Contract**（Offer 本身不产生资金流）。

## 23. Escrow

- **一等经济能力**，不是"直接付款"的语法糖：
  ```
  payer → escrow（fund）→ 条件满足 → payee（release） 或 → payer（refund）
  ```
- **每个 Escrow 一个独立 `escrow` 账户**（`kind=escrow`、归 `system` actor、`subject_ref = escrow id`；§10）：
  资金既不属于 payer 也不属于 payee，**Total Supply 不变**（E7）；
- **归因规则（M1.1 冻结）**：escrow 账户的出资人 = 该账户 `escrow_fund` 交易中 credit 腿所属主体；
  v1 要求**一笔 Escrow 只有一个出资人**（创建时校验），因此
  `reserved(actor) = Σ 归因到该 actor 的 escrow 账户余额`，且完全可由 Ledger 重建（E30）；
  多出资人 Escrow（联合投资）留到 M2，届时按出资腿比例归因 —— 不提前实现；
- 释放/退回后 escrow 账户余额必须归零（E25），该 actor 的 `reserved` 同步归零；
- 支持：`fund / release / refund / expire`；竞争情形（release vs refund）由 CAS + 唯一约束裁定（§33）；
- Escrow 余额必须能归零（结算完成后不允许残留）。

## 24. Settlement

`SettlementService` 是**所有资金终局的唯一入口**：

```
settlement_key (unique)      — 幂等锚点（E12）
  ↓
校验业务条件（合同/订单/escrow 状态、Evaluation 结果、双方）
  ↓
执行 Ledger Transaction（多腿：可分账手续费/税/ treasury/burn）
  ↓
更新 Contract / Escrow / WorkOrder 状态
  ↓
触发业务领域动作（★ T2：调用 RecruitmentService，不重新实现）
  ↓
记录 SettlementResult（成功/失败/原因，可重放）
```

- **幂等**：同 `settlement_key` 重复调用不重复扣钱（唯一约束 + 状态判断）；
- **原子**：失败不得留下半笔账（E13）；资金与业务状态同事务提交（E14/E15）；
- 人才交易的结算**必须先完成资金→再调用 T2 招募**，或由同一事务包裹（M1.7 裁定，见 §27）。

## 25. Company Economy

- **Revenue**：官方合同/玩家合同/科研资助/采购/人才出售/服务出售 → 全部经 Ledger 入账；
- **Cost/Expense**：招聘、人才购买、培养、算力、运行时/Provider、工具、市场费、合同费、基础设施；
- `EconomicCategory`（枚举）标注每笔账的业务类别 → 报表与观测的基础；
- **接入节奏**：M1.5 只接 3 件事（培养、算力、手续费），其余按真实业务逐步收费化，不一次性全上。

## 26. Compute Cost

```
ComputeUsage（计量，先做内部 Compute Unit）
  ↓
ComputeCost（计价，按政策单价换算 CREDIT）
  ↓ Ledger（Debit Treasury / Credit company.actor）
```

- v1 不映射真实 token 价格，但 `ComputeUsage` 保留 `provider_id/model/tokens/duration` 等字段，
  未来可接 `Provider cost mapping`；
- 它是**最重要的持续 Sink**：Agent 跑得越多，消耗越多（经营决策的核心成本）。

## 27. Talent Commercialization（T2 集成）

```
T2 MarketListing（= 可被发现/可被招募）
  ↓ M1 附加 CommercialTerms（价格/货币/是否可议价/出售模式）
Offer（买方出价）
  ↓ 接受
Talent Contract（含 Escrow）
  ↓ Settlement
T2 RecruitmentService.recruit_existing_person(...)   ← 不重新实现招聘
  ↓
Employee
```

- **不把 `MarketListing` 改造成金融订单**（T2 冻结）：商业条款是**扩展**（独立表/1:1）；
- 结算与招募的一致性：优先"同一事务内先资金后招募"；若 T2 招募失败 → 整笔回滚（钱不动）；
  若采用两阶段（先锁资→招募成功→释放），必须由 Contract 状态机保证最终一致（M1.7 裁定，默认后者更简单：
  **Escrow funded → recruit（同事务）→ release**）；
- **T2 不变量在交易前后必须成立**（E19/E20）。

## 28. Ownership（M1.0 重点裁定）

**问题**：`character_profiles.owner_company_id` 目前同时被读成"谁持有/谁培养的/市场在哪"，
M1 之后还会出现"谁拥有经济权利"。**不能让一个字段承担全部语义**。

**冻结裁定（M1-A1）**：

| 语义 | 载体 | 说明 |
| --- | --- | --- |
| 身份（人是谁） | T2 `persons` + `identity_id` | 终身不变，交易不改写 |
| 培养历史归属 | T2 `character_profiles.owner_company_id` | **历史事实**，永不因交易改写（E18） |
| 当前任职/控制 | T2 `employments`（生效 primary） | 谁在用这个人工作 |
| 市场可发现性 | T2 `market_listings`（active/closed） | 与价格无关 |
| 经济所有权/收益权 | M1 `Contract`（talent_contract）+ 未来 `equity` 表 | M1 不把它塞进 T2 字段 |

**结论**：M1 **不新增** `ownership` 表到 T2 实体上；经济权利由"合同 + 任职"表达；
`owner_company_id` 明确定义为**历史/培养期持有方**（T2 已如此使用，M1 只做显式化，不改行为）。

## 29. NPC Economy

- NPC 的钱包 = `npc_company` 经济 actor（对应 `market_participants(kind=npc_company)`，不进 companies）；
- NPC 的资金来源：`npc_treasury`（系统按预算注入，属于 mint 的一种，计入发行统计）；
- **第一版全 deterministic**：
  ```
  if budget_available >= price and fit >= threshold and price <= max_price: buy
  ```
  不给 NPC 上 LLM（金融底层稳定前不做 AI 经济决策）；
- NPC 的 Revenue/Cost 先只记录，不做经营模拟；人才需求/工作需求用配置表达。

## 30. Economic Policies（配置化 + 版本化）

`EconomicPolicy`（实现：`app/economy/policy.py`，值来自 `Settings`，不硬编码在 service）：

| 参数 | 含义 | 默认（v1） |
| --- | --- | --- |
| `starter_grant` | 启动资金 | 100_000 |
| `profile_reward` / `company_profile_reward` | 资料完善 | 500 / 1_000 |
| `tutorial_reward` | 教程完成 | 2_000 |
| `daily_reward` / `weekly_activity_reward` | 活跃 | 100 / 500 |
| `recovery_grant` / `recovery_threshold` / `recovery_cooldown_hours` | 破产兜底 | 1_000 / 2_000 / 24 |
| `market_fee_bps` / `burn_ratio` / `treasury_ratio` | 手续费与拆分 | 500（5%）/ 0.4 / 0.6 |
| `official_reward_multiplier` | 官方奖励系数（调控用） | 1.0 |
| `compute_credit_per_unit` | 算力单价 | 1 |
| `policy_version` | 政策版本（落库到 Reward/Contract） | `econ-1` |

**运维口径（M1.1 记录）**：政策快照是**进程内 `lru_cache`** —— 改配置需要**重启服务**
或显式 `cache_clear()` 才能生效；在线调参 / 政策中心属于 **M1.9**，本阶段不提前设计 Admin Policy Center。

## 31. Analytics（一等能力）

从账本派生的只读观测（不落第二真相）：

```
Total Minted / Total Burned / Total Supply / Circulating Supply
Treasury Balance / Escrow Outstanding
Starter Grants 发放数 / Official Rewards 总额
Player↔Player Volume（Work + Talent）/ Fees / Compute Spend / Training Spend
公司分布（Top holders / Gini 类指标，M1.9 视需要）
```

## 32. Security（三层 API）

| 层 | 例子 | 权限 |
| --- | --- | --- |
| Player/Company API | 查余额/流水、领奖励、发任务、接任务、查看合同 | 登录 + **公司作用域**（沿用 `resolve_company_id` + repo 层强制） |
| Internal domain API | `LedgerService.post`、`EscrowService.fund/release`、`SettlementService.settle` | 仅服务层调用（不出 router） |
| System/Admin API | `MonetaryAuthority.mint/burn`、treasury 划转、发行统计 | 内部/CLI（v1 无 admin 角色体系，**不暴露给玩家 router**） |

**冻结**：mint / burn / treasury / grant 不出现在任何 `protected` 玩家 router 中；
M1.1 用守卫测试钉死（AST：`api/v1/*` 不得 import `MonetaryAuthority` 的写入口）。

## 33. Concurrency

| 场景 | 手段 |
| --- | --- |
| 重复领 Starter / 签到 | `unique(reward_type, actor, reference)` + 幂等 claim |
| 两个请求同时花同一余额 | **CAS 条件更新**投影：`UPDATE wallet SET balance = balance - :amt WHERE actor=? AND balance - reserved >= :amt`，rowcount=0 → 409 |
| 两个公司同时买同一人才 | T2 已有：listing CAS 关闭（`close_active_listing` rowcount） |
| 两个 Worker 同时结算同一合同 | `settlement_key` 唯一约束 + Contract 状态 CAS |
| Escrow refund 与 release 竞争 | Escrow 状态 CAS（`UPDATE escrow SET status='released' WHERE id=? AND status='funded'`） |
| 重复事件/重试 | 幂等键 + posted 标记；消费者可重放（概念架构 §4 规则 6） |

**不做**：分布式锁/多节点共识（SQLite 单写 + 事务 + 唯一约束足够，且不假装多线程安全）。

## 34. Idempotency

- 所有产生资金变化的入口都带**幂等键**：`reward claim`（业务键）、`settlement_key`、
  `ledger.idempotency_key`；
- 幂等语义：**返回既有结果**（200 + 既有对象），而不是报错（除非状态冲突需要 409）；
- 重复调用不得产生第二条 Ledger Transaction（有唯一约束兜底 + 测试）。

## 35. Events（领域事件，只发跨域有价值的）

| 事件 | 何时 | 载荷要点 |
| --- | --- | --- |
| `RewardGranted` | 奖励过账后 | reward_type / actor / amount / ledger_tx_id / policy_version |
| `LedgerTransactionPosted` | 过账后 | tx_id / kind / amount / reference |
| `EscrowFunded` / `EscrowReleased` / `EscrowRefunded` | Escrow 状态变更 | escrow_id / amount / parties |
| `WorkOrderAccepted` / `SubmissionApproved` | 工作市场流转 | order_id / actor / score |
| `ContractActivated` / `ContractSettled` | 合同流转 | contract_id / amount |
| `SettlementCompleted` | 结算终局 | settlement_key / reference / amount |
| `TalentPurchased` | 人才交易结算 | listing_id / buyer / seller / amount（不含 person 资产） |

同步财务事务与异步副作用边界：**资金不变量全部在同步事务内**；事件只承载"已发生"的事实，
消费者幂等可重放。不为 CRUD 发事件。

## 36. Auditability

- 每笔资金变化都能回答：**谁、何时、多少、为什么、对应什么业务**（E16）；
- `reference_type/reference_id` + `reason` + `policy_version` 是必填语义（Reward/Contract/Settlement）；
- 账本不可修改（E17），纠错走**反冲交易**（reversal，引用被冲正 tx）；
- 观测口径全部可从账本重算（§31），缓存丢失不影响事实。

## 37. State Machines（冻结）

```
RewardGrant:   ELIGIBLE → CLAIMED → POSTED
                        ↘ VOID（过期/条件失效）

WorkOrder:     DRAFT → OPEN → ACCEPTED → IN_PROGRESS → SUBMITTED → REVIEWING
                                                             ├→ APPROVED → SETTLED
                                                             └→ REJECTED →（可重提）
               OPEN/ACCEPTED → CANCELLED | EXPIRED | DISPUTED

Contract:      DRAFT → PENDING_ACCEPTANCE → ACTIVE → FUNDED → FULFILLED → SETTLING → SETTLED
                        ↘ CANCELLED | EXPIRED | FAILED | DISPUTED

Escrow:        UNFUNDED → FUNDED → RELEASED
                            ↘ REFUNDED | EXPIRED

Settlement:    PENDING → PROCESSING → COMPLETED
                            ↘ FAILED（可重试；COMPLETED 不可重放）
```

## 38. Invariants（M1.0 正式编号，均有测试锚点）

| # | 不变量 |
| --- | --- |
| **E1** | 任何业务代码禁止直接修改余额（只能经 Ledger 过账） |
| **E2** | Ledger 是资金事实来源；余额是派生/缓存 |
| **E3** | 所有 posted Transaction 复式守恒：Σdebit = Σcredit |
| **E4** | 只有 MonetaryAuthority 可以 Mint |
| **E5** | 只有 Burn 操作减少 Total Supply |
| **E6** | Transfer 不改变 Total Supply |
| **E7** | Escrow 不改变 Total Supply |
| **E8** | 玩家之间的 WorkOrder 不允许 Mint |
| **E9** | 官方 Reward 必须带 reason + reference（可审计） |
| **E10** | 任何 Reward 不允许重复领取 |
| **E11** | Player WorkOrder 发布前必须 fully funded（Escrow 锁资） |
| **E12** | Settlement 必须幂等（同 key 不重复扣钱） |
| **E13** | 失败 Settlement 不得留下半笔账（同事务原子） |
| **E14** | 不得出现"钱扣了但业务没有完成" |
| **E15** | 不得出现"业务完成但钱未结算" |
| **E16** | 所有资金变化可追溯到业务 reference |
| **E17** | 历史账本 Entry 不可修改/删除（纠错走 reversal） |
| **E18** | 经济 ownership 不得改写 Person 的历史 provenance |
| **E19** | Talent Settlement 不允许复制 Person-owned data |
| **E20** | M1 不重新实现 T2 Recruitment（只调用） |
| **E21** | 金额必须是整数最小单位（禁止 float） |
| **E22** | 货币必须显式（`currency` 列，不在代码里硬编码币名） |
| **E23** | 玩家/公司 API 不暴露 mint/burn/treasury（三层边界） |
| **E24** | 余额不可为负（available = balance − reserved ≥ 0） |
| **E25** | Escrow 结算完成后余额必须归零 |
| **E26** | 余额语义只有一个入口（`kind → normal_side → balance_delta`），业务代码不得自行解释借贷方向 |
| **E27** | 所有账务操作汇聚到唯一 Posting Core（`LedgerService.post()`），便利原语不得自行写 Ledger |
| **E28** | CAS、Ledger 写入、Projection 更新必须在同一事务内完成（禁止跨事务两阶段） |
| **E29** | WalletProjection 必须可由 Ledger 完整重建（清空后重算逐账户逐字段一致） |
| **E30** | `reserved` 必须可由 Ledger 推导（escrow 账户余额 + 出资腿归因），不得是账本外的独立数字 |
| **E31** | LedgerEntry append-only：不提供 UPDATE / DELETE 入口，纠错走 reversal |

E26–E31 是 M1.1（账本底座）引入的落地性不变量；实现与测试锚点见
`tests/test_economy_ledger.py` / `test_economy_projection.py` / `test_economy_concurrency.py`。

## 39. M1 / M2 Boundaries

- M2 = 联网市场与真实身份注册（远端撮合、反作弊、玩家间真实经济规模）；
- M1 的 `MarketAdapter`/`SettlementService` 保持可替换抽象（本地实现先行）；
- 法币/链上/外部支付永不在本仓库范围（§3 Non-goals）。

## 40. Future Extensions（留边界，不提前实现）

`salary / equity / investment / fundraising / loan / interest / valuation / bankruptcy /
acquisition / share market / insurance / tax / dividend` —— 底层不堵死（账户/合同/账本可承载），
但**不建空壳表、不写空流程**。
