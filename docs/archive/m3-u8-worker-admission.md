# U8：Worker 与管理会话的领取互斥

Worker 的开工前检查和创建会话原先没有共同的 SQLite 写锁。并发测试先让 Worker 完成
`evaluate_dispatch`，再让另一个事务领取同一员工的管理会话，实际复现了两个 running
WorkSession 同时存在。

`Orchestrator._run_task` 现在在第一次读取前复用 `dispatch_notices.begin_observation`，
在短写事务中检查当前目标、就绪和占用并创建会话。事务在已有的提交点结束，之后才调用
Runtime。原有 DAG 判断、人员选择、Task 转换和 Worker 收尾逻辑保持不变。

反例测试先 **1 failed**，加锁后与开工前校验、Worker 生命周期及 DAG 测试共
**43 passed / 1 warning**；U8 权限批次的父任务整合回归另有 **610 passed**，包含本例。
两个修改的 Python 文件通过 Ruff 和格式检查，整合 `make lint` 亦通过。

原始日志在本次 U8 证据目录中：

- `worker-admission-red.log`：同一员工同时出现 management 与 worker 会话。
- `worker-admission-green.log`：43 项通过，耗时 4.10 秒。
- `scope-parent-integrated.log`：610 项通过，耗时 40.86 秒。

目录为 `/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u8-df8tj2id`。
此测试使用两个真实数据库连接，只替换外部 Runtime 调用及无关的 Worker 收尾；
管理事件消费者的完整领取链路将在后续 U8 批次验证。
