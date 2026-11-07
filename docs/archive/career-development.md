# 职业发展与人才培养（Career & Talent Development，P10）

**Decision Support，不是 Autonomous HR Engine。** 复用 P5-P9 全部底层：
Employee Competency / Evidence / Assessment / Position Fit / Candidate Analysis，
回答"这个人未来可以去哪里、为什么还没准备好、缺的是能力还是 Evidence、该通过什么
真实经历补足" —— 但任何任命/调岗/晋升/代理**仍由 Human 显式确认**。

实现状态：v18（2026-09）。模型 `models/career.py`，服务 `services/career.py`，
API `api/v1/career.py`，UI 员工详情「发展」tab。

## 1. 边界（都有测试）

- **CareerEvent = 职业履历审计**（Joined/Promoted/Transferred/…）——
  **不是当前职位真相**（真相仍是 PositionAssignment）。
- **Career Recommendation ≠ Promotion Decision**；Readiness ≠ Fit ≠ 能力。
- **Unknown ≠ Weak**：UNRATED / 低置信 → `EVIDENCE_GAP`（需要评估/项目机会/评审），
  不是 `COMPETENCY_GAP`（需要学习/实践）。已达标=强项，不生成发展项。
- Development Plan **只给建议**；不能 PATCH 能力；动作由 Human 触发
  （如 Create Learning Priority —— 显式、幂等、`source=development_plan`，
  不自动启动 LearningSession / Agent / 烧 Token）。
- 晋升/调岗底层 = `release old + assign new`（复用 PositionAssignment），
  再记 `CareerEvent`，随后 P4d 异步同步权限；低匹配只 Warning，不拦。

## 2. Career Path（分支，不强制）

`career_paths` / `career_path_steps`：PROMOTION/LATERAL/SPECIALIZATION/MANAGEMENT/
CROSS_FUNCTIONAL；一对多分支；`recommended` 只是"被认可的发展" —— 用户可以
Researcher → Product Manager 等任意调岗（系统只分析差距/风险/请求确认，不硬阻止）。
默认 seed 只对**已存在的职位定义**接线（不自动造职位）；公司可 Clone/Customize。

## 3. Career Readiness（结构化，不造 87%）

`readiness_for` = P8 Fit（**同一引擎，无第二套公式**）+ Experience（任务/项目/评审/
考核数）+ Tenure + Development 进度：

| Status | 条件 |
| --- | --- |
| READY | Target QUALIFIED + Evidence 足够 + 无 Critical Gap + 必要 Experience 满足 |
| NEAR_READY | 达最低、目标未全达（仅 target 缺口） |
| DEVELOPMENT_NEEDED | 存在 Required Gap（能力不足 → 学习/实践） |
| NEEDS_EVIDENCE | 主要是 Evidence 不足（→ 评估/项目机会/评审） |
| CRITICAL_GAPS | 存在关键能力真实不足 |

输出 `reasons[]`（机器码）+ `inputs_hash`（含 fit hash、experience、tenure、版本）——
确定性、可解释、无 LLM。

## 4. Development Plan & Reconciler

- Plan（DRAFT→ACTIVE→PAUSED/COMPLETED/CANCELLED）+ Items（按 Competency 唯一；
  need_type 来自 Fit：COMPETENCY/TARGET/EVIDENCE/SKILL/EXPERIENCE/ASSESSMENT_GAP）。
- 进度**由 Reconciler 按真实 Competency/Evidence 派生**（Assessment 完成后自动重判，
  不靠手点"完成"）：达标（score≥target 且 conf≥target_conf）→ COMPLETED；
  全部完成 → Plan COMPLETED。
- Skill/Experience Gap 保留类型；本次从 Fit 派生（skill 映射数据在 P6 已有，但
  DevelopmentNeed 的 skill/experience 来源留 P11 对接）。

## 5. Promote / Transfer

`POST /employees/{id}/career/promote|transfer`：Human 显式触发；读 readiness →
（低匹配 Warning：Critical Gaps / Needs Evidence 显示"高风险，仍可继续"）→
`position_service.assign_position`（复用任职工作流 + P4d）→ 记 `CareerEvent` →
返回 warnings。人级数据（brain/runtime/memory/knowledge/skills/evidence/history）逐项保留。

## 6. 明确不做（P11+）

自动晋升/调岗/任命、Agent 代做 HR 决策、Full Succession Planning、薪酬/绩效排名、
强制发展计划、自动 Web Learning、Fit 影响 Runtime Success、Overall Talent Score、
XP 数值（进度用真实 Competency/Evidence/Assessment/Experience 表达）。