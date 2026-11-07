# U8：持久事件与一次性会话领取

本批完成管理与评审事件的发现、等待记录和原子领取。U8 的运行器、共享查询与真实
Manager / Reviewer 验收仍待接入；本批的本地环境事实不作为真实 Runtime 验收。

`work.management_dispatch` 是 `ManagementDispatch` 的唯一应用写入者。来源仅为既有
`DECISION_NEEDED_EVENTS` 和 `task.review_named`，事实取自已提交的 Event 行。
启动或周期扫描可以发现未广播、漏消费的来源；每批最多 100 条，按最久未检查的时间
轮转。新记录也保存观察时间，防止持续到来的新事件饿死旧等待记录。

接收人只来自 Project 的指定管理者或 ReviewRequest 的指定评审人。领取时重新检查
当前人员、Person、公司、目标、评审有效性、占用、真实 Docker Runtime、Provider、Model
及就绪观察；显式启用和预算准入默认关闭。缺主职仍为软就绪项，写动作的 Authority /
Autonomy 继续由既有执行器校验。领取过程不访问 Docker、探测或调用模型。

来源、用途及目标不可变。SQLite 在第一次读取前取得 `BEGIN IMMEDIATE`，在同一短事务
创建 WorkSession 并关联来源。它与 Worker 的开工锁互斥。同一来源只能关联一次会话，
不同来源引用同一 ReviewRequest 时也最多创建一次评审执行；结束或失败不解除去重。
重复来源引用原始领取记录。只有 `ClaimResult.created=True` 可以启动新的 Runtime 调用。

记录仅保存来源、等待原因、检查时间、失效或重复原因及会话引用。WorkSession 仍是执行
状态的唯一来源；查询从它读取当前状态、起止时间及脱敏错误。等待会重查当前指定人及
环境，已领取会话不会因改派而换绑。旧评审、终止项目和已结束通知周期不再发起执行。
这里不创建业务 Task、不改变 Task / Project 状态，也不负责恢复未知的旧运行。

v54 `54e93ab821cd` 在 v53 后新增表、索引和 SQLite 守卫。临时库完成空表 up/down/up、
Alembic check、单 head、模型/迁移约束与触发器一致性检查。存在任何消费历史时，降级在
第一条 DDL 前拒绝，避免删除去重依据后重复调用。v53 的拒绝测试固定在 v53 执行，仍
验证该版本自身的第一条 DDL 前拒绝；当前 head 断言推进到 v54。

行为测试先观察到缺少实现/迁移，以及来源删除、非空闲人员、SQL 来源范围和持续新事件
导致旧记录饥饿的失败，再修复并验证。工作子任务定向 **67 passed**；父任务整合运行来源、
迁移、会话、权限、就绪、出处与 M2 黄金路径等 **452 passed / 1 warning，49.55 秒**。
`make lint` 通过：479 个 Python 文件的 Ruff/格式，以及前端类型、ESLint、Prettier。

命令、原始日志摘要和 SHA-256 见 [验证证据](evidence/m3-u8-event-claims.json)。
本批未修改共享夹具，未运行付费调用，未迁移开发数据库或修改已有容器与开发服务。
预算布尔准入不等于费用上限；后续运行器必须另行执行逐轮准入，并使用真正限额的调用通道。
