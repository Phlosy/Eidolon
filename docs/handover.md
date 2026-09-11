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

## 6. 下一步建议（按优先级）

1. ~~实机过一遍教程后段~~ **已完成**（§1.6，17 步全走通，截图在 `tmp/tutorial-audit/`）。可选复验：小视口（1280x800）再过一遍，招聘向导弹窗较高的子步骤是历史上最挤的场景。
2. 若要 git 权限：`make runtime-pull` → 启动 builtin gitea → `POST /provisioning-jobs/{id}/retry`。
3. 可选增强：上传文件夹保留层级；表格/PPT/画板格式；CoachPanel sticky footer（按钮始终可见）。
4. P6.1 flaky 的根因排查（注入时钟 / 事件循环生命周期 fixture）。

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
