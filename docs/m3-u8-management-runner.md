# U8：指定管理者与评审人的受控会话

`ManagementRunner` 使用已有 EventEngine 的轻量通知和有界周期扫描，读取持久事件来源。
启动时也扫描未处理来源；领取成功者才启动会话。事件载荷不提供身份、目标或提示词内容。
管理会话绑定 Project，评审会话绑定 ReviewRequest，二者的 `task_id` 均为空。

运行器只唤醒来源当前指定的接收人。预算准入之后重新检查来源、任职、项目与就绪事实，
领取与关联 WorkSession 使用同一短事务。会话建立和每轮预算等待之后再次校验当前身份
与目标；业务动作继续经过 U7 的真实工具桥、DecisionEnvelope 和权限检查。
第一轮只给出来源及目标引用，项目目标和评审材料由 Agent 经登记的读工具取得。

`EIDOLON_MANAGEMENT_RUNNER_ENABLED` 默认关闭。显式开启但没有预算钩子时，只保存
`budget_unavailable`，不会调用 Runtime。构造器分别接受按来源检查的 `budget_available`
和逐轮检查的 `budget_check`；它们是准入接口，实际 Provider 或中继仍须独立强制执行
金额上限。U17 将处理产品级用量与费用事实，本批不声明已具备该能力。

扫描批量默认 25、并发会话默认 2；等待记录轮转，不因较早的阻塞来源饿死后续事件。
重复事件、重复注册或同时运行的消费者不会重复启动同一领取。旧的 running 会话不在
启动时接管或关闭，跨进程恢复归 U16。

模型明确 `finish` 只完成本次 WorkSession。自然语言“通过”、异常退出或结束管理会话
都不会推进目标业务；只有已校验的决定或评审结论改变业务状态。结束写入仅更新当前运行器
领取且仍运行的会话，保留其他处理者已经写入的终态。

关闭时先停止新准入，再取消并等待自己启动的会话。反复取消等待者仍会等待独立清理结束。
本地 cancelled 不等于远端已停止：已知未确认和提交结果未知分别保存
`remote_stop_unconfirmed`、`remote_stop_unknown`。服务 lifespan 正常和异常退出都会
先停止运行器，再停止其他生产者和排空事件引擎。

测试先记录缺少运行器/配置的 **4 failed**；异常 lifespan 的守卫另记录 **2 failed /
2 passed**。最终新增运行器与扩展生命周期用例 **43 passed**，生命周期位置复查
**4 passed**。父任务检查实际实现和全部测试后，运行共享查询、来源领取、受控对话、
身份/目标约束及 M2 黄金路径等整合检查：**528 passed / 1 warning，45.74 秒**。
`make lint` 通过：482 个 Python 文件格式检查，以及 Web 类型、ESLint、Prettier 检查。

原始日志目录为
`/var/folders/qb/xn8nq_t53rz276g17td9yts40000gn/T/eidolon-m3-u8-df8tj2id`：
`runner-red.log`、`runner-lifecycle-red.log`、`runner-final-tests.log`、
`runner-lifecycle-final.log`、`runner-reads-parent-integrated.log` 与
`runner-reads-parent-lint.log`。

这些离线测试使用真实数据库、事件引擎、会话、工具桥和领域写入，仅模型返回由适配器脚本
提供。固定镜像的真实管理/评审验收、U8 整体简化、正式审阅和阶段门禁另行记录。
