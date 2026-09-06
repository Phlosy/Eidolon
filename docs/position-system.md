# 组织与职位系统（Position System）

上级约束：`docs/workforce-domain-refactor.md`（ADR-1/2/3/5/9）
职责边界：**公司需要什么**（职位定义、编制、任职、岗位权限、考核挂接、职业路径）。
不拥有：人的人格、能力、Runtime、Provider、Memory、Workspace（那些是 `Employee` 的，见
`docs/talent-roster.md` 与 `docs/competency-system.md`）。

---

## 1. 三层结构

```
PositionDefinition   职位模板："Software Engineer"         公司级（含内置模板作用域）
      └── PositionSlot     编制："Engineering / SE #1"          部门级 + 序号
               └── PositionAssignment  任职："Charlie 在 2026-… 期间占据 #1"  人 × 坑 × 时间
```

为什么定义与编制要分两层（§5/§6）：定义是**可复用的模板**（考核档案、能力要求、职业路径挂在它上面），
编制是**有限的组织资源**（headcount、空缺、汇报线）。合并成一张表就永远回答不了
"Engineering 还有几个坑"，也回答不了"SE 这个职位的要求是什么"。

---

## 2. 表规格

### 2.1 `position_definitions`

| 列                          | 类型                     | 说明                                                                                                                                                            |
| --------------------------- | ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                        | PK                       |                                                                                                                                                                 |
| `company_id`                | FK→companies             | 公司自建定义                                                                                                                                                    |
| `template_scope`            | enum `company`\|`system` | 内置模板用 `system` 且 `company_id` 为空；公司采用时**复制**成 company 行（不共享可变行，避免改内置模板污染历史）                                               |
| `code`                      | String(100)              | 稳定机器名，`uq(company_id, code)`；内置值：`ceo`、`software_engineer`、`qa_engineer`、`researcher`、`product_manager`、`technical_lead`、`engineering_manager` |
| `name` / `description`      | String                   | 展示                                                                                                                                                            |
| `job_family`                | String(50)               | `engineering`\|`qa`\|`research`\|`product`\|`management`\|`design`… → Office 分区与统计用                                                                       |
| `level`                     | Integer                  | 职级序数（晋升推荐用），非显示文本                                                                                                                              |
| `responsibilities`          | JSON list                | 职责条目（考核与 UI 引用）                                                                                                                                      |
| `assessment_profile_id`     | FK→assessment_profiles   | 该职位按什么考核（§26-28）                                                                                                                                      |
| `career_path_metadata`      | JSON                     | `{successors:[code], prerequisites:{code:min_level}, track:"ic\|mgmt"}`                                                                                         |
| `legacy_role`               | String(50), nullable     | **仅用于迁移与兼容镜像**（映射到旧 `EmployeeRole`），新业务禁止读                                                                                               |
| `created_at` / `updated_at` |                          |                                                                                                                                                                 |

`PositionDefinition` 永远不代表某个具体员工（§5）。

### 2.2 `position_definition_packages`（定义 ↔ 默认 AccessPackage）

`position_definition_id`、`package_id`，`uq(两者)`。取代 `ROLE_TO_PACKAGE_SLUG` 硬映射表。

### 2.3 `position_slots`

| 列                                                            | 说明                                                                                                                  |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `id`, `company_id`, `department_id`, `position_definition_id` |                                                                                                                       |
| `slot_code`                                                   | 展示码，如 `ENG-SE-1`；`uq(department_id, position_definition_id, headcount_index)`                                   |
| `headcount_index`                                             | 第几号坑（§6 的 `#1 #2 #3`）                                                                                          |
| `status`                                                      | **只允许**人工值 `PLANNED`\|`ACTIVE`\|`FROZEN`\|`CLOSED`（ADR-2：行政态）；`VACANT`/`OCCUPIED` 属占用态，**永远派生** |
| `manager_slot_id`                                             | 自引用：本坑向哪个坑汇报（组织树，替代"员工带上级"的含糊语义）                                                        |
| `metadata_json`, `created_at`, `closed_at`                    |                                                                                                                       |

两个轴不得混成一个字段（拍板要求，防漂移的接口层表达）：

```python
def occupancy_state(slot, active_assignments) -> OccupancyState:
    if slot.administrative_status == CLOSED: return CLOSED
    if slot.administrative_status == FROZEN: return FROZEN   # 冻结坑不报空缺，避免误导招聘
    return OCCUPIED if any(a.is_primary and a.effective_to is None
                           for a in active_assignments) else VACANT
```

```json
{
  "slot_id": 7,
  "slot_code": "ENG-SE-1",
  "administrative_status": "ACTIVE",
  "occupancy_status": "OCCUPIED",
  "incumbent": { "employee_id": 24, "name": "Charlie", "since": "…" }
}
```

`VACANT`/`OCCUPIED` 在枚举里根本不存在写入口 —— 试图入列就会在迁移与守卫测试里被拒。

### 2.4 `PositionAssignment` —— 物理表仍为 `employments`

> **拍板记录（ADR-1 修订，用户 2026-08-09 定稿）**
>
> ```text
> DB table      : employments          （不改名）
> Domain entity : PositionAssignment   （领域层 / 仓库层 / 文档统一用这个名字）
> Meaning       : 唯一的任职关系 Source of Truth
> ```
>
> 不改物理表名的理由：SQLite `ALTER TABLE RENAME` 会涉及引用它的 FK 子句，是本次设计里
> 风险最高、收益最低的一步；而“两份任职历史”这个真正要防的问题，靠**单一实体映射**已经解决。
>
> 为什么必须把这条写进文档：否则后继开发者会把 `employments` 当成与职位无关的
> “HR 入离职记录”，再开第二张表。调岗、晋升、降职、代理、兼任**只操作这一条时间轴**。
>
> ORM：`class PositionAssignment(Base): __tablename__ = "employments"`；
> `models/lifecycle.py` 保留**名字别名** `Employment = PositionAssignment`（不是第二个 mapper），
> 仅供未迁移调用点过渡，P15 清除。

| 列                                                                                                                          | 来源                            | 说明                                               |
| --------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | -------------------------------------------------- |
| `id`, `employee_id`, `department_id`, `manager_employee_id`, `joined_at`, `effective_from`, `effective_to`, `metadata_json` | **原样保留 v0.4 `employments`** | 历史不重写                                         |
| `position_slot_id`                                                                                                          | v14 回填                        | 新外键；历史孤儿行允许 NULL                        |
| `position_definition_id`                                                                                                    | 由 slot 派生（不存双份）        | 查询便捷字段用 repo 计算                           |
| `assignment_type`                                                                                                           | 新                              | `PRIMARY`\|`ACTING`\|`TEMPORARY`\|`SECONDARY`      |
| `is_primary`                                                                                                                | 新                              | 与 `assignment_type==PRIMARY` 一致，由服务层保证   |
| `status`                                                                                                                    | 复用 `employment_status` 语义   | `ACTIVE`\|`CLOSED`\|`SUPERSEDED`                   |
| `assigned_by`                                                                                                               | 新                              | 操作者（employee id 或 user id，记 metadata 区分） |
| `reason`                                                                                                                    | 新                              | 任用理由（审计与 CareerEvent 文案）                |
| `position_title_snapshot`                                                                                                   | 新                              | 任命当时的职位名（定义改名后历史仍可读）           |

**约束（部分唯一索引，SQLite 原生支持）**

- `uq_slot_primary`：`UNIQUE(position_slot_id) WHERE effective_to IS NULL AND assignment_type='PRIMARY'`
  → 一个坑同时最多一人。
- `uq_employee_primary`：`UNIQUE(employee_id) WHERE effective_to IS NULL AND assignment_type='PRIMARY'`
  → MVP 一人同时只有一个主职；`ACTING`/`SECONDARY` 不受此约束（为兼任/代理预留）。

### 2.5 `position_competency_requirements`

`position_definition_id`、`competency_definition_id`、`kind`(`general`|`professional`)、
`minimum`(0-100)、`weight`(0-1)、`uq(definition, competency)`。
`Σweight` 不强制 = 1（UI 显示相对权重，避免回填期因权重和不为 1 而 500），但提供校验端点。

### 2.6 `career_events`

`employee_id`、`kind`(`hired`|`position_assigned`|`transferred`|`unassigned`|`promoted`|
`demoted`|`acting_assigned`|`suspended`|`resumed`|`offboarded`)、`position_definition_id?`、
`position_slot_id?`、`from_definition_id?`、`assessment_run_id?`、`occurred_at`、`reason`、`metadata_json`。
append-only；调岗/晋升**只追加**，从不改写旧行。

---

## 3. 服务接口（`app/services/position.py`）

```python
def create_definition(db, payload) -> PositionDefinition
def open_slot(db, definition_id, department_id, headcount_index=None, manager_slot_id=None) -> PositionSlot
def freeze_slot(db, slot_id, reason) / close_slot(db, slot_id, reason)
def assign(db, employee_id, slot_id, *, assignment_type=PRIMARY, effective_from=None,
           assigned_by=None, reason="") -> AssignOutcome      # 建任职 + 权限 diff + Provisioning + CareerEvent
def unassign(db, employee_id, *, reason, effective_to=None) -> PositionAssignment  # 关窗 + REMOVE 职位权限 + 回 AVAILABLE
def transfer(db, employee_id, to_slot_id, *, reason, ...) -> TransferOutcome       # 关旧窗 + 开新窗 + diff + CareerEvent
def workforce_status(db, employee) -> WorkforceStatus
def active_assignment(db, employee_id, *, type=PRIMARY) -> PositionAssignment | None
def current_position(db, employee_id) -> PositionView | None   # code/name/family/level/slot/fit/since
def organization_tree(db, company_id) -> list[DepartmentNode]
def vacancies(db, company_id) -> list[SlotView]                # 派生 VACANT
def candidates_for(db, slot_id, limit=10) -> list[CandidateView]  # fit 降序 + skill gap
```

`assign()` 的**原子性**：一个事务里写 assignment + employee_packages 变更 + career_event，
Provisioning job 在事务提交后异步跑（沿用现有 `ProvisioningEngine.run()`）；job 失败**不回滚**任职，
而是留下 `Drift` 供 reconcile（与 v0.4 语义一致）。

---

## 4. 权限两层化（§12）

| 层     | 载体                                                                                                                                | 生命周期                             |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| 人级   | `base-employee` 包 + workspace/git/docs 人级账号 + runtime/provider 绑定 + `drive/knowledge/personal/{slug}`                        | 入职建立，离职才回收；**调岗绝不动** |
| 职位级 | `PositionDefinition.default_access_packages`（如 `git:engineering-team`、`docs:engineering`、`workspace:dev`、CI、repo、dev tools） | 随 assignment 生效/结束而 ADD/REMOVE |

实现要点：

1. 复用 `access.union_entitlements()` + `access.diff_entitlements()`，不写第二套 diff。
2. `EmployeePackage.source` 新增 `position`；老的 `role` 行 v14 回填为 `position` + 指向映射后的定义。
3. `sync_role_packages()` → 改名 `sync_position_packages()`，仍遵守既有不变量：
   **`manual` 行永不隐式移除**（这条现在有测试，必须继续绿）。
4. 部门归属：`employee.department_id` 降级为"最后任职部门"的兼容镜像，真实归属来自 assignment 的 slot。
   AVAILABLE 的人 `department_id` 允许为 NULL（不假装他属于某部门）。
5. 调岗序列（§38）：
   ```
   关闭旧 assignment（effective_to=now, status=SUPERSEDED）
   → desired = base ∪ 新职位包（∪ 仍生效的 ACTING/SECONDARY 职位包）
   → diff = diff_entitlements(current, desired)
   → 开新 assignment
   → engine.plan(diff) + ProvisioningJob（KEEP 的不动，REMOVE 的先撤，ADD 的先发）
   → CareerEvent(transferred) + 记录 competency gap（不阻断）
   ```
   注意 REMOVE 与 ADD 的顺序：先 ADD 后 REMOVE，避免"两职位共有的 entitlement"被误撤
   （现有 diff 已按 key 计算，天然安全；测试要覆盖"SE 与 Researcher 共享 `git:company-org-member`"）。

---

## 5. Position Fit（§20，只读，不入库）

```
component_i = 1                                   若 score_i ≥ minimum_i
            = 0.5 × score_i / minimum_i           若 score_i < minimum_i 且 minimum_i > 0
            = 0                                   无证据（UNRATED）→ 记为 gap，不给虚假分数
fit = round(100 × Σ weight_i × component_i / Σ weight_i)
gap = [(competency, minimum_i − score_i) for 未达标项]
```

- 低 confidence **不降低** fit，但 UI 必须并列显示 confidence 与证据数（否则"91%"会被误读成确定事实）。
- fit 只在名册推荐、候选列表、调岗向导三处出现；守卫测试禁止 orchestrator / runtime / 验收路径 import 它。
- 玩家可以把 fit 30% 的人任命到任何职位 —— 系统只报 gap，不拦、不改运行时行为（§20 的"玩家可以自由安排"）。

---

## 6. 兼容层（§43）

```python
# app/services/position_compat.py —— 唯一允许读 employees.role 的模块
def derived_current_position(db, employee) -> dict | None:
    """从生效 PRIMARY assignment 派生：{code,name,level,family,department,slot,since,fit}"""
def legacy_role_of(db, employee) -> str:
    """旧字段口径：优先 assignment.definition.legacy_role，其次 employees.role 镜像，最后 engineer。"""
```

- 所有 API 响应加 `current_position`（对象，可为 null），保留 `role`（字符串）。
- `position_compat` 之外读 `employee.role` ⇒ `test_architecture_guards.py` 红。
- 内置 5 个定义的 `legacy_role` 覆盖现有全部枚举值，所以迁移期旧前端行为不变；
  用户自定义的职位没有 legacy 对应 → `legacy_role_of()` 回落 `engineer` 并在响应里带
  `"position_is_custom": true`，**不谎报**成一个不存在的 role。

---

## 7. Office 联动（§40，Phase 14）

`apps/web/src/features/auth-office-game/`：

| 现状                                                                                          | 目标                                                                                      |
| --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `office-state-adapter.ts` 用 `input.role` / `input.department` 字符串                         | 用 `current_position.job_family` + `workforce_status`                                     |
| `zone-system.ts` 的 `OfficeZoneType`（CEO_OFFICE / ENGINEERING / LOUNGE / SHARED_WORKSPACE…） | 增加 `TALENT_AREA`；`zoneFor()` 改为按 family 映射                                        |
| `OfficeAssignmentService.workstationFor(employee)`                                            | 按 **slot** 绑定工位（`slot_code → workstation id`），调岗即换区，AVAILABLE 去公共区/沙发 |

映射：`management→CEO_OFFICE 或 MEETING 邻近`、`engineering→ENGINEERING`、`research→LIBRARY`、
`product→WHITEBOARD 区`、`qa→ENGINEERING 邻座`、无职位→`SHARED_WORKSPACE`/`LOUNGE`/`TALENT_AREA`。
工位是**职位的属性**，不是人的属性 —— 人调岗时移动，这正是 §40 想要的可读反馈。

---

## 8. API 契约（Phase 5）

```
GET    /api/v1/organizations/tree                      部门 → 定义 → slot（含派生状态与任职者摘要）
GET    /api/v1/organizations/vacancies                 空缺（派生 VACANT）
POST   /api/v1/organizations/definitions               建定义
PATCH  /api/v1/organizations/definitions/{id}
POST   /api/v1/organizations/definitions/{id}/slots    开编制
POST   /api/v1/organizations/slots/{id}/freeze|close
GET    /api/v1/organizations/slots/{id}/candidates?limit=10   fit 降序 + gap
GET    /api/v1/employees/{id}/assignments?include_closed=1    任职史（Career 标签页数据源）
POST   /api/v1/employees/{id}/assign
POST   /api/v1/employees/{id}/transfer
POST   /api/v1/employees/{id}/unassign
GET    /api/v1/positions/fit-estimate?employee_id=&slot_id=   显式试算（调岗向导用）
```

写端点一律要求 CSRF（沿用现有中间件），并要求 `membership_role` 具备管理权限（现状即有，不新增鉴权模型）。

---

## 9. 机器验收（对应 §52 后半）

1. Position 可以没有任职者存在（定义 + slot 建完即可查，状态派生为 VACANT）。
2. 一个 slot 同时只能有一个生效 PRIMARY（部分唯一索引，第二个直接 409）。
3. 一个员工同时只能有一个生效 PRIMARY（ACTING/SECONDARY 允许多条）。
4. 分配 → 调岗 → 卸任的完整历史可按时间轴还原，旧行不被改写。
5. 调岗后：brain / runtime / provider / memory / skills / workspace / learning 逐项不变（§8.1）。
6. 调岗权限 diff 正确：base KEEP、旧职位 REMOVE、新职位 ADD；共享 entitlement 不误撤。
7. `PositionCompetencyRequirement` 驱动的 fit 与 gap 计算有属性测试（边界：minimum=0、UNRATED、无权重）。
8. fit 不影响任务成功率的守卫测试（`test_evidence_invariants.py` 扩一条）。
9. 迁移后旧 `POST /employees`（带 role）行为等价于 recruit+assign，且历史员工 id 不变。
10. 组织树在一个 5 部门 / 12 定义 / 30 slot / 8 任职的数据集上单查询完成（无 N+1）。
