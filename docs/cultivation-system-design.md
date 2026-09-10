# T1 培养子系统实施方案

> 状态：实施中。上游依据：docs/talent-ecosystem-vision.md §2-§4（两类角色/模板/同构复用）、
> docs/talent-ecosystem-plan.md §3 T1（验收标准）、docs/concept-architecture.md §2.1（候选人定义）。
> 本文把愿景裁定成可落地的实体、迁移与阶段拆解。

## 1. 核心裁定：Character 不建新「人」实体

R1 之后，「候选人 = 无所属关系的 PersonCore」（concept-architecture §2.1）。因此 T1
**不实现愿景 §7 草案里的独立 Character 表**——角色就是 Person，培养/市场语义按
D4.1 约定挂扩展表：

```
persons（身份聚合根，不动）
  └─ character_profiles  1:1   培养/市场域：origin、owner_company、lifecycle、identity_id
  └─ training_programs   1:N   一次培养实例：模板、当前阶段、资源消耗、RNG seed
  └─ education_events    1:N   履历事件流：课程/考试/际遇/项目，回链证据
```

入职动作（T2 范围）= 为该 person 建 employee 行，知识/技能/证据/人格**天然继承**
（它们已经在 person_id 口径上，无外键改写——这正是 R1 换来的红利）。

## 2. 关键设计决策

### D1：遗留 employee_id 列放开 NOT NULL（v27 迁移，T1 的硬前置）

现状勘察：person 域各表的旧 `employee_id` 列全是 NOT NULL（仅
`knowledge_items.owner_employee_id` 可空）——无 employee 行的 person 无法拥有
记忆/技能/学习/能力数据。v27 用 `batch_alter_table` 放开以下列为 nullable
（约束需命名，逐表核实；匿名约束炸了的表单独处理）：

- employee_brains / runtime_instances（runtime.py:55,81）
- memory_entries / skills / learning_records / skill_usages / learning_priorities（knowledge.py）
- learning_sessions（learning.py:32，company_id 一并放开）
- employee_competencies / competency_evidence / assessment_runs（competency.py）

放开后的服务层纪律（写进各 repo docstring）：员工路径永远双写两列；培养路径
只写 person 列。**架构守卫**：person-only 行（employee_id IS NULL）必须能在
character_profiles 找到对应角色——防止脏数据。

### D2：character_profiles（培养/市场域扩展表）

| 字段 | 说明 |
| --- | --- |
| person_id unique | 1:1 挂 Person |
| identity_id unique | 全局唯一身份标识（愿景 §6.1），生成即终身不变，T2/M1 交易锚点 |
| origin | issued（官方发行）/ trained（玩家自训）/ blank（空白自由养成起点） |
| owner_company_id nullable | 培养/持有它的公司；NULL = 在市场（T2 用） |
| lifecycle | cultivating → ready（养成完成）；（listed/hired 属 T2，枚举预留） |

T1 只实现 trained/blank 的创建（玩家自训）；issued 的发行方生成器属 T2。

### D3：培养会话 = 学习链路的 pre-hire 模式（愿景 §4 同构复用）

- `training_programs`：person_id、template（academic/vocational/self_taught/自由=无模板）、
  当前阶段游标、资源消耗累计、`rng_seed`（创建时落库——分布采样的确定性来源，
  测试可注入固定 seed）、status。
- 模板是**数据不是代码**：三个首发模板（学院派/职业派/自学派，愿景 §3.2 的倾向表）
  定义为 JSON 配置（阶段序列：主题集合 + 途径 + 强度 + 时长 + 概率倾向），
  放 `app/talent/cultivation/templates.py`（常量）以便测试断言，后续可挪 DB。
- 阶段推进 = 复用学习链路产出：发起 learning_session（v27 后 employee/company 可空，
  加 `program_id` 列挂培养实例）→ 产出知识条目（owner_person_id）、技能候选、
  教育证据——**产出代码路径与员工学习完全共用**，差异只在输入参数与证据分级。
- 自由养成（无模板）：玩家逐次指定主题/途径/强度，走同一条产出路径。

### D4：教育证据分级（愿景 §4.1，防证据通胀）

`evidence/policy.py` 的可靠性序列表加教育来源档（source_kind 新值）：

| source_kind | 可靠性 | 对应培养活动 |
| --- | --- | --- |
| edu_course | ~0.5（与 mock 同档） | 课程学习/阅读 |
| edu_exam | ~0.8 | 考试/测验 |
| edu_project / edu_internship / edu_competition | ~0.9-0.95 | 毕业项目/实习/竞赛（接近真实工作） |

教育证据写 `environment="education"`（与 mock 的打折机制同构）。
**隔离硬规则**：教育证据参与角色自己的阶段评估，但公司员工的正式考核
（project.completed 链路）**不采集**教育来源——守卫测试钉死。

### D5：际遇事件与人格成型（愿景 §3.1/§4.3）

- 际遇：阶段推进时按模板定义的概率触发（竞赛获奖/遇到好老师/沉迷课外领域/项目
  失败……），效果 = 对当前阶段产出分布的扰动（证据强度修正、额外知识主题、
  traits 偏移）。全部落 `education_events`（kind=fortune），履历可追溯。
- 随机性全部来自 `training_programs.rng_seed` 派生的 RNG——同 seed 同结果
  （测试确定），不同 seed 产出分布有差异（验收标准 1）。
- 人格：培养期按模板倾向 + 际遇扰动生成 8 维 traits 初值（employee_brains 的
  person-only 行），入职后继续按既有机制演化。

### D6：能力画像只由证据聚合（验收标准 2）

角色阶段评估复用 assessment 引擎（assessment_runs 走 person 口径，company_id
快照用 owner_company）。**加架构守卫**：competency score 的写入只准出现在
聚合器（competency/assessment service），角色相关代码无任何直接写分路径。

## 3. 阶段拆解

| 子阶段 | 内容 | 验收增量 |
| --- | --- | --- |
| T1.0 ✅（v27，2026-09-10） | v27 遗留列放开 + 三张新表 + Character CRUD API/repo + 守卫 | 建角色（trained/blank）→ person+profile 落库，identity_id 唯一 |
| T1.1 | 模板定义 + 培养引擎（阶段推进→学习产出复用）+ 教育证据分级 | 跑完一个模板阶段，知识/技能/证据按分级落库 |
| T1.2 | 际遇事件 + 人格成型 + 阶段评估接线 | 同模板不同 seed 产出分布有差异；评估全走证据聚合 |
| T1.3 | 培养 UI（建角色→选模板/自由→时间线→成品档案） | 页面全流程可走通，i18n 中英 |

每子阶段独立迁移、独立提交、门禁全绿。

## 4. 边界（防范围膨胀）

- 不做：发行方生成器（T2）、挂牌/交易（T2/M1）、培养资源扣货币（M1 前用简单
  预算计数占位）、角色间关系模拟（愿景 §10）；
- 不改 K1 的公司隔离 join 口径（person-only 行的可见性经 character_profiles
  单独解析，培养 UI 只展示本公司 owner 的角色）；
- LearningSession 的员工路径行为零变化（既有 656 测试是回归网）。
