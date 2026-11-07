# U7 会话身份与即时授权证据

日期：2026-09-20。基线：`0e82ded`。本切片提供内部桥调用前的身份与权限检查，
不代表结构化业务桥及持久回执已经完成。

## 行为与边界

`context_for_work_session` 从 WorkSession 的不可变创建快照取得 actor，并每次重新读取
会话、成员、人、公司、任务和项目。结束但仍标记 running 的会话、缺失历史归属、成员
换公司或换人、停用、任务改派或移到其他项目、执行范围被暂停或结束，均不能继续发起调用。
ORM 中缓存的旧行不能代替数据库当前事实。

当前只允许原员工仍在执行的已分配任务，沿用现有调度的项目状态集合。U8 会显式扩展
管理与评审用途；本次不改变 WorkSession 的模型或历史不可变守卫。期望使用的 Runtime、
Provider、Model 和名字发生变化，不会重写已存在会话的历史归属。缺少职位仍可读取本公司
事实；写工具仍须经过 Authority 与 Autonomy。

授权的任职查询使用开始时间包含、结束时间不包含的实际时间窗；不以“未关闭任职”表示
“当前已生效”。历史 `at` 查询仍能解释已关闭任职，重叠的主职记录不能建立明确授权。
带时区的时间先转换为 UTC，避免提前授予未来权限。

Authority 每次重新读取成员、编制、职位与有效 grant，拒绝跨公司编制/职位，采用当前
授权额度。决策归属快照复用相同的任职时间窗。人事管理中用于表示尚未关闭占用的既有
repository 查询保持原语义；新的时间查询用于授权及决策归属。

身份工厂不提交或回滚事务；后续桥必须在取得动作锁之后调用，并在执行时再次验证。
这些检查本身不是并发锁、操作去重或 Runtime 连接凭据认证。

## 验证

| 检查 | 结果 |
| --- | --- |
| 身份反例，生产修改前 | 41 failed / 8 passed |
| 身份修改后同组测试 | 49 passed |
| 身份最终定向检查，含既有 T5 | 50 passed（48 个新增场景） |
| 授权反例，生产修改前 | 7 failed / 4 passed |
| 非 UTC 时间反例，修复前 | 1 failed |
| 授权中间定向检查，含 RoleContext 与决策 | 69 passed；其后补充时区场景 |
| 最终集成定向检查 | 353 passed / 1 warning，24.83 秒 |
| 完整后端默认顺序 | 1725 passed / 12 deselected / 118 warnings，136.16 秒 |
| make lint | 464 个 Python 文件及 TypeScript / ESLint / Prettier 通过 |

集成检查覆盖身份、授权、公司隔离、工具事务、U5 历史快照、就绪、M2 工具/决策/评审/
交接/黄金路径及职位 repository。既有 T5 测试仅将任务设置为合法的已分配执行状态，
保留原身份来源与结束会话拒绝断言。身份/授权用独立数据库连接提交变更后再检查旧 ORM
会话，避免只证明同一对象上的属性变化。

原始身份证据：`/tmp/eidolon-u7-identity-OHGz3H/` 下的 `red.log`、`green.log`、
`final.log`、`ruff.log`。其他证据在
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u7.CHpV6Rthkx/`：
`authority-red.log`、`authority-timezone-red.log`、`authority-final-focused.log`、
`identity-authority-integrated.log`、`identity-authority-lint.log`、
`identity-authority-full-default.log`、`identity-authority-order-default.json`。
首次集成命令误写了不存在的 U5 测试文件，未执行测试；失败记录保留在
`identity-authority-invalid-selection.log`，更正后运行上述 353 项检查。

没有新增真实模型调用、容器操作或迁移，没有改动开发数据库。完整业务桥的简化、审查及
两种 Runtime 的真实业务往返仍在 U7 剩余验收范围内。
