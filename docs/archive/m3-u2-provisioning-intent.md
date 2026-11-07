# M3.1 U2：提交后开通意图

日期：2026-09-20。实施基线：`d838823`（U1）。本包将招聘事务与环境步骤分开，沿用
`ProvisioningJob/Step` 保存进度。真实容器的幂等开通及主动探测由 U3/U4 继续完成。

## 原因与行为

原 `readiness.orchestrate()` 在招聘事务提交前创建工作区。用真实
`RecruitmentService.recruit_existing_person(commit=False)` 可以复现：回滚后员工与任务行
消失，磁盘目录仍然存在。测试先得到失败证据，再修改这条调用链。

现在招聘事务只保存员工、任职与 `pending` 开通意图。调用方选择提交或回滚；此过程
没有开通资源或内部提交。响应含当前就绪事实，`ok` 只有在开通任务完成时才为 true。

后台执行者通过独立连接扫描已提交意图，使用条件更新竞争领取。每一步开始与结果分别
短事务落库；工作区操作期间没有数据库连接或写锁。分步回执保存状态、尝试次数、时间、
脱敏说明和 `ProvisioningFailureReason`。完成计数随步骤更新，不必等整个任务结束。

`partial` 保留招聘结果和成功步骤，等待显式 retry；retry 仅重置失败步骤。进程正常
停止会释放领取，中断后扫描可重新领取过期任务。令牌校验约束结果回写，旧执行者和
陈旧 retry 请求不能覆盖新领取。已完成步骤在恢复时不重复执行。

## 归属与兼容

- `owner=runtime` 由新的 Runtime 执行者处理；`owner=lifecycle` 继续由原生命周期引擎处理。
- 旧 workforce sweeper 只消费 lifecycle 任务。原引擎也会拒绝错误 owner，避免旁路调用。
- Job 查询新增 owner/source，步骤新增结构化回执；既有 retry 端点按 owner 路由。
- `EIDOLON_RUNTIME_PROVISIONING_ENABLED` 默认开启，默认每秒扫描。测试默认关闭轮询，
  需要的用例显式驱动；独立进程测试单独打开真实启动扫描。
- [M2 兼容增补](m2-freeze.md#7-m3-兼容增补)先于实现登记。RD1–RD7 及原测试名称保留，
  同步开通断言改为提交后执行并查询结果。M2 六版迁移仍检查存在、顺序和祖先关系，
  历史 v45 不再被断言为永远等于仓库 head。

## v46 迁移

迁移 `46c2d8109a7b` 接在 v45 `325887b7109a` 之后，增加 Job 的 owner/source、
领取令牌/时间，Step 的 `result_json`，以及 owner/status 联合索引。

只有历史 `metadata.source == "app.work.readiness"` 被识别为 Runtime 任务，其余保留
lifecycle 归属；无有效来源使用 `legacy`，不推测归属。新增写入保留原 writer 标记，
`requested_by` 记录调用来源，因此降级再升级后仍可正确分类。回填以最多 500 行一批更新。
原状态、原因和 metadata 保留；迁移不执行任务，不创建或删除外部资源。

迁移测试使用独立临时 SQLite：v45 旧数据 → head → `alembic check` → v45 → head →
再次检查；同时验证唯一 head、v46 父版本、五种历史来源/状态及未知 JSON 形状。
本次没有升级开发服务正在使用的数据库。

## 行为证据

`tests/test_m3_provisioning.py` 覆盖：

1. 实际招聘回滚后没有员工、开通意图或工作区；未提交意图对独立连接不可见。
2. 新 Python 进程导入应用并运行真实 lifespan，仅凭数据库中的意图完成开通。
3. 工作区执行时，另一连接可以读取已提交员工并写入提交，证明没有持有执行者写锁。
4. 工作区、Runtime、Provider 三类故障分别生成结构化原因，员工保留，partial 不被自动重试。
5. 两个线程用屏障同时领取，只有一个成功；过期领取恢复后旧令牌不能写入。
6. 陈旧 retry 请求不能重置新领取；取消发生在第二步时，已完成第一步保留并从未完成步骤恢复。
7. Runtime 与 lifecycle 各自路由；查询回执与 API retry 正确，成功步骤的尝试次数不增加。

`tests/test_m3_provisioning_migration.py` 验证上述迁移往返。既有 M1 招聘/经济、T2、
M2 readiness、生命周期与 workforce 回归均保留。

## 验证结果

最终默认序及五个实际随机序全部通过，六轮选择相同的 1415 个用例，实际顺序各不相同。
每轮使用私有数据库及目录；Docker 集成测试按默认配置排除，没有计作通过。

| 顺序 / seed | passed | deselected | 用时 |
| --- | --- | --- | --- |
| 默认顺序 | 1415 | 6 | 141.67s |
| 20260920 | 1415 | 6 | 148.92s |
| 20260921 | 1415 | 6 | 149.73s |
| 20260922 | 1415 | 6 | 144.95s |
| 20260923 | 1415 | 6 | 144.06s |
| 20260924 | 1415 | 6 | 144.06s |

[实际顺序证据](evidence/m3-u2-test-orders.json)保存完整用例目录、六轮索引排列、顺序校验和
及本包 18 个 Python 源文件校验和。用 `nodeids[index]` 还原各轮 `runs[].order`。
随机化仍由 U1 的收集钩子执行，运行示例：

```bash
python -m pytest apps/server/tests -q --tb=short \
  --test-order-seed 20260920 --test-order-output /tmp/eidolon-u2-order.json
```

`make lint`（Ruff、后端格式、tsc、ESLint、Prettier）及 v46 独立 Ruff 检查通过。
`make test-web` 为 88 文件、390 passed；`make build` 通过。每轮后端仍有 118 条既有
依赖弃用警告，前端构建仍提示部分分包超过 500 kB。

原始日志位于本地执行目录
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u2-rckwk4d5`：

| 记录 | 结果 / 用途 |
| --- | --- |
| `red.log` | 原招聘回滚仍遗留工作区，1 failed |
| `boundary-green.log` | 同一事务边界场景修复后 1 passed |
| `targeted.log` | 92 passed、1 failed；新增迁移测试夹具缺少必填 memory_namespace，修正夹具 |
| `migration-economy-final.log` | 40 passed，含临时迁移往返、M1/T2 招聘与经济锚点 |
| `targeted-final.log` | 26 passed，含领取进度、过期竞争、取消恢复、M2 readiness |
| `fixed.log` | 简化前首轮完整回归，1415 passed |
| `final-*.log` / `final-*-order.json` | 最终六轮套件结果和实际顺序 |
| `lint-final.log` / `web-final.log` / `build-final.log` | 静态检查、前端测试与构建 |

修复原因后才重新验证；没有跳过失败用例、放宽业务断言或依赖重跑插件。

## 审查与边界

三项独立简化检查已结束，共采纳六项：复用 2 项、质量 2 项、效率 2 项，未跳过发现。
包括复用 Job/Step 仓库入口、使用既有状态枚举、显式回执默认工厂、按任职变化重查
就绪事实及批量历史回填。保留领取条件更新、短事务边界和迁移来源标记。

U2 的成功表示“提交后可靠处理意图”，不能据此认定真实 Agent 已就绪。当前 Runtime
步骤仍沿用登记实例的能力；Docker 资源与实例收敛、真实挂载/权限、Provider/模型探测
及最小历史快照分别进入 U3–U5。M3.1 保持进行中。
