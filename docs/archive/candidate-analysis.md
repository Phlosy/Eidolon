# 职位候选分析（Candidate Analysis，P9）

**Position → People**：给一个职位（PositionDefinition / Slot）找候选并比较。
与名册的 **Person → Position**（评估职位入口）共用同一个 P8 `PositionFitService` ——
**没有第二套 Fit 公式**。

## 1. 为什么不做“按 Fit 排一列”

Charlie Fit 91% @ Conf 22% vs David 84% @ 91% —— 只排一个数会制造虚假精确感。
候选分析必须同时看 Qualification / Fit / Fit Confidence / Required Coverage /
Critical Gaps / Critical Uncertainty / Workforce Status，但**不混成一个 AI 综合分**。

## 2. Band 分组（CandidateAnalysisPolicy v1，集中配置）

| Band | 规则 |
| --- | --- |
| `RECOMMENDED` | Qualification=QUALIFIED 且 fit_confidence ≥ 0.5 |
| `VIABLE` | QUALIFIED_WITH_GAPS 且无技术缺口（required/target gap 之外）且置信达标 |
| `DEVELOPMENTAL` | 存在 REQUIRED_GAP/TARGET_GAP，证据足够，无 Critical Gap |
| `NEEDS_EVIDENCE` | INSUFFICIENT_DATA（含关键 Critical Uncertainty） |
| `CRITICAL_GAP` | 存在真实 CRITICAL_GAP |

Unknown 候选（Emma：Known 95% 但 Coverage 25%）归 **NEEDS_EVIDENCE**，
而不是“Rank #8 / 最差”。组内排序（确定性）：
`required_coverage desc → fit_confidence desc → known_fit desc → employee_id asc`。

## 3. 计算 / 性能

`calculate_many`：一个职位 × 一批员工，**共享** position profile / requirements /
definitions/domains，员工能力一次批量取 —— 候选不逐人重读同一画像。
批量 `inputs_hash`（position + profile_version + candidate ids + engine/policy）。
N+1 守卫：100 + 50 员工增量 ≤ 6 查询。Company 隔离：A 公司职位绝不读 B 公司员工。

## 4. 人仍然决定

候选分析只读：没有自动任命/调岗/晋升端点。UI 的「分配」调**现有 Position Assignment
Workflow**，且必须先看预览（Fit/Confidence/Coverage/Critical Gaps/资格）；低 Fit 或
数据不足只 Warning，**不禁止** —— 玩家可以任命任何人。真正 Assign 才进既有 Audit。

## 5. 对比

Compare Tray（2~4 人）逐项对照每个 Position Requirement（Score/Confidence）；
**Unknown 显示 — / 未评估**（ADR-12），不是 0。

## 6. 明确不做（P10+）

自动分配/调岗/晋升、Career Path、Succession、Development Plan 自动创建、
LearningPriority 自动写入、FitSnapshot 历史、Overall Talent Score、跨职位全球排名。