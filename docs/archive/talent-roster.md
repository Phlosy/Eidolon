# 人才名册（Talent Roster）

上级约束：`docs/workforce-domain-refactor.md`
命名：中文 **人才名册**，英文 **Talent Roster**。
**不用** Employee Pool / Talent Pool —— 它不是"一堆待投放的资源"，而是"已经加入公司的人的正式名册"。

---

## 1. 模块边界

名册 = **公司已经拥有的 AI 人才的集合**，与"有没有职位"无关。

```
In   ：Recruitment（招募完成 → 入册）
Out  ：Position Assignment（把人交给组织经营，本模块只读它的结果）
Owns ：员工身份、人级资源状态、可见性（是否入册/离职）、名册查询与筛选
Never owns：职位定义、编制、权限包、考核档案
```

入册的充分条件（全部满足才是"正式 Employee"）：
`Employee` 行存在 + 人级资源就绪（workspace / git / docs / runtime / provider binding 可选 / brain 已配置）

- `lifecycle_status ∉ {offboarded}`。

---

## 2. Workforce Status（§8 的落地口径）

```
RECRUITING    招募流程中（尚未入册；草稿/预创建态，见 §5）
ONBOARDING    已入册但人级 Provisioning 未完成
AVAILABLE     已正式加入、人级资源就绪、当前无生效 PRIMARY 任职   ← 合法常态，不是异常
ASSIGNED      有生效 PRIMARY 任职
TRANSFERRING  任职变更进行中（旧任职已关，新任职 Provisioning 未结束）
SUSPENDED     人被暂停（职位保留但停权）
OFFBOARDING   离职流程中
OFFBOARDED    已离职（保留历史，不在名册默认视图）
```

**派生规则（唯一入口：`WorkforceStatusResolver`，ADR-4 拍板正式命名）**

两个正交轴（拍板口径）：

```text
Lifecycle Axis （来自 employees.lifecycle_status）
  ONBOARDING · ACTIVE · SUSPENDED · TRANSFERRING · OFFBOARDING · OFFBOARDED

Assignment Axis （来自 employments / PositionAssignment）
  no active assignment · active PRIMARY · transferring（旧关新未开之间）
```

两个轴**都不加状态列**；前端看到的 `AVAILABLE` / `ASSIGNED` / … 是下面这个
唯一 resolver 的输出（API / service / 前端均不得各自再推一遍）：

```python
# app/workforce/status.py —— 单一事实源
resolve(employee, lifecycle, active_assignments) -> WorkforceStatus
```

```text
resolve(employee, lifecycle, active_assignments):
    if lifecycle == offboarded:     OFFBOARDED
    if lifecycle == offboarding:    OFFBOARDING
    if lifecycle == suspended:      SUSPENDED
    if lifecycle == transferring:   TRANSFERRING      # 只由 lifecycle 轴决定，见下方修订 3
    if lifecycle in (pending, onboarding): ONBOARDING
    if lifecycle == active:
        return ASSIGNED  if occupies_establishment(active_assignments) else AVAILABLE
    return ONBOARDING                                  # 未知 lifecycle：保守，不猜
```

三处 P4a 实测修订（规则以代码 `app/workforce/status.py` 为准，本节跟着它改）：

1. **`ASSIGNED` 要求"占住编制"，不是"有主职行"。**
   `occupies_establishment()` = 存在生效 PRIMARY **且其 `position_slot_id` 能解析到坑与定义**。
   理由：v0.4 遗留下来 11 条生效主职根本没有坑（老代码写任职从不填 `position_id`），
   那记录的是"曾被雇入"，不是"现在承担编制"。按旧规则会把这 11 条报成 `ASSIGNED`，
   等于凭空造出一个不存在的职位 —— 正是本次重构要消灭的东西。
2. **`person_resources_not_ready` 这个判据被删掉。** `lifecycle_status` 已经由 provisioning
   job 收尾翻转（`pending/onboarding → active`）；再读一次 runtime 会让同一件事有两个判据，
   两个判据不一致时没人能解释状态。
3. **`TRANSFERRING` 不许从"两条 assignment 同时存在"反推**（拍板）。effective date 交叠、
   代理任职（`acting`）、兼任都会让这种反推误判；转岗是工作流事实，归 lifecycle 轴。

悬空引用同样按"宁缺不错"处理：`position_slot_id` 指向不存在的坑（本项目 SQLite 不开
`PRAGMA foreign_keys`，所以没有 DB 外键兜着），resolver 会剔除这条任职并如实报
`AVAILABLE`，而不是给这个人配一个解析不出来的职位。

- `RECRUITING` 只在"招募草稿"存在时出现在候选列表，不落 `employees` 行（避免半个人进名册）。
- `counts()` 直接给 `on_roster`（= assigned + available + transferring）：`available` 是
  "已就绪但没编制"，它是 `on_roster` 的子集而不是兄弟，`available + assigned` 当在岗数会漏。
- 两条读法（详情页 `resolve()` / 名册 `views()`）**共用同一份坑→定义解析**（实例内缓存）。
  不一致时最容易的症状是"详情页说已分配、名册说待分配"，所以这条有测试。
- 状态**不入库**：由 `WorkforceStatusResolver` 读时计算，杜绝漂移。
  将来若名册需对百万级数据筛 `AVAILABLE`，再引入 materialized status / generated column /
  denormalized read model，但那时**写入方仍只能是这个 resolver**（现在不留半实现列）。
- `AVAILABLE` 的员工必须**可以**被派任务（人级 runtime 就绪），只是不承担职位职责；
  不允许任何代码因为"没有职位"而把该员工排除在工作循环之外 —— 这条有测试（§6 #4）。

---

## 3. API 契约

```
GET  /api/v1/talent-roster                      名册列表（筛选见 §3.1）
GET  /api/v1/employees/{id}                     详情（含 current_position / workforce_status）
POST /api/v1/talent-roster/recruit              招募 → 入册（不接受 position 字段）
POST /api/v1/talent-roster/{id}/assignments     分配职位（→ position-system §4）
POST /api/v1/talent-roster/{id}/unassign        卸任（回 AVAILABLE，人级资源不动）
POST /api/v1/talent-roster/{id}/suspend         暂停
POST /api/v1/talent-roster/{id}/offboard        离职
```

P4b 落地差额（写在这里，免得前端按上面的清单去调不存在的端点）：

- 已可用：`GET /talent-roster`（含 `?status=&department_id=&position_code=none`）、
  `GET /talent-roster/stats`、`GET /talent-roster/integrity`、
  `GET/POST /talent-roster/{id}/assignments`、`POST /talent-roster/{id}/unassign`、
  以及单人派生视图 `GET /talent-roster/{id}`。
- `recruit / suspend / offboard` **不在名册上**：它们仍是 v0.4 生命周期端点
  （`POST /employees/onboard`、`/employees/{id}/suspend`、`/offboard`）。
  名册刻意不提供 recruit —— "招聘与任命是两个流程"这条裁定要体现在端点上，
  否则一次调用就能同时造人 + 塞职位，两个轴的边界会在 API 层重新糊掉。
- 列表项尚无 `position_fit` / `actions` / `traits_summary` / `runtime` / `provider` /
  `top_*_competencies`。特别是 `actions`：它由服务端按状态给（`AVAILABLE` 才有
  `assign_position`），必须与 fit 一起做，提前给一个假的 `actions` 数组就是
  §3.2 结尾警告的"第二个状态机"。

### 3.1 查询参数

| 参数                                          | 语义                                                              |
| --------------------------------------------- | ----------------------------------------------------------------- |
| `status`                                      | 多值，按 §2 派生态过滤                                            |
| `department_id`                               | 按**当前任职的部门**过滤（不是 `employees.department_id` 历史列） |
| `position_code`                               | 按当前任职的定义 code 过滤；`position_code=none` = 未分配         |
| `runtime_type`                                | Hermes / OpenClaw / mock                                          |
| `competency` + `min_score` + `min_confidence` | 能力筛选（三者必须同时给，避免"高 score 低证据"混入）             |
| `view`                                        | `card` \| `table`（服务端只回数据，视图是前端选择）               |

### 3.2 列表项（卡片所需，一次查询拿全，禁止 N+1）

```json
{
  "employee_id": 24,
  "name": "Charlie",
  "slug": "charlie",
  "workforce_status": "AVAILABLE",
  "current_position": null,
  "position_fit": null,
  "runtime": { "type": "hermes", "status": "ready" },
  "provider": { "name": "OpenRouter", "model": "…", "alias": "…" },
  "brain": {
    "configured": true,
    "policy_version": "behavior-v1",
    "band": "high"
  },
  "person_resources": {
    "workspace": "ready",
    "git": "ready",
    "docs": "ready",
    "memory": "ready"
  },
  "top_general_competencies": [
    { "code": "execution", "score": 84, "confidence": 0.62 }
  ],
  "top_professional_competencies": [
    { "code": "frontend_engineering", "score": 92, "confidence": 0.35 }
  ],
  "traits_summary": [{ "code": "curiosity", "value": 0.78 }],
  "career": { "hired_at": "…", "stints": 0 },
  "actions": ["view", "assign_position", "suspend", "offboard"]
}
```

`actions` 由服务端按状态给（例如 `AVAILABLE` 才有 `assign_position`，`ASSIGNED` 才有 `transfer`），
前端不自己推断 —— 避免第二个状态机。

### 3.3 单人详情 = `EmployeeDetailOut`（/employees/{id} 的最终契约）

P4 收尾拍板：`/employees/{id}` = **人物详情 + 当前派生任职视图**；`/talent-roster` =
**名册查询 / 筛选 / 分页视图** —— 不长期让 `/talent-roster/{id}` 当第二套详情 SoT。

当前实现把 `EmployeeDetailOut`（`app/schemas/organization.py`）挂在 `GET /talent-roster/{id}`
（`position_compat.enrich_employee()` 的唯一出口），因为 `app/api/v1/employees.py` 是并行
WIP（约定不混改别人在写的文件）。等 WIP 落地，`/employees/{id}` 调同一个出口即可
（行为不变，端点收敛）。

派生三区**只读**、**无 PATCH 入口**（`EmployeePatch` 不含这些键，写面只在 assignment 工作流）：

```jsonc
{
  "id": 24, "name": "Charlie",
  // ... EmployeeOut 原有字段（role 已变成派生镜像）...

  "workforce_status": "assigned",          // WorkforceStatusResolver
  "has_primary_assignment": true,
  "occupies_establishment": true,
  "current_position": {                     // CurrentPositionOut：无主职时为 null
    "definition_id": 3, "code": "software_engineer", "name": "Software Engineer",
    "level": 1, "job_family": "engineering", "legacy_role": "engineer",
    "department_id": 7, "department_name": "Engineering",
    "slot_id": 12, "slot_code": "SE-1", "since": "…",
    "assignment_type": "primary", "position_is_custom": false
  },
  "assignment_integrity": {                 // 与 /integrity、名册行 integrity 同源
    "status": "valid",                     // valid | invalid
    "issues": [],
    "read_only": true
  }
}
```

契约测试：`tests/test_employee_detail_contract.py`（AVAILABLE / ASSIGNED 两态、单 resolver
同源、无 PATCH 入口、read_only 公开）。

---

## 4. UI 契约（Phase 12）

- 一级模块 **人才名册**（`/talent-roster`），默认卡片视图，可切表格。
- 卡片顺序：头像 → 姓名 → 状态徽章 → 当前职位（无则"未分配"）→ Runtime / Provider →
  Top 通用能力（3）→ Top 专业能力（3）→ Traits 摘要（取最高的 3 维）→ Fit（有职位才显示）→
  操作按钮（查看 / 分配职位 / 调岗 / 暂停 / 离职）。
- "未分配"必须**中性色**显示，不用警示色 —— 它不是错误。
- 空名册的空状态文案要说明下一步是"招募人才"，而不是"创建员工"。
- 现有 `employees-page.tsx` 收敛为名册的一个视图，不做第二个列表（避免两份筛选逻辑）。

---

## 5. 招募流程重定义（§9/§10）

```
Recruit Talent
  → 配置 Identity（name/slug/avatar/skin）
  → 配置 Brain（personality/goals/interests/traits 8 维/learning policy）
  → 选择 Runtime（Hermes/OpenClaw/mock）
  → 选择 Provider + Model（条目化绑定，沿用已合入的 v09 别名能力）
  → Provision 人级资源（workspace / git / docs / memory）+ Base Access（base-employee 包）
  → 入册，workforce_status = AVAILABLE
```

**明确不再包含**：部门必选、role 必选、`ROLE_TO_DEPARTMENT_SLUG` 兜底、AccessPackage 按 role 派生。

招募请求 `RecruitRequest` 与旧 `OnboardRequest` 的关系：
`OnboardRequest` 保留为兼容入口（旧前端/脚本还在用），内部转调 `recruit()`；
其中 `role` / `department_id` 字段若出现，走 §`position-system.md` §6 的兼容路径
（翻译成"招募后立即分配对应内置定义的 slot"），并且**打上 deprecation 事件**便于后续统计还有谁在用。

---

## 6. 机器验收（Phase 4/5 的测试清单，对应 §52）

1. Employee 可以在没有任何 PositionAssignment 的情况下存在且可查询。
2. AVAILABLE 员工 `person_resources` 全部 ready，且 `current_position is None`。
3. `recruit()` 不需要 position/role 参数即可成功（旧参数缺失不再 422）。
4. AVAILABLE 员工仍可被派任务（orchestrator 不因无职位排除它）。
5. 卸任 `unassign()` 后人级资源与 brain/runtime/memory/skills **逐项不变**，状态回 AVAILABLE。
6. 名册列表一次请求完成（无 N+1）：用 SQL 计数断言。
7. `status` 派生函数是唯一真相：给同一输入构造 8 种状态，逐个断言。
8. `actions` 与状态一致（ASSIGNED 不给 assign_position，反之亦然）。
9. 离职后默认视图不含该员工，但 `?include_offboarded=1` 可查（历史不丢）。
10. 旧 `POST /employees` 兼容路径仍然工作，且落库结果 = 新流程 + 一次自动分配。
