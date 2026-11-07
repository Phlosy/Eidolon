# Eidolon 概念架构：抽象骨架与子系统边界

> **文档定位**：`architecture.md` 是实现地图（表、文件、端口怎么摆），本文是**概念骨架**
> ——这个系统的宇宙里有哪些抽象、它们如何组合、新功能（尤其是「人才培养子系统」，
> 见 [talent-ecosystem-vision.md](talent-ecosystem-vision.md)）应该插在哪里、遵守什么规则。
>
> 写作动机：在加人才生态之前先把抽象立住。所有判断都以当前代码为据（引用到文件），
> 与代码冲突时以代码为准并修订本文。

---

## 1. 一句话模型

**Eidolon 的宇宙里只有三类东西：主体（Actors）、资产（Assets）、过程（Loops），
外加一个横切的度量平面（Measurement）。**

一切功能都是这四类的组合：主体经历过程，过程产生或改变资产，度量平面负责
让所有"价值声明"可追溯。

## 2. 三类核心抽象 + 一个横切平面

### 2.1 主体（Actors）——宇宙里"谁"存在

| 主体 | 现状载体 | 语义 |
| --- | --- | --- |
| 玩家 | `users` + 会话/成员关系（P12 起） | 经营公司的人 |
| 公司 | `companies` / `departments` / 职位体系 | 组织容器，资产的归属边界（company scope） |
| 人 | `employees`（+ 未来的 `Character`） | **人格（traits）、知识、技能、能力的挂载点** |

关键抽象决策（人才生态的地基）：**"人"必须先于"任职"存在**。
任职早已拆出去（`PositionAssignment` 是当前职位唯一真相，`employees.role`
只是镜像）；"人"与"公司成员"的拆分也已落地：

```
PersonCore（身份/人格/知识/技能/能力 —— 随人走，与任何公司无关）
   + EmploymentRelationship（所属公司/任职 —— 可建立、可转移、可解除）
   = 产品里的"员工"；培养期的"候选人" = 只有 PersonCore 的人
```

> **落地状态（R1.0–R1.4，迁移 v21–v25，详见 docs/person-core-migration.md）**：
> `persons` 表是「人」的聚合根（slug 全局唯一为权威；`employees.slug` 为同源镜像）；
> 人格/记忆/知识/技能/学习/能力/资源/署名各域已按 `person_id` 切读，
> 旧 `employee_id` 列保留为 deprecated 兼容镜像（双写维持，不删）。
> 候选人 = 没有任何 active 任职/employee 行的 Person —— 其 UI 与市场流通属 T2。

这一拆，候选人（pre-hire）、在册员工、被交易的角色就是同一实体的三种状态，
人才培养和交易市场都变成"给 PersonCore 换关系/加资产"，而不是新造平行实体。

### 2.2 资产（Assets）——可积累、有归属、能流转的东西

| 资产 | 载体 | 所有权 | 可转移性 |
| --- | --- | --- | --- |
| 知识 | `KnowledgeItem`（scope 三级） | 人/部门/公司 | 个人随人走；晋升到公司后归公司 |
| 技能 | `Skill` + `SkillUsage` | 人 | 随人走 |
| 能力画像 | `EmployeeCompetency`（无写入口，评估聚合器唯一写入方） | 人 | 随人走 |
| 履历 | `CareerEvent` + `AssessmentRun` + 证据链 | 人（公司可审计） | 随人走，不可篡改 |
| 记忆 | `MemoryEntry` | 人（仓库层强制隔离） | 随人走 |
| 文档/产物 | Drive 四区 + `Artifact` | 公司/项目 | 归公司，不随人走 |
| 货币 | （未来 Wallet/Ledger） | 公司 | 交易流通 |
| 人本身 | （未来 Character/Employee 的所有权） | 公司 | 合同交易（愿景 §6） |

所有资产遵守**统一的生命周期词汇表**（新资产类型必须套用，不许自造）：

```
积累 accumulate → 验证 validate → 发布/晋升 promote → 转移 transfer → 失效 stale/deprecate
```

已有实例：Skill `candidate→validated→deprecated`；知识 `private→proposed→company`、
`freshness_status`；能力 `unrated→provisional→assessed→stale`。
货币与"人"作为新资产入场时，也用这套词汇表达状态机。

### 2.3 过程（Loops）——让资产增值的循环

系统中所有"业务在动"的部分都是**同一个形状**：

```
事实发生 → 发事件（bus）→ 消费者幂等收敛/派生 → 新状态（或只派生不落库）
```

| 循环 | 事实源 | 收敛/派生方 |
| --- | --- | --- |
| 工作循环 | 任务/项目完成（orchestrator） | evidence pipeline → 证据；项目末 → assessment → 能力 |
| 学习循环 | 反思/学习会话（learning） | 知识条目、技能候选、学习优先级 |
| 开通循环 | 任职变更（position_service 只提交事实+发事件） | workforce 算期望集 → lifecycle engine 执行 provisioning |
| 培养循环（未来） | 培养期学习/考试/项目 | 同工作循环，跑在 pre-hire 实体上，教育证据分级 |
| 交易循环（未来） | 挂牌/报价 | escrow 结算 → 所有权转移 |

**"循环即插件"**：一个新子系统 = 新的事实源 + 新事件 + 幂等消费者 +
（可选）派生读模型。消费者必须有启动兜底（sweep/reconcile 模式，
进程死在半路也能自愈——入职卡死四层根因的教训）。

### 2.4 度量平面（Measurement）——横切的"验真机"

- **证据即货币**：一切价值声明（能力分、技能有效性、履历含金量）必须挂证据链
  （`CompetencyEvidence` / `AssessmentRun.inputs_hash`）。这条纪律让交易市场的
  "验货"成为系统内生能力，而不是外挂功能。
- 评估引擎确定性：同输入同输出；无证据不编分；score 与 confidence 永不混算。
- 来源分级贬值：mock 环境证据 ×0.5（先例），教育证据将按同样思路分级。

## 3. 子系统地图

现状归并（包 → 子系统），以及未来的两个新子系统：

| # | 子系统 | 包含（现状包） | 单一事实 | 唯一写入口 |
| --- | --- | --- | --- | --- |
| S1 | 组织与任职 | organization / position / workforce.status | `PositionAssignment`（当前职位）、`companies.stage`（公司阶段） | position_service 提交事实+发事件 |
| S2 | 工作执行 | workflow / project_delivery / runtimes / providers | `tasks`/`project_phases` 状态机 | orchestrator（执行）、project_delivery（交付治理）——两者分管，勿混 |
| S3 | 知识与学习 | knowledge / learning / brain | `KnowledgeItem`/`Skill`/`EmployeeBrain.traits` | learning（产出）、knowledge.promotion（晋升）；brain.resolver 是 trait→策略唯一映射（AST 守卫） |
| S4 | 度量与证据 | evidence / assessment / competency / talent.fit | `competency_evidence` / `employee_competencies` | evidence pipeline 写证据；assessment 聚合器写能力分（无 PATCH 入口） |
| S5 | 资源与权限 | lifecycle（engine/provisioners/access 代数）/ git / drive 权限 | `employee_packages`（期望集）+ `resource_accounts`（实际） | lifecycle engine 唯一执行点；drive 目录只走 `create_node` |
| S6 | 教程与引导 | tutorials | `user_tutorial_progress` | 后端 reconcile 唯一推进方（Facts 纯函数门禁，前端不得伪造） |
| S7 | 平台设施 | core / events / api / models / schemas / repositories / services | —— | 集中四层惯例：models/schemas/repositories 按域分文件，领域包不放模型 |
| **S8** | **人才培养（未来）** | Character / TrainingProgram / 际遇事件 / 教育证据分级 | Character 及其证据链 | 复用 S3 学习产出、S4 度量——**不新造数值系统** |
| **S9** | **市场与经济（未来）** | MarketListing / TradeContract / Wallet / Ledger | Ledger 复式流水（余额为派生） | 交易引擎唯一结算点；MarketAdapter 抽象（本地↔联网可替换） |

**子系统间铁律**：不许跨子系统直接写别人的表。集成只有两条路：
事件（bus.publish / subscribe）或调用对方公开的服务函数。现状已经遵守
（只有 assessment 写能力分、只有 lifecycle engine 动资源、教程只读 Facts），
S8/S9 入场时同样遵守。

## 4. 扩展规则（加新东西时的纪律清单）

从现有不变量泛化而来。新子系统/新资产/新循环落地前逐条自查：

1. **单一事实**：每个概念指出它的唯一真相表；派生量不落库（ADR-12），
   派生字段不给"语义合法"的默认值（ADR-10）。
2. **事实与判断分离**：客观结果（success）与人的评价（outcome）分列，
   互不推导（SkillUsage 先例）。
3. **能力只能被证据证明**：任何路径（培养、交易、发行、管理后台）都不得
   直接写能力分；价值声明必须挂证据链。
4. **人格只影响方式不影响结果**：trait 不得进入分数/成功率/置信度计算
   （有 AST 守卫先例）。
5. **环境分级贬值**：非真实环境（mock/教程/教育）产出的证据必须标注并降权。
6. **事件集成 + 幂等消费 + 启动兜底**：消费者可重放；进程死亡靠 sweep 自愈。
7. **幂等创建走约束兜底**：并发创建靠唯一约束 + ON CONFLICT 回查赢家，
   不靠"先查再插"（TOCTOU 教训）。
8. **租户边界在仓库层强制**：跨公司/跨人读取在 repo 层就不可能，不靠
   服务层自觉（memory_entries 先例）。
9. **新资产套用统一生命周期词汇**（§2.2），新循环套用统一形状（§2.3）。
10. **UI 同等呈现 score 与 confidence**；无证据显示"未评估"而非 0 分。

## 5. 人才培养子系统的接入点

对照愿景文档（[talent-ecosystem-vision.md](talent-ecosystem-vision.md)），
S8/S9 落在骨架上的位置：

- **PersonCore 拆分**（§2.1）是前置重构：Employee = PersonCore + 所属关系。
  候选人 = 只有 PersonCore 的人。这一步做完，S8 的其余部分全是"复用"。
- **培养循环**（§2.3 第四行）：培养期学习会话产出 KnowledgeItem/Skill candidate/
  CompetencyEvidence——复用 S3/S4，唯一新增是**教育证据的来源分级**（S4 扩展，
  不是新机制）。
- **模板与际遇**：是培养循环的"事实生成器"（驱动学习会话的调度器），
  只产事实（教育事件），不碰资产写入口。
- **身份标识**：身份 ID 签发给 PersonCore，履历 = 证据链 + 内容哈希（S4 现成）。
- **交易市场**（S9）：交易的是"PersonCore 的所有权关系"，验货能力来自
  度量平面的证据链（§2.4），货币遵守复式流水 + 派生余额。

## 6. 已知错位与技术债（诚实清单）

抽象骨架对照现状，以下位置已知不整齐，改动相关区域时顺手归位（不要专门返工）：

| 错位 | 现状 | 应属 |
| --- | --- | --- |
| 教程进度表住在 `models/project_delivery.py:187` | 与交付治理模型混放 | S6 教程域的模型文件 |
| `EmployeeBrain` 模型住在 `models/runtime.py` | 持久层放运行时域 | S3（brain 语义所在域）；迁移成本高，先记录 |
| `services/` 集中编排层 30 文件 | 随子系统增多会继续变胖 | 维持"编排层"定位，领域逻辑沉到领域包，不许反向 |
| 早期文档状态清单滞后 | talent-profile.md / competency-system.md 的"未做"部分已过时 | 以本文与代码为准，逐步修订 |
| `services/competency.py` 的 P5 聚合器 | 仅测试调用的 legacy | 生产路径走 services/assessment.py；择机删除 |

## 7. 不变量注册处

各域不变量的权威出处（新增不变量在此登记）：

- ADR-10/11/12（派生纪律）：architecture.md §16–17
- ADR-4（WorkforceStatus 派生不入库）、ADR-5（role 镜像只读）：workforce/status.py、position_compat.py docstring
- 能力证据链与 UI 硬要求：assessment-system.md、competency-system.md
- 教程三纪律：services/tutorial.py docstring
- 事实/判断分离：models/knowledge.py SkillUsage docstring（§10.1/10.2）
- 人才生态不变量：talent-ecosystem-vision.md §1
