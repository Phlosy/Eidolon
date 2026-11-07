# 人才名册（Talent Roster）— P9 实现状态

人才名册 = **公司已招募人才的统一视图**。它聚合 Employee / WorkforceStatus /
CurrentPosition / Runtime / Provider / Traits / Competencies / Assessment / Activity，
但**全部是既有事实的派生出口**（WorkforceStatusResolver / position_repo /
BrainTraits / EmployeeCompetency）—— 不新增 `talent_roster_status` 第二真相。

配套文档：dev 侧规格见 [talent-roster 原设计](talent-roster.md)（P4b）；本页是 P9 落地状态。

## 1. 语义（都有测试）

- `workforce_status` 是派生态；`available`（待分配）与员工 Runtime 活动态
  （idle/working/learning/meeting）是**两个维度**，不混。
- 默认列表**排除已离职**（offboarded）；`include_offboarded=true` 才能看到历史。
- 未评估员工照常可见；Top 能力只从“已评估且 confidence ≥ 0.4”的维度选
  （Score95/Conf8% 只能算 Promising / 待验证，不进 Top）。
- 能力筛选强制“分数 + 置信度”同时给（防止 Score90/Conf5% 误导）；
  人格倾向（Trait）筛选是独立的 Behavioral Preference 维度，不与能力混。
- Assessment Coverage 口径明确：对 10 项通用能力中已评估（score 且 conf≥0.4）
  的占比 / 标签 none|low|medium|high —— 不做模糊的 “Talent Health”。
- **不做**：员工总战斗力、全公司统一 Talent Score、跨职位全球排名。

## 2. API

```
GET /talent-roster?status=&department_id=&position_code=&runtime_type=&provider_id=
                  &competency_code=&min_competency_score=&min_competency_confidence=
                  &trait_code=&min_trait_value=&search=&limit=&offset=&include_offboarded=
```

分页/过滤/搜索 company-scoped；条目含 `runtime/provider/traits_summary/
top_general_competencies/top_professional_competencies/assessment_summary/
recent_activity`。整页富化 ~9 条一次性批量查询（员工数翻三倍增量 ≤3，守卫测试）。

## 3. UI

侧边栏「人才名册」（/talent-roster）：Card/Table 切换（localStorage 持久化）、
过滤器分组（人格倾向 / 通用能力 / 专业能力）、人才卡（头像/状态/当前职位/runtime/
人格 chips/Top 能力/评估覆盖徽章）、搜索；点击进员工完整档案（Employee Detail 仍是
人物 Source UI，不复制第二套详情）。

## 4. 边界

- Roster ≠ Ranking；Candidate Analysis 是 Employee × Position 上下文（见
  candidate-analysis.md）。