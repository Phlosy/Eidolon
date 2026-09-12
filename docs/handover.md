# Eidolon 交接文档（2026-09-09）

> 覆盖范围：P5–P11 之后的「本地开发体验 / 入职与权限开通可靠性 / 教程交互」连续修复。
> 状态基线：分支 `dev`，HEAD `430601e`，**工作树干净**，全量门禁绿（见 §5）。
> 续篇：当天下午完成「教程全流程实机走查」，见 §1.6。

---

## 1. 本次会话做了什么（按主题）

### 1.1 Makefile 开发工具链
| 目标 | 用途 | 关键约束 |
| --- | --- | --- |
| `make dev-clear-data` | 清空 `apps/server/data`（sqlite/workspaces/employees），保留 `.env` | 必须 `DATA_CONFIRM=yes` 或交互确认；`DRY_RUN=1` 只列不删；`.env` 的 `EIDOLON_DATABASE_URL` 指向仓库外则拒绝 |
| `make dev-seed-user` | 注入测试账号 **username `user` / email `user@example.com` / 密码 `user`**（argon2 同源哈希、自建 test-co 公司 + OWNER） | 幂等（重跑=重置密码）；前置 `migrate`，清库后无需先 `make run` |
| `make dev-restart-clean` | 一条龙：guard → stop → 清数据 → migrate → 建账号 → run | 需 `DATA_CONFIRM=yes`；拒绝 `DRY_RUN=1`（防"以为清了其实没清"） |

提交：`9e41c25` / `44cfeff` / `755582d`。实现：`scripts/dev_clear_data.sh`、`scripts/dev_seed_user.py`（均有回归测试 `test_dev_clear_data.py`、`test_dev_seed_user.py`）。

### 1.2 登录：用户名或邮箱（迁移 v20）
- `POST /api/v1/auth/login` 接受 `identifier`（含 `@` 按 email，否则按 username，大小写不敏感）；兼容旧 `email` 字段。
- 注册可显式传 `username`（小写字母/数字/`_`/`-`），缺省从邮箱 local part 派生，冲突自动加 `-2/-3…`。
- 迁移 **v20 `s5a7c9e1f3b5`**：`users.username`（唯一索引）+ 既有用户回填；`pending_registrations.username`。
- 未知用户名与错误密码同为 `401 invalid login credentials`（反探测）。
- 前端登录页单字段「用户名 / 邮箱」；`loginWithPassword` 按是否含 `@` 决定载荷字段。

提交：`892319d` / `5a3b2f8`。文档：`docs/authentication.md`。

### 1.3 入职 / 权限开通「卡死」的完整根因链与修复
症状演进：教程永远卡在入职第 2 步 → 多次修复后仍有 `8/11 partial` + `UNIQUE constraint failed: drive_nodes.path`。

**四层根因（全部已修）**
1. **进程死在 `engine.run` 中途** → step 永远 `running`、job 卡 `running`，前端无限轮询。
   → 启动补收敛：`workforce/access.py::sweep_stale_provisioning_jobs()` + `rerun_stale_provisioning_jobs()`（lifespan 无条件调用；与 P4d 职位层收敛同模式）。`1d9322a`
2. **flush 失败后 session 处于 DEACTIVE** → `_fail_step` 直接 commit 抛 `PendingRollbackError`，`failed` 写不进去（且异常分支里访问 `step.id` 也会炸）。
   → `_fail_step`/`_skip_step` 先 `db.rollback()`；`_run_step` 执行前预取 `step_id/job_id/provider_key`。`9c2dd03`
3. **gitea 未安装 → git 步骤 failed → job partial** → 教程 `CEO_ONBOARDED/CEO_RESOURCES_PROVISIONED` 永不满足。
   → 新增 `SkippableStepError`：未安装 → step `skipped`、job `done`、员工直接 `active`（账户留 `provisioning_state=skipped`）；装上后重试 job 即可补开。`9c2dd03`
4. **drive 目录创建 TOCTOU 竞态**（入职 job 与教程轮询/补收敛同时开同一目录，双方都读到 None 再各自 INSERT）。
   → 所有带 `path` 的创建统一走 `drive_repo.create_node` 的 **SQLite `INSERT … ON CONFLICT DO NOTHING` + 回查赢家行**（无异常、不毒化会话、任意事务内可用）。`0ffdf62`

**兜底时间线**：超时兜「活着但挂住」（`settings.provisioning_step_timeout_seconds`，默认 120s，`1be9810`）→ 启动补收敛兜「进程死亡」→ gitea 未装可跳过 → 竞态从根上消除。

### 1.4 教程交互（聚光灯）
- **操作过即免点下一步**：点击聚光灯目标（含操作卡自己的「下一步/确定」）→ `engaged` → 指引自动前进；未操作却点教学卡「下一步」→ 纯函数 `shouldRemindStep` 拦下并让光环转红脉动提醒。`2f5b24a` + `e4afaa1`（`configure_ceo_runtime` / `configure_ceo_provider` 标记 `metadata.engage_to_advance`）
- **弹窗关闭不再补跳**（本轮修复）：auto-nav effect 原在 `dialogOpen` 翻回 false 时补跳 —— 用户在弹窗里填 provider 密钥（密码管理器自动填入也会触发提交/关闭）后一关弹窗就被拽回上一步页面。现在弹窗开着即把该步骤标记为「已给过跳转机会」，关闭后绝不补跳。`e5e5690`
- **体积碰撞避让**（本轮新增，回答"游戏碰撞"的诉求）：`components/tutorial/collision.ts::choosePlacement` 对 top/bottom/left/right 四个候选做 **AABB 重叠面积打分**，保护区 = 聚光灯目标 + 所有打开的 `aria-modal` 弹窗 + 页面用 `data-tutorial-protected` 声明的关键元素，取重叠最少者；`CoachPanel` 先自选方位，再交给 floating-ui 做视口内微调（`flip.fallbackPlacements` 清空，防止翻回遮挡侧）。6 例纯函数单测。

### 1.6 教程全流程实机走查（Playwright，2026-09-09 下午）

用 Python Playwright（conda 环境，chromium headless 1440x900）把核心教程 10 步 + 实战教程 7 步**全部实机走完**（两个教程状态均 completed），逐步截图 + 几何审计（卡片 vs 聚光灯目标/弹窗/保护区的重叠面积），产物在 `tmp/tutorial-audit/`（截图 + report.jsonl + 可重跑驱动 `tmp/tutorial_audit.py`，均不入库）。

走查揪出并已修的四个真问题：
1. **补跳竞态**（`1ea38c3`）：`e5e5690` 的 `dialogOpen` 瞬时值判断有赛道 —— 表单提交时弹窗先关、进度 refetch 后到，新步骤到达时 `dialogOpen` 已翻回 false，补跳照样发生（实测被拽到 /drive）。修复：MutationObserver 记录关窗时间戳，关窗后 **2s 宽限期**内的跳转请求一律标记"已给过机会"。三种场景（取消/提交/步骤已推进后开关弹窗）实测均不再补跳。
2. **degraded 卡片居中压死弹窗**（`1ea38c3`）：`cloud_docs` 步打开「新建文档」弹窗时，兜底态卡片居中把弹窗整个盖住（145160px²）。修复：degraded/向导无锚点段落且有弹窗时，以弹窗为锚点贴侧边。
3. **弹窗锚点过期**（`430601e`）：向导翻到没有 hint 锚点的子步骤时以弹窗为锚点，但 `dialogRect` 在 render 期捕获成冻结 DOMRect —— 弹窗内容长高（如点「新建独立配置」展开表单）后没有任何状态变化触发重渲染，卡片停在旧位置压住弹窗标题栏（84170px² → 修后 2048px² 边缘贴触）。修复：`CoachPanel` 新增 `getAnchor` 惰性锚点，每次摆放重新读矩形（autoUpdate `animationFrame` 逐帧跟随，setCoords 浅比较防抖动）。
4. **`POST /drive/documents` 404**（`a5605bc`，后端）：启动期种子在无请求上下文时建 zone 根（`company_id=NULL`），带身份请求按 company 过滤看不到 → 「新建文档」404（测试环境 `AUTH_REQUIRED=false` 无 identity 过滤，所以门禁一直绿）。修复：`ensure_zone_roots` 显式传 company_id 并把无主根**收养**到当前公司名下；回归测试用 `RequestIdentity` 显式模拟带身份请求。

另：`git_setup` 路由 `/settings` → `/settings/git`（`1ea38c3`）；`data-tutorial-protected` 从"文档里的补救手段"落成真实机制（`2e379d0`，此前代码并未收集该属性）。

### 1.5 Drive 与教程文案
- 新增**原生新建文档** `POST /drive/documents`（Markdown，内容可选）+ Drive 页「新建文档」弹窗；`create_node` 增加 **parent 公司继承兜底**。`35be4bf`
- 教程 `cloud_docs` 步改为教「新建文档」，上传只是补充说明、无需真的导入（文案 zh/en 同步）。
- Drive 页工具栏改**飞书式图标下拉**：`新建 ▾`（新建文档/新建文件夹/表格·演示·画板置灰「即将支持」）与 `上传 ▾`（上传文件/上传文件夹 `webkitdirectory`）；菜单 `createPortal` 到 z-70，**浮在教程遮罩 z-60 之上**。`22eec67`
- 移除页面顶部「云文档」大标题（左侧空间栏已表明语义），内容整体上移；`cloud_docs` 步 placement 改 `left`。`22eec67`
- 通用 `Dialog` 加 `max-h` + 内容区滚动（14 寸屏 provider 弹窗确认按钮可点）。`12a9540`

---

## 2. 架构与不变量（改代码前必读）

- **单一事实**：`PositionAssignment` 是当前职位唯一真相；`CareerEvent` 仅审计；Fit/Readiness/Coverage 是派生读模型，不落库（ADR-12：派生字段不得给 `0/[]/false` 默认值）。
- **教程完成只认后端**：`app/tutorials/requirements.py` 的 `Facts` + `evaluate(requirement, facts)`，每次 `GET /tutorial` 由 `_reconcile` 推进；前端**不得**伪造完成（`acknowledge` 也只调 `complete_step` 且受 `engage_to_advance` 门禁约束）。
- **权限开通不阻塞任职**：分配成功只发事件；`workforce/access.py` 消费者算 desired state。开通失败**绝不能**回滚任职。
- **drive 目录创建**：必须走 `drive_repo.create_node`（带 `path` 自动 ON CONFLICT 幂等）；**不要**再手写 `DriveNode(...)` + `flush`。
- **SQLite 注意**：写事务期间其他会话的读会阻塞写（`database is locked`）；因此不要在请求会话持有读事务时另开 writer 会话写库（曾试过，已回退）。所有幂等创建走单会话 + ON CONFLICT。
- **教程引擎导航**：`navigated` ref 每步最多自动跳转一次；弹窗开着 → 标记跳过（防补跳）；`EXCLUDED_ROUTES`（像素办公室/独立评审页）不叠遮罩。

---

## 3. 迁移链与数据

```
v14 m8b1d4e7f063 → v15 n9e8d7c6b5a4 → v16 o1f2e3d4c5b6 → v17 p2e4a6c8d0f3
→ v18 q3f5a7b9c1d2 → v19 r4a6b8c0d2e4 → v20 s5a7c9e1f3b5（users.username）
```

- `alembic check` 无漂移；**本地开发库已升到 v39**（… / v37 合同 / v38 人才条款 / v39 NPC 经济）。
- 新增模型列一律遵守仓库既有约束：SQLite 不给既有表加 FK（服务层校验）；派生字段不加默认值。

---

## 4. 已知问题 / 技术债

| 项 | 说明 | 建议 |
| --- | --- | --- |
| gitea 未安装 | 本地无 docker 容器 → `git:*` 步骤 `skipped`（透明提示，不阻塞入职）。要 git 权限需 `make runtime-pull` + 启动 builtin gitea，再对 job 点 retry | 需要时再装；不要为了过教程假造 git 成功 |
| 上传文件夹 | 当前平铺上传所选文件夹内的文件，不保留子目录层级 | 需要时按 `webkitdirectory` 的 `webkitRelativePath` 逐层建目录 |
| 新建表格/演示/画板 | 菜单项置灰「即将支持」（无后端格式，未造假模板） | 有格式设计后再点亮 |
| 6 个既有 WIP 未格式化文件 | `ruff format --check` 期望恰好这 6 个红：`employees.py`、`auth.py`、`providers.py`、`test_authentication.py`、`test_employee_providers.py`、`test_providers.py` | **不要**顺手格式化 |
| P6.1 flaky | `test_updates.py::test_managed_update_success` 全量套跑偶发、单跑必过（历史记录在 `docs/evidence-pipeline.md`） | 未修；勿用 sleep/retry 掩盖 |
| 教程后段遮挡 | ~~未人工过一遍~~ 已实机走查完毕（§1.6），17 步全部无功能性遮挡 | 剩余仅边缘贴触级重叠（<3000px²），不挡任何控件 |

---

## 5. 环境与验证基线

- 环境：conda `eidolon`（Python 3.12）→ `/Users/ponk/miniconda3/envs/eidolon/bin/python`；前端 pnpm。
- 服务：`make run`（后台，日志 `.run/server.log` / `.run/web.log`，端口 26881/26880）。
- **一键重置到可测试状态**：
  ```bash
  make dev-restart-clean DATA_CONFIRM=yes     # 清库 → migrate → 注入 user/user → 起服务
  ```
- 门禁（改动后必须全绿）：
  ```bash
  pytest apps/server/tests -q        # 期望 1018 passed / 6 deselected（M1.10 起，M1 FROZEN）
  ruff check apps/server/app apps/server/tests
  ruff format --check apps/server/app apps/server/tests   # 只允许 5 个既有 WIP 红
  cd apps/server && alembic check    # No new upgrade operations detected
  cd apps/web && pnpm exec tsc --noEmit && pnpm exec eslint . && pnpm exec prettier --check .
  cd apps/web && pnpm exec vitest run    # 期望 78 files / 328 passed
  cd apps/web && pnpm build
  ```
- 数据库：`apps/server/data/eidolon.db`；快速查 job：
  ```bash
  sqlite3 apps/server/data/eidolon.db "SELECT id,kind,status,done_steps,total_steps FROM provisioning_jobs;"
  sqlite3 apps/server/data/eidolon.db "SELECT seq,provider_key,status,substr(COALESCE(error,''),1,60) FROM provisioning_steps ORDER BY seq;"
  ```

---

## 5b. T2 人才市场（已完成并冻结，2026-09-11）

- 领域设计：`t2-talent-market-design.md`（§2 术语 / §4 三轴 / §6 公开投影 / §8 Adapter /
  §9 Person 读面 / §10a–§10f 各阶段落地形态与冻结清单）
- 执行基线：`t2-implementation-plan.md`（§4 T2.0–T2.8 拆解 / §5 API / §6 schema /
  §7 事件 / §13 Golden Path 26 步 / §14 验收 A–D / §16 Progress）
- 迁移：v29 市场核心（participants + listings）、v30 培养参数（training_programs.metadata_json）、
  v31 NPC 成交、v32 账本、v33 奖励、v34 官方市场、v35 托管、v36 经营成本、v37 合同、v38 人才条款、v39 NPC 经济；当前 head `64fec2d13d9b`
- 关键不变量（均有测试）：I1–I13（身份不变/不复制人级资产/历史 provenance 不可改写/
  只 active 挂牌可招募/一人一 employee/市场投影白名单/培养态只存 cultivating-ready…）
- 常用命令：`make market-issue ISSUE_ARGS="--tier rare --count 2"`、`make market-npc NPC_ARGS="--dry-run"`

## 5c. M1 经济与合同系统（**M1.0–M1.10 全部完成，M1 FROZEN**，2026-09-11）

- 领域设计：`m1-economy-design.md`（Vision / 货币供给与 Source-Sink / MonetaryAuthority /
  EconomicActor / Account / 复式账本 / Currency / Reward / 救援经济 / WorkOrder / 官方与玩家工作市场 /
  Evaluation / Contract / Offer / Escrow / Settlement / 公司经营 / 算力成本 / 人才商业化 /
  Ownership 裁定（§28）/ NPC / 政策 / 观测 / 安全三层 / 并发幂等 / 事件 / 可审计 / 状态机 / **E1–E25** / 边界）
- 执行基线：`m1-implementation-plan.md`（M1.0–M1.10 拆解 / §5 迁移路线 v32–v39 / §6 API 三层 /
  §13 Golden Path / §14 验收 A–F / §16 Progress）
- 现状：**M1 已冻结**（M1.0–M1.10 全 DONE，v32–v39，无迁移收尾）；冻结面见设计 **§39b**：
  Schema / 不变量 E1–E31 / 钱的口径 / 唯一写入路径 / 三层 API / T2 接入缝 / 模块边界 / 政策 / 关键裁决；
  **T2 是硬门禁**：触碰人才/招募/NPC 后必跑
  `test_t2_golden_path` / `test_recruitment` / `test_market_*`
- M1 收尾证据：`test_m1_golden_path.py`（闭环 E2E + 全库复式平衡）、`test_m1_hardening.py`
  （失败注入/并发矩阵：钱不动 + 状态一致）、`test_m1_invariants.py`（E1–E31 锚点表）
- **下一步（M2，不在 M1 范围）**：政策中心（表化/审计/灰度）、多币种、联合出资 Escrow、
  争议仲裁、股权分红、NPC 出售与发布、真实 provider 成本映射
- 经济界面（M1.9）：`/economy`（余额/收支分类/流水/我的钱包/奖励领取）、`/work-orders`（在招/我承接 +
  领取 + 提交）、`/contracts`（接受/交付并结算/取消 + 多腿结算明细）；i18n `economy`/`workOrders`/
  `contracts`（中英逐键一致，有测试）
- 管理员观测（M1.9）：`make economy-stats`（supply/奖励/算力/NPC/状态计数）、
  `make economy-check`（一致性巡检）、`make economy-policy-reload`
  （+`POST /economy/admin/policy/reload`，需 `EIDOLON_ECONOMY_ADMIN_ENABLED=true`）
- 契约代码：`app/economy/{contracts,policy}.py`（枚举值、金额整数、腿蓝图、守恒校验、
  `balance_delta` 单入口、`requires_funds`、状态机、政策 DTO）+ `Settings.economy_*` / `.env.example`
- 账本底座：`app/models/economy.py`（4 表）+ `app/repositories/economy.py`（幂等开户 / CAS / 账本聚合）
  + `app/services/economy/{accounts,ledger,monetary,balances,projection,authority}.py`
  + 只读 API `app/api/v1/economy.py`（balance / accounts / transactions，公司作用域，**无写端点**）
- 运维：`make economy-verify`（只读对账）、`make economy-rebuild`（由账本重建投影）、
  `make economy-supply`（供给快照）；CLI `scripts/economy_wallets.py`
- 奖励（M1.2）：`app/models/economy.py::RewardGrant` + `app/services/economy/rewards.py`
  （7 类自助奖励 + 资格判定 + 幂等领取）；API `GET /economy/rewards`、
  `POST /economy/rewards/{type}/claim`；政策在 `Settings` + `policy_version` 快照（**不建政策表**）；
  官方类奖励不可自助领取（白名单 `SELF_SERVICE_KINDS`）
- 官方工作市场（M1.3）：`app/models/economy.py`（WorkOrder/Submission/Evaluation）+
  `app/services/economy/{work_orders,evaluations,settlement}.py`；玩家 API
  `GET /work-orders`、`POST /work-orders/{id}/accept|submit`；管理面（发布/验收/结算/过期）
  走 `scripts/work_orders.py` + `make work-order-*`（**不进玩家 router**，§32）；
  官方发行预算内：`official_max_reward` / `official_outstanding_budget`
- 托管与玩家市场（M1.4）：`escrows` 表 + `app/services/economy/escrow.py`；玩家订单
  `POST /work-orders`（发布前锁资，E11）/ `POST /work-orders/{id}/cancel`（退款）；
  玩家之间的钱**绝不 mint、不写 reward_grants**（E8）；release-vs-refund 竞争由 Escrow 状态 CAS 裁定
- 经营成本（M1.5）：`compute_usage` 表 + `ledger_transactions.category`；
  `app/services/economy/costs.py`（CompanyCostService / ComputeCostService / FeeService）、
  `app/services/economy/consumers.py`（培养成本事件消费者，默认关）；
  报表 `GET /economy/overview` + `GET /economy/compute-usage`；CLI `--overview` / `--compute`；
  成本扣款用 **SAVEPOINT** 隔离（失败不留半笔账）；余额不足记 **unpaid**（不是免费）
- 合同（M1.6）：`contracts` / `offers` 表 + `app/services/economy/contracts.py`
  （ContractService / OfferService）；API `GET|POST /contracts`、
  `POST /contracts/{id}/accept|fulfill|cancel|fail`；托管权威指针 `escrows.contract_id`；
  **创建即锁资**、**履约即结算**（多腿：净额 + Treasury + Burn，`contract_fee_bps`），
  取消/失败/过期 ⇒ 退款；结算与退款没有独立玩家端点
- 人才商业化（M1.7）：`talent_commercial_terms` 表 + `app/services/economy/talent_trade.py`
  （TalentTradeService：条款/出价/成交）；API `GET|POST /market/listings/{id}/commercial-terms`、
  `POST|GET /market/listings/{id}/offers`、`POST /market/offers/{id}/accept`
  （新 router，T2 market.py 不动）；成交 = 合同锁资 → **T2 招募（commit=False）** → 多腿放款，
  同一事务，失败整笔回滚；**system 承接方（人才卖方）⇒ 成交款进 Treasury**
- NPC 经济（M1.8）：`npc_economic_profiles` 表 + `app/services/economy/npc_economy.py`；
  CLI `make npc-economy-status|inject|run`（**没有玩家路由**）；**注入是唯一 mint 入口**
  （受 `budget_cap` 封顶），NPC 出手只是转移；成交复用 T2 `NpcMarketService.take_candidate`
- 纪律：Ledger 是事实来源、钱包投影可删可重建、reserved 只能由账本归因推导（E26–E31）、
  所有资金变化只能经 `LedgerService.post()`、只有 `MonetaryAuthority` 能 mint/burn（令牌守卫）、
  奖励金额只来自政策（claim 无金额入参）

## 5d. M2 工作与组织运行域（**M2.0 已完成**，2026-09-11）

- 领域设计：`docs/m2-agent-work-runtime-design.md`（最高原则 `System provides facts. Agent makes decisions.` /
  Position = Responsibility + Authority + Expectations / RoleContext / Role Resource Index /
  Institutional vs Personal Memory / DecisionRecord / Project-Task-WorkOrder 边界 /
  **W1–W31 不变量** / M2-ADR-1..10）
- 执行基线：`docs/m2-implementation-plan.md`（M2.0–M2.10 拆解 / §14 验收矩阵 / §15 横切要求 / §16 Progress / §17 交付证据 / §18 风险）
- 事实基线：`docs/current-system-audit.md`（Repository Audit：Domain Map / 真实业务链 / Top 10 gaps / 重复真相风险）
- 契约代码：`app/work/contracts.py`（纯契约层，**不碰 Session、不建表、不发事件**）+ 8 个枚举进 `app/models/enums.py`
- 守卫：`tests/test_m2_contract.py`（46 个；含 AST 守卫 + 不变量锚点表；已做 4 组反例注入验证）
- 迁移：**无**（head 仍 `64fec2d13d9b` / v39）；M2.0 commit：`366c540`
- **M2 最重要的三条纪律**（改 M2 代码前必读）：
  1. 系统**不得**替公司决定「接什么任务 / 怎么拆 / 选谁 / 是否返工 / 是否采购 / 是否辞退」（W1/W2/W3/W14/W17）；
  2. 职位是 `Responsibility + Authority + Expectations`，**不是** prompt / workflow / skill package；
     任命**永不**授予能力、**永不**复制前任的人级资产（W4/W7/W8/W26）；
  3. 不新建 Mission / Agent SoT；`Project` 是唯一执行根，`WorkOrder` 只是商业包装（W20/W21/W22/W23）。
- 下一步：**M2.2 Role Context & Adaptive Onboarding**（见 plan §5）。

---

## 5e. M2.1 Canonical Executable Project Spec（**DONE**，2026-09-11）

- 迁移 **v40** `a1c2e3f40517`（纯 additive）；`upgrade → downgrade → upgrade` 实测；head = v40
- **唯一立项入口** `services/projects.create_project()`，两个**正交**维度：
  `work_mode`（产品：guided | managed）+ `planning_fixture`（基础设施：none | deterministic_template）
- **三个产品拍板**（写进设计 §17 ADR-11..15）：
  1. **D1** Work Intake = **公司可配的责任路由**（默认 CEO，可配 COO/PM Lead…）：
     `Company.settings["work_routing"]` → 职位 code → PositionSlot → 生效 PRIMARY 任职。
     解析不到 ⇒ `ProjectStatus.waiting_for_management`（**零任务零规划**），提示 Owner；
     **系统绝不随便挑人、绝不代管规划**（W32/W34）
  2. **D2** 新公司 `guided` → 首次真实项目完成后公司默认转 `managed`；`work_mode` 是**项目级快照**，
     公司默认变化不改写既有项目（W35）；两种模式差别只有 **human involvement**，不是决策归属（W36）
  3. **D3** 确定性模板**保留但降级为 Test/Tutorial/CI Fixture**（`DETERMINISTIC_TEMPLATE_PLAN`）：
     只能**显式请求** + `EIDOLON_ALLOW_PLANNING_FIXTURES=true` 门控（未开启 ⇒ 422，不静默降级）；
     生产**永不** fallback（W33）。`GRAPH_TEMPLATE` 这个名字已退役
- 新读面：`GET /projects/{id}/spec`（回答 8 个问题）、`GET|PATCH /company/work-policy`
- **改了行为的地方（有意）**：`POST /projects` 只传 `{name, description}` 不再隐式跑固定模板；
  CI/教程/测试改用显式 `planning_fixture`，生产改走 managed 路由并停在等待管理动作
- **测试纪律**：想只要"干净 Project 容器"的用例用 `no_work_intake` fixture（停在
  `waiting_for_management`），避免后台 mock 派发污染断言
- 前端：项目详情新增工作模式面板（模式 / 责任职位 / `waiting_for_management` 引导 / 负责人 stale 提示）
- 下一步：M2.2 RoleContext（派生读模型 + Authority Projection + Role Resource Index）

---

---

## 5f. M2.2 Role Context & Adaptive Onboarding（**DONE**，2026-09-11）

- 迁移 **v41** `b2d4f6a8c013`（纯 additive）；`upgrade -> downgrade -> upgrade` 实测；head = v41
- **用户拍板（方案 A，八条）** → 设计 §17 M2-ADR-16..21 + W37–W42：
  1. 新增薄表 **`position_authority_grants`** 承载管理授权；
     `position_definition_packages` **保持**资源开通语义（不许混）
  2. Authority **default-deny**，随 Active PositionAssignment 生效/失效，**不是** Person 资产
  3. 权限检查**绝不**读 `employee.role`（也不读 Fit / 分数 / 访问包）—— AST 守卫钉死
  4. 作用域只有 `company` / `department` / `direct_reports` + `spend max_amount`；**不建 ABAC**
  5. 系统只**校验**「在不在授权内」，**不**替 Agent 选人/排序/判断该不该做
  6. **append-only + 时间窗**，双摘要 `grants_hash`（这次凭什么）/ `position_grants_hash`
     （当时手里有什么）—— 历史 DecisionRecord 可解释「当时为什么有权」
  7. `offboard` / `release_position` 不允许作用于自己（硬安全约束，不是管理判断）
  8. 金额授权**没有上限不等于不限**，而是「无法确认在授权内」⇒ 拒绝
- **Role Resource Index** 只存**指针**（`knowledge_items` / `drive_nodes` / `companies.settings`）；
  解析结果三态 `resolved` / `advisory`（skill_hint 按设计无内容）/ `missing`（如实报告，不造假）
- 新读面：`GET /employees/{id}/role-context`（只读；**不含**任何 score/level/rank）
- 事件：任职变化 → `role.context_available` / `role.context_withdrawn`（**只带事实**，
  不发「请去学习」的系统指令）；受 `EIDOLON_ROLE_CONTEXT_EVENTS` 门控（测试默认关）
- 冷启动种子 `app/work/authority_seed.py`：CEO 10 项授权 / PM 2 / QA 1 / 研究·工程 **0**；
  金额上限来自政策 `EIDOLON_AUTHORITY_DEFAULT_SPEND_LIMIT`
- **踩过的坑（务必别重复）**：迁移里的 `server_default` 必须写成 `sa.text("'company'")`；
  普通字符串会被再包一层引号，SQLite 里存成带引号的字面量，读回来 JSON 解析直接炸
- **测试纪律**：授权测试必须用 `_authority_lab()` 开**独立职位定义** ——
  否则 append-only 的 grant 会被别的用例「合法地」兜住，结果依赖执行顺序
- 下一步：**M2.3 Management Agent Tooling**（工具面声明 `mode / authority_required /
  hard_constraints`；写工具走 `authority.requires()` 并把 `authority_snapshot()` 交给
  M2.4 的 DecisionRecord）

---

---

## 5g. M2.3 Management Agent Tooling（**DONE**，2026-09-11）

- **无迁移**（head 仍 v41）；新增 `TaskStatus.blocked` / `cancelled`（字符串列，加值不改表）
- **用户拍板（方案 3 修正版：Read Shared / Write Internal）** → 设计 §14b + M2-ADR-22..27 + T1–T12：
  1. **读能力共享**：读工具与 HTTP 读面复用同一批 QueryService/ReadModel；
     **不**新增 `/tools` 读路由
  2. **写能力只在内部执行面**；人类管理动作走各领域自己的正式 API，
     二者调用**同一个** application/domain service（`UI rules == Agent rules`）
  3. **禁止**通用玩家 `POST /api/v1/tools/*`（有 AST/路由守卫）
  4. Tool Registry 自描述：`side_effect` / `required_authority` / `authority_target` / schema / handler；
     写工具**缺任一声明就在注册时炸**
  5. **Transport 不代表信任**：内部面照样完整 Authority 校验；调试口只解决"谁能发起"
  6. **Actor 身份由上下文注入**（`WorkSession` / `context_for_employee`），
     参数里出现 `actor_*` / `company_id` 等身份字段**一律拒绝**
  7. 系统只**校验并应用** Agent 已做出的决定；Fit 只作前置读事实（无排名、未知不当 0）
  8. Side-effect 三级 `READ / WRITE / HIGH_IMPACT`；M2.3 **不注册**任何 high_impact 工具
  9. **Authority ≠ Autonomy**：只冻结边界；`requires_confirmation` 在 M2.3 **拒绝执行**
 10. 调试口默认关（`EIDOLON_AGENT_TOOL_CLI_ENABLED=false`），走同一段执行代码
 11. 每次工具调用都留审计（读也留，T12 字面要求）
- **工具清单**：读 15 个 / 写 9 个（`make agent-tools` 可列全）
- **写工具授权映射**：工作图（建/改/连依赖/阻塞/取消/请评审）→ `plan_project_work`（M2.3 新增）；
  派活 → `assign_task`；返工 → `request_rework`；项目委派 → `delegate_management`
- **诚实边界**：`request_review` 只做状态推进 + 留痕 + 事件，返回值明说
  `review_entity: deferred_to_M2.7`（ReviewRequest 实体是 M2.7 的）
- **踩坑记录（务必别重复）**：`SessionLocal(expire_on_commit=False)` 下 ORM **关系属性会保持旧值** ——
  依赖图必须从**表**读（`repositories.project.list_dependencies`），
  否则同一会话里第二次建边时环检测会漏（实测踩到）
- 测试纪律：授权行为用例用 `_lab()` 开**独立职位定义**（M2.2 的同一条纪律）
- 下一步：**M2.4 Leadership Planning & Delegation**（`decision_records` + `DecisionService`；
  写工具的 `authority_snapshot()` 直接落进 `DecisionRecord.context_snapshot`）

---

---

## 5h. M2.4 Leadership Planning & Delegation（**DONE**，2026-09-11）

- 迁移 **v42** `c3e5a7b9d124`（纯 additive：`decision_records` + `tool_audits`，**无回填**）
- **用户拍板（方案 3 + Decision Envelope）** → 设计 §10 + §14c + M2-ADR-28..35 + DR1–DR10：
  1. **三层不混**：`DecisionRecord`（为什么）/ `ToolAudit`（执行了什么）/ domain state（真相）
  2. **tool call ≠ decision**：一条决策 → N 个动作
  3. 关联**单向**：`ToolAudit.decision_id → DecisionRecord.id`；不建 `audit_ids[]`
  4. **Decision Envelope**：一次提交 `{type, reason, intended_outcome, scope, context, actions}`
  5. `DecisionRecord` **不复制** tool 名/入参/出参/错误（那些在 `tool_audits`）
  6. `decision_semantics`（`none`/`optional`/`required`）是**工具属性**，注册时强制：
     READ=none；`create_task`/`create_dependency`/`assign_task`/`delegate_project`/
     `request_rework`/`cancel_task`=required；`update_task`/`request_review`/
     `mark_task_blocked`=optional
  7. `parent_decision_id` 表达 CEO → CTO → Lead；**不建** workflow 模型
  8. 状态：`PROPOSED/EXECUTING/APPLIED/PARTIALLY_APPLIED/FAILED/SUPERSEDED`
     （`DecisionOutcome` **已退役** —— 结果只由 `DecisionStatus` 表达）
  9. 不假定整个决策是一个事务：意图**先提交**，动作逐个执行（可跨阶段）
 10. 决策**不授予权限**；每个动作重新走 Authority（`authority_json` 只是证据）
 11. 上下文是**有界键集**快照 + 稳定引用 + 哈希（`DECISION_CONTEXT_KEYS`），不是库副本
 12. 只留 Decision → Outcome 可追踪；**不做** CEO/CTO 能力评分
- **只读读面**：`GET /decisions`（按 project/task/status 过滤）、`/{id}`、
  `/{id}/tool-audits`（**反查**执行事实）、`/decisions/stats`；**没有写端点**
- **工具事实改落 `tool_audits`**（不再写 `audit_logs`）：需要按 `decision_id` 反查，
  JSON blob 里没有可索引列；`audit_logs` 继续承载人/领域动作
- **踩坑记录（真实 bug，务必别重复）**：执行面原来的 `db.rollback()` 会把**调用方**
  （决策信封）已经写下的东西一起抹掉 —— 一个动作被领域拒绝，整条 `DecisionRecord`
  与前序成功动作全部消失。修复：**SAVEPOINT**（`db.begin_nested()`）只回滚该 handler，
  且决策意图**先提交**再执行动作。对应用例补了"成功的动作必须真的留在领域状态里"
- **测试纪律**：`_lab()` 现在**幂等**（`org_snapshot` 不删测试期间新建的职位定义，
  同一 code 会被多个用例复用）—— 重复调用不再 409
- 下一步：**M2.5 Dynamic Task Graph Runtime**（`GRAPH_TEMPLATE` 退出业务真相；
  `Orchestrator` 变纯调度器；`validate_task_graph` / `resolve_ready_tasks` 接进运行时）

---

## 5i. M2.5 Canonical Task Graph Runtime（**DONE**，2026-09-12）

- **无迁移**（主线仍是 **v42** `c3e5a7b9d124`）：这一阶段是**语义收口**，不是加表
- **用户拍板**（Plan 1 + 事件化 + 例外升级）→ 设计 **§14d** + 不变量 **R1–R12**：

```text
Manager chooses. System schedules. Worker executes. Manager intervenes only when judgment is required.
```

  1. **决策边界**：管理 = 建哪些 Task / 依赖 / 谁负责 / 改派 / 取消 / 重规划；
     系统 = 依赖满足与否 / 是否就绪 / 能不能派 / 派给谁（**只能是已存在的负责人**）
  2. **规范流程**：`resolve_ready_tasks` → `task.ready`（事实）→ 可派发判定 →
     派给**它自己的** assignee → WorkSession → Runtime
  3. **结构就绪 ≠ 可派发**：前者是纯结构事实，后者要查人/运行时/项目状态
  4. **就绪但没负责人** ⇒ `task.assignment_required`，**绝不**自动挑人
  5. **负责人不可用** ⇒ 报原因并等待，**绝不**自动改派
  6. **guided / managed / fixture 共用一个运行时**，没有 per-mode 派发器
  7. **Decision-needed 事件集封闭**（6 个）：`task.assignment_required` /
     `task.runtime_unavailable` / `task.blocked` / `task.failed` /
     `task.review_failed` / `project.replan_required`
  8. **Manager Agent 不是调度器**：普通推进不唤醒它（正常 DAG 推进不产生 DecisionRecord）

- **新增/重写**：
  - `app/work/dispatch.py`：**"System schedules" 的唯一实现**（就绪适配 + 可派发判定 + 例外上报）
  - `app/work/planning_fixture.py`：确定性模板搬出编排器（6 阶段整图，**建完即退出**）
  - `app/workflow/orchestrator.py`：**纯调度器**（删掉模板 / 建图 / `kind` 分支 / `_unblock_dependents`）
  - `app/work/contracts.py` §10b：两类原因集 + 封闭事件集 + **R1–R12**（注册表 76 条）
  - `tests/test_m2_dag_runtime.py`：16 条（12 锚点 + 4 反例注入）
- **fixture 项目行为变化**：立项时一次性建好 6 阶段图（Intake/Planning/Discovery/Build/
  Verify/Release），项目一开始就是 `in_progress`（旧实现是分步生成 → 依赖 `kind` 推进）
- **managed 项目**：Manager Agent 建出**第一个任务**时项目才 `requested/planning → in_progress`
  （这是结构事实，不是管理判断）
- **踩坑记录（真实 bug，务必别重复）**：`Orchestrator._running[employee_id]` **泄漏**。
  旧写法把"释放键"写在 `_run_task` 的 `finally` 里，用局部 `employee_id`；而那里有若干
  **早退分支**（任务被别处改状态、员工不 idle）会在赋值**之前** return → 释放成了 `pop(None)`
  → 该员工被**永久跳过**，后续所有 fixture 项目都卡在同一个环节（本次实测卡在 researcher）。
  修复：把释放挂在**会话任务的生命周期**上（`add_done_callback`），而不是挂在协程内部逻辑上
- **第二条踩坑**：失败任务**不能**自动重跑。契约把 `failed` 列为"就绪候选"（结构上确实如此），
  但运行时若顺着这条去派，就会无限重试 + 悄悄重做管理决策。M2.5 把 `task_failed` 归入
  **需要管理决策**的原因（发 `task.failed`，等管理层决定重做/改派/改方案，R10）
- **坏图 fail-closed**（W16，设计 §14d.8）：图有环/悬空依赖 ⇒ 权威就绪口径给**空集**、
  上报一次 `project.replan_required`（带结构问题清单）、**不挑着跑**看起来没问题的那部分。
  注意 `validate_task_graph` 的"重复边"分支在库里**不可能出现**
  （`task_dependencies` 有 UNIQUE(task_id, depends_on_id)），它是给内存图用的防御
- **反例注入验证**：**13/13** 条注入全部被守卫拦住（注入生产代码 → 目标用例转红 → 还原 → 转绿），
  其中一条专门验证"绕过图校验 ⇒ 坏图被调度"会被拦住
- 下一步：**M2.6 Artifact Handoff & lineage**

---

## 5j. M2.6 Artifact Handoff & Shared Work Context（**DONE**，2026-09-12）

- 迁移 **v43** `43a70cd19cc7`（additive + 事实驱动回填）：
  `drive_nodes.task_id` / `tasks.produces_json` / `task_inputs` / `artifact_links`
- **用户拍板语义**（计划 §9 + 设计 **§14c**）→ 不变量 **H1–H8**（注册表 84 条：W42/T12/DR10/R12/H8）
- 一句话：**让 A 的输出真正成为 B 的输入**，且可追溯

```text
Manager 声明：B 要用 A 的产品   ← task_inputs（指向 Task，不是 artifact）
A 跑完 → 产物落 Drive          ← drive_nodes.task_id = A（产出归属）
B 就绪 → 系统**运行期解析**输入  ← 有界内容摘要进 TaskContext / prompt
B 开工 → 记下被谁在哪次会话用掉  ← artifact_links（role 只有 consumed_by）
```

- **四条硬纪律**：
  1. **不建第二套 Artifact 系统**（H1）：内容/版本/sha256 仍在 Drive；
     工作域只加"归属"与"使用"两类事实
  2. **声明 ≠ 引用**（H4/H5）：声明指向上游 **Task**，硬约束是**顺序保证**
     （上游必须是 DAG 祖先，否则 422）；产物由系统在**运行期**解析
  3. **只消费已完成的产出**（H8）：在跑的产物永不被引用（服务层 + HTTP 422 + 工具拒绝）
  4. **同一个事实不留两个落点**（H3）：产出归属只有 `drive_nodes.task_id` 一处，
     链接表只记使用（`ARTIFACT_LINK_ROLES == {"consumed_by"}`）
- **读面**：`GET /tasks/{id}/artifacts`（产出 / 使用 / 声明 / 上游链 depth≥2）；
  Agent 读工具 `list_task_artifacts` 与它**同一份**事实
- **写面**：`POST /tasks/{id}/inputs`、`POST /tasks/{id}/artifacts/{aid}/consume`（越界 422）；
  Agent 工具 `consume_artifact`（decision_semantics=**required**）+ `create_task.consumes/produces`
- **踩坑记录（真实 bug，务必别重复）**：`drive_repo._create_node_collision_safe()`
  用**显式白名单**拼 values 做 `ON CONFLICT DO NOTHING` 插入 —— 新列 `task_id`
  不在白名单里就被**静默丢掉**（不报错、字段为 NULL）。产物归属全丢就是这么来的。
  教训：凡给 `drive_nodes` 加列，必须同时改这条白名单；H2 的守卫就是为这种"静默丢失"写的
- **第二条**：`consume_artifact` 是 required 语义 ⇒ 工具面**必须**经决策信封，
  裸调用会被 DR7 拦住（`decision_required`）。测试因此走 `submit_envelope`，
  拒绝理由落在 `tool_audits.error`（决策行不复制执行细节，DR5）
- **输入缺失**（声明的上游 done 却零产出）⇒ 不派发、发一次 `project.replan_required`：
  复用 M2.5 的**封闭**事件集，**没有**新增事件（只新增一条原因 `input_artifacts_missing`）
- **反例注入验证**：**11/11** 条（归属丢失 / 白名单吞列 / 不记会话 / 第二套产物表 /
  声明当引用 / 不校验祖先 / 只给指针 / lineage 只走一跳 / 未完成也能引用 ×2 / 静默派发）
- **测试纪律**：M2.3/M2.4 的"工具面清单 + 决策语义"是**钉住的** —— 加工具必须显式改那两条
  （本次：`consume_artifact` required、`list_task_artifacts` read）
- 下一步：**M2.7 Review / Rework / Replan**

---

## 5k. M2.7 Review / Rework / Replan（**DONE**，2026-09-12）

- 迁移 **v44** `b3aee926425c`（additive）：`review_requests` / `review_facts` / `tasks.rework_count`
- **用户拍板语义**（计划 §10 + 设计 **§12.4**）→ 不变量 **RV1–RV8**（注册表 92 条；enforced 80）
- 一句话：**Worker 完成后不再自动 `done`**

```text
Worker 干完            → task 停在 in_review（系统不替谁通过）
Manager/Requester 发起 → review_requests(open, reviewer=**指定的人**)
系统                   → 收集事实（review_facts：产物/声明差异/交接/会话/返工次数）
Reviewer Agent 出结论  → PASS / REWORK / REJECT / ESCALATE
系统按**显式映射**落地  → REVIEW_VERDICT_TARGETS（ESCALATE 没有目标，停下来等人）
```

- **唯一的结论 → 状态映射**：`PASS→done` / `REWORK→todo(+计数)` / `REJECT→rejected` / `ESCALATE→无`
- **事实与结论分开存**（RV3）：`review_facts` 是系统写的（**没有**判断词），
  `review_requests.verdict` 是评审人写的；**追加式**（改判走新请求）
- **系统仍然不做**：不产生结论、不替管理层指定评审人、不自己 replan（RV7）、
  不把评估/结算/能力枚举拿来当任务级评审（W29/RV8）
- **替身评审**（`app/work/review_fixture.py`）：与 `planning_fixture` **同款门控**
  （`allow_planning_fixtures` + `planning_fixture=deterministic_template`），
  结论**署在项目负责人名下**、理由写明是替身、走**同一段**评审服务；
  替身失败 ⇒ 任务留在 `in_review` 等管理层，**绝不**因此自动通过
- **踩坑记录（真实 bug，务必别重复）**：**事件必须在事务提交之后发**。
  `bus.publish` 用**另一个数据库连接**写 `events`；在写事务还没提交时发事件，
  SQLite 的单写者直接 `database is locked`（而且会等满 15s busy_timeout）。
  第一版替身评审把"发事件"塞在评审事务里 → 每个任务卡 15s，整条 fixture 链跑不完。
  修法：替身评审拆成**两段独立短事务**（发起 / 出结论），每段各自提交后再发事件
- **第二条**：`project_runtime_state` 新增 `work_finished`（"全干完、含等评审"）——
  `all_tasks_done`（严格完成）与它**不能混**：等评审不算完成（项目不能据此交付），
  但此时确实没有可调度的工作（项目该回 `planning` 等管理层，而不是卡在 `in_progress`）
- **第三条**：`SessionLocal` 的**长事务读者会挡住写者**（rollback journal 模式）。
  测试里"用 `db` 会话轮询后台进度"会把 15s busy_timeout 直接吃满 ——
  后台跑图时用 HTTP 轮询（每请求一个短事务），别用常驻会话读
- **反例注入验证**：**12/12** 条（自动通过 / 事实里写判断词 / 结论行塞事实 /
  代签 / 改判 / 不计数 / ESCALATE 被翻译成状态 / 谁都能 replan / 反向映射 /
  替身不门控 / 替身绕过状态机 / 结论工具去掉 required）
- 下一步：**M2.8 Recruit → Ready-to-Work**

---

## 5l. M2.8 Recruit → Ready-to-Work（**DONE**，2026-09-12）

- **无迁移**（主线仍 v44 `b3aee926425c`）：全部复用既有表 + `companies.settings`
- **用户拍板语义**（计划 §11 + 设计 **§8b**）→ 不变量 **RD1–RD7**（注册表 99 条；enforced 87）
- 一句话：招募得到的人**能立刻执行 Agent Task**

```text
招募 → Employee → PositionAssignment → RoleContext
     → 环境编排（工作区目录 / 运行时实例 / 供应商绑定）
     → READY_TO_WORK（**派生量**，不是一列）
```

- **四项事实**（逐项可核对，RD2）：`position`（生效主职→编制→定义）、
  `workspace`（路径 + 目录真的存在）、`runtime`（实例状态/健康）、
  `provider`（主绑定 + provider enabled）
- **`READY_TO_WORK` 与执行门禁刻意分开**：门禁只查"跑起来真的需要"的项
  （mock ⇒ 不额外要求；真实运行时 ⇒ 工作区/运行时/供应商）。
  `position` 是软契约（W5），它在报告里、但**不在闸门里** —— 缺编制不该等于"永远不能干活"
- **公司运行时策略只配环境**（RD4/I6）：允许 `runtime_type` / `deployment_mode` /
  `provider_id` / `model` / `runtime_config`；人格 / 提示词 / 工作流 / 技能 / 职位行为
  一律 **422**（未知键也 422：静默忽略会让"配了没生效"变成谜）
- **谁说了算**：`runtime_type` 员工行权威（`gateway.adapter_for` 读它）、
  招募时由策略写入；`runtime_config` 员工优先、否则继承公司策略；provider/model 只在公司策略
- **失败不四舍五入**（RD5/I2）：单步失败 ⇒ job `partial` + 步骤 `error` + 人就绪 false；
  `orchestrate()` **不 commit**（招募路径同一事务 ⇒ 失败整笔回滚，I4）
- **就绪摘要进招募响应**（`RecruitOut.readiness`）：未达 READY 时**明确告知缺什么**
- **踩坑记录（真实 bug，务必别重复）**：
  1. `PositionAssignment`（物理表 `employments`）**没有** `position_definition_id` 列 ——
     职位定义要经 `position_slot_id → position_slots.position_definition_id` 解析。
     直接读那个属性会 AttributeError（实测踩到）。
  2. 第一版 `resolve_runtime_policy` 把"员工行是 mock"当成"没配"，从而回落到公司策略 ——
     结果**门禁按公司策略算、执行按员工行跑**，两边不一致（真实运行时的人被门禁放行、
     mock 的人被门禁拦住）。现在员工行权威，门禁与 `gateway.adapter_for` 读同一个值。
  3. 写测试时用常驻 `db` 会话轮询后台进度会挡住写者（rollback journal）——
     与 M2.7 同一条纪律：后台跑图用 HTTP 轮询。
- **反例注入验证**：**11/11** 条（就绪落列 / 不报缺口 / 门禁失效 / 放行提示词键 /
  绕过键校验 / 失败报 done / 假成功 / 跨公司读设置 / 注入技能 / 编排自己 commit / 招募忽略策略）
- 下一步：**M2.9 WorkOrder Bridge**

---

## 5m. M2.9 WorkOrder Bridge（**DONE**，2026-09-12）

- 迁移 **v45** `325887b7109a`（additive + 事实驱动回填）：`work_order_project_links` +
  `ix_work_orders_project_id`
- **用户拍板语义**（计划 §12 + 设计 **§11.4**）→ 不变量 **WO1–WO6**（注册表 105 条；enforced 93）
- 一句话：连接商业需求与执行载体，**不把 WorkOrder 变成执行图**（W23）

```text
WorkOrder ACCEPTED（M1 经济事实，状态机**不动**）
     ↓ 事件 work_order.accepted
桥：投递给公司 Work Intake 责任人（routed；系统只投递、不署名、不决策）
     ↓ 管理层决定
   bind_project → bound ／ decline_binding → declined（理由必填）
```

- **只加一条边**（WO1/J5）：`contracts.WORK_ORDER_STATES_FROZEN` 是**手写**快照 +
  导入期断言 —— 给订单加状态会**直接导致导入失败**（必须显式改契约）
- **指针 vs 历史**：`work_orders.project_id` 是当前绑定的指针；
  `work_order_project_links` 是决定历史（J1 要"绑定或显式拒绝都有记录"）；
  两者由同一个函数在同一事务里写
- **引用受校验**（WO3/WO4）：`submit.project_id` 必须存在且同公司（跨公司 **404**）、
  `artifact_refs` 必须是 `12` / `"drive:12"` 且指向**本公司真实 Drive 文档**（W19）
- **交付意图匹配只记事实**：订单 `deliverables` vs 项目 `deliverables` 的差异写进绑定边
  （`deliverable_facts`），系统**不据此拒绝**（那是管理判断）
- **桥不碰钱**（WO6）：验收/结算/托管/账本仍在 M1 路径（AST 守卫 + 账本行数前后对比）
- **踩坑记录（真实 bug，务必别重复）**：
  1. 第一版"状态机冻结快照"是**从枚举派生**的（`tuple(item.value for item in WorkOrderStatus)`）
     —— 派生快照会跟着枚举一起变，**永远测不出"有人加了状态"**。改成手写元组 +
     导入期断言后才真正拦得住（反例注入验证过）。
  2. `WorkIntakeResolution` 的字段是 `configured_position_code`（不是 `position_code`），
     且它没有 `is_routed` 方法（是 property）—— 桥里一开始按错名字取值，AttributeError。
  3. 断言"账本没被写"不能写"全表为空"：同一个测试库里别的用例会写账本。
     一律用**前后对比**（与 M2.5/M2.7 的同类教训一致）。
- **反例注入验证**：**10/10**（加状态 / 桥推状态 / 拒绝不需要理由 / 投递不幂等 /
  绑定不校验 / 提交不校验 / 引用不解析 / 放行任意字符串 / 桥建项目 / 桥做结算）
- 另：M1 的 `test_order_detail_surfaces_submissions_and_evaluations` 从
  `artifact_refs=["drive:1"]`（不存在的节点）改成引用**真实**产物 —— 那是 W19 的必然结果，
  测试意图（提交记录能在详情里读到）没变
- 下一步：**M2.10 Golden Path × 3 & Freeze**

---

## 5n. M2.10 Golden Path × 3 & Freeze（**DONE**，2026-09-12）— **M2 收官**

- **无迁移**（M2 最后一个迁移仍是 v45 `325887b7109a`）
- **三条黄金路径**（`apps/server/tests/test_m2_golden_path.py`，5 条测试）：

```text
A 公司已有完整团队：Project → 路由给管理层 → Manager 用工具查人（只拿事实）
                   → 决策信封建 DAG + 选人 → 系统执行 → Artifact 归属
                   → Reviewer 逐个 PASS → 交付 → Evidence
B 能力不足：Manager 查出缺口（无建议字段）→ 系统不替它选 → 走"改方案 + 派人"
                   → 留 DecisionRecord + 审计 + 结果
C 新 CEO 接任：CEO A 离任 → CEO B 上任 → B 不继承 A 的技能/私人知识/人格
                   → B 拿到 RoleContext + 公司策略 + 制度知识 + 历史决策 + 在跑项目
                   → B 自己决策
```

- **冻结面落盘**：`docs/m2-freeze.md`（形态 / 不变量 / 唯一写入路径 / 模块边界 /
  关键裁决 / 留给 M3 的 10 项清单）；设计 §14 顶部加冻结声明
- **K2 收口**：12 条历史上"冻结待锚点"的不变量（W1/W2/W3/W12/W14/W15/W16/W17/W19/
  W22/W30/W31）补齐**现存**测试锚点 ⇒ 注册表 **105 条全部 enforced**
- **踩坑记录（真实 bug，务必别重复）**：
  1. **评审结论不触发项目终态**：`submit_verdict` 只发 `dispatch`，而"全部完成 ⇒ 交付"
     的判断在 `Orchestrator._advance` 里 ⇒ 最后一个任务被结论推进 `done` 时项目永远停在
     `in_progress`。修：PASS ⇒ 发 `task_finished`（`_finalize_external` = 反思 + 推进）。
     三个场景串起来才暴露 —— 这就是黄金路径的价值。
  2. **工具审计被 datetime 打挂**：`list_company_people` 的载荷带 `created_at` ⇒
     `tool_audits` 的 JSON 列 INSERT 失败（丢一整条执行事实）。
     修：`tools.json_safe`（datetime/Decimal/UUID/set/bool 递归降级），入参出参都过。
  3. 读工具的**载荷键**与参数名要现查：`list_company_people` 返回 `items`、
     `get_current_load` 要 `employee_ids`（数组）、`get_runtime_status` 不要参数、
     `calculate_task_fit` 返回 `candidates`。凭印象写测试会一路 AttributeError。
  4. "某员工名下没有 X 行"要**直接查表**：`list_skills` 按 person 口径过滤，
     注入的行（`person_id` 为 NULL）在仓库读路径里看不见 ⇒ 靠仓库断言会漏掉违规
     （反例注入当场抓到）。
- **反例注入验证**：M2.10 **7/7**；连同 M2.5–M2.9 共 **64 条**注入全部"转红→还原→转绿"
- 下一步：**M3**（见 `docs/m2-freeze.md` §6 的 10 项清单）；M2 已冻结，改动需显式理由

---

## 6. 下一步建议（按优先级）

1. **M3**：先读 `docs/m2-freeze.md` §6（留给 M3 的 10 项）。M2 已冻结 —— 改冻结面需要显式理由 + 先跑对应锚点。
2. ~~实机过一遍教程后段~~ **已完成**（§1.6，17 步全走通，截图在 `tmp/tutorial-audit/`）。可选复验：小视口（1280x800）再过一遍，招聘向导弹窗较高的子步骤是历史上最挤的场景。
3. 若要 git 权限：`make runtime-pull` → 启动 builtin gitea → `POST /provisioning-jobs/{id}/retry`。
4. 可选增强：上传文件夹保留层级；表格/PPT/画板格式；CoachPanel sticky footer（按钮始终可见）。
5. P6.1 flaky 的根因排查（注入时钟 / 事件循环生命周期 fixture）。

---

## 7. 本轮提交索引（倒序）

```
430601e fix(tutorial): 弹窗锚点改惰性读取 —— 弹窗内容长高后卡片不再压弹窗
a5605bc fix(drive): zone 根目录 company_id 收养 —— 新建文档不再 404
1ea38c3 fix(tutorial): 弹窗关闭宽限期防补跳 + degraded 卡片贴弹窗侧边 + git_setup 路由修复
2e379d0 feat(tutorial): data-tutorial-protected 参与碰撞避让 —— 页面可声明关键内容区
e5e5690 fix(tutorial): 弹窗关闭不再补跳 + 教学卡片体积碰撞避让
22eec67 feat(web/drive): 飞书式新建/上传图标下拉 + 去掉顶部云文档大标题 + 教程卡片不再压按钮
35be4bf feat(drive/tutorial): 原生「新建文档」+ 教程改为教创建（上传只是补充）
0ffdf62 fix(provisioning): drive 目录创建竞态根治 —— INSERT ON CONFLICT DO NOTHING
1be9810 feat(provisioning): 单步执行超时 —— 活体挂起不再无限 running
e4afaa1 feat(tutorials): configure_ceo_runtime/provider 标记 engage_to_advance
2f5b24a feat(web): 聚光灯交互优化 —— 操作过即免点下一步，未操作点下一步转聚光灯提醒
9c2dd03 fix(provisioning): 入职彻底不再卡死 —— 会话恢复 + 并发安全 + gitea 未装可跳过
12a9540 fix(web): Dialog 内容超视口滚动 —— 小屏下 provider 弹窗确认按钮可点
1d9322a fix(provisioning): 启动补收敛 —— 进程死在 engine.run 中途导致教程卡死
5a3b2f8 feat(web): 登录页改「用户名 / 邮箱」+ api 载荷
892319d feat(auth): 登录支持 username 或 email（v20）
755582d feat(make): dev-restart-clean —— 停服→清数据→建测试账号→重启
44cfeff feat(make): dev-seed-user —— 一键注入测试账号 user@example.com / user
9e41c25 feat(make): dev-clear-data —— 清除本地开发数据
```
