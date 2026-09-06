# 劳动力领域重构总纲（Workforce Domain Refactor）

状态：设计定稿，待实施
基线提交：`b5e559b`（`dev`）
作者：领域设计（Phase 0 + Phase 1 产出）
关联：`docs/talent-roster.md`、`docs/position-system.md`、`docs/competency-system.md`、`docs/assessment-system.md`

本文只回答三件事：**现在到底耦合在哪**、**目标领域模型长什么样**、**数据怎么迁过去且不丢**。
四个子系统的规格在各自文档里，本文是它们的上级约束（冲突时以本文为准）。

---

## 1. 一句话目标

把"**先有职位、再找个 Agent 填进去**"改成"**先招募并培养 AI 人才，再按组织需要把人安排到职位上**"，
两套系统通过 `PositionAssignment` 连接，彼此独立演化。

| 概念     | 英文                 | 归属     | 语义                         |
| -------- | -------------------- | -------- | ---------------------------- |
| 人       | `Employee`           | 人才经营 | 这个人是谁                   |
| 职位定义 | `PositionDefinition` | 组织经营 | 公司需要什么职位             |
| 编制     | `PositionSlot`       | 组织经营 | 这个职位在哪个部门开第几号坑 |
| 任职     | `PositionAssignment` | 连接点   | 这段时间这个人承担这个坑     |
| 行为倾向 | `Trait`              | 人       | 倾向于怎样工作               |
| 能力     | `Competency`         | 人       | 已经被证据证明能做什么       |
| 技能     | `Skill`              | 人       | 具体可复用的做法             |
| 考核     | `Assessment`         | 度量过程 | 能力是怎么被量出来的         |

**铁律（贯穿全部子系统）**

1. `Employee` 的人格 / Runtime / Provider / Memory / Knowledge / Skills / Workspace / 成长历史
   属于人，职位变化**绝不删除或重置**它们。
2. Trait 不得直接决定 Competency、Success Rate、confidence、验收 verdict（沿用
   `behavior-v1` 已建立的架构守卫，见 §8.3）。
3. Competency 变化必须有 Evidence 且可追溯；禁止 `score += 5`、禁止随机初始化。
4. 派生态不允许被写入（`VACANT`/`OCCUPIED`、`AVAILABLE`/`ASSIGNED`、Fit 都是派生的）。

---

## 2. Phase 0 基线（实测，不是估计）

```
ruff check app tests      : All checks passed!
ruff format --check       : 6 files would be reformatted   ← 全部是用户既有 WIP，见 §9
pytest                    : 267 passed, 6 deselected
vitest                    : 210 passed / 50 files
pnpm build                : ✓ built in 5.01s
alembic upgrade head      : OK（v01…v11 单线）
alembic check             : No new upgrade operations detected
git status                : clean
```

规模：后端 app 约 200 个 py 文件、39 个测试文件；前端 50 个测试文件。

---

## 3. 现状耦合测绘（这次重构的真实工作量所在）

### 3.1 已有的职位底座（关键发现）

`app/models/lifecycle.py`（v0.4）**已经存在**：

```python
class Position(TimestampMixin, Base):        # positions
    department_id, title, level              # ← 部门内的自由文本标题，无 company / code / 职责

class Employment(Base):                      # employments：注释写明"IS the employment history"
    employee_id, department_id, position_id, manager_employee_id,
    employment_status, joined_at, effective_from, effective_to, metadata_json
```

- `position_id` 在 16 处被引用（`_validate_position` / `create_employment` / schemas / seed）。
- 访问包侧另有 `AccessPackage.role`（nullable）与 `PackageSource.role`（`source="role"` 的行由
  `sync_role_packages()` 自动增删，manual 行永不隐式移除 —— 这个不变量要保留）。
- `lifecycle/access.py` 里三张映射表把 **role / department / package 强行一对一**：
  `ROLE_TO_PACKAGE_SLUG`、`DEPARTMENT_TO_PACKAGE_SLUG`、`DEPARTMENT_TO_ROLE`，
  注释自己承认"seed departments map 1:1 to roles" —— **这就是要把人和职位焊死的代码**。

**结论**：不新建第三套职位概念。`positions` 被收编为 `position_definitions`（换语义 + 换表名），
`employments` 被收编为 `position_assignments`（**它就是我们要的任职历史，字段几乎齐了**），
真正新增的只有 `position_slots` 与能力/考核那几张表。

### 3.2 `employee.role` 的 26 个读取点（逐点定去处）

`grep -rn '\.role\b' apps/server/app --include='*.py'` = 26 处 / 13 文件。逐个判定：

| #     | 位置                                              | 现在做什么                                                                    | 目标                                                                  |
| ----- | ------------------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 1-8   | `services/lifecycle.py` ×8                        | onboard/transfer 用 role 决定包集合、审计、`ROLE_TO_DEPARTMENT_SLUG` 兜底部门 | 走 `position_service.assign()`；onboard 不再收 role                   |
| 9-10  | `services/seed.py` ×2                             | 5 个种子员工写 role                                                           | 种子改为：建定义→开编制→招募→分配                                     |
| 11-12 | `services/employees.py` ×2                        | 旧 `POST /employees` 兼容垫片：`role→部门` 兜底                               | 垫片保留但内部转调 recruit；不再读 role 决定部门                      |
| 13    | `workflow/orchestrator.py:194`                    | `employee_role=employee.role` 进 runtime 上下文                               | 取 `current_position.title` + `job_family`                            |
| 14    | `workflow/orchestrator.py:405,456-458`            | `get_employee_by_role()` 按 role 找 PM/里程碑负责人                           | `get_employee_in_position(code=…)`：按**任职**找人；空缺照旧返回 None |
| 15    | `runtimes/gateway.py:50`                          | `EmployeeRef(role=…)`                                                         | `EmployeeRef(position=…, role=deprecated镜像)`                        |
| 16    | `lifecycle/audit.py:18`                           | `employee_snapshot()["role"]`                                                 | 快照加 `position`，`role` 标注 deprecated（审计历史不改写）           |
| 17    | `repositories/organization.py`                    | `get_employee_by_role()` 定义                                                 | 保留为 compat 读，新代码禁用；新增 `position_repo` 查询               |
| 18-19 | `tutorials/requirements.py` ×2                    | 教程门禁用 role 判定                                                          | 改 `talent_recruited` / `position_assigned` 两个 requirement          |
| 20-21 | `services/tutorial.py:254,347-348`                | 按 role 找 ceo/engineer 填 `ceo_employee_id`                                  | 按 primary assignment 的 definition code 找                           |
| 22-23 | `lifecycle/access.py:108,116`                     | `ROLE_TO_PACKAGE_SLUG` → 岗位包                                               | `PositionDefinition.default_access_packages`                          |
| 24-25 | `provisioners/docs_builtin.py`、`api/v1/drive.py` | `collaborator.role`（文档协作者权限）                                         | **无关**，不改                                                        |
| 26    | `api/dependencies.py:48`                          | `membership_role`（公司成员角色 OWNER）                                       | **无关**，鉴权不依赖 employee.role（好消息：解耦无授权风险）          |

> 两个"假阳性"值得写进文档：鉴权用的是 `CompanyMembership.role`，文档协作用的是
> `CollaboratorRole` —— 都不属于本次解耦范围，避免实施时误伤。

### 3.3 前端 132 个 role 引用（热点）

`hire-wizard.tsx` 13、`hire-wizard-steps.ts` 7、`types/index.ts` 6、`office-state-adapter.ts` 5、
`user-menu.tsx` 5、`profile-section.tsx`(employee) 3、`employees-page.tsx` 2、
`project-intake-wizard.tsx` 3、`zone-system.ts` 2、tutorial 组件 8+。
其中 `auth-office-game` 是**办公室分区/工位**的所在地（§Phase 13 目标）。

---

## 4. ADR（决定，锁定后不再讨论）

### ADR-1 收编 `positions`/`employments`，不造第三套概念（拍板修订：**物理表不改名**）

- `positions` → 新表 `position_definitions`；旧行按 `(department, title)` 生成定义 + 一个编制。
- `employments` → **表名不变**，只加列（`position_slot_id`、`assignment_type`、`is_primary`、
  `assigned_by`、`reason`、`position_title_snapshot`）；领域实体正式命名 `PositionAssignment`，
  `models/lifecycle.py` 保留名字别名 `Employment = PositionAssignment`（**不是第二个 mapper**）。
  `id`、`employee_id`、`manager_employee_id`、`effective_from/to`、`metadata_json` **原值保留**。
- 只有 `position_slots` 是全新表。
- 两条否决：❌ 新表与 `employments` 并存（两份任职历史，说不清谁写谁读）；
  ❌ `op.rename_table`（SQLite 改名会牵扯引用它的 FK 子句，风险最高收益最低，
  而“两个真相”靠单一实体映射已消除）。详见 `docs/position-system.md` §2.4 拍板记录。

### ADR-2 Slot 的占用态是派生态

`position_slots` 存**行政态** `administrative_status ∈ {PLANNED, ACTIVE, FROZEN, CLOSED}`
（这四个值才是人/业务决定的）；`occupancy_status ∈ {VACANT, OCCUPIED, FROZEN, CLOSED}`
由“是否存在生效中的 PRIMARY 任职”计算，**不提供写入口**，也不入列。
API 必返两个字段（拍板要求），不得合并成一个 `status`：
`{"administrative_status": "ACTIVE", "occupancy_status": "OCCUPIED"}`。
配测试：枚举里没有 `VACANT`/`OCCUPIED` 的写入值，试图入列即 fail；
“库里写 VACANT 而实际有人任职”这个漂移场景**结构上不可表达**。

### ADR-3 权限分两层，沿用 Desired State + Provisioner

- 人级（入职即有，永不随职位消失）：`base-employee` 包 + workspace/git/docs/runtime/provider。
- 职位级：`PositionDefinition.default_access_packages`，任职生效时加入、结束时移除。
- `PackageSource.role` → 新增 `position`；老行按映射回填为新值。`manual` 行永不隐式移除（保留既有不变量）。
- 复用 `access.diff_entitlements()`，不写第二套 diff。

### ADR-4 Workforce 状态是**读时派生**，不加列

`RECRUITING / ONBOARDING / AVAILABLE / ASSIGNED / TRANSFERRING / SUSPENDED / OFFBOARDING / OFFBOARDED`
由 (`lifecycle_status`, 是否有生效 PRIMARY) 单一函数派生（**正式命名 `WorkforceStatusResolver`**，`app/workforce/status.py`）。
理由：这两个轴本来就有正交状态，物化列必然漂移。
**AVAILABLE 不是异常态**：已入册、人级资源就绪、暂无职位 —— 完全合法。
若将来名册分页需要，再加物化列，届时以本函数为唯一写入方（现在不留半成品列）。

### ADR-5 `Employee.role` 保留为 deprecated 只读镜像 + 守卫锁死

- 列不删（外部脚本/历史审计需要）。
- 新代码禁止读取：扩展 `tests/test_architecture_guards.py`，加
  `test_employee_role_is_not_read_outside_the_compat_allowlist`，AST 扫描 `\.role` 属性读取，
  白名单只含 `services/position_compat.py`（唯一 compat 读点）+ 明确无关的 `collaborator.role` /
  `membership_role`。这与 `behavior-v1` 里"trait 阈值只能待在 resolver"是同一套手段，已验证有效。
- API 响应新增 `current_position`（对象）+ 保留 `role`（字符串），§43 的兼容要求由此满足。

### ADR-6 Trait / Competency / Skill 三张不同的表

- Trait：`EmployeeBrain.traits`（已存在，8 维扩展见 `competency-system.md` §2）。
- Competency：`competency_definitions` + `employee_competencies`（score/confidence/trend）。
- Skill：现有 `skills` 不动，只作为 Competency 的 **Evidence 来源**。

### ADR-7 能力只能被证明，不能被分配

新员工默认 **UNRATED**（无 `employee_competencies` 行）；只有跑过 Initial Assessment 才产生
`PROVISIONAL` 记录。禁止 `random.uniform(60, 90)`（AST 守卫：competency 写路径不得调用随机源）。

### ADR-8 考核驱动能力，聚合算法可解释

`Work → Evidence → Assessment → Competency Update`，算法见 `assessment-system.md` §4，
每次 run 的输入输出都落库，可重放。

### ADR-9 Fit 只用于推荐，不碰执行

`Position Fit` 是查询期计算（不落库、不缓存列），只出现在推荐/名册/调岗界面；
守卫测试断言 orchestrator 与 runtime 出口都不读 fit。玩家可以自由任命低 Fit 的人，系统只报 Skill Gap。

### ADR-10 每段独立可绿、可回滚

沿用本轮已验证的手段：分段迁移 + 每段导出树跑 `pytest` + `alembic check`，
迁移必须成对可逆（`upgrade`/`downgrade` 都实测）。

---

## 5. 目标领域模型

```
Company
├── 人才经营（Person domain）
│   Employee ─┬─ EmployeeBrain ─── traits(8)      → BehaviorPolicy（已实现，behavior-v1）
│             ├─ VisualProfile（avatar / skin）
│             ├─ RuntimeInstance / ModelBinding / Provider（人已绑定）
│             ├─ Memory namespace / Knowledge / Skill / SkillUsage / LearningRecord
│             ├─ Workspace + ResourceAccount（人级：git/docs/workspace）
│             ├─ EmployeeCompetency ── CompetencyDefinition ── CompetencyDomain
│             ├─ CompetencyEvidence（TASK/PROJECT/REVIEW/ARTIFACT/…）
│             ├─ AssessmentResult ── AssessmentRun ── AssessmentProfile/Criterion
│             ├─ CareerEvent（任职与考核构成的职业履历）
│             └─ ProjectExperience（既有 project/task 参与史）
└── 组织经营（Organization domain）
    Department
    PositionDefinition（code, job_family, level, responsibilities, assessment_profile,
                        career_path_metadata, required/preferred competencies）
    ├── PositionCompetencyRequirement（dimension, minimum, weight, kind）
    └── PositionSlot（department, headcount_index, status∈{PLANNED,FROZEN,CLOSED}, manager_slot）
            └── PositionAssignment（employee, slot, type∈{PRIMARY,ACTING,TEMPORARY,SECONDARY},
                                    status, effective_from/to, is_primary, assigned_by, reason）
```

关系数：一个 Employee 同时最多 1 个生效 PRIMARY（`uq` 部分唯一索引保证），
可以有多个 ACTING/SECONDARY（结构支持，MVP 不开 UI）；一个 Slot 同时最多 1 个生效 PRIMARY。

完整字段与约束：见 `docs/position-system.md` §2 与 `docs/competency-system.md` §3。

---

## 6. 迁移方案（v12 → v18，SQLite / 保留全部现有行）

**总原则**：先加后删；每一步 schema 与模型同步（`alembic check` 无漂移）；回填分批 + 时间预算 +
可重入（沿用 v10 的做法）；旧列保留为镜像，删除列放在最后一个迁移且只删已无人读的空表。

| 迁移                                                                    | 内容                                                                                                                                                                                                                                                                                                                                        | 回填                                                                                                                                                                                                                                                                                                                                                                                                                   | 可逆性                                   |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| **v12** `position_definitions`                                          | 新表 + `company_id`/`code`/`job_family`/`level`/`description`/`responsibilities(JSON)`/`career_path_metadata(JSON)`/`assessment_profile_id`/`template_scope`/`legacy_role`；`uq(company_id, code)`；`position_definition_packages`（定义↔默认包）                                                                                           | 为每个现存 `(department, title)` 造一个定义，`legacy_role` 用 `DEPARTMENT_TO_ROLE[dept.slug]` 反推，默认包 = `ROLE_TO_PACKAGE_SLUG` 指向的现有包                                                                                                                                                                                                                                                                       | drop 表即可逆                            |
| **v13** `position_slots`                                                | 新表，FK→department/definition；`uq(department_id, definition_id, headcount_index)`；`status` 默认 `PLANNED`；`manager_slot_id` 自引用                                                                                                                                                                                                      | 每个部门按现有 `positions` 行各开 1 个 slot（index=1），status 由是否有生效 employment 决定（先填 PLANNED，生效态由派生）                                                                                                                                                                                                                                                                                              | drop 表                                  |
| **v14** `employments` 加列（**不改表名**；实体叫 `PositionAssignment`） | 表名不变、只 `ADD COLUMN`：加 `position_slot_id`/`assignment_type`/`is_primary`/`assigned_by`/`reason`/`position_title_snapshot`；`position_id` 保留为 deprecated 只读；索引 `ix(employee_id, effective_to)`、两个部分唯一 `(position_slot_id) WHERE effective_to IS NULL AND assignment_type='PRIMARY'` 与 `(employee_id) WHERE … PRIMARY` | 每条 employment 用 `(department_id, position_id)` 找到 v13 的 slot 回填；`assignment_type='PRIMARY'`，`is_primary=True`，`reason=metadata_json.kind`；**先清洗后建索引**：同一员工/同一 slot 出现多条生效 PRIMARY 时按 `effective_from` 最新保留，其余确定性关窗并在 `metadata_json.normalized` 留痕，清洗条数写进迁移输出（不静默改历史）；缺 slot 的行保留 slot_id=NULL 并在 metadata 记 `orphan:true`（**不删行**） | drop 新增列（表名未动，无需反向 rename） |
| **v15** 能力域                                                          | `competency_domains`、`competency_definitions`（`uq(code)`、`domain_id`、`kind∈{general,professional}`）、`employee_competencies`（`uq(employee,definition)`、`score`、`confidence`、`evidence_count`、`last_assessed_at`、`last_used_at`、`trend`、`status∈{UNRATED,PROVISIONAL,ASSESSED}`）、`competency_evidence`                        | 目录 seed（10 通用 + §17 专业域）；**不给任何员工造能力行**（新员工 UNRATED）                                                                                                                                                                                                                                                                                                                                          | drop 表                                  |
| **v16** 职位×能力                                                       | `position_competency_requirements`（`uq(definition,competency)`、`minimum`、`weight`、`kind`）                                                                                                                                                                                                                                              | 5 个内置定义各配一组需求（`legacy_role` 已知者用 §26-28 的权重）                                                                                                                                                                                                                                                                                                                                                       | drop 表                                  |
| **v17** 考核域                                                          | `assessment_profiles`、`assessment_criteria`、`assessment_runs`、`assessment_results`、`career_events`；`PositionDefinition.assessment_profile_id` FK 生效                                                                                                                                                                                  | 内置 3 个 profile（Software Engineer / Researcher / CEO-Manager）+ criteria 权重；为**已有** employment 历史生成 `career_events`（append-only，不改旧行）                                                                                                                                                                                                                                                              | drop 表                                  |
| **v18** 清理                                                            | drop `positions`（空表，内容已在 v12/v13 收编）、drop `position_assignments.position_id`、`employees.role` 保留列但注释 deprecated                                                                                                                                                                                                          | —                                                                                                                                                                                                                                                                                                                                                                                                                      | 需要前向修复，不做自动 downgrade（写明） |

**迁移正确性的机器验收**（每个迁移都必须过）：

1. 迁移前 dump 关键不变量：`employee_id`、`runtime_instance.id/employee_id`、`model_bindings`、
   `employee_brains.traits`、`memory_namespace`、`workspace_path`、`skills`、`knowledge`、
   `employments` 的行数与 id 集合 —— 迁移后逐项相等（`tests/test_position_history.py`）。
2. 每条旧 employment 都必须能在新表里找到对应 assignment，且 `effective_from/to` 完全一致。
3. 迁移在**已有 dev 库**（含真实数据，当前停在 `j5e8a1b4c730`）和**空库**两条路径上都跑通。
4. `alembic downgrade -6` → `upgrade head` 往返后 `alembic check` 仍无漂移。

### 6.1 v12–v14 落地后的实测回填结论（真实 dev 库副本）

在 `data/eidolon.db`（v11：24 员工 / 19 任职 / 5 职位）的副本上跑 `upgrade head`：

| 检查项             | 结果                                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| 人侧行数与 id 区间 | employees 24、employee_brains 24、runtime_instances 24、model_bindings 3、skills 7、knowledge_items 26 — 迁移前后逐项相同 |
| `employments`      | 19 → 19，id 集合不变，19 行全部回填                                                                                       |
| 重叠主职清洗       | 0 条（dev 数据本就无冲突；冲突路径改由 `tests/test_position_history.py` 构造并断言）                                      |
| 定义 / 坑          | 5 / 5，`code` 无冲突（ceo、product_manager、researcher、engineer、qa_engineer），派生占用态每坑 1 人在任                  |
| `alembic check`    | 干净（空库与真实库两条路径都验）                                                                                          |
| 任职挂上坑的比例   | **5/19** — 另外 14 条老任职的 `position_id` 本来就是空                                                                    |

最后一行是这次实测最有价值的发现：**v0.4 的 `positions` 表基本没被写过**，入职与调岗只落 `employees.role` 文本。所以迁移之后 11 名在岗者的 `workforce_status` 会是 `AVAILABLE` —— 有身份、有运行时、没有编制。这不是数据损坏，而是旧模型一直藏着的真实状态被显式暴露出来。

由此定下两条纪律，P4 之后都受它约束：

1. **迁移绝不代为猜坑。** `position_id` 为空就留空，孤儿 `position_id` 也不映射 —— 宁缺不错。
2. **补编制是业务动作，不是数据修补。** 走 P4 的 `position_service` 分配工作流，产出 `PositionAssignment` 与履历事件，而不是在迁移里悄悄补一行。

---

## 7. 分阶段实施与 PR 切分（对齐你给的 Phase 0–14）

| 阶段  | 交付                                                                                                                               | 门禁（每段独立绿）                             |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| P1 ✅ | 本文件 + 4 份规格文档                                                                                                              | 文档不含未实现承诺                             |
| P2    | `models/position.py`（Definition/Slot/Assignment 三类）+ 仓库层 + schemas，**不接线**                                              | 新模型可导入、单测建表可用                     |
| P3    | v12–v14 迁移 + 回填 + 保留性验收测试                                                                                               | 迁移往返、历史不变量                           |
| P4    | `position_service`：`recruit()`（不再要求职位）、`assign()`、`transfer()`、`unassign()`、`workforce_status()`；旧 `onboard()` 转调 | 名册 API + 20 项任职测试                       |
| P5    | 名册 API：`GET /talent-roster`、`GET/PATCH /employees/{id}/assignments`、`GET /positions/definitions                               | slots                                          | vacancies` | 契约测试 + 权限 |
| P6    | `position_compat`：API 响应 `derived_current_position`；`role` 转 deprecated 镜像；**架构守卫禁止新读点**                          | 守卫测试                                       |
| P7    | 权限两层化：`PositionDefinition.default_access_packages` 接线、`PackageSource.position`、调岗 KEEP/REMOVE/ADD diff                 | 复用现有 `diff_entitlements` 测试 + 新调岗测试 |
| P8    | Traits 扩到 8 维（registry + schema + 投影 + UI），**行为只接已定义策略的那几个**                                                  | 扩展性测试（已存在，扩到 8）                   |
| P9    | 能力目录 + `employee_competencies`（含 UNRATED/PROVISIONAL 语义）                                                                  | 禁止随机 + 无证据不可变                        |
| P10   | 考核系统（profiles/criteria/runs/results/evidence + 聚合 + confidence + trend）                                                    | §48 全部不变量 + 可重放                        |
| P11   | Position Fit + Skill Gap 推荐                                                                                                      | Fit 不影响 runtime 成功（守卫）                |
| P12   | UI：人才名册（卡片/表格/筛选）、人物详情 Overview/Traits/Competencies/Assessment/Career、组织与职位、分配向导                      | tsc/eslint/prettier/vitest/build               |
| P13   | 教程：`recruit → roster → assign`（步骤 id 不变，门禁换 requirement，玩家进度不丢）                                                | 教程测试                                       |
| P14   | Office：zone 由 `job_family` 决定；AVAILABLE → SHARED_WORKSPACE/LOUNGE                                                             | zone-system 测试                               |
| P15   | 清理：v18、移除残留 role 读点、文档收口                                                                                            | 全量门禁                                       |

> 顺序说明：UI（P12）刻意排在领域/迁移/考核之后，符合"先领域正确、再界面"。
> 唯一提前的是 P8 的 Schema 部分（Trait 的 schema/UI 先建、行为后接），因为你第 §14 条明确要求。

---

## 8. 三条不可协商的守卫（先写测试，再写实现）

### 8.1 人的数据不随职位消失

`transfer()` 前后逐项断言相等：`employee_brains.id/personality/goals/traits`、
`runtime_instances.id`、`model_bindings`、`memory_namespace`、`workspace_path`、
`ResourceAccount(人级)`、`skills`、`knowledge`、`learning_records`、`employments` 历史行数只增不减。

### 8.2 派生态不被写入

Slot 占用态、workforce status、Fit 都是函数输出；任何试图持久化的列都会在这三条测试里被发现
（因为根本没有对应列 —— 测试的作用是**阻止后人加列**）。

### 8.3 Trait ↛ Competency ↛ Success

扩展 `test_architecture_guards.py`：

- 业务层不得读 `employee.role`（白名单除外）；
- competency/evidence 写路径不得 import `random`、不得引用 trait 值；
- `assessment` 计算路径不得引用 `position_fit`；
- orchestrator/runtime 出口的成功判定不得引用 competency 之外的能力数值。

---

## 9. 风险登记（会真咬人的那几个）

| 风险                                           | 后果                                       | 处置                                                                                                                                                                       |
| ---------------------------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `seed_lifecycle()` 每次启动都跑                | 与迁移打架、重复造 slot                    | 幂等键：`uq(dept,definition,index)` + 只补空，不重建                                                                                                                       |
| 教程进度按 step id 存库                        | 改步骤会清空老玩家进度                     | 步骤 id 冻结，只换 requirement 判定（P13 明确）                                                                                                                            |
| `Position.title` 自由文本 vs `definition.code` | 老数据同名不同义                           | 回填时按 `(dept,title)` 归并，冲突保留 `title` 快照到 assignment                                                                                                           |
| SQLite `rename_table` + 部分唯一索引           | 老 SQLite 行为差异                         | ~~SQLite `rename_table` 行为差异~~ —— 拍板后 v14 **不再改名**，只 `ADD COLUMN` + 建索引；真正危险降为“存量脏数据 vs 部分唯一索引”，处置见 v14 行（先清洗、留痕、输出条数） |
| 6 个未格式化 WIP 文件                          | CI 的 `ruff format --check` 红（已进历史） | 与本次重构无关，但挡 `make lint`；等用户点头单独一条 chore(style)                                                                                                          |
| 26 个 role 读点分阶段清理                      | 中途出现"两个真相"                         | ADR-5 白名单：未迁移的读点必须留在白名单里，迁移完一个删一个，白名单只减不增                                                                                               |

---

## 10. 验收场景（§53 的可执行版本）

1. 新公司 → 名册为空（`GET /talent-roster` = `[]`）。
2. `recruit(Alice)` → 人级资源就绪、**无** assignment → `workforce_status = AVAILABLE`、
   `current_position = None`、`role` 镜像为空值语义（不猜）。
3. `POST /organizations/positions/{definition}/slots` → 一个 `VACANT`（派生）slot，可以没有任职者存在。
4. `assign(Alice → CEO slot)` → 人级 KEEP、CEO 包 ADD、`workforce_status = ASSIGNED`、
   `CareerEvent(position_assigned)`、Office zone → CEO_OFFICE。
5. `recruit(Charlie)` → AVAILABLE；`assign(Software Engineer #1)` → ASSIGNED；
   人物面板显示 8 traits / 10 general / 专业能力 / Fit / Assessment / Career；
   能力初值 **UNRATED**，跑一次 Initial Assessment 才变 PROVISIONAL。
6. `transfer(Charlie → Researcher)` → 同一 `employee_id`，brain/runtime/memory/skills 逐项相等；
   Engineering 专属 entitlement 被 REMOVE、Research 被 ADD、base 保留；
   旧 assignment `effective_to` 关闭（不删），Fit/考核档案/工位随之变化。
7. 全库回归：`pytest`、`vitest`、`build`、`alembic check`、历史不变量测试全绿。

---

## 11. 拍板记录（ADR 定稿）

用户已亽 5 项调整全部拍板通过（并追加一条命名要求），本表是权威结论，与正文冲突时以本节为准：

| #   | 拍板                                                                           | 与初稿的差异                                                                                                                                                                                                                                                   |
| --- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `PositionAssignment` **不新建表**，由现有 `employments` 演进为唯一任职关系 SoT | 初稿要 `op.rename_table`；拍板为**物理表名不变**，只在领域层叫 `PositionAssignment`，文档写死映射（避免后人把 HR 入离职与任职关系混用）                                                                                                                        |
| 2   | Slot 的 `VACANT`/`OCCUPIED` 改为派生态                                         | 初稿枚举好包含 `PLANNED/FROZEN/CLOSED`；拍板**补上 `ACTIVE`**（行政态四个值），并要求 API 分开返 `administrative_status` 与 `occupancy_status`                                                                                                                 |
| 3   | `workforce_status` 不落库，并且**正式命名 `WorkforceStatusResolver`**          | 初稿只说"单一函数派生"；拍板要求它成为具名组件，API / service / 前端都不得各自再推一遍；Lifecycle Axis 与 Assignment Axis 正交写入文档                                                                                                                         |
| 4   | Criterion → Competency 一对多，中间表带权重                                    | 字段按拍板命名为 `contribution_weight` + `evidence_type`（后者让"同一证据不重复供证"成为声明）                                                                                                                                                                 |
| 5   | 保留 `assessment_runs.inputs_hash`，但**覆盖面更宽**                           | 需覆盖 profile 版本 / criterion 定义 / 证据 id **与内容摘要** / employee / 时间区间 / 算法版本 / 相关配置；并区分两段承诺：非 LLM 部分确定性可重放，LLM 部分只承诺可追溯（`model`/`provider`/`prompt_version`/`temperature`/`request_hash`/`raw_result_hash`） |

拍板后的领域骨架（取代本文 §5 图中的命名歧义）：

```text
Employee ── lifecycle axis（不新增列）
    └── employments  （DB 表名｜领域实体 PositionAssignment）
            └── PositionSlot （administrative_status 入库；occupancy_status 派生）

PositionSlot Occupancy = derived from active PRIMARY employment
WorkforceStatus        = Lifecycle State + Assignment State + Transfer State（WorkforceStatusResolver）
```
