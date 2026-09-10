# 人才培养生态 · 总体实施计划

> 上游文档：[概念架构](concept-architecture.md)（抽象骨架与扩展规则）、
> [人才生态愿景](talent-ecosystem-vision.md)（培养/知识库/市场的产品形态）。
> 本文是落地计划：阶段划分、每阶段的设计要点与验收标准。
>
> 核心策略：**基础设施先行**。先把「过程 = 事件」的地基（事件驱动引擎）立起来，
> 后续每个子系统都以插件方式接入，而不是各自发明调度。

---

## 0. 总体判断

用户的关键洞察（已纳入概念架构 §2.3）：**不同主体的过程在没有交互时天然可并发**。
当前事件总线（`events/bus.py`）是同步 publish + 三个手写 asyncio 消费者，
没有统一的分发与并发模型。要支撑培养/市场这类"很多角色同时各自演化"的场景，
需要把事件消费升级为一个**分区并发的事件引擎**——这是所有后续阶段的地基。

技术约束（决定了引擎形态）：

- **SQLite 单写者**：真实写并发天花板很低，引擎的价值在于*逻辑上的*并发与保序
  （重活在事务外做，DB 写事务保持短），而不是假装能并行写库；
- **单进程部署**：不引入 Kafka/Redis 等外部 MQ；事件已持久化在 `events` 表，
  进程内分发 + sweep/reconcile 兜底的模式已被验证（入职卡死四层根因的教训）；
- **orchestrator 刻意不是通用引擎**（`workflow/orchestrator.py:1` docstring），
  事件引擎也不要做成通用工作流引擎——它只做分发、保序、重试、兜底。

## 1. 事件引擎设计要点（E0 的核心决策）

**分区并发调度器（keyed partition dispatcher）**：

```
bus.publish（现状：同步、线程安全、先落库）
   → 引擎消费者（lifespan 里唯一的 asyncio 入口）
   → 按 partition key 分流：key = (主体类型, 主体 id)，如 ("employee", 12) / ("company", 1)
   → 每个 key 一个串行队列（同主体的事件严格按 publish 顺序处理）
   → 不同 key 的队列并发执行（无交互的主体互不阻塞）
```

规则：

1. **处理器注册表**替代硬编码消费者：`register(event_type, handler, key_of)`，
   `key_of(event_payload)` 声明分区键提取方式；无键事件（广播类）走全局分区。
2. **失败隔离**：某 key 的处理器失败 → 该 key 进入退避重试/死信，**不阻塞其他 key**；
   处理器必须幂等（可重放），启动时 sweep 补偿（沿用 PositionAccessConsumer 的先例）。
3. **写纪律**：handler 内 DB 写事务要短；runtime/LLM 等重活先在事务外做完再落库。
4. **可观测**：每分区队列深度、重试次数进日志/诊断端点（`make dev-inventory` 口径）。
5. 现有三个消费者（PositionAccessConsumer / EvidenceConsumer / WS 广播）**迁移到**
   引擎上，作为注册表的首批处理器——行为不变，只换骨架（有既有测试兜底）。

## 2. 阶段总表

| 阶段 | 名称 | 内容 | 依赖 | 对应愿景 |
| --- | --- | --- | --- | --- |
| **E0** | 事件引擎 ✅ 已完成（commit `feat(events): E0 事件引擎`，hash 见 git log） | 分区并发调度器 + 处理器注册表 + 既有三消费者迁移 | 无 | 概念架构 §2.3 |
| **K1** | 知识库桥 ✅ 已完成（commit hash 见 git log） | 检索 scope 分层（private→+dept+company）+ 晋升物化到 drive/handbook + 公司级知识浏览 UI | 无（可与 E0 并行） | 愿景 §5 |
| **R1** | PersonCore 拆分（进行中：R1.0-R1.4 四批次切读 ✅，R1.5 收尾待做，方案 docs/person-core-migration.md） | Employee = PersonCore + 所属关系；候选人 = 无所属关系的 PersonCore | E0（事件迁移面） | 概念架构 §2.1 |
| **K2** | 检索增强 | SQLite FTS5 全文 + freshness 降置信接入 | K1 | 愿景 §5 |
| **T1** | 培养子系统 | Character 实体 + 培养会话（三模板 + 际遇事件）+ 教育证据分级 | R1 + K1 | 愿景 §3/§4 |
| **T2** | 人才市场（本地） | 发行投放 + Fit 筛选招募 + 履历浏览 + 入职转化 | T1 | 愿景 §2/§6 |
| **M1** | 货币与合同交易 | Wallet/Ledger 复式流水 + 挂牌/报价/escrow 结算 + 所有权转移 | T2 | 愿景 §6 |
| K3 | 语义检索（可选） | embedding 可插拔层（sqlite-vec + provider 绑定 + 混合检索） | K2 + 真实 provider | 愿景 §5 |
| M2 | 联网市场（可选） | MarketAdapter 联网实现 + 身份注册中心 | M1 + 产品决策 | 愿景 §6.4 |

并行关系：E0 与 K1 可并行（无交集）；R1 必须等 E0（消费者迁移面定型后才动
人的实体）；T/M 线依赖 R1 与 K1；K3/M2 是可选增强，随时可插。

## 3. 各阶段详情

### E0 · 事件引擎（基础设施）

- **范围**：`events/` 包内新增 dispatcher（分区队列、注册表、退避/死信、sweep 钩子）；
  迁移 PositionAccessConsumer、EvidenceConsumer、WS 广播；`main.py` lifespan 只起引擎一个消费者。
- **验收**：现有门禁全绿（602+ 后端测试不动行为）；新增分区保序/失败隔离/幂等重放的
  单元测试；`docs/architecture.md` §7 事件系统章节同步更新。
- **风险**：行为回归——靠既有测试 + 迁移后手动过一遍入职/项目流程（教程走查脚本
  `tmp/tutorial_audit.py` 可复用）。

### K1 · 知识库桥

- **范围**：`learning/retrieval.py` 放开 scope 分层（private + department + company，
  scope 越高优先级越高）；`knowledge/promotion.py` 评审通过时物化到 drive
  （company→handbook 区，department→knowledge 区部门夹）并双向回链；
  前端 `/knowledge` 公司级浏览/检索/评审页（接线既有 `api/v1/knowledge.py`）。
- **验收**：员工做任务时能检索到公司已发布知识（端到端测试）；晋升文档落盘可浏览；
  教程 cloud_docs 之后新增可选步骤引导看知识库（评估后再定，不强行加）。

### R1 · PersonCore 拆分（前置重构）

> 详细迁移方案（字段归属裁定、兼容期设计、批次拆解）：docs/person-core-migration.md

- **范围**：`employees` 拆出 PersonCore 语义（身份/人格/知识/技能/能力外键归 person），
  所属公司变为关系行；迁移 alembic + 全仓引用点梳理。这是全计划**风险最高**的一步，
  宜小步：先加 person 层并让 employee 1-1 代理过去（兼容期），再逐域切换读口径。
- **验收**：602+ 测试全绿无行为变化；`EmployeeBrain.traits`、知识/技能/能力的外键
  全部指向 person；文档 §2.1 的拆分声明落地。
- **不做**：本阶段不做候选人 UI、不做培养——只完成实体拆分。

### T1 · 培养子系统

- **范围**：`Character`（identity_id、origin、owner 可空）+ `TrainingProgram` +
  `EducationEvent`；三个首发模板（学院派/职业派/自学派，概率倾向 + 际遇事件扰动）；
  教育证据来源分级（课程 < 考试 < 项目/实习，可靠性递减序列进 `evidence/policy.py`）；
  培养 UI（建角色 → 选模板/自由养成 → 过程时间线 → 成品档案）。
- **验收**：同一模板跑 N 次产出分布有差异（际遇生效）；角色能力画像全部由证据聚合
  （无直接写分路径，加守卫测试）；教育证据不进入公司员工的真实考核。

### T2 · 人才市场（本地模拟）

- **范围**：发行方投放调度（每游戏周期 N 人，品质分层不标战力）、市场浏览
  （履历=证据链视图，score/confidence 并列）、Fit 引擎对 Character 直接可用、
  招募入职（Character→Employee 整包继承，身份 ID 不变）、NPC 公司作为交易对手雏形。
- **验收**：招募页读履历可追溯到每条证据；入职后员工即带着预培知识参与检索。

### M1 · 货币与合同交易

- **范围**：`Wallet`/`LedgerEntry`（复式流水，余额派生不落库）、`MarketListing`/
  `TradeContract`、escrow 冻结/结算、所有权转移；公司营收入口（项目交付计价）。
- **验收**：复式流水平衡校验（借贷必相等）；escrow 并发安全（唯一约束兜底）；
  对倒刷钱的基础防护（培养成本 > 最低回本线的参数化校验）。

### K3 / M2（可选增强，单独立项时再细化）

K3：embedding 走 provider 绑定（配真实模型才启用，mock 回落 FTS5），sqlite-vec 存储，
个人库与公司库同索引 + scope 过滤。M2：发行方服务器撮合 + 身份注册中心。

## 4. 横切要求（每个阶段都要过）

1. 落地前逐条自查概念架构 §4 的十条扩展规则；新增不变量登记到 §7 注册处。
2. 门禁全绿（pytest / ruff / tsc / eslint / prettier / vitest / build / alembic check）。
3. 每个阶段结束更新本文状态列 + 相关域文档；i18n 中英同步。
4. UI 硬要求：score 与 confidence 同等视觉权重；无证据显示「未评估」而非 0。

## 5. 建议的第一刀

**E0 与 K1 并行开工**（互不依赖、都是纯增益无破坏）：
E0 给后面所有阶段提供引擎地基，K1 让「公司知识库」立刻对用户可见可用。
R1 等 E0 合入后启动，T1 等 R1 + K1 就位。
