# M3.1 U1：测试隔离验收

日期：2026-09-20。执行基线：`b35c2af` 的 U1；不涉及生产数据库、运行中的开发服务或 Docker 资源。

## 原因与修复

原夹具让整个套件共用一个 `TestClient` 和文件 SQLite。独立 `SessionLocal` 的提交会保留到后续用例；仅在 `db` 夹具退出时关闭 Session 无法隔离这些写入。

实际随机种子 `20260920` 暴露了可用三个测试稳定复现的碰撞：

1. `test_assessment_engine::test_run_produces_criterion_and_contribution_results_and_updates_competencies` 创建编制 8001。
2. `test_roster_api::test_frozen_slot_refuses_assignment_through_the_api` 通过 API 的最大编号加一创建编制 8002。
3. `test_assessment_engine::test_inputs_hash_changes_with_profile_version` 按本模块计数器再次创建 8002，触发唯一约束。

修复后，每个用例都有自己的应用生命周期。完整退出 `TestClient`、取消残留后台任务并关闭事件循环后，才恢复迁移和 seed 生成的数据库与目录基线。引擎、Session 工厂和被多处导入的单例保留对象身份；事件总线、编排器、Gateway、注册表、配置及缓存恢复到明确基线。数据库仍是文件 SQLite，用例内不同连接的提交及竞争均真实发生。

新增五个夹具回归用例，覆盖独立连接提交、文件、配置、缓存、限流、注册表及编排状态。子进程中的故意失败用例启动未跟踪后台任务，任务在取消清理时写入数据库和磁盘；下一用例同时验证任务结束、旧循环关闭及迟到写入没有泄漏。

隔离还揭示三个原测试依赖了前序数据，修复保留其冻结锚点名称与断言：

- M1 账本守卫自己创建发行、销毁和财政划转记录，验证平衡、供给及解释字段。
- M1 孤儿实体守卫自己执行取消和资金不足场景；相同关系检查也在失败矩阵的每个用例结束时执行。
- 教程事件守卫先触发本公司的进度刷新，再只查询本公司的事件，避免把历史事件当成本次成功。

M1 并发测试保留原有独立连接与线程屏障，仍断言竞争只有一个赢家。没有增加测试重跑插件、跳过失败用例或放宽业务断言。

## 运行方式与证据

测试环境：项目 conda 环境，Python 3.12.14 / pytest 9.1.1。从仓库根运行：

```bash
python -m pytest apps/server/tests -q --tb=short \
  --test-order-seed 20260920 --test-order-output /tmp/eidolon-order.json
```

`tests/conftest.py` 在收集完成后使用 `random.Random(seed).shuffle(items)` 打乱具体用例，包括参数化用例。无 seed 时保持默认顺序。输出 JSON 记录实际选中顺序，不仅记录 seed。

最终默认顺序与五个不同 seed 全部通过，每轮选中相同的 1401 个用例，六种实际顺序各不相同。Docker 集成测试按默认配置排除，未把它们计作通过。

| 顺序 / seed | passed | deselected | 用时 |
| --- | --- | --- | --- |
| 默认顺序 | 1401 | 6 | 141.61s |
| 20260920 | 1401 | 6 | 150.12s |
| 20260921 | 1401 | 6 | 150.19s |
| 20260922 | 1401 | 6 | 149.10s |
| 20260923 | 1401 | 6 | 148.52s |
| 20260924 | 1401 | 6 | 148.50s |

[实际顺序证据](evidence/m3-u1-test-orders.json) 保存一份完整用例目录、每轮的索引排列、顺序 SHA-256 和对应测试代码 SHA-256。用 `nodeids[index]` 逐项还原每个 `runs[].order`，得到该轮真实收集顺序。六轮在两批独立进程中执行，各自使用私有临时数据库。每轮另有 118 条既有依赖弃用警告。

修复期间的失败记录保存在执行证据目录中：

| 记录 | 结果及用途 |
| --- | --- |
| `baseline.log` / `confirmed-repro.log` | 随机序唯一约束失败；最小三个测试为 1 failed、2 passed |
| `regression-red.log` / `globals-red.log` | 旧夹具无法隔离文件、后台任务和配置；新增回归确实能抓住问题 |
| `fixed.log` / `m1-anchors.log` | 隔离后显现 M1 账本与孤儿检查对前序数据的依赖 |
| `seed-20260920.log` / `tutorial-alone.log` | 显现教程事件测试缺少触发动作及公司过滤，单独运行也失败 |
| `targeted-final.log` | 修复后的夹具、M1 锚点和教程定向回归：69 passed |
| `lint.log` | `make lint` 通过：ruff、后端格式、tsc、eslint、prettier |

以上失败分别经原因修复后再验证，没有用同一代码反复重跑直至通过。原始本地日志目录为
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u1-r3vstwi0`。

## 检查范围

三项独立只读简化检查已完成：代码复用没有发现可替代实现，质量检查确认保留单例身份和退出顺序的必要性，资源检查采纳目录删除后处理 `FileNotFoundError`，跳过为少量测试实体增加批量查询映射的建议。另移除仅为旧夹具红测保留的可选 helper 文件复制分支。完整 diff 自检确认生产代码未改，冻结锚点未删除。

本包不代表 M3.1 真实 Runtime 开通完成；U2–U5 和两种 Runtime 的真实开通证据仍待完成。
