# Position Fit（P8）—— 岗位匹配度与能力差距

一句话：**把"这个人实际会什么"（Employee Competency Profile）与"这个职位需要什么"
（Position Competency Profile）连接成人才管理决策指标。** 它**不是**任务成功率、
**不是**晋升决定、**不是**能力分数本身 —— 最终任命仍由 Human/Playwright 决定。

实现状态：`app/talent/fit/`（policy/evaluator/engine/hashing/serializer/service），
API `GET /employees/{id}/position-fit/{position_definition_id}`（读端点）。

---

## 1. 最重要语义：Unknown != Bad

严格区分三件事：

| 情况 | 结论 |
| --- | --- |
| 能力不足（score < minimum，且有足够 confidence） | 缺口（Gap） |
| 没有足够 Evidence（UNRATED / INSUFFICIENT_CONFIDENCE） | **不确定（Unknown）** —— 不是 0 分、不是失败 |
| 证据不足但分数高（如 82 分 @ 18% confidence < 要求 50%） | `INSUFFICIENT_CONFIDENCE` |

Unknown **不进 Known Fit 的惩罚**（不把"没测过"当"能力为 0"），但 Coverage 永远基于
**全部需求** —— unknown 不会让 coverage 抬高成 100%。UI 由此可以诚实显示：
"当前已观察到的能力与职位较匹配，但证据覆盖不足，结论不可靠"。

## 2. Requirement 评估状态机

`UNRATED → INSUFFICIENT_CONFIDENCE → BELOW_MINIMUM → MEETS_MINIMUM → MEETS_TARGET`
（顺序即判定）：无能力/score null ⇒ UNRATED；confidence < minimum_confidence ⇒
INSUFFICIENT_CONFIDENCE；score < minimum ⇒ BELOW_MINIMUM；`[minimum, target)` ⇒
MEETS_MINIMUM；≥ target ⇒ MEETS_TARGET（EXCEEDS_TARGET 预留）。

REQUIRED 缺 ⇒ 未来 Qualification Gap；PREFERRED 缺 ⇒ 发展机会（绝不判"职位失败"）。

## 3. 分类输出

- **Strengths**：score ≥ target 且 confidence ≥ minimum_confidence。
- **Gaps**：`CRITICAL_GAP`（关键 + below minimum）/ `REQUIRED_GAP` /
  `TARGET_GAP`（required 达最低未达目标）/ `PREFERRED_GAP`。
- **Uncertainties**：`CRITICAL_UNCERTAINTY` / `REQUIRED_UNCERTAINTY`
  （UNRATED / INSUFFICIENT_CONFIDENCE）—— 关键 unknown 是"关键不确定"，不是失败。
- **Development Opportunities**：preferred 达最低未达目标、或 preferred 未评估
  （标 Not Assessed，不是 Weakness）。

## 4. 数字怎么算（确定性、集中、可版本化）

- **Known Fit**：只对 known requirements（有 score 且置信达标）在各自组内按 weight
  归一后加权 ——未知不参与惩罚。
- **normalized_fit（target 曲线）**：BELOW_MINIMUM → (score/minimum)×0.69；
  MEETS_MINIMUM → 0.70 线性到 1.0；MEETS_TARGET → 1.0。
- **Fit Confidence = 加权 known competency confidence × required_coverage**（0..1）。
- **Coverage**：required/preferred/全部 —— 基于全部需求。
- **Qualification**：CRITICAL_GAP ⇒ NOT_QUALIFIED；required 覆盖不足或关键 unknown ⇒
  INSUFFICIENT_DATA（关键 unknown 永远不能 QUALIFIED）；required gap ⇒
  QUALIFIED_WITH_GAPS；否则 QUALIFIED。
- **FitStatus**：CRITICAL_GAP 优先于覆盖度报告（总体接近但有关键短板要可见）；
  覆盖不足/关键 unknown ⇒ INSUFFICIENT_DATA（不给 73% 虚假精确感）；然后
  STRONG/PARTIAL/WEAK。
- `inputs_hash` 含需求（含权重/门槛/关键位）+ 员工能力（score/conf/last_assessed）+
  profile_version + 引擎/策略版本；`POSITION_FIT_ENGINE_VERSION=v1`。
- 无 ACTIVE Profile ⇒ `NOT_EVALUABLE`（绝不默认 100%）。

## 5. 边界（都有测试/守卫）

- Fit ≠ Task Success Probability；Fit 不改 Runtime、不改 EmployeeCompetency、
  不改 Assignment（玩家仍可任命 Fit 低的人，只提示不硬拦）。
- **Trait 不参与 Fit**：fit 包禁止读取 EmployeeBrain traits（源码级守卫）；
  Behavior Preference 是未来单独维度。
- Info 不落库：Fit 是派生 read model；结果可版本化、可追溯、同输入同输出。
- 公司隔离：员工与职位必须同一公司（或系统模板上下文），跨公司 404。
- 不做全公司 ranking / recommended-candidates（P9）。

## 6. UI

- Position Detail → "评估人才"：选员工 → Fit 分析（Fit+Confidence+Coverage 并列，
  Status/Qualification；Reason code 由前端 i18n）。
- Employee Detail → "岗位匹配" tab：当前任职职位 Fit（无任职/未配置显示中性提示）。
- RequirementBar 只在 Fit 分析中叠加员工当前值（P7 编辑器仍不显示）。
- 颜色语义：Strength=success、Meets Minimum=info、Target Gap=warning-light、
  Required/Critical Gap=warning/danger、Uncertain=info、Unrated=muted（不是红色）。
- 点击任一 requirement → P6 Competency Explanation（Score/Confidence/Evidence/
  考核历史 + 岗位要求 min/target/min_confidence 并列）。