# 岗位能力画像（Position Competency Profile，P7）

一句话：**"一个职位需要什么样的人"** —— 与 [talent-profile.md](talent-profile.md) 的
"这个人实际会什么"（Employee Competency Profile）是两套独立、可版本化的数据模型。
本阶段**不计算 Position Fit / 推荐 / 晋升**（P8 再把这些左右拼起来）。

实现状态：v17（2026-09）。数据在 `position_profile_versions` +
`position_competency_requirements`；服务在 `app/services/position_profile.py`；
模板 seed 在 `position_profile_templates.py`；API 在 `app/api/v1/position_profiles.py`。

---

## 1. 边界（都有测试）

- Position **只回答岗位需要什么**；Position 不能修改 EmployeeCompetency，
  不改 Runtime，不参与成功判定。
- `Position Requirement ≠ Employee Competency ≠ Task Success Probability`。
- Employee 的能力仍只能来自 Evidence → AssessmentRun → EmployeeCompetency（P6 原样）。
- 缺失某能力 ≠ 下滑；REQUIRED 缺失才是未来 Gap，PREFERRED 缺失是发展机会。

## 2. Requirement 语义

| 字段 | 语义 |
| --- | --- |
| `requirement_type` | `required`（缺=Competency Gap）/ `preferred`（缺=开发机会）；OPTIONAL 预留 |
| `minimum_score` | 最低胜任门槛（0-100） |
| `target_score` | 该岗位理想水平（0-100；≥ minimum，校验） |
| `minimum_confidence` | 分数多可信才作数（0-1）—— P8 用 Score+Confidence 算 Match/Uncertainty |
| `critical` | 职位关键能力（P8 把 Critical Gap 与普通 Gap 分开） |
| `weight` | 岗位最看重哪些（≠ Assessment Weight；不混表；Fit 按分组归一） |

## 3. 版本化

```
PositionProfileVersion: position_id × version（unique）
  status: draft | active | retired
  effective_from / effective_to / published_at / published_by / note
```

- 一个定义**最多一个 ACTIVE**（部分唯一索引 → 只写 status='active'）。
- DRAFT 可编辑；ACTIVE/RETIRED **不可静默修改** —— 改正式标准 = 新建版本；
  Publish 时旧 ACTIVE → RETIRED。
- 历史语义：员工在某段时间任职时用的是当时的版本；`PositionAssignment` /
  `AssessmentRun` 未来可据此恢复"当时岗位要求什么"（P8 Historical Fit 的基础）。

## 4. Not Configured ≠ 空画像

没有 profile 的职位显示 **Not Configured**（integrity code `NO_ACTIVE_PROFILE`），
绝不"自动猜一个标准"；列表空也不等于"没有要求"。

## 5. Coverage / Integrity（派生，不落库）

- `AssessmentCoverage`：required 能力被绑定考核档案（assessment_criteria →
  criterion_competencies 映射）覆盖多少；未覆盖的 required 列出（如 SE 的
  backend_engineering），提示"岗位标准与考核制度不匹配"，不是 Fit。
- `ProfileIntegrity` codes：`VALID` / `NO_ACTIVE_PROFILE` / `NO_ASSESSMENT_PROFILE` /
  `MISSING_COMPETENCY` / `INVALID_SCORE_RANGE` / `ASSESSMENT_GAP`；`read_only=True`。
- 一律读时派生，不存 `coverage_percent`（ADR-12）。

## 6. 默认模板（seed 幂等，code 定位）

Engineer / QA / Researcher / Product Manager / CEO-Manager 五套 ACTIVE v1，绑定 P6
考核档案（engineer→software_engineer、qa→qa_engineer、researcher→researcher、
pm/ceo→manager）。数值是数据不是硬编码；系统模板只读，公司用 Clone/Apply Template
生成公司自有 Draft。

## 7. API / UI

```
GET  /position-profiles                         org 列表摘要（batch，无 N+1）
GET  /position-profiles/templates               可 Clone 模板
GET  /position-definitions/{id}/competency-profile   详情（版本历史）
POST /position-definitions/{id}/competency-profile/versions   新建 Draft
POST /position-definitions/{id}/competency-profile/clone     Clone
POST /position-profile-versions/{id}/requirements            Add（仅 DRAFT）
PATCH/DELETE …/requirements/{rid}                            Update/Remove（仅 DRAFT）
POST …/publish | …/retire
```

UI：侧边栏「职位标准」→ 列表 → 详情（General/Professional、REQUIRED/PREFERRED、
Critical、min/target/conf/weight + RequirementBar，**不显示员工当前值**，P8 才叠加）；
Draft 编辑器（能力从目录选择，禁止手输）。

## 8. 明确不做（P8+）

Position Fit / Talent Recommendation / Promotion Readiness / Skill Gap——本阶段
只把"岗位标准"这一侧做对。