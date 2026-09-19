# U7 工具事务与公司范围证据

日期：2026-09-20。协议基线：`64994e4`。这是 U7 的事务基础切片，
不是 U7 整体验收；结构化业务桥、会话身份复查及持久操作回执尚待实现。

## 行为

- Runtime 的公司项目查询显式使用后端 actor 的 company_id。没有 HTTP 身份或
  HTTP 身份属于另一家公司时，都不会返回另一公司的项目。人类请求的默认查询行为保持不变。
- ToolExecutor 统一提交单个动作的领域效果、ToolAudit 和来源 Event。
  `commit=False` 交由调用方提交；审计落库失败会回滚该动作，保留调用方此前的修改。
- SQLite 在动作 SAVEPOINT 前建立物理事务，防止 RELEASE SAVEPOINT 提前提交。
  调用方如果自行建立更外层 SAVEPOINT，也必须先建立物理事务；此助手不能追溯修复
  已经建立在物理事务之外的外层 SAVEPOINT。
- 广播与评审调度通知只在最外层事务提交后执行。嵌套事务回滚、会话关闭或整体回滚
  丢弃待发送通知；提交后通知失败记录日志，已生效动作仍报告成功。
- DecisionEnvelope 继续先保存意图、逐动作提交。后续动作失败不会撤销前面的成功动作。
  工具处理函数仍使用原领域服务，T1 守卫新增禁止处理函数自行 commit / rollback。

回调不是持久队列。进程在提交后退出仍可能丢失内存通知；已提交 Event 是后续事实扫描的
来源。当前改动没有新增通用 outbox、公开写工具端点或数据库迁移。

## 验证

| 检查 | 结果 |
| --- | --- |
| 公司隔离反例，修复前 | 2 failed |
| 公司隔离、M2 工具与项目时间线，修复后 | 39 passed |
| 事务反例，修复前 | 17 failed、2 个既有行为检查通过 |
| 事务新增场景与 T1 守卫 | 21 passed（20 个事务场景及 T1） |
| 集成定向回归：工具、决策、评审、交接、黄金路径、事件及隔离 | 209 passed / 1 warning，19.33 秒 |
| 完整后端默认顺序 | 1663 passed / 12 deselected / 118 warnings，133.44 秒 |
| make lint | 462 个 Python 文件及 TypeScript / ESLint / Prettier 通过 |

事务场景使用实际 SQLite、独立观察连接和数据库拒绝 ToolAudit INSERT 的触发器，
覆盖创建、更新、读与拒绝审计、部分成功、事件持久化失败、外层回滚、嵌套回滚、
评审通知时序及提交后通知异常。两个直接调用私有处理函数的既有测试补充调用方提交，
保留业务断言。没有修改共享 fixture，没有新增真实模型调用或 Docker 验收。

原始证据目录：

- `/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u7.CHpV6Rthkx/`：
  `company-scope-red.log`、`company-scope-green.log`、`atomic-integrated-targeted.log`、
  `atomic-full-default.log`、`atomic-order-default.json`、`atomic-lint.log`。
- `/tmp/eidolon-u7-atomic-20260920-agent/`：`red.log`、`green-1.log`、
  `compatibility-1.log`、`final-focused.log`。

## 复查与限制

三项独立简化复查均已返回：复用、质量未发现问题；效率复查提出两项优化建议，本次未采用：

1. 事务事件逐条读取并广播，可在有性能证据后考虑合批。目前复用按 Event ID 读取
   已提交事实的路径；合批需要额外验证嵌套回滚及发送顺序，没有引入另一份批量队列。
2. 读工具在查询期间也持有 SQLite 写锁，因为同一次调用要保存审计。当前保持统一事务
   语义；缩短锁范围需要明确读取、会话身份与审计的一致性，并验证并发行为。

本切片没有独立的完整代码审查报告；后续 U7 整体验收仍需审查完整业务桥。权限复查、
操作去重、跨进程恢复不由这里的内存回调或协议回复缓存保证。
