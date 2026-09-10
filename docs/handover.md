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

- `alembic check` 无漂移；**本地开发库已升到 v20**。
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
  pytest apps/server/tests -q        # 期望 649 passed / 6 deselected
  ruff check apps/server/app apps/server/tests
  ruff format --check apps/server/app apps/server/tests   # 只允许 5 个既有 WIP 红
  cd apps/server && alembic check    # No new upgrade operations detected
  cd apps/web && pnpm exec tsc --noEmit && pnpm exec eslint . && pnpm exec prettier --check .
  cd apps/web && pnpm exec vitest run    # 期望 67 files / 281 passed
  cd apps/web && pnpm build
  ```
- 数据库：`apps/server/data/eidolon.db`；快速查 job：
  ```bash
  sqlite3 apps/server/data/eidolon.db "SELECT id,kind,status,done_steps,total_steps FROM provisioning_jobs;"
  sqlite3 apps/server/data/eidolon.db "SELECT seq,provider_key,status,substr(COALESCE(error,''),1,60) FROM provisioning_steps ORDER BY seq;"
  ```

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
