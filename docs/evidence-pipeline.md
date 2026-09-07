# Evidence Pipeline（P6）—— 真实工作 → 能力证据 → 可审计考核

一句话：**业务系统只产生事实；这里负责把事实解释成能力证据，再由确定性考核引擎
把证据折算成人物能力 —— 每个数字都必须能回链到 Evidence。**

规格细节见 [assessment-system.md](assessment-system.md)（算法）、[competency-system.md]
(competency-system.md)（能力目录）。本文是实现状态文档。

---

## 1. 链路

```
真实工作（Task / Review Decision / Test / SkillUsage / LearningRecord）
    ↓ 业务事件 / 重扫（reconcile）
EvidenceCollector（解释：这些事实能证明哪些能力）
    ↓ EvidenceCandidate（还没落库，只是解释）
EvidenceNormalizer（校验来源存在/公司目录 + 稳定 dedup key 幂等）
    ↓ CompetencyEvidence（strength / reliability / environment / signal / source_ref）
AssessmentRun（profile 驱动，inputs_hash 可重放）
    ↓ AssessmentResult（criterion 观测 + competency 贡献 —— 解释层）
EmployeeCompetency（score / confidence / trend —— 当前投影）
```

## 2. 关键抽象（app/evidence/、app/assessment/、app/services/assessment.py）

| 组件 | 职责 |
| --- | --- |
| `EvidencePolicy` | 来源可靠性、角色 strength、mock 打折(×0.5)、默认 signal、任务类型提示 —— **权重集中一处**，禁止业务代码散落 `if source=="test": weight=...` |
| `EvidenceCollector + Registry` | WorkItem（task/test 按 kind 切换来源）、Review（presenter + decision）、SkillUsage（人评 useful）、Learning；新增来源=新注册 collector |
| `EvidenceCandidate` | 业务事实的解释层（employee/source/observation/competency/signal/strength/reliability/metadata） |
| `EvidenceNormalizer` | 校验 employee/source/competency 合法性 + 稳定 dedup key 幂等 upsert（同事务内也去重） |
| `Reconcile` | 重扫 employee/project 的全部可解释事实（事件丢失兜底；幂等） |
| `pipeline.handle_event` / `EventsConsumer` | 事件驱动：task.completed/failed、project.completed（顺带跑 project_end 考核）；settings 门控 |
| `CompetencyExpectation` | 工作项/职位模板声明"预期验证哪些能力"（PRIMARY/SUPPORTING/OPTIONAL）；**Position 只给模板，不产生证据** |
| `AssessmentProfile(+Criterion+CriterionCompetency)` | 档案版本化（code+version 唯一）；criterion → 多能力映射（contribution_weight） |
| `AssessmentResult` | criterion 观测 + contribution 行：回答"为什么 Communication 是 72" |

## 3. 硬约束（都有测试）

- **Task completed ≠ Competency improved**：数值只经 Assessment 更新，没有 `score+=5`。
- **失败也是证据**：failed 任务/评审用低 signal（30/45）参与聚合（压低观测），
  绝不"失败一次 → score-10"。
- **SkillUsage**：`outcome=useful` 且技能映射能力 ⇒ 高质量证据；`not_useful` ⇒ 无证据
  （禁止"没用过却涨能力"）。
- **Mock/教程**：`environment=mock` + reliability×0.5 —— Tutorial 刷不动能力；跳过
  Classic Snake 不产生证据（无任务/无运行时）。
- **确定性**：同证据 + 同窗口（UTC 日粒度）+ 同档案版本 + 同引擎 ⇒ 同 hash、同输出。
- **公司隔离**：证据按 employee 键归属；跨公司 source 校验拒绝；聚合按员工。
- **Interpretation**：`GET /employees/{id}/competencies/{comp}/explanation` 返回
  历史 run + 最近证据 + 来源分布 + criterion 贡献 + 相关技能。

## 4. API（docs/evidence-pipeline.md §31~§33）

```
GET  /assessment-profiles(+/id)                档案（criteria + 多能力映射）
GET  /employees/{id}/assessments               考核历史（run 摘要）
GET  /assessments/{id}                         单次考核（outputs + results）
POST /employees/{id}/assessments/run           受限触发（只能选档案/按任职；引擎算分）
GET  /employees/{id}/competencies/{comp}/explanation   能力解释
GET  /employees/{id}/competency-evidence?competency=&source_type=&…   证据过滤
```

没有"提交最终分数"入口 —— 想造假得先伪造 Evidence，而 normalizer 的源校验/幂等会顶住。

## 5. 未做（明确不背 P6 的名）

- LLM 定性证据提取（仅预留；P6 完全确定性优先，spec §24）。
- periodic / promotion / position_change 触发、Position Fit 业务计算（P8/P10）。
- 能力衰减曲线（只允许标记 stale，不改分）、自动晋升/推荐（P8+）。
- 事件实时消费在部分模块仍是"事件后重扫"语义（reconcile 保证一致），
  完整 per-event 接线可随业务模块继续接入 pipeline.handle_event。