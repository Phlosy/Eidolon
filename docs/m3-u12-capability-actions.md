# U12 七类能力动作与组合结果

日期：2026-09-20。实现基线：`410387a`。对应 M3 计划 U12、R3、P9–P13 / P33。
本包实现与定向验证已完成；M3.3 阶段简化、正式审阅与最终门禁见
[阶段验收](m3-3-capability-actions.md)。
[结构化证据](evidence/m3-u12-verification.json) 保存命令、退出码、日志及代码哈希。

## 可用入口

| 能力动作 | 实际工具 | 执行边界 |
| --- | --- | --- |
| learn | create_learning_priority / request_training | 记录 Person 学习意图，不直接加能力或花费 |
| use_internal_employee | assign_task | Manager 指定员工；未指派的 backlog 任务进入 todo |
| reassign | assign_task / update_task | 明确改派或调整任务，原负责人和决策署名可追溯 |
| recruit | recruit_from_market | 招聘与支出双授权，固定条款经 Owner 确认后成交 |
| revise_task | update_task / replan_project | 改定义或发起重规划，不自动生成替代图 |
| split_task | create_task / create_dependency | Agent 明确指定节点和边，系统校验 DAG |
| delegate | delegate_project | 改当前管理指针，历史决策仍属于原 Manager |

Runtime 实际收到的工具目录增加 `capability_actions`，从同一契约映射派生，没有第二份
动作选择策略。七类动作全部有真实工具，P33 不再接受 M3.3 的 `owner_stage` 占位。
`replan_project` 属于改方案，不新增第八种动作。读取缺口不会创建学习、招聘、任务或决策。

补齐 `assign_task` 对 failed 任务的终态拒绝，避免失败后改写历史负责人；done / cancelled
继续拒绝。blocked 任务可由 Manager 明确改派，但仍保持 blocked，不能借指派自动解除阻塞。
普通领域状态迁移仍走原状态机，没有 force 重开终态的捷径。

## 组合与恢复

组合复用原有决策信封及 U7 持久回执。Agent 可以先创建节点，再用返回的实际 ID 提交边，
并用 parent_decision_id 关联两次决定；系统不猜 ID，不替 Agent 选择或补齐依赖。

测试实际创建一个子任务，再尝试连接会成环的边。第一项保存，第二项明确拒绝，决策是
PARTIALLY_APPLIED，读面只计一次成功。另一路在第一项提交后让独立 Python 进程
`os._exit(23)`，由第二个进程使用原请求恢复；系统复用第一份 ToolAudit，只执行尚未完成的边。
恢复和再次重放都不重复创建节点、来源事件或审计。这里验证的是宿主动作进程恢复，
没有冒充 U16 / U30 的远端运行接管验收。

## 验证

新增 30 个场景覆盖：实际 Runtime 目录、三种任务终态、初次指派与已有任务改派、九个真实
工具分别缺参和缺权、只读缺口无业务效果、多轮信封拆图、图环部分失败、独立进程恢复、
修改方案及管理委派。既有学习、确认、实际招聘、评审终态及返工计数测试一起运行。
受控对话测试进一步确认目录中的动作映射进入实际发送给适配器的首轮消息；适配器为脚本替身，
本包没有新增付费模型调用。真实 Manager 招聘证据保留在 U11，最终 A/B/C 仍归 U28–U30。

| 检查 | 结果 |
| --- | --- |
| 实施前相关锚点 | 361 passed |
| 先行反例 | 3 failed / 2 passed：学习占位、目录无映射、failed 任务可改派 |
| 修复后先行反例 | 5 passed |
| 中间组合 | 204 passed / 1 failed：旧欠账总表仍要求能力动作欠账非空 |
| 最终组合回归 | 599 passed / 1 warning，57.73 秒 |
| 变更 Python 检查 | Ruff、格式检查通过，共 6 个文件 |

欠账总表只对已经落地的能力动作允许空集，其余未完成欠账守卫保留。
无需修改决策聚合或持久回执实现，实际组合证据确认它们已满足本包要求。
本包没有迁移、共享夹具、前端界面、Docker 或开发数据库改动；阶段完整检查由收尾记录承接。
当前任务内联实现，子代理容量限制不算独立审阅通过。仅本地分包提交，用户 tmp/ 未读取或改动。
