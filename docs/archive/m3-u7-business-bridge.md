# U7：受控业务对话

`app/work/runtime_conversation.py` 将真实 adapter 的完整响应与
[持久请求桥](m3-u7-durable-requests.md) 接成有限对话循环。两种固定镜像均已完成真实业务往返；
本批独立代码审阅已结束，覆盖限制和后续修复证据见下文。

## 输入、身份与反馈

`run_conversation(work_session_id, adapter=..., runtime_session=..., goal=...,
budget_check=..., limits=..., session_factory=...)` 是内部入口。首轮包含现算的 RoleContext、
ToolExecutor 目录、桥接协议和后端会话作用域。模型仅通过 `complete_turn` 的完整响应提交
`read`、`decision` 或 `finish`；事件流、自由文本和普通工具遥测不能触发业务动作。

宿主在预算等待及模型等待前后重查 WorkSession、Person/Employee、原项目与任务，核对实际
Runtime 行、连接凭据、实例指纹、镜像/版本和原 Provider/Model 配置。原 Provider 的可见权限、
公司归属及 enabled 状态也须仍有效；Employee 的未来配置不改写本次执行的历史归属。
`runtime_session_ref` 在短事务内一次绑定；每个动作事务再次校验实际绑定和截止时间。

预算和模型 I/O 期间没有打开的数据库事务。已提交动作返回真实回执，部分成功、拒绝及同 key
冲突进入下一轮模型输入；身份失效、存储不确定和不可恢复的协议错误停止循环。超大结果只保留
可追溯的 operation/decision/audit 引用，不声称已发生动作被回滚。普通错误输出经过脱敏。

`finish` 只结束本地 RuntimeSession，保留模型的确认摘要；它不改变 WorkSession、Task 或
Project 的业务状态。无业务 task 的管理会话与 ReviewRequest 消费属于 U8。

## 有限执行与取消

| 边界 | 默认值 |
| --- | --- |
| 模型往返 | 8 轮 |
| 每轮时长 | 60 秒 |
| 总时长 | 300 秒 |
| 每条宿主输入 | 128 KiB |
| 模型完整响应 | 64 KiB |
| 每条执行结果 | 128 KiB |
| 提交动作 | 32 次，包含读、拒绝与重放 |

配置有严格类型及硬上限。一个本地 RuntimeSession 只进入一次宿主循环，并发或重复进入不能
重置本地额度。预算检查回调是必需参数，每轮调用前必须明确返回 `True`；缺失、拒绝或异常均
阻止调用。该回调不是美元计费实现：实际 Provider/relay 必须独立限制支出，U13/U15/U17 继续
负责持久尝试、跨重试额度和正式用量事实。

调用方取消会继续抛出 `CancelledError`。失败时只对当前已知的远端 run 尝试停止，adapter
已经尝试停止时不重复操作，也不调用 OpenClaw 的全局会话取消。已返回的晚到结果不能越过
宿主截止时间；即使依赖吞掉取消信号，宿主也不会执行其动作。已经执行中的短事务保留真实结果，
下一动作必须重新满足总截止时间。

## 本地验证

新增 `test_m3_runtime_conversation.py` 的 84 项用例，沿真实 SQLite、桥、DecisionRecord、
ToolExecutor、Task 和 ToolAudit 链验证读事实、创建任务和下一轮回执确认。另覆盖错误 handle、
伪造响应归属、旧事件流、等待期间撤权/离职/改派、Provider 权限变化、重放、部分成功、存储失败、
各类额度、并发进入及取消竞态。

工作者先观察到缺少新模块的失败；后续 6 个 Provider 反例和 3 组取消/截止时间反例均先失败后
修复。工作者未把原始输出保存为单独日志，证据在其工具记录中；不能将其描述为磁盘日志。
最终定向结果 **130 passed**，包括 84 个新用例和 46 个未改动的 adapter 用例。

父任务检查实际代码和测试后，默认序全量回归为 **1904 passed / 14 deselected / 118 warnings，
148.00 秒**。`make lint` 通过，包括 471 个 Python 文件的格式检查及前端类型、ESLint、Prettier。
日志与实际收集顺序位于
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u7.CHpV6Rthkx/` 的
`conversation-full-default.log`、`conversation-order-default.json`、`conversation-lint.log`。
14 个真实环境测试与默认套件分开，未将 deselected 算作真实验收通过。

## 真实验收设计

`tests/integration/test_m3_management_bridge.py::TestRealBusinessBridge` 在独立数据库/目录中，
先建立真实 Person、主职任命和计划授权，再配置固定镜像与受限模型 relay。原有两个协议测试
保留；新增测试要求模型先读项目事实，再自行提出一个具体任务，最后确认真实 task/audit ID。
任务标题必须包含仅出现在 `inspect_project` 返回事实里的随机标记，以验证模型消费了读结果。
Python 只安排环境与检查结果，不直接调用工具代替模型决策。

每种 Runtime 的单次验收最多 4 次上游请求，输出上限 768 tokens，总金额上限 USD 0.05。
relay 在每次上游调用前持久预留费用，原生重试或辅助调用也计入同一额度。
测试记录镜像、版本、执行出处、完整结构化请求、实际回执、Decision/ToolAudit/Task 引用及
relay 的用量凭据；不保存密钥或隐藏思考。

## 实际结果

| Runtime | 实际版本 | 业务验收 | 上游请求 | 估算费用（USD） |
| --- | --- | --- | --- | --- |
| Hermes | v0.21.0 / 2026.8.31 / c0495c6b | 1 passed，9.96 秒 | 3 | 0.0094326 |
| OpenClaw | 2026.8.1 / ea80657 | 1 passed，13.76 秒 | 3 | 0.0132684 |

两次独立测试均保存一条 APPLIED 决策、读与写各一条 ToolAudit，以及模型提出的 backlog 任务。
模型从读取的项目目标取得随机标记，并在第三轮确认真实 `task=2;audit=2`；这些 ID 属于各自
独立测试库。原任务仍为 in_progress，原 WorkSession 仍为 running，未用退出文本推进业务。

本轮 6 次上游调用全部结算，无失败尝试或未结算预留。按返回用量与
[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)的峰值 cache-miss 单价计算，
合计 **USD 0.022701**；这是用量估算，不是供应商账单，且不包含前批协议/探测调用。
固定镜像摘要、每轮完整请求/回执、持久引用、价格核对时间和代码哈希见
[机器可读证据](evidence/m3-u7-business-bridge.json)。原始日志也列于该证据中。

测试创建的容器已清理；前后核对的 9 个既有容器及 26880/26881 开发服务保持原状。
开发数据库仅只读取得已配置的测试 Provider 凭据，没有迁移或业务写入。用户 `tmp/` 未读取或修改。

复用、代码质量与效率三个简化检查均已完成，前两项无发现。效率检查指出知识引用校验会读取
可见知识全集；这超出当时简化检查的文件边界，因此交由下述独立修复处理。

## 审阅与修复

完整审阅覆盖正确性、测试、可维护性、Agent 工具可用性、安全、性能、接口、迁移、可靠性和
故障场景。10 个本地结果均已收齐，没有保留的阻断项。Claude 与替代 Grok 通道均返回认证错误，
没有取得跨模型审阅结果；故障场景由本地审阅补齐。额外的独立验证器因并发额度未能启动，性能
候选未在正式报告中被确认为缺陷。这些限制保留在机器证据中，不描述为完整独立验证通过。

父任务随后用真实桥接回归复现知识查询问题：128 条知识、1 条引用和 2 个动作累计加载了
384 条完整 KnowledgeItem 记录。修复将原仓储可见性谓词提取为共享查询，引用检查仅投影指定
ID；公开列表的过滤和排序保持原样。新增回归要求不加载正文，并验证公司/部门、自己/他人的
私有知识、person-only 属主和重复引用。修复前 **1 failed / 6 passed**，修复后桥与知识测试
**118 passed**。另一次针对这 3 个文件及失败/成功日志的审阅无发现；它属于修复复查，不代替
前述未运行的正式验证器。

修复后的最终默认序回归为 **1911 passed / 14 deselected / 118 warnings，146.99 秒**；
`make lint` 再次通过。日志为同目录下的 `business-final-default.log`、
`business-final-order.json` 和 `business-final-lint.log`。本批没有修改共享夹具。

README 和架构文档已按现状注明 SQLite 支持基线；PostgreSQL 的迁移触发器和并发行为尚需
适配。v52 降级会删除请求身份及审计关联，其备份与重放边界已补入持久请求文档。

真实模型证据记录的是查询修复前的准确源码哈希；后续变更仅涉及知识引用查询及其回归测试，
不改变 adapter 协议或本次无知识引用的真实业务场景，因此没有追加付费重复调用。
