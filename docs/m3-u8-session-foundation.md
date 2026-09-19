# U8：会话用途与历史归属基础

本批实现 WorkSession 的用途、目标、创建守卫和兼容迁移。它是 U8 的第一批提交；
事件唤醒、真实管理/评审运行及其业务验收尚未完成，不能据此关闭 U8 或 M3.2。

## 用途与唯一写入路径

| 用途 | 会话直接关联 | 历史快照 | 结束会话的含义 |
| --- | --- | --- | --- |
| `worker` | 必须关联真实 Task | 原有任务、项目、人员及环境事实 | 继续使用既有 Worker 收尾流程 |
| `management` | Project；`task_id` 为空 | 项目与执行者事实；任务为空 | 仅结束本次管理执行 |
| `review` | ReviewRequest 及其 Project；`task_id` 为空 | 请求、被评任务、项目与评审者事实 | 仅结束本次评审执行 |

`project.create_work_session` 仍是创建入口，调用 `provenance.capture` 记录当时的
Person、Employee、公司、任职、目标和实际 Runtime/Provider/Model。调用方不能自行填入
历史身份或快照。管理者必须是项目指定的人，评审者必须是请求指定的人，且仍在册、公司相符。
用途词表在 `work.production.WorkSessionPurpose` 冻结。

非 Worker 会话不创建业务 Task，不出现在 `Task.sessions` 或按 task_id 查询的最后一次
Worker 会话中，也不会替换评审事实中的 Worker 执行来源。数据库用途/目标约束拒绝缺目标、
混合目标、跨公司及不存在的目标；SQLite 即使关闭外键，也有独立的目标插入守卫。
用途与目标加入已有历史不可变守卫，Runtime 会话引用仍只能绑定一次，结束时间仍不能改写。

## 迁移与兼容边界

v53 `53d82fa710bc` 紧接 v52 `52c94fd1a807`，保持单 head。迁移重建 SQLite 的
WorkSession 表以允许两种非 Worker 用途没有 task_id，并恢复全部 v50 历史守卫。
旧行默认 `worker`，旧快照字节、未知归属、ID 和其他表对会话的引用均原样保留。
已有 ToolAudit 回执和 RuntimeOperation 幂等身份不会因本次重建消失。

降到 v52 前若存在管理/评审会话，迁移在任何破坏性 DDL 前拒绝：旧结构无法准确表示这些
记录。不得通过删除记录或补造 task_id 绕过该限制。SQLite 重建使用仓库既有的
`foreign_keys=OFF` 专用迁移连接；检测到开启外键的连接时提前拒绝，不在事务中切换 PRAGMA。

后端 WorkSessionOut 和前端 WorkSession 类型显式包含用途与可空目标。
模型补齐 `behavior_snapshot_hash` 的空字符串 server default，使其与既有 v19 迁移一致；
这项调整不回填或修改历史数据。

## 验证记录

实施前冻结锚点 356 项通过，兼容增补先登记于 `docs/m2-freeze.md`。
新增用途与迁移测试先观察到 59 failed、2 passed，再实施代码；用途契约另观察到缺少枚举的
AttributeError 后补齐。第一轮实现的测试辅助函数冲突和时区断言已修正，严格 schema 对拍
发现的既有默认值漂移按上述方式收敛。

最终 worker 定向检查：新用例、既有出处和 Worker 收尾共 129 passed。父任务另确认旧迁移
锚点的两处预期冲突后，保留 v50 自身的非空验证，并在当前 head 验证 worker 的条件性非空
约束；4 项旧迁移锚点通过。新测试同时检查临时库 up/down/up、alembic check、单 head、
完整旧行、入向引用、不可变回执、拒绝降级前 schema 不变，以及迁移/metadata 约束完全一致。

父任务整合回归：后端 **1980 passed / 14 deselected**，`make lint` 通过；前端
**390 passed**，生产构建通过。未改共享夹具，本批不重复随机序验收。
命令、范围、源文件及日志摘要见 [验证证据](evidence/m3-u8-session-foundation.json)。
本批未修改开发数据库、调用真实模型或操作 Runtime 容器；真实管理和评审证据将在后续
U8 运行批次单独记录。
