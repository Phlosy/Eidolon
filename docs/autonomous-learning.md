# 自主学习（Autonomous Learning，P11）

**WorkSession = 为公司项目工作；LearningSession = 为员工自身成长学习。**

## 原则
- Learning ≠ Competency Improvement：学习产出 KnowledgeItem(private, FRESH) /
  SkillCandidate（validation=candidate）/ OpenQuestion / 可选 LearningPriority ——
  **学习绝不直接改 EmployeeCompetency**（源码守卫）；能力只能经
  Practice/真实工作 → Evidence → Assessment。
- Learned ≠ Truth：Web Research 每条带 Source URL/Title/Retrieved At/Claim/
  Source Quality/Confidence/environment（mock 明标）；Knowledge 默认 private，
  升级仍走 Proposal → Review → Promotion。新鲜度 fresh/stale 预留（stale 检索可降置信）。
- 预算硬限制（Company.settings.learning_policy + EmployeeBrain.learning_policy
  员工覆盖；默认 **autonomous_learning_enabled=false** + 前端成本警告）：
  日 token/cost/session/time 上限，`reserve_budget` 原子扣减（行锁串行化，
  防并发双超）；超限 `WAITING_BUDGET`，绝不偷偷继续。
- 触发：Scheduler（idle ≥ idle_delay、无 pending 任务/无 running 学习、预算允许、
  存在高价值候选）；Planner 优先级 DevPlan > 项目将要需求 > 重复失败 > 既有
  LearningPriority > SkillCandidate > 兴趣（Desire）；**没有真实需求就不学**
  （不空闲必学）。LearningSession 幂等（同主题并行拒）+ 冷却。
- Crash ⇒ FAILED（保留已消耗预算）；会话记录 runtime/provider/model；
  学习事件（learning.started/completed/…）只把有意义事件进 Activity Feed。

## API
GET/PATCH /company/learning-policy；GET/PATCH /employees/{id}/learning-policy；
GET /employees/{id}/learning-usage；GET /employees/{id}/learning-sessions；
POST /employees/{id}/learning-sessions（手工）；GET /learning-sessions/{id}；
POST /learning-sessions/{id}/cancel；POST /company/learning/run（调度触发）。
