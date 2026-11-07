# U9 缺口事实与学习请求验收

日期：2026-09-20。实现基线：`db3b94ddf90d68d2bf5e3ca16dd5448cd5e601fd`。对应 [M3 计划 U9](m3-implementation-plan.md#u9-m33-gap-facts-and-learning-tools) / R3。

U9 已实现并完成本地整合验证。M3.3 仍在进行，审批、招聘和七类动作的完整映射继续由 U10–U12 交付。

## 当前行为

- `inspect_gap` 与正式 `GET /api/v1/tasks/{task_id}/gap` 共用 [gap_facts](../apps/server/app/work/gap_facts.py)，返回任务需求、Person 能力、负载与就绪事实。未知能力保持未知，不生成推荐动作；跨公司目标拒绝。默认公司全员查询不会被显式候选列表的 200 人限制截断。
- `search_market` / `inspect_candidate` 与现有市场 API 共用 [T2 白名单投影](../apps/server/app/talent/market/read_model.py)。职位按公司校验，下架候选不再公开；未评估候选仍保留。
- `create_learning_priority` / `request_training` 必须经过本项目管理会话的 `enroll_learning` 决策信封、当前 Authority 和 U7 持久操作回执。目标按实际公司、部门和有效任职校验；评审会话、其他项目和伪造的孤立决策不能授权学习写入。
- 新优先级归 Person；同主题已有优先级及其来源、分数、原因保持不变。训练只创建计划事实；同 Person、项目与主题的未结束请求在参数相同时复用，参数冲突拒绝。运行中、等预算、暂停或失败的真实状态不被改写成完成。
- 相同操作键返回原审计回执；不同操作键并发提交同一未结束训练只创建一条。实际数据库审计插入失败时，学习效果随事务回滚。

## 唯一写入与读面

优先级沿用 `learning/priorities.py` 与 Person repository；训练沿用 `services/learning.py` 创建 `LearningSession`。新工具不调用训练执行器，不生成成果、不预留或扣除预算、不改变 competency；以后实际学习的成果仍归既有 Evidence 管道。API 与工具共用当前学习会话序列化。

[M2 兼容增补](m2-freeze.md#u9缺口读面与学习请求实施前登记2026-09-20) 在生产修改前登记。精确工具目录与决策语义枚举只增补两个 `required` 工具，原有 `optional` 集合和拒绝路径保留。没有新增模型、迁移或共享夹具改动。

## 验证

[结构化证据](evidence/m3-u9-verification.json) 保存命令、原始日志路径、哈希与代码指纹；[行为测试](../apps/server/tests/test_m3_gap_actions.py) 包含 36 个场景。

| 检查 | 结果 |
| --- | --- |
| 实施前 T2 / Person / learning 锚点 | 25 passed |
| 实施前新增功能用例 | 32 failed；原因为工具尚未注册，不宣称此时已触发每个内部守卫 |
| 边界反例 | 可选 null 限制、P9 市场导入守卫、公司人数超过 200 的查询，均记录修复前失败 |
| 工作包组合回归 | 156 passed，包含 M3 契约与既有工具、市场、学习、U7/U8 锚点 |
| 首次后端完整回归 | 2266 passed / 1 failed：原 M2 决策语义清单未列入新增工具；已补齐精确集合 |
| 决策清单修正后定向回归 | 63 passed |
| 最终后端完整回归 | 2267 passed, 16 deselected, 118 warnings in 249.75s (0:04:09) |
| 静态检查 | Ruff、488 个 Python 文件格式、TypeScript、ESLint、Prettier 通过；修改后的枚举测试另行通过 Ruff 与 diff 检查 |
| 前端 | 392 tests / 89 files；生产构建通过，有既有 chunk-size 提示 |

并发和回滚测试使用真实隔离 SQLite 数据库，经 RuntimeBridge → RuntimeOperation → DecisionEnvelope → ToolExecutor → 学习服务 → ToolAudit；API 一致性使用实际路由，只固定就绪查询的观测时钟。

## 证据边界

本包未进行付费模型调用、真实训练、Docker 操作或开发库迁移；数据库集成不能替代真实模型验收。PostgreSQL 锁行为未实测。U9 的父级整合检查和阶段末正式审阅分别记录，本次不宣称新增独立审阅已完成。用户 `tmp/` 保持原样，仅创建本地提交。
