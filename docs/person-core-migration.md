# PersonCore 拆分迁移方案（R1）

> 状态：**R1.0–R1.5 已全部落地**（迁移 v21–v25，2026-09-10）。上游依据：docs/concept-architecture.md §2.1、docs/talent-ecosystem-plan.md §3 R1。
> 本文是 R1 的详细迁移方案，回答三个问题：拆什么、怎么拆不炸、分几步验收。

## 1. 为什么拆

当前 `Employee`（`apps/server/app/models/organization.py:36-59`）一行同时承载两种语义：

- **人本身**：姓名、slug、人格（EmployeeBrain）、知识、技能、记忆、runtime/workspace 资源——离职后应当跟随人走，候选人尚未入职时就应该拥有；
- **公司成员身份**：company_id、department_id、title、lifecycle_status、任职（employments）——换公司即变。

不拆的直接后果：人才市场（T2）的「候选人」无处安放。现状勘察确认**候选人根本不是独立实体**——talent roster 只是在职员工的聚合视图（services/talent_roster.py:60），「从候选人转化为员工」的流程不存在。愿景里的「发行角色」「空白自建角色」「市场交易角色」都要求一个先于雇佣关系存在的「人」。

拆完的目标形态：

```
PersonCore（人）1 ──── 0..* Employment（任职/雇佣关系）* ──── 1 Company
     │
     ├─ 人格（brain）、知识、技能、记忆、学习、能力证据   ← 挂在人上
     └─ 候选人 = 没有任何 active 任职的 PersonCore
```

## 2. 字段归属裁定（现状逐字段分类）

### 2.1 Employee 自身字段

| 字段 | 归属 | 说明 |
| --- | --- | --- |
| name / slug / avatar / username | **Person** | 身份与命名。slug 全局唯一，是 workspace/memory_namespace 的根 |
| workspace_path / memory_namespace | **Person**（资源） | 换 runtime/换公司不清身份记忆（services/employees.py:92-94 已证实此语义） |
| runtime_type / runtime_config | **Person**（资源） | 同上 |
| status / current_task_id | Person（运行态） | 工作状态跟人走 |
| company_id / department_id / title / lifecycle_status | **Employment/成员** | 雇佣关系语义 |
| role | 遗留镜像 | ADR-5 已锁死，新代码禁读，不迁移，随 employees 表留存 |

### 2.2 指向 employees 的外键（逐表裁定）

**指「人」——应迁到 person_id（长期）：**

| 表 | 列 | 依据 |
| --- | --- | --- |
| employee_brains | employee_id (unique) | 人格/学习配置，models/runtime.py:45 |
| runtime_instances | employee_id (unique) | 执行环境，models/runtime.py:58 |
| memory_entries | employee_id | 记忆，models/knowledge.py:20 |
| knowledge_items | owner_employee_id | 知识所有者，models/knowledge.py:30 |
| skills / learning_records / skill_usages / learning_priorities | employee_id | 技能与学习，models/knowledge.py |
| learning_sessions | employee_id | models/learning.py:31（company_id 列保留，是公司上下文快照） |
| employee_competencies / competency_evidence / assessment_runs | employee_id | 能力度量，models/competency.py |
| drive_nodes.owner_employee_id / drive_revisions.author_employee_id | 可空 | 所有权/署名，models/drive.py |
| artifacts.author_id / messages.sender_id / document_artifacts.author_employee_id | 可空 | 署名/发言，models/project*.py |
| events.actor_employee_id | 可空 | 行为主体，models/event.py |
| providers.owner_employee_id / model_bindings | 可空 | 员工级 provider 绑定，models/provider.py |

**指「成员身份」——留在 employee_id（不动）：**

| 表 | 依据 |
| --- | --- |
| employments（PositionAssignment） | 任职真相本体，models/position.py:147 |
| employee_packages / resource_accounts / provisioning_jobs | 权限包与生命周期作业，models/lifecycle.py |
| tasks.assignee_id / projects.owner_id / milestones.owner_id | 工作分派，公司上下文 |
| work_sessions | 执行任务（含公司上下文），models/project.py:108 |
| career_events / development_plans | 本公司内的职业历史与发展计划，models/career.py |
| drive_collaborators / audit_logs | 协作授权/审计，成员语义 |
| resource_assets.owner_employee_id | 离职时已改挂部门（offboard rewires），成员语义 |

**裁定原则**：署名/所有权/能力记忆跟人走（人），分派/授权/审计/任职跟雇佣关系走（成员）。模糊个案按此原则逐个点名，不批量拍脑袋。

## 3. 关键设计决策

### D1：id 空间——独立，不共享

`persons.id` 与 `employees.id` 各自独立自增。**不采用**「employee 与 person 共享同一 id」的省迁移方案：它把两张表的 id 空间永久耦合，新人入职要显式指定 id 插入，是个长期陷阱。

代价是 person 侧各表需要加 `person_id` 列并回填（§4 R1.2），但这是机械操作，且有 SQLite 纪律护航（见 D3）。

### D2：兼容期——Employee 1-1 代理 Person

不一次性切读口径。第一阶段只加层不改读：

1. 新建 `persons` 表；`employees` 加 `person_id` 列（nullable，DB 层不强制）；
2. 数据迁移：每个现存 employee 生成一个 person，`employees.person_id` 回填（**迁移内联完成，不靠应用层补**）；
3. 写入口收敛：`services/lifecycle.py:onboard`（:230）与 `services/seed.py`（:121）改为「先建 person 再建 employee」双写；架构守卫测试钉死——`Employee(...)` 裸构造只允许出现在这两个入口 + 测试工厂；
4. 兼容期内**所有读路径不变**（仍按 employees 读），API schema 不变，前端零改动。

兼容期的意义：任何一个后续阶段出问题都可以独立回滚，主流程（入职→任务→学习）始终有 1-1 数据兜底。

### D3：SQLite 迁移纪律（沿用项目既有约定）

- 新列一律 `nullable + server_default` 或裸 nullable，**不加 FK 约束**（项目不开 `PRAGMA foreign_keys`，匿名 FK 重建会炸，见 models/position.py:160-186 的纪律记录）；
- 完整性靠服务层 + 部分唯一索引（如 `persons.slug` 唯一、`employees.person_id` 唯一的部分索引），索引必须写进模型定义，否则 `alembic check` 漂移守卫会红；
- 避免 `batch_alter_table` 重建既有表；回填用迁移内的 `UPDATE ... WHERE person_id IS NULL`；
- 迁移命名沿用 `<12位id>_vNN_<topic>.py`，head 从 `s5a7c9e1f3b5`（v20）往后接 v21 起。

### D4：读口径切换——逐域、先写后读、双向兜底

person 侧各表（§2.2 上半区）的切换按域分组，每域三步：

1. **加列回填**：该域各表加 `person_id` nullable 列 + 回填（`UPDATE t SET person_id = (SELECT person_id FROM employees e WHERE e.id = t.employee_id)`）+ 索引；
2. **双写**：该域的写入路径（service/repo 层）同时写 employee_id 与 person_id；
3. **切读**：repo 读口径从 employee_id 改为 person_id（入参仍是 employee_id 时在 repo 入口 join 一次解析，上层签名尽量不动）；跑全量门禁。

切读稳定后，employee_id 列**保留不删**（SQLite 删列要重建表，不值得；标注 deprecated 即可）。这与项目「employments 不改名、role 镜像留存」的既有风格一致。

切换顺序按风险从低到高、依赖从内到外：

| 批次 | 域 | 涉及表 | 风险点 |
| --- | --- | --- | --- |
| 批次 1 | 人格与学习 | employee_brains, memory_entries, skills, learning_*, skill_usages, learning_priorities | retrieval/orchestrator 读 traits 的口径（brain/resolver.py:50） |
| 批次 2 | 知识 | knowledge_items.owner | K1 刚做的 scope 分层检索（repositories/knowledge.py:55-88） |
| 批次 3 | 能力度量 | employee_competencies, competency_evidence, assessment_runs | assessment pipeline 事件消费者 |
| 批次 4 | 资源与署名 | runtime_instances, providers, model_bindings, drive owner/author, artifacts, messages, events.actor | 事件 actor、drive 权限检查（check_write_permission 按 owner） |

每批次一个迁移 + 一批测试，独立提交。

### D4.1：person 侧模块接入约定（抽象边界）

人格、记忆、知识、技能、学习这些域后续都会长复杂（K2 检索增强、T1 培养会话、教育证据……），扩展性靠下面三条简单约定，**不引入插件机制、抽象基类或注册中心**（那是过度抽象）：

1. **各域表独立，挂 `person_id`**：person 只是聚合根（身份 + 命名），各域数据仍在自己的表里（brain 在 employee_brains、knowledge 在 knowledge_items……），通过 `person_id` 列关联。新模块要接入「人」，就建自己的表 + `person_id` 列——不需要改 persons 表，不需要注册任何东西。T1 的培养会话、T2 的发行档案都按此办理。
2. **人称解析单一入口 `repositories/persons.py`**：employee_id ↔ person_id 的换算只在这一个文件里（`resolve_person_id` 等），各域 repo 切读时调用它，不各自写 join。这样兼容期/切读期/切完后的口径差异被封印在一处。
3. **person 的创建收敛在服务层招聘/种子入口**，repo 只提供 `create_person` 原语。没有 PersonService 大杂烩——person 本身没有业务行为，行为都在各域自己的 service 里。

persons 表本身保持最小（身份字段 + 时间戳），宁可后续迁移加列，不提前放 JSON 万能字段。

### D5：员工无登录身份——不动 auth

`users` 表（操作游戏的人）与 Employee（AI 员工）刻意分离（models/auth.py:1-5），PersonCore 不引入任何登录概念。`Employee.username` 只是命名策略产物，迁到 persons 表纯属命名字段移动。

### D6：测试面策略

现状 21 个测试文件直接 `Employee(...)` 裸构造（无统一 factory）。兼容期方案：

- 新增测试工厂 `tests/factories.py::make_person` / `make_employee`（make_employee 内部自动建 person 并回填 person_id）；
- 21 个文件分批替换为工厂，随各批次迁移顺手改（不单独开一批纯重构 PR）；
- 架构守卫（test_architecture_guards.py 既有模式）新增：裸 `Employee(` 出现位置白名单收敛到 seed/onboard/工厂三处。

### D7：API 与前端——兼容期零改动，T2 才暴露 person

兼容期内 `/api/v1/employees/*` schema 完全不变（service 层组合 person + employment 字段）。person 作为独立资源的 API（候选人列表、person 履历）属于 T2 人才市场阶段，届时新增 `/api/v1/persons/*`，不复用 employees 路由。

## 4. 阶段拆解与验收

### R1.0 · persons 表 + 兼容层（本方案的第一批实施）—— ✅ 已完成（v21）

- 迁移 v21：`persons` 表（id/slug unique/name/avatar/username/status/created_at/updated_at）+ `employees.person_id` nullable 列 + 回填 + 部分唯一索引；
- models 新增 `Person`；`Employee` 加 `person_id` 列与 `person` relationship；
- onboard/seed 双写改造 + 测试工厂 + 架构守卫；
- **验收**：622 测试全绿、alembic check 无漂移、`make dev-restart-clean` 后入职→任务→学习全流程实机走通（复用 tmp/tutorial_audit.py）；所有 employee 行 person_id 非空。
- **落地备注**：persons 表实装未含 `status` 列（按 D4.1 最小列集原则从简，需要时后续迁移加列）；实机验收走的等价路径（迁移回填校验 + 重启 + 真实入职 API + 清理），未跑 dev-restart-clean 全量重置。

### R1.1-R1.4 · 四批次读口径切换（D4 表格）—— ✅ 全部完成（v22–v25）

每批次：迁移（加列+回填+索引）→ 双写 → 切读 → 门禁全绿 → 提交。
**验收（每批次相同）**：全量 pytest 绿；该域 API 响应与切换前逐字段一致（对拍测试或既有等价性测试覆盖）；ruff/alembic 干净。

落地对照：批次 1 人格与学习（v22）、批次 2 知识 owner（v23，`owner_person_id`）、
批次 3 能力度量（v24）、批次 4 资源与署名（v25，镜像命名 `<前缀>_employee_id`
↔ `<前缀>_person_id`；`artifacts.author_id` → `author_person_id`）。

### R1.5 · 收尾 —— ✅ 已完成

- 文档同步：concept-architecture.md §2.1 的拆分声明标记落地、architecture.md 实体章节更新、handover 基线数字更新；
- 技术债登记：遗留镜像列清单见 §7；最终删除条件：等 T2 稳定后评估；
- **R1.3 遗留观察（已排查，确认是 bug 并已修复）**：`evidence/normalize.py::upsert_evidence` 曾在 `db.add` 后不 flush，SessionLocal `autoflush=False` 导致同事务内「先 upsert 后聚合」漏读最后一条 pending 证据（实机实测复现：聚合 outputs 为空）。修复：add 后补 `db.flush()`；回归测试 `tests/test_evidence_pipeline.py::test_upsert_then_assess_in_same_transaction_sees_the_new_evidence`；
- **验收**：`SELECT count(*) FROM employees WHERE person_id IS NULL` 为 0；person 侧四批次表 `person_id` 非空率 100%；全门禁绿。

## 5. 风险与对策

| 风险 | 概率 | 对策 |
| --- | --- | --- |
| 回填遗漏导致 person_id 悬空 | 中 | 每个迁移内联回填 + 收尾阶段非空率校验；repo 切读时 person_id 为 NULL 回落 employee_id 旧口径并记 warning |
| 21 个测试文件构造签名漂移 | 高（必然） | D6 工厂方案，随批次改，不做大爆炸重写 |
| K1 刚改的知识检索被批次 2 波及 | 中 | 批次 2 排在批次 1 之后，retrieval 的 scope 分层测试（test_knowledge_retrieval_scopes.py）即回归网 |
| slug 唯一性双表语义分叉 | 低 | 迁移后 persons.slug 是唯一权威；employees.slug 保留为镜像列（双写），架构守卫禁读 |
| 事件 actor 口径切换漏改消费者 | 中 | 批次 4 单独处理 events.actor；E0 引擎的 handler 注册表让消费点可枚举（engine.stats() 可对拍） |

## 6. 不在本方案内（防止范围膨胀）

- 候选人的 UI/市场流通（T2）；培养模板（T1）；embedding/向量检索（K3）；
- employments 表改名、role 列清理（ADR 明确不做）；
- employees 表 person 相关旧列的最终删除（遗留镜像，T2 稳定后另议）。

## 7. 遗留镜像列清单（R1.5 技术债登记）

读口径已全部切到 person 侧，以下 deprecated 镜像列由双写维持、随表留存不删
（SQLite 删列要重建表，不值得）。**最终删除条件：T2 人才市场稳定后评估**
（届时候选人/跨公司流动真正依赖 person 口径，镜像列无人再读后逐批拆除，
拆除时同步清掉 persons.py 的回落分支与架构守卫白名单）。

| 表 | 镜像列（deprecated） | 权威列 | 批次 / 迁移 |
| --- | --- | --- | --- |
| employees | slug / name / avatar / username | persons.* | R1.0 / v21 |
| employee_brains | employee_id | person_id | R1.1 / v22 |
| memory_entries | employee_id | person_id | R1.1 / v22 |
| skills | employee_id | person_id | R1.1 / v22 |
| learning_records | employee_id | person_id | R1.1 / v22 |
| learning_sessions | employee_id | person_id（company_id 是公司快照，非镜像） | R1.1 / v22 |
| skill_usages | employee_id | person_id | R1.1 / v22 |
| learning_priorities | employee_id | person_id | R1.1 / v22 |
| knowledge_items | owner_employee_id | owner_person_id | R1.2 / v23 |
| employee_competencies | employee_id | person_id | R1.3 / v24 |
| competency_evidence | employee_id | person_id | R1.3 / v24 |
| assessment_runs | employee_id | person_id（company_id 是公司快照，非镜像） | R1.3 / v24 |
| runtime_instances | employee_id | person_id | R1.4 / v25 |
| providers | owner_employee_id | owner_person_id | R1.4 / v25 |
| model_bindings | employee_id | person_id | R1.4 / v25 |
| drive_nodes | owner_employee_id | owner_person_id | R1.4 / v25 |
| drive_revisions | author_employee_id | author_person_id | R1.4 / v25 |
| artifacts | author_id | author_person_id | R1.4 / v25 |
| messages | sender_id / recipient_id | sender_person_id / recipient_person_id | R1.4 / v25 |
| document_artifacts | author_employee_id | author_person_id | R1.4 / v25 |
| review_meetings | presenter_employee_id | presenter_person_id | R1.4 / v25 |
| events | actor_employee_id | actor_person_id | R1.4 / v25 |

明确**不是**镜像、永远留在 employee 口径的列（方案 §2.2 下半区裁定，勿误清）：
drive_collaborators.employee_id、project_phases.owner_employee_id、
resource_assets.owner_employee_id、employments.*、tasks.assignee_id、
projects/milestones.owner_id、work_sessions.employee_id、career_events /
development_plans.employee_id、audit_logs 等（成员身份/工作分派/审计语义）。
