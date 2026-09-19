# U7：持久请求与逐动作回执

本批完成宿主内部的同步请求入口、请求身份持久化和逐动作恢复。
前置为 [工具事务](m3-u7-tool-transactions.md) 与 [会话身份校验](m3-u7-runtime-identity.md)。
它仍不是 U7 的完整真实业务验收：受控会话循环和两个真实 Runtime 的业务往返证据继续实施。

## 请求与结果

`app/work/runtime_bridge.py` 提供 `parse_reply()`、`reply_contract()` 和
`handle_reply(work_session_id, text, session_factory=...)`；没有新增 HTTP 写工具入口。
宿主传入 WorkSession，模型仅提交 version 1 的 `read`、`decision` 或 `finish` JSON。
工具输入来自现有目录，Actor 与 Authority 始终由后端解析。

```json
{"version":1,"kind":"read","operation_key":"inspect-1","tool":"inspect_project","args":{"project_id":1}}
```

写动作置于 `decision.actions`，沿用已有 `decisions.submit_envelope()` 和 ToolExecutor。
`reply_contract()` 为模型提供合法决定类型、闭合结构、引用编码及边界，避免调用方另写一份协议。
自由文本、代码围栏、重复 JSON key、未声明字段、伪造身份、非有限数、不可编码字符和越界整数
均在创建请求或决定前拒绝。请求上限 64 KiB，嵌套上限 16，每份决定最多 16 个动作；
operation key 为 1–128 个安全 ASCII 字符。

结果包含 operation / decision / audit 引用、逐动作结果与 action index。
初始身份失效抛出类型化错误；执行途中身份或作用域失效则返回 `interrupted` 和此前已提交
的回执，不为尚未尝试的动作伪造拒绝审计。普通领域拒绝由 ToolExecutor 保存，继续参与原有
`PARTIALLY_APPLIED` 聚合。`finish` 仅确认对话退出意图，不改变 Task、Project 或 WorkSession 状态。

## 唯一事实与恢复

v52 `52c94fd1a807`（父版本 `51b83e04a6f2`）增加 `runtime_operations`，保存会话内唯一的
operation key、规范化请求摘要、请求种类、输入动作数量和一次绑定的 DecisionRecord 引用。
输入动作数量不是执行结果汇总。请求正文由调用者原样重传；系统不从摘要猜测缺失动作。

ToolAudit 增加 nullable 的 `runtime_operation_id` / `action_index` 和组合唯一索引，仍是执行
结果的唯一持久来源。旧调用和旧审计保持两列为空，不依据当前状态补造历史。
SQLite 守卫同时装入迁移和 `create_all`：请求身份不能修改或删除，决定只能绑定一次，已关联
回执不能修改或删除，审计与请求的会话、决定、动作序号必须匹配。

每个新决定先将请求身份、意图和绑定原子提交。之后每个动作使用新的短 Session，在读取身份、
权限、作用域和已有回执前获取 SQLite 写事务。已有回执直接返回；没有回执时调用现有执行器，
将业务效果、ToolAudit、请求关联和来源 Event 一起提交。回执存储失败会回滚该动作的效果。
先前已提交动作不回滚；事件广播继续由原有提交后回调负责。

同 key 不同内容拒绝。重复读返回当时保存的事实，即使数据已改变；权限恢复也不重跑已有拒绝
回执，需要新的 operation key 表达新的尝试。中途退出后只执行缺少回执的动作，已终结或被替代
的决定不能继续缺失动作。恢复时仍重查当前身份与公司可见性。

## 验证与边界

新增 `test_m3_runtime_bridge.py` 和 `test_m3_runtime_operation_migration.py`。
实施前先观察到缺少 parser 入口的失败；后续测试覆盖真实 create/update 领域链、独立连接竞争、
部分成功、逐动作撤权、会话失效、跨公司引用和存储失败回滚。
真实子进程在第一个动作提交后执行 `os._exit(23)`，第二进程重发后只执行第二个动作；
原任务、审计与来源 Event 均没有重复。

工作者定向结果为 **95 passed**。迁移在临时 SQLite 库完成 up/down/up、两次 `alembic check`、
单 head、旧审计保留与模型建表守卫验证。实现前失败记录和工作者原始日志位于
`/tmp/eidolon-u7-requests-QOqLjA/`；初轮失败中的夹具缺字段及 SQL 测试绑定错误也保留，未计作
已通过行为证据。父任务复核实际改动后，运行新桥、迁移、身份、授权、事务及 M2/M3 冻结面
的集成定向检查，结果为 **439 passed / 1 warning，31.03 秒**。`make lint` 通过：469 个 Python
文件的格式检查及前端类型、ESLint、Prettier 均通过。未修改共享 fixture。

父任务日志目录为
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u7.CHpV6Rthkx/`，
定向与静态检查分别见 `durable-integrated-targeted.log` 和 `durable-lint.log`。
默认序全量后端回归 **1820 passed / 12 deselected / 118 warnings，143.06 秒**；
结果和实际收集顺序分别保存在 `durable-full-default.log`、`durable-order-default.json`。
12 个真实环境测试继续与默认套件分开，本批未把 deselected 计作真实验收通过。

本批未调用真实模型，未变更开发数据库、用户容器、开发服务或用户 `tmp/`。
外部 run 的持久恢复、跨重试预算及用量计费仍属于 U13/U15/U17；领域事实的补发消费者属于
U8/U16。进程内 adapter 缓存、同步请求回执与这些后续能力不混为一项完成声明。
完整 U7 的简化及独立代码审阅在业务桥接齐后统一进行，既有协议审阅不覆盖本批新代码。
