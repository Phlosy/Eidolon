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

运行器的发现、待处理查询、检查、领取、身份复核、容器句柄读取和结束写入均将完整数据库
操作交给线程，在线程内创建并关闭 Session；请求的公司上下文随操作传递。取消不会丢弃
已开始的操作：尤其领取必须等待实际结果，已经提交的 WorkSession 必须进入本运行器的
清理范围。每次等待后仍复核运行状态与当前身份。U7 `runtime_conversation` 内部的绑定、
投影和工具桥事务仍使用同步路径，本次不声明整条对话链已经异步化。

模型明确 `finish` 只完成本次 WorkSession。自然语言“通过”、异常退出或结束管理会话
都不会推进目标业务；只有已校验的决定或评审结论改变业务状态。结束写入仅更新当前运行器
领取且仍运行的会话，保留其他处理者已经写入的终态。

本进程自己领取的会话最多尝试三次结束写入，失败后分别等待 0.1、0.5 秒；每次重新打开
事务，保留原来的完成摘要或远端停止未知/未确认状态，绝不为补写终态重跑模型对话。
重试只做带身份及 `running` 条件的更新，因此不覆盖并发终态，也可安全处理提交成功但
确认失败的情况。数据库操作仍受驱动的锁等待设置约束，关闭须等待已开始的事务完成。
三次均失败时记录不含连接/异常正文的来源与会话 ID，并在 `finalization_failures` 保留
尚未确认写入的期望终态；排空全部任务后 `stop()` 抛出 `ManagementFinalizationError`，
同一运行器也拒绝重新启动。该有限清理策略不接管其他进程或更早运行留下的会话。

关闭时先停止新准入，再取消并等待自己启动的会话。反复取消等待者仍会等待独立清理结束。
扫描器与全部会话先一起收到取消，再等待各自数据库操作和会话清理，慢扫描不会延后模型
会话的取消。结束写入已开始重试时，关闭保留已确定的原始终态。管理清理耗尽重试时，
lifespan 仍关闭其他生产者并排空事件引擎，然后保留该错误向上传递。
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

审查修复先复现 **9 failed / 2 passed**：真实 SQLite 写锁阻塞共享循环、取消期间数据库
操作未让出循环、结束事务首次失败后没有重试及重试耗尽不报告。受控适配器类型不符和
`supports_controlled_turn=False` 两项原本已通过，均未创建远端会话或模型回合。
生命周期守卫另复现 **2 failed / 4 passed**；取消后数据库异常恢复扫描等待、慢扫描延后
模型取消各复现 **1 failed**。修复后的运行器及事件关闭测试为 **69 passed / 1 warning**；
既有夹具及辅助函数签名保持不变。新增覆盖包括公司上下文跨线程传递、实际领取提交后的
取消清理、提交确认失败、外部终态保护、远端停止事实、重试耗尽后其他会话仍完成清理。
证据为同一目录的 `runner-review-red.log`、`runner-review-lifespan-red.log`、
`runner-review-scan-cancel-error-red.log`、`runner-review-concurrent-shutdown-red.log`、
`runner-review-final-tests.log`、`runner-review-ruff.log`。

这些离线测试使用真实数据库、事件引擎、会话、工具桥和领域写入，仅模型返回由适配器脚本
提供。固定镜像的真实管理/评审验收、U8 整体简化、正式审阅和阶段门禁另行记录。
