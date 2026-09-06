# Employee Brain & Behavioral Policy v1

> 状态：**v1 已实现**（PR1–PR6 完成；PR7 的扩展路径由 `tests/test_brain_extensibility.py` 证明）。
> 实现与本文的偏差、以及真机验证证据记录在 §19。
> 原实现契约（下文各节保留为设计依据，其中 §16/§17/§18 已按落地结果更新）
> 取代 `docs/curiosity.md`（已删除；其「curiosity 调制 `reflection.confidence`」方案按 §2 作废）
> 上游原则：**Personality changes behavior, not truth or competence.**

---

## 0. 决策记录（ADR）

| # | 议题 | 决策 | 连带约束（本文已落实） |
|---|---|---|---|
| 1 | trait 存储 | **`traits` JSON 列**，不逐 trait 建列 | §4：`schema_version` + 域层/Pydantic 双重 `0..1` 校验 + 一切读取必须过 `BrainTraits`，业务代码禁止 `traits["curiosity"]`；需要索引时先走表达式索引，最后才 materialized column |
| 2 | 投影范围 | **P1 + P2 同期，同一版本交付，端到端验收** | §3.4 验收链路 + §7.7「adapter 必须显式消费」+ §8 三路投递 + §15 端到端测试；commit 可拆但发布门禁要求真实链路打通 |
| 3 | Hermes `SOUL.md` | **不写、不覆盖** | §8：只写 `brain/PROFILE.md` + `brain/eidolon/behavior.md`，并镜像 `EIDOLON_BEHAVIOR.md` 到已挂载的 `runtime/<type>` |
| 4 | open question 归属 | **`LearningKind.question`**（Learning 域） | §11：不塞 `MemoryEntry`；为 `question → priority → session → evidence → knowledge` 预留 |
| 5 | 候选技能追踪 | **建 `SkillUsage` 表**，不只是「（未经复现）」文字 | §10：schema + 写入点 + outcome 语义 + 三项 benchmark + 红线（指标永不参与判定、永不进 resolver） |
| 6 | 候选技能阈值 | **0.70**，分档 `0–0.3 low / 0.3–0.7 moderate / ≥0.7 high` | §6：阈值集中在 `BehaviorPolicyConfig`（不散落 if），且可由 `Company.settings["behavior_policy"]` 覆盖（该 JSON 列已存在，无需 migration） |

---

## 1. 现状核对（全部已验证，带 `file:line`）

### 1.1 curiosity：写入 3 处、读取 0 处

| 环节 | 位置 | 事实 |
|---|---|---|
| 存储 | `app/models/runtime.py:36` `curiosity: Mapped[float] = mapped_column(default=0.5)` | Float 列，v0.2 `c7f2a9d3e812` 建表即有 |
| 写入 | `app/services/lifecycle.py:283`（hire，clamp [0,1]）、`app/services/seed.py:43-51`、`app/services/runtimes.py:279-284`（`patch_brain` 盲 `setattr`） | 三入口 |
| Task orchestration | `app/workflow/orchestrator.py:161-211` | ❌ 不读 brain |
| Retrieval | `app/learning/retrieval.py:17` `TOP_KNOWLEDGE = 5` | ❌ 常量 |
| Reflection / Learning / Skill | `app/learning/reflection.py:55,103-116`、`app/learning/priorities.py:9` | ❌ 常量 |
| Runtime prompt | `app/workflow/orchestrator.py:205-208` | ❌ 只有 title+description+验收标准 |
| Hermes / OpenClaw brain | `app/runtimes/manager/docker_manager.py:81`（仅 `mkdir`） | ❌ 空目录 |
| 前端 | `hire-wizard.tsx:542-557,666` | 只有填写与摘要 |

**⇒ `curiosity=0.2` 与 `0.9` 的实际行为完全相同。**

### 1.2 必须纠正的三条前提（直接影响设计）

1. **`brain/` 未挂载进容器**。bind 只有 `runtime/<type>`：`docker_manager.py:252`（Hermes → `HERMES_MOUNT="/opt/data"`，`:58`）与 `:276-278`（OpenClaw → `OPENCLAW_CONFIG_MOUNT="/home/node/.openclaw"`，`:59`；auth_dir → `OPENCLAW_AUTH_MOUNT`）。所以"写进 brain/ 文件 = agent 能读到" **不成立** → §8 用三路投递解决。
2. **`confidence` 目前不是证据驱动**：`reflection.py:55` 写死 `0.9/0.6`，`:116` 拿它当 private knowledge 晋升门槛。所以"人格不得影响 truth"的可执行定义 = **`:55` / `:116` 的判定输入禁止引用任何 policy 字段**，并由 §15 的 `test_evidence_invariants.py` 钉住。证据驱动 confidence 属独立议题，明确不在 v1 范围。
3. **prompt 组装位置要下沉**：目前 `orchestrator.py:205-208` 组 prompt，而真实投递点是各 adapter 的出口（Hermes `POST /v1/runs` `json={"input": prompt}`，`hermes/adapter.py:158-172`；OpenClaw WS RPC `chat.send {"message": prompt}`，`openclaw/adapter.py:212-222`）。v1 把「行为指令渲染」下沉到 adapter 出口（§7.7），orchestrator 只传 profile —— 否则无法证明"进了 agent context"。

### 1.3 可复用的既有模式（不新造轮子）

- 参数进入执行的现成通道：`TaskContext`（`runtimes/base.py:57-70`）与 `send_task(session, prompt, {"task_context","runtime_config"})`（`orchestrator.py:209-211`），三个 adapter 都从 `context` 取键（`mock:85`、`hermes:100`、`openclaw:112`）。
- 可观测样板：`retrieval` 结果被 mock emit 成 `using prior knowledge:`（`mock/adapter.py:136-147`）并写进 Artifact `## Prior Knowledge Applied`（`mock/templates.py:122-131`）。
- **往挂载目录写文件有先例**：`docker_manager.py:233-239` 每次 `_container_spec()` 都渲染 provider 配置文件（含 `.env` 的 `chmod 0o600`）→ 行为投影复用同一时机与同一写法。
- 免 migration 的两个 JSON 载体：`RuntimeInstance.metadata_json`（`models/runtime.py:65`，记 applied revision）、`Company.settings`（`models/organization.py:17`，记公司级策略覆盖）。
- `LearningPriority` 支持 `(employee_id, topic)` upsert（`uq_learning_priority`）；`FAILURE_PRIORITY_SCORE = 70`（`priorities.py:9`）。

---

## 2. 核心原则

```
Trait ≠ Capability      Trait ≠ Success Rate      Trait ≠ Knowledge Confidence
```

**允许被影响（6 类）**：探索范围（相邻经验条数与新旧配比）、探索意愿（是否引用 candidate 技能）、Reflection 深度（open question / 备选假设**条数与措辞**）、后续学习主题（延伸 `LearningPriority` 条数与分数上限）、Runtime 工作方式指令、上下文预算上限。

**禁止被影响（5 类，均配守卫）**

| 禁止 | 真源 | 守卫手段 |
|---|---|---|
| 任务成功/失败 | `orchestrator.py:213-225`（runtime 事件）、mock 的 `runtime_config.mock_fail_rate` | policy 不得出现在判定路径；§3.3 AST 扫描 |
| Skill 晋升 | `reflection.py:103-112`（`attempts>=3` ∧ `success_rate>=0.8`） | 输入只有真实结果统计；回归断言数值不变 |
| `LearningRecord/KnowledgeItem.confidence` | `reflection.py:55`、`:116` | `test_evidence_invariants.py` |
| 知识晋升 department/company | Proposal + Review 流程 | 无 policy 入参 |
| 验收/评审结论 | `tutorials/requirements.py`、`POST /reviews/{id}/decision` | 无 policy 入参 |

**红线补充（决策 5 带来的新红线）**：`SkillUsage` 与由其算出的 benchmark 属**观察面**，
禁止被 `app/brain/` 或任何派发逻辑读取 —— 即"探索多 ⇒ 系统更相信他"这条路径必须不存在。

---

## 3. 架构

### 3.1 分层

```
                      ┌────────────────────────────────────────────┐
 持久层                │ EmployeeBrain (DB)                          │
                      │ curiosity: Float(legacy 别名) + traits: JSON │ ← 只存原始 trait 值
                      └────────────────────┬───────────────────────┘
                                           │ 唯一读取通道
                                           ▼
 契约层   app/brain/traits.py  BrainTraits（Mapping 只读视图）
                             · schema_version 演进 · clamp · 未注册 key 忽略
                                           │
 声明层   app/brain/registry.py TraitSpec（key/domain/default/**affects**）
                                           │
 解析层   app/brain/resolver.py resolve(brain, config) -> BehaviorPolicy
          app/brain/config.py   BehaviorPolicyConfig（阈值/分档；Company.settings 可覆盖）
                                           │   ← 全仓唯一允许出现阈值算法之处
             ┌──────────────┬──────────────┼──────────────────┬───────────────┐
             ▼              ▼              ▼                  ▼               ▼
 策略层  RetrievalPolicy ReflectionPolicy LearningPolicy RuntimeBehaviorProfile
             │              │              │                  │
 消费层      ▼              ▼              ▼                  ▼
        learning/       learning/       learning/         app/brain/projection.py
        retrieval.py    reflection.py   priorities.py      （PROFILE.md / behavior.md /
        （唯一参数点）   （唯一参数点）   （唯一参数点）       EIDOLON_BEHAVIOR.md 镜像）
                                                             │
                                                             ▼
                                             runtimes/{mock,hermes,openclaw}/adapter.py
                                             （出口显式渲染 behavior_block）
                      └──── 单一构造点：workflow/orchestrator.py 每任务 resolve 一次 ────┘
```

### 3.2 数据流（每个任务恰好一次解析）

`orchestrator` 取 brain → `resolve()` → 用 `retrieval.policy` 检索（拿到带 id 的 skill 引用）→ 写 `SkillUsage` →
把 `RuntimeBehaviorProfile` 挂进 `TaskContext` 与 `send_task` context → adapter 出口渲染进真实 payload →
任务结束 `reflect(policy.reflection, policy.learning)` 产 open question 与延伸学习项 →
`SkillUsage.outcome` 按真实结果**观察性**回填。

### 3.3 强制规则（写成测试，不写成约定）

| 规则 | 手段 |
|---|---|
| 业务模块禁止读单个 trait：`learning/`、`runtimes/`、`workflow/`、`tutorials/` 内出现 `.curiosity` / `.creativity` / `traits[` 即失败 | `test_behavior_policy_architecture.py` AST 扫描（`app/brain/`、`app/services/` 白名单） |
| 业务模块只接受 policy 参数，形参名固定 `policy` / `learning` / `behavior` | 同上，扫函数签名 |
| `resolve(` 在派发路径上只允许 1 个调用点（orchestrator）；其余仅 UI 预览端点 | 调用点计数 |
| 所有 policy dataclass frozen；字段类型仅 `int/float/bool/str/tuple[str, …]`（禁止 Session/ORM 对象） | `dataclasses.fields` 反射测试 |
| `implemented=True` 的 adapter 必须渲染 `behavior_block(`，或诚实地 `brain_projection=False` | AST + `get_capabilities()` 断言 |
| `app/brain/` 禁止 import `skill_usage` 相关（观察面不得回流成判定） | import 扫描 |
| 未注册 trait key 必须被拒（写）/忽略（读） | §4.3 + `test_brain_patch_validation.py` |

### 3.4 端到端验收链路（决策 2 的硬定义）

```
EmployeeBrain → BehaviorPolicy → Projection → Hermes/OpenClaw → Real Task Context
```

验收 = 下面五条同时成立（§15 各有对应测试，PR 合并门禁）：

1. **解析确定**：同一 brain 两次 `resolve()` 结果全等，且 `policy_version`/`profile_revision` 一致。
2. **投递可证**：Hermes 的 `POST /v1/runs` 请求体 `input`、OpenClaw 的 `chat.send` 的 `message` 中，均包含该员工 `profile_revision` 对应的行为指令文本（fake HTTP / fake WS 断言）。
3. **文件可翻阅**：`brain/PROFILE.md`、`brain/eidolon/behavior.md` 已写出，并在 recreate 时镜像为 `runtime/<type>/EIDOLON_BEHAVIOR.md`（容器内可读写路径）。
4. **差异可观测**：curiosity 0.1 与 0.9 的员工执行同一任务，检索条数 / open question 数 / 延伸学习项数 / `SkillUsage` 行数不同，而**事件终止类型、Artifact 条数、耗时预算、confidence、skill 统计完全一致**。
5. **诚实降级**：`GET /runtime-types` 暴露 `brain_projection`，任何不支持的 runtime 明确报 False，UI 标注"该 Runtime 不会读到行为投影"。

---

## 4. Trait 存储与 `BrainTraits`（决策 1）

### 4.1 列定义

```python
# app/models/runtime.py::EmployeeBrain
traits: Mapped[dict] = mapped_column(JSON, default=dict)   # {"schema_version": 1, "curiosity": 0.72}
```

`curiosity` Float 列保留为 **legacy 别名**：读优先级 `traits > Float 列 > TraitSpec.default`；
写同时落两处（旧客户端与既有报表不破）。Float 列的 deprecate 不进 v1。

### 4.2 三条约束的实现位置

| 约束 | 实现 |
|---|---|
| `0.0 <= trait <= 1.0` 统一校验 | 域层 `BrainTraits.coerce(key, value)`（越界 → `ValueError`）；Pydantic 层 `TraitMap`（`field_validator` + 白名单，越界/未知 key → **422**）。写路径异常绝不落到 `resolve()`：解析阶段遇脏数据只 clamp + warn |
| JSON 内带 `schema_version` | `BrainTraits.SCHEMA_VERSION = 1`；读出时若无/低于当前版本走 `_migrate_v0_to_v1`（把 Float `curiosity` 提升进 JSON）；**高于**当前版本（回滚场景）不崩，缺失 key 全部回落 default 并 warn |
| 读取必须过 `BrainTraits` | `BrainTraits` 是唯一 `Mapping` 视图（只读、`__getitem__` 不暴露可变 dict）；业务代码禁止 `traits["curiosity"]`，由 §3.3 扫描强制 |

### 4.3 只读视图与写入面

```python
# app/brain/traits.py
class BrainTraits(Mapping[str, float]):
    SCHEMA_VERSION: ClassVar[int] = 1
    @classmethod
    def from_brain(cls, brain: EmployeeBrain | None) -> "BrainTraits": ...  # 脏数据 clamp + warn，绝不抛
    @classmethod
    def build(cls, patch: dict[str, float]) -> dict: ...                    # 校验 + 注入 schema_version，越界/未知 key 抛 ValueError
    def get(self, key: str) -> float: ...                                   # 未设置 → TraitSpec.default
    def band(self, key: str) -> str: ...                                    # "low" | "moderate" | "high"（§6 分档）
    def snapshot(self) -> tuple[tuple[str, float], ...]: ...                # 排序后可哈希，供 profile_revision
```

### 4.4 需要可查询时的升级阶梯（不做提前列化）

1. **表达式索引**（首选中间档，免 migration 之外的成本）：SQLite `CREATE INDEX … ON employee_brains (json_extract(traits,'$.curiosity'))`；PostgreSQL 用 `((traits->>'curiosity')::float8)` 表达式索引。
2. **materialized / generated column**：仅当出现真实排序、约束或跨表 JOIN 需求时，加 `ADD COLUMN` + 回填 migration，并在 `TraitSpec` 上标 `materialized=True` 保持单一真源仍为 JSON。
3. 逐 trait Float 列方案**明确否决**（会把 Employee Brain 变成 schema 驱动系统）。

---

## 5. `BehaviorPolicy` 契约（v1）

```python
# app/brain/policy.py
@dataclass(frozen=True)
class RetrievalPolicy:
    knowledge_limit: int = 5              # 取代 retrieval.TOP_KNOWLEDGE
    include_candidate_skills: bool = False
    novel_topic_ratio: float = 0.0        # 相邻（非直接命中）主题占比
    max_context_items: int = 8            # token 兜底上限

@dataclass(frozen=True)
class ReflectionPolicy:
    open_question_count: int = 0          # 额外 LearningRecord(kind=question)
    alternative_hypotheses: int = 0       # 备选假设条数（写进 question 正文，不是结论）
    note_style: str = "standard"          # "standard" | "exploratory"（只改措辞）
    # ⚠ 故意没有 confidence 字段：见 §2

@dataclass(frozen=True)
class LearningPolicy:
    followup_topics_per_task: int = 0     # 延伸 LearningPriority 条数
    priority_score_cap: int = 69          # 严格 < FAILURE_PRIORITY_SCORE(70)
    topic_source: str = "kind_map"        # 固定映射表，不抽任务文本

@dataclass(frozen=True)
class RuntimeBehaviorProfile:
    trait_snapshot: tuple[tuple[str, float], ...]
    work_directives: tuple[str, ...]      # 渲染进 payload 的指令行
    band: str = "moderate"                # low | moderate | high（决策 6 分档）
    profile_revision: int = 0             # hash(traits + policy_version + config)
    policy_version: str = "behavior-v1"

@dataclass(frozen=True)
class BehaviorPolicy:
    retrieval: RetrievalPolicy
    reflection: ReflectionPolicy
    learning: LearningPolicy
    runtime: RuntimeBehaviorProfile
    def as_dict(self) -> dict: ...        # 进 send_task context 与 behavior.applied 事件
```

| 字段 | 消费者 | 单位/边界 | 禁止用途 |
|---|---|---|---|
| `retrieval.knowledge_limit` | `learning/retrieval.py` | 条数 1..10 | 不得改排序权重 |
| `retrieval.include_candidate_skills` | `learning/retrieval.py`、`orchestrator`（写 usage）、prompt | bool | 不得跳过 `validation_status` 判定，只多带一份名单 |
| `retrieval.novel_topic_ratio` | `learning/retrieval.py` | 0..0.4 | 不得引入 department/company 知识（越 `Private Memory ≠ Company Knowledge` 边界） |
| `reflection.open_question_count` | `learning/reflection.py` | 0..3 | 不得改 `confidence`、不得直接产 `KnowledgeItem` |
| `learning.followup_topics_per_task` | `learning/priorities.py` | 0..2 | 分数不得 ≥70 |
| `runtime.work_directives` | adapter 出口 + 投影文件 | 文本行 | 不得含验收标准/结论性判断/成功预期 |

---

## 6. 解析与策略配置（决策 6：阈值集中、可被公司策略覆盖）

```python
# app/brain/config.py
@dataclass(frozen=True)
class BehaviorPolicyConfig:
    candidate_skill_threshold: float = 0.70          # ← 决策 6，唯一出现处
    band_boundaries: tuple[float, float] = (0.30, 0.70)   # low / moderate / high
    knowledge_limit_range: tuple[int, int] = (1, 10)
    open_question_max: int = 3
    followup_topics_max: int = 2
    novel_topic_ratio_max: float = 0.4
    priority_score_cap: int = 69
    policy_version: str = "behavior-v1"

DEFAULT_CONFIG = BehaviorPolicyConfig()

def config_for(company: Company | None) -> BehaviorPolicyConfig:
    """Company.settings["behavior_policy"] 覆盖 —— 该 JSON 列已存在，免 migration。
    只允许覆盖已知字段；未知字段忽略 + warn。"""
```

```python
# app/brain/resolver.py
def resolve(brain: EmployeeBrain | None, config: BehaviorPolicyConfig = DEFAULT_CONFIG) -> BehaviorPolicy:
    """纯函数：无 IO、无随机、无时间依赖。全仓唯一允许出现阈值算法的地方。"""
    if brain is None or not settings.behavior_policy_enabled:
        return DEFAULT_POLICY
    traits = BrainTraits.from_brain(brain)
    c = traits.get("curiosity")
    lo, hi = config.knowledge_limit_range
    return BehaviorPolicy(
        retrieval=RetrievalPolicy(
            knowledge_limit=max(lo, min(hi, 1 + round(c * 9))),
            include_candidate_skills=c >= config.candidate_skill_threshold,
            novel_topic_ratio=round(min(config.novel_topic_ratio_max, 0.4 * c), 2),
            max_context_items=min(12, 4 + round(c * 8)),
        ),
        reflection=ReflectionPolicy(
            open_question_count=round(config.open_question_max * c),
            alternative_hypotheses=1 if c >= config.candidate_skill_threshold - 0.1 else 0,
            note_style="exploratory" if c >= 0.6 else "standard",
        ),
        learning=LearningPolicy(
            followup_topics_per_task=round(config.followup_topics_max * c),
            priority_score_cap=config.priority_score_cap,
        ),
        runtime=RuntimeBehaviorProfile(
            trait_snapshot=traits.snapshot(),
            work_directives=_directives_for(traits, config),
            band=traits.band("curiosity"),
            profile_revision=_revision_of(traits, config),
            policy_version=config.policy_version,
        ),
    )

# 关闭开关 / 无 brain ⇒ 逐字等于今天的常量行为（回滚语义锚点）
DEFAULT_POLICY = BehaviorPolicy(
    retrieval=RetrievalPolicy(knowledge_limit=5, include_candidate_skills=False,
                              novel_topic_ratio=0.0, max_context_items=8),
    reflection=ReflectionPolicy(),
    learning=LearningPolicy(),
    runtime=RuntimeBehaviorProfile(trait_snapshot=(), work_directives=(), profile_revision=0),
)
```

trait → 效果对照（`0.6` 为向导默认值；**全表没有一列动过成功率、耗时或 confidence**）：

| curiosity | 分档 | 相邻经验 | candidate 技能 | 延伸项 | open question | 备选假设 |
|---|---|---|---|---|---|---|
| 0.00 | low | 1 | 否 | 0 | 0 | 0 |
| 0.30 | moderate | 4 | 否 | 1 | 1 | 0 |
| 0.60 | moderate | 6 | 否 | 1 | 2 | 1 |
| **0.70** | **high** | **7** | **是** | **1** | **2** | **1** |
| 0.72 | high | 7 | 是 | 1 | 2 | 1 |
| 0.90 | high | 9 | 是 | 2 | 3 | 1 |
| 1.00 | high | 10 | 是 | 2 | 3 | 1 |

不变式（表驱动测试逐条钉）：`knowledge_limit ∈ [1,10]`、`open_question_count ∈ [0,3]`、
`followup_topics ∈ [0,2]`、`priority_score ≤ 69 < FAILURE_PRIORITY_SCORE`、`resolve` 纯函数、
`resolve(None) == DEFAULT_POLICY`。

---

## 7. 注入 seam（每模块恰好一处）

| # | 位置 | 改法 |
|---|---|---|
| 1 | `workflow/orchestrator.py:161-176` | 全仓唯一任务期 `resolve()`：`cfg = config_for(company)`；`policy = resolve(runtime_repo.get_brain(db, employee.id), cfg)` |
| 2 | `learning/retrieval.py:25` | 返回类型升级为 `RetrievalResult`（§9），签名 `retrieve_for_task(db, employee_id, title, description, *, policy: RetrievalPolicy)`；`TOP_KNOWLEDGE` 改读 `policy.knowledge_limit` |
| 3 | `workflow/orchestrator.py`（紧接 2 之后、`send_task` 之前） | 写 `SkillUsage`（§10.2） |
| 4 | `runtimes/base.py:57-70` | `TaskContext` 新增 `behavior: RuntimeBehaviorProfile \| None = None`（默认 None ⇒ 老测试不破）；`orchestrator.py:166` 填入 |
| 5 | `orchestrator.py:205-211` | prompt 组装**不再**拼行为段；改为把 profile 交出去：`send_task(session, prompt, {"task_context", "runtime_config", "behavior_profile": task_ctx.behavior})` |
| 6 | `learning/reflection.py:40` | `reflect(..., policy: ReflectionPolicy, learning: LearningPolicy)`；`:55` 与 `:116` **一行不改**；新增 open question 记录与延伸 priority（同 session、同 commit 边界） |
| 7 | **各 adapter 出口（决策 2/3 的关键）** | `mock/adapter.py:83-95`、`hermes/adapter.py:158-172`、`openclaw/adapter.py:212-222` 统一调用 `app/brain/projection.py::behavior_block(profile, style=…)` 并拼进**真实外发载荷**：Hermes `json={"input": prompt + block}`、OpenClaw `chat.send {"message": prompt + block}`、mock 转成事件 + Artifact 段。无 profile 时字节级不变 |
| 8 | `runtimes/base.py:95-108 RuntimeCapabilities` | 新增 `brain_projection: bool = False`；mock/hermes/openclaw 置 True（真实投递后），`GET /runtime-types` 自动透传 |
| 9 | `services/{lifecycle,runtimes}.py` | brain 写入后走 `BrainTraits.build()`；`patch_brain` 不再盲 `setattr`（改走白名单 + clamp） |

`LearningKind` 新增 `question`：`models/knowledge.py` 的 `kind` 是无 CHECK 的 `String(50)`（默认 `reflection`），**加枚举成员不需要 migration**（已核对 v0.2 建表 SQL）。

---

## 8. Runtime Brain Projection（决策 2/3：不只写文件）

### 8.1 三路投递，职责不重叠

| 通道 | 落点 | 时效 | 作用 |
|---|---|---|---|
| **T1 载荷内联（保证生效）** | Hermes `input` / OpenClaw `chat.send.message` / mock 事件 | **每任务实时** | 唯一被验收链路 §3.4-2 证明"进了 agent context"的通道 |
| **T2 权威文件（人读 + 审计 + 未来 bind）** | `data/employees/{id}/brain/PROFILE.md`、`brain/eidolon/behavior.md` | brain 变更即重写 | 兑现 `docs/architecture.md:379`；不依赖容器 |
| **T3 容器镜像（agent 可翻阅）** | 已挂载的 `runtime/<type>/EIDOLON_BEHAVIOR.md` → 容器内 `/opt/data/EIDOLON_BEHAVIOR.md`（Hermes home）与 `/home/node/.openclaw/EIDOLON_BEHAVIOR.md`（OpenClaw config 根） | **create / recreate / managed update 时**（`_container_spec()` 内，与 provider 配置文件同一时机同一写法） | 让 agent 跨任务复用；不改容器规格 |

**明确不做**：不写、不覆盖、不 include `SOUL.md` / `IDENTITY.md` / `AGENTS.md` / `MEMORY.md`（决策 3）。
T3 只放 Eidolon 自有文件名的独立文件；OpenClaw 若要读 workspace 约定路径，留给后续单独决策。

### 8.2 版本一致性与并发安全

- 文件首行版本戳：`<!-- behavior-v1 rev=<hex> traits=curiosity:0.72 -->`，内容幂等重写、可 diff。
- PATCH brain **不写挂载目录**：`docs/research.md:13` 明确「同一 Hermes profile 不可被两个进程并发写入」。T3 只在 `_container_spec()` 写（该函数本就每次重渲染 provider 文件，且只在 create/recreate/update 被调用）。
- 已应用版本记在 `RuntimeInstance.metadata_json["behavior_revision"]`（免 migration）；PATCH 后 UI 提示「实时投递已生效，容器内文件将在下次 recreate 时同步」——诚实标注差异，不假装即时。
- 权限沿用 `docker_manager.py:233-239` 的既有做法；若文件含敏感内容则 `chmod 0o600`（行为投影不含 secret，保持默认）。

### 8.3 文件 schema

```markdown
<!-- behavior-v1 rev=17f3a1 traits=curiosity:0.72 -->
# Employee Behavior — {name} ({role})

## Identity
Personality / Goals / Interests（来自 EmployeeBrain，原样）

## Behavior policy (derived, read-only)
- Curiosity 0.72 → band=high
- 相邻经验检索上限 7 条
- 允许试用未验证技能（引用须标注「未经复现」）
- 反思：额外 2 条未解问题 + 1 条备选假设
- 延伸学习项 1 条/任务

## Work directives
1. 动手前最多查阅 7 条相邻经验，其中可含未直接命中的相邻主题
2. 可试用尚未验证的技能，引用时标注「未经复现」
3. 完成前提交 2 个未解问题与 1 个备选假设，标注为探索性

> 这些参数只改变工作方式，不改变交付判定、验收标准与成功与否的评价口径。
```

最后一行是**给 agent 的边界声明**，也防止 agent 自己把"好奇心高"理解成"可以降低质量要求"。

---

## 9. `RetrievalResult`：为了 SkillUsage 必须先升级检索返回

现状 `retrieve_for_task()` 返回 `tuple[list[str], list[str]]`（**只有技能名，没有 id**，`retrieval.py:25-53`），无法写 `SkillUsage.skill_id`。v1 改为：

```python
@dataclass(frozen=True)
class SkillRef:
    skill_id: int
    name: str
    validated: bool

@dataclass(frozen=True)
class RetrievalResult:
    knowledge_topics: tuple[str, ...]
    skills: tuple[SkillRef, ...]              # validated（+ 允许时 candidate）
    policy: RetrievalPolicy                    # 便于自描述与日志

    @property
    def validated_names(self) -> tuple[str, ...]: ...
    @property
    def candidate_names(self) -> tuple[str, ...]: ...
```

调用点只有 `orchestrator.py:163-165`：`task_ctx.validated_skills` 继续填名字（**adapter 契约不变**），
同时把 `result.skills` 拿去写 usage。改动面 1 处 + 测试若干。

---

## 10. `SkillUsage` 与长期 benchmark（决策 5）

### 10.1 schema

```python
# app/models/knowledge.py
class SkillUsage(TimestampMixin, Base):
    __tablename__ = "skill_usages"
    __table_args__ = (UniqueConstraint("task_id", "skill_id", name="uq_skill_usage"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    work_session_id: Mapped[int | None] = mapped_column(ForeignKey("work_sessions.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    usage_type: Mapped[str] = mapped_column(String(20), default="validated")   # candidate | validated
    outcome: Mapped[str] = mapped_column(String(20), default="unknown")        # unknown|useful|not_useful|failed
    outcome_source: Mapped[str] = mapped_column(String(20), default="pending") # pending|manual|evaluation|task_result
    validated_after_use: Mapped[bool] = mapped_column(default=False)
```

`usage_type` 在写入瞬间按 `skill.validation_status` 定档（**记录事实，不预测**）；
`outcome_source` 是证据溯源的最小要求：`task_result`（自动观察）、`manual`（评审人）、`evaluation`（未来自动评测）。
`task_id` 允许 NULL（未来的学习会话复用此表）。

### 10.2 三个写入点

| 时机 | 谁 | 写什么 |
|---|---|---|
| 派发后、`send_task` 前 | `orchestrator`（seam #3） | 每个被带入的技能一行；`outcome=unknown`；`(task_id, skill_id)` 唯一约束保证重试幂等 |
| 任务终态 | `orchestrator` finalize（seam 与 `reflect()` 同一事务边界） | 失败 → `outcome=failed`，`outcome_source=task_result`；成功 → **保持 `unknown`**（成功不等于该技能有用） |
| candidate→validated 翻转时 | `reflection.py:105,112` 的 `skill_validated` 分支（发布 `skill.validated` 事件的位置在 `:154`） | 该 skill 最早的 usage 置 `validated_after_use=True` |
| 人工评审 | 新 `PATCH /skill-usages/{id}/outcome` | `outcome ∈ useful\|not_useful`，`outcome_source=manual`（评审室 UI 可后置） |

**方向性红线**：`outcome` 只能被真实结果或人写，policy / resolver 永不写、永不读（§3.3）。
任务失败时把 usage 记成 `failed` 是**观察**（不改变任何判定）；反过来"因为好奇所以默认 useful"绝对禁止。

### 10.3 Benchmark（观察面，只读）

```
候选技能试用率 TrialRate      = candidate usage 数 / 总 usage 数
候选转正率 ConversionRate     = candidate usage 覆盖的 skill 中 validated_after_use=True 的比例
候选有用率 UsefulRate         = candidate usage 中 outcome=useful 的比例（剔除 outcome=pending 的分母！）
```

端点：`GET /employees/{id}/skill-benchmarks`、`GET /companies/{id}/skill-benchmarks?group_by=curiosity_band`
（按 `low/moderate/high` 三档 cohort 对比转正率与有用率）。
`UsefulRate` 的分母必须排除未评级的 usage —— 否则早期数据会伪装成"探索没用"。

这组指标才真正回答决策 5 的目标：**"更爱探索的员工，最后长出了什么不同的技能"**，
而不是只给一条 `Curiosity 80%` 的进度条。同时它们**只读**：不参与派发、不参与晋升、不进 policy。

---

## 11. `LearningKind.question`（决策 4）

```python
class LearningKind(StrEnum):
    reflection = "reflection"
    research = "research"
    question = "question"      # 完成任务后产生的、待进一步学习/验证的问题
```

- 由 `ReflectionPolicy.open_question_count` 决定条数；正文结构 `problem`（现象）/ `observation`（备选假设）/
  `solution` 留空 —— **空 solution 是"未解"的显式标记**，防止未来把问题当结论复用。
- `confidence` 一律 `0.0`（未验证），且 `kind=question` 的记录**不参与** §5 `reflection.py:116` 的知识晋升判定（该分支只认 `LearningKind.reflection`；实施时加显式 `kind` 过滤，别依赖隐式巧合）。
- 为未来自主学习链路留形：`question → LearningPriority → learning session → evidence → KnowledgeItem`；
  `topic` 复用 `SKILL_BY_KIND` 口径，保证与延伸 priority 可 JOIN。

---

## 12. API 与可观测性

| 接口 | 变化 |
|---|---|
| `GET /employees/{id}/brain` | `EmployeeBrainOut` 增 computed：`traits: dict`、`behavior`（四子 policy 摘要 + `band` + `profile_revision`）。派生值不入库 |
| `PATCH /employees/{id}/brain` | 接受 `traits`（白名单 + `0..1`）与 legacy `curiosity`；返回新 `profile_revision`；脏数据 422 |
| 新 `GET /employees/{id}/brain/projection` | 返回 `PROFILE.md` 文本（UI 预览 / 排障） |
| 新 `GET /employees/{id}/skill-benchmarks`、`/companies/{id}/skill-benchmarks` | §10.3 三指标，支持 `group_by=curiosity_band` |
| 新 `PATCH /skill-usages/{id}/outcome` | 人工评级（`outcome_source=manual`） |
| 新事件 `behavior.applied` | `{task_id, employee_id, policy_version, profile_revision, band, knowledge_limit, include_candidate_skills, open_question_count, followup_topics, projected: {inline, files, mirror}}`；**不含任何 confidence/成功率字段** |
| `GET /runtime-types` | 透传 `brain_projection`（诚实位） |
| 前端 | 向导滑杆 `step` 0.1→0.05、档位词用 `band`；摘要行 `已启用 · 60%` → `Curiosity 72% · high · 相邻经验 7 条 · 候选技能 是 · 延伸学习 1 项/任务`；员工详情新增只读 Behavior 卡 + SkillBenchmark 卡 |

---

## 13. 校验与一致性修复（随 PR1）

1. `schemas/runtime.py:107` `EmployeeBrainPatch.curiosity: float \| None` 无边界 → 实测 `5.0` 能过校验并被盲 `setattr` 写库；修成 `Field(default=None, ge=0.0, le=1.0)` + 白名单 traits。
2. `services/runtimes.py:279-284` 的盲 `setattr` 改为经 `BrainTraits.build()` 的显式写入。
3. `services/lifecycle.py:283` 的 clamp 保留为双保险。
4. `learningEnabled=false` 组合语义：`learning_policy.enabled=false` ⇒ `resolve()` 返回 `DEFAULT_POLICY`（trait 属学习回路，不与"关掉学习"叠加）。
5. `app/core/config.py` 增 `behavior_policy_enabled: bool = True`；`.env.example` 登记 `EIDOLON_BEHAVIOR_POLICY_ENABLED`。

---

## 14. Migration、兼容性与回滚

- **Migration（2 条）**：
  - `v10 employee_brain_traits`：`employee_brains.traits` JSON（`server_default="{}"`）。
  - `v11 skill_usages`：新表 + `uq_skill_usage(task_id, skill_id)` + 三个 FK 索引。
  - 均沿用现有 `upgrade()` 判存写法（`sa.inspect(bind)`，见 `g2b5d8e1f407` 注释）。`LearningKind.question` 与 `RuntimeCapabilities` 字段不需 migration。
  - 落地后必跑：`make migrate` → `alembic check` → 空库 `upgrade head`/`downgrade base` 往返（§15 最后一条）。
- **回滚**：`EIDOLON_BEHAVIOR_POLICY_ENABLED=false` ⇒ `resolve()` 返回 `DEFAULT_POLICY`，逐字回到今天的常量行为；`SkillUsage` 表留着不动（纯增量表，无人读即无影响）。
- **存量数据效应**：库里 12 名员工已有 curiosity 0.4–0.9，开关一开他们的探索行为立刻变化 —— 属预期，PR 描述必须写明，并把开关作为首周兜底。
- **教程安全**：`app/tutorials/requirements.py` 只断言 `ResourceAccount.status`（`:201-213`）与 `facts.delivery_count > 0`（`:306`），不看知识条数/Artifact 条数/事件序列 ⇒ §2「只影响过程」保证引导流程不被走挂。
- **需复核的既有测试**：`test_mock_runtime.py`（断言 `kinds[0]/[-1]`/包含关系，中间多消息安全）、`test_artifacts_learning.py:131`（子串）、`test_lifecycle.py:99-110`（hire 0.8 回写 0.8）、`test_database_schema.py`（新表须能被 `upgrade head` 建出）。

---

## 15. 测试计划

| 文件 | 断言要点 |
|---|---|
| `test_brain_traits_registry.py` | TraitSpec 注册合法、`affects` 只引用存在的 policy 字段、`default ∈ domain` |
| `test_brain_traits.py` | `schema_version` 读写、脏数据 clamp+warn 不抛、高于当前版本（回滚）回落 default、`BrainTraits.build` 越界/未知 key 抛 `ValueError` |
| `test_behavior_policy_resolver.py` | §6 对照表逐行、纯函数（两次全等）、`resolve(None) == DEFAULT_POLICY`、全部不变式 |
| `test_behavior_policy_config.py` | `config_for(company)` 能被 `Company.settings["behavior_policy"]` 覆盖阈值；未知字段忽略 |
| `test_behavior_policy_architecture.py` | §3.3 全部 AST 规则（trait 直读、`resolve(` 调用点数、形参名、frozen、adapter 投递守卫、`app/brain/` 不 import usage） |
| `test_evidence_invariants.py` | **验收闸门**：curiosity 0.0/0.5/1.0 跑同一 `reflect()`，`confidence`、`KnowledgeItem` 生成与否、`skill.*` 统计三者完全一致 |
| `test_retrieval_policy_consumption.py` | `knowledge_limit` 生效；`include_candidate_skills=False` 时 candidate 绝不出现；`novel_topic_ratio` 不越 private scope；`RetrievalResult` 带 skill_id |
| `test_skill_usage.py` | usage 幂等（重试同 task 只 1 行）；失败→`outcome=failed/source=task_result`；成功→保持 `unknown`；candidate 转正→`validated_after_use`；人工 PATCH 能改 outcome |
| `test_skill_benchmarks.py` | 三指标口径（尤其 `UsefulRate` 分母排除 pending）、cohort 分档 |
| `test_reflection_questions.py` | `open_question_count` 只产 `kind=question` 且 `confidence=0.0`，不产 `KnowledgeItem`；延伸 priority 幂等且 `score ≤ 69` |
| `test_brain_projection_files.py` | T2/T3 文件内容、版本戳、幂等重写；PATCH 后挂载镜像**不变**、recreate 后变；`SOUL.md` 绝不被触碰（断言不存在则仍不存在） |
| `test_behavior_delivery_adapters.py` | **§3.4-2**：Hermes fake HTTP 断言 `input` 含行为段；OpenClaw fake WS 断言 `chat.send.message` 含行为段；mock 断言事件 + Artifact 段；无 profile 时字节级不变 |
| `test_behavior_observable_end_to_end.py` | §3.4-4：0.1 vs 0.9 员工同一任务的差异集与不变集（事件终止、Artifact 条数、耗时预算、confidence、skill 统计不变） |
| `tests/integration/test_behavior_runtime_projection.py` | fake docker 断言 `runtime/<type>/EIDOLON_BEHAVIOR.md` 出现在挂载路径，且 `metadata_json["behavior_revision"]` 同步 |
| migration 往返 | 空库 `upgrade head` → `downgrade base` → `upgrade head` → `alembic check` 无漂移 |

---

## 16. 分期与发布验收

commit 可拆，但 **PR1–PR5 属同一版本交付**（决策 2 要求端到端，不允许只交文件）。

**落地状态**：PR1–PR6 已完成（下表 PR 编号对应实际交付内容）。
PR 与测试文件的实际对应关系有出入 —— 实际交付的测试是
`test_brain_contract.py`(16) / `test_behavior_policy.py`(21) / `test_runtimes_behavior.py`(11) /
`test_brain_extensibility.py`(4) / `test_evidence_invariants.py`(14，闸门) / `test_architecture_guards.py`(8)
+ 前端 `behavior-preview.test.tsx`(2) / `behavior-tab.test.tsx`(3)，见 §19.3。
PR7 未落地为一个真的第二特质（避免半用人格），改为用 `test_brain_extensibility.py`
机器证明"注册 ≠ 生效、生效只需 resolver 一处"，见 §19.2。

| PR | 内容 | 关键验收命令/测试 |
|---|---|---|
| **PR1** Brain 契约层 | `app/brain/{traits,registry,config,policy,resolver}.py` + `v10 traits` migration + §13 校验修复 + §3.3 架构守卫 | `make migrate` → `alembic check` → `make test-server` |
| **PR2 Retrieval + Usage** | `RetrievalResult`、`v11 skill_usages` migration、seam #2/#3、`GET …/skill-benchmarks` | `test_skill_usage.py`、`test_retrieval_policy_consumption.py` |
| **PR3 Reflection/Learning** | `LearningKind.question`、open question、延伸 priority、`behavior.applied` 事件 | `test_evidence_invariants.py` **必须绿**（闸门） |
| **PR4 T1 真实投递** | `behavior_block()` + 三 adapter 出口 + `RuntimeCapabilities.brain_projection` | `test_behavior_delivery_adapters.py`、`GET /runtime-types` 人工核对 |
| **PR5 T2/T3 文件投影** | `projection.py`、brain 文件、recreate 镜像、`metadata_json.behavior_revision`、`GET …/brain/projection` | `test_brain_projection_files.py`、`tests/integration` |
| **PR6 UI** | 向导 band 文案 + 摘要额度 + 详情 Behavior/SkillBenchmark 卡 + i18n 双语 | `pnpm exec tsc --noEmit && pnpm exec vitest run` |
| **PR7 trait 演练** | 加 `risk_tolerance`（只影响备选假设数与指令措辞） | 验收标准：**只改 `registry.py`+`policy.py`+`resolver.py`**，workflow 零改动 |

整体 §3.4 五条同时成立才可标记 **Behavioral Policy v1 完成**。

---

## 17. 文件清单（按实际落地修订）

**新增（后端）**：`app/brain/{__init__,traits,registry,config,policy,resolver,projection}.py`；
`migrations/versions/<rev>_v10_employee_brain_traits.py`、`<rev>_v11_skill_usages.py`；§15 的 14 个测试文件。
**改动（后端）**：`models/{runtime,knowledge,enums}.py`、`schemas/runtime.py`、`services/{runtimes,lifecycle}.py`、
`repositories/knowledge.py`、`learning/{retrieval,reflection,priorities}.py`、`runtimes/base.py`、
`runtimes/{mock/adapter,mock/templates,hermes/adapter,openclaw/adapter}.py`、`runtimes/manager/docker_manager.py`、
`workflow/orchestrator.py`、`api/v1/{employees,knowledge,runtimes}.py`、`core/config.py`、`.env.example`。
**改动（前端，实际路径以 `apps/web/src/` 为准）**：`types/index.ts`、`api/{runtimes,employees,behavior}.ts`、
`hooks/{useEmployees,useBehavior}.ts`、`components/lifecycle/hire-wizard.tsx`、
`components/lifecycle/behavior-preview.tsx`、`components/employee/behavior-tab.tsx`、
`pages/employees/employee-detail-page.tsx`、`i18n/locales/{zh-CN,en-US}/{lifecycle,employee}.json`。
（本文初稿写的 `src/lib/*`、`api/learning.ts`、`useRuntimes.ts` 与实际目录不符 —— 该仓库前端是 `api/ + hooks/ + components/ + pages/`。）
**测试（前端）**：`behavior-preview.test.tsx`（服务端档位/额度 + 学习关闭文案）、`behavior-tab.test.tsx`
（额度渲染、`null` 显示为 `—` 而不是 0%、策略缺失时的降级）。
**文档**：`docs/architecture.md:379` 已改写为"curiosity 等 trait 经 `BehaviorPolicyResolver` 解析后由
retrieval / reflection / prompt / 投影消费"并指向本文；§11 启动流程补投影重建一步。

---

## 18. 原待确认 3 项 —— 均已定稿并实现

1. **T3 镜像文件名与位置**：Hermes 放 `/opt/data/EIDOLON_BEHAVIOR.md`（profile home 根）会不会污染 Hermes 自己的文件约定？备选是 `/opt/data/eidolon/behavior.md`（多一层自有目录，更干净但需要 Hermes 侧"知道去哪读"，而 v1 不打算改 SOUL/AGENTS，所以 agent 未必主动翻阅 —— 反正 T1 实时投递已保证生效，T3 只算可翻阅冗余）。我倾向 `eidolon/behavior.md`。
2. **`useful/not_useful` 的评级入口**：v1 只提供 `PATCH /skill-usages/{id}/outcome`（API 可用、UI 后置），还是同时在评审室 `review-room-page.tsx` 放一个按钮？前者成本 0.5 天、后者 1.5 天。
3. **`alternative_hypotheses` 的门槛**：现在复用 `candidate_skill_threshold - 0.1 = 0.6`（§6 代码里是显式表达式）。要么单列 `hypothesis_threshold: float = 0.60` 进 config（推荐，语义独立、便于公司策略覆盖），要么继续派生。

---

## 19. 实现结果与偏差（v1 落地后回填）

本节记录"写代码时发现本文与事实不符的地方"，以免后来者按旧文推断实现。

### 19.1 三处必须记住的偏差

1. **`Skill` 没有 `confidence` 列**。本文 §7/§9 假设"候选技能按 confidence 门槛放行"。
   实际 `skills` 表只有 `attempts / success_count / validation_status`。所以候选门改为
   **观测成功率**：`success_count / attempts >= candidate_min_success_rate` 且
   `attempts >= candidate_min_attempts`。人格只控制"要不要把它暴露给这次任务"，
   不控制它算不算可靠 —— 可靠性是数据，不是性格（§2 原则的具体化）。
2. **`/behavior/preview` 的契约是 `?trait=<名>&value=<数>`，不是 `?curiosity=<数>`**。
   原因不是审美：`tests/test_architecture_guards.py` 禁止业务层出现
   `traits["curiosity"]` 这类按键取 trait 的写法（AST 检查），而换算必须只有一份。
   于是预览走 `app.brain.preview_policy()`，API 层只搬运数字 —— 副作用是端点天然
   支持未来任何新 trait，不必再加 query 参数。越界值夹紧到 0..1（滑杆拖到边界也该有可读预览），
   **未注册 trait 名返回 422**（拼错字段不能静默显示默认档位）。
3. **`PATCH …/skill-usages/{id}/outcome` 用 `extra="forbid"`**。真机验证时发现
   请求体里夹带 `success` 会被 pydantic 静默丢弃并返回 200 —— 安全，但对集成方是谎言。
   现在直接 422。人评落库的 `outcome_source` 值是 `manual_rating`。

### 19.2 解析结果的实际公式（与 §6 表述对齐）

`c = curiosity`，`config` 为公司可覆盖的 `BehaviorPolicyConfig`：

| 策略字段 | 公式 |
|---|---|
| `retrieval.knowledge_limit` | `clamp(round(knowledge_limit_mid + (c - 0.5) * knowledge_limit_span), 1, 10)`，mid=5 ⇒ 中档与改造前的常量 5 逐字相同 |
| `retrieval.max_context_items` | `min(max_context_items_cap=12, knowledge_limit + 3)` |
| `retrieval.novel_topic_ratio` | `round(c * novel_topic_ratio_max, 3)` |
| `retrieval.include_candidate_skills` | `c >= candidate_skill_threshold`（并受 §19.1-1 的观测成功率门约束） |
| `reflection.open_question_count` | `min(open_question_max=3, _ladder(c, 3))` |
| `reflection.alternative_hypotheses` | `c >= hypothesis_threshold(0.60)` 时 `_ladder(c, 2)`，否则 0（独立配置项，非派生表达式） |
| `reflection.note_style` | `c >= band_boundaries[1]` ⇒ `exploratory`，否则 `standard` |
| `learning.followup_topics_per_task` | `min(followup_topics_max=2, _ladder(c, 2))` |
| `learning.followup_priority_score` | `min(priority_score_cap=69, floor + _ladder(c, span))` —— **上限派生自 resolver**，延伸主题优先级永远低于失败优先级（70+），人格不得改变"什么算必须补的课" |
| `learning.topic_source` | `c >= interest_topic_threshold(0.30)` ⇒ `kind_map+interest` |
| `runtime.profile_revision` | `int(sha256(traits + config + policy_version)[:8], 16)` |

`_ladder(c, cap)` 把连续 trait 量化成 0..cap 的整数额度，避免同一档位内抖动出无意义的差异。

### 19.3 §3.4 验收条件的证据来源

| 条件 | 自动化证据 | 真机证据（dev server + dev DB，非 pytest） |
|---|---|---|
| ① 人格改变检索额度 | `test_behavior_policy.py::test_high_curiosity…*` | `GET …/brain` → `knowledge_limit=8 / candidate=true / questions=3`（c=0.9）；`PATCH traits.curiosity=0.15` → `band=low / questions=0` |
| ② 策略真的到达运行时 | `test_runtimes_behavior.py`（T1 出站 payload 断言） | 交付物正文含 `## 行为方式（behavior-v1 · rev 2045140619 · 档位 high）` + 探索指令 |
| ③ 候选技能被暴露且可度量 | `test_skill_usage.py` 系列 + `test_behavior_policy.py` e2e | 第二轮任务写入 `skill_usages`：`selection_reason=policy_candidate`、`policy_version=behavior-v1`、`profile_revision` 与 brain 一致；`benchmarks.total_usages=1` |
| ④ 学习产出被改变 | `test_behavior_policy.py` + `behavior.applied` 断言 | `events.behavior.applied`（`open_questions=3`、`band=high`）；`learning_records` 出现 3 条 `kind='question'` |
| ⑤ 关掉开关逐字回到今天 | `test_behavior_policy.py::test_kill_switch*`、`test_evidence_invariants.py` | `EIDOLON_BEHAVIOR_POLICY_ENABLED=false` ⇒ `resolve()` 返回 `DEFAULT_POLICY`；legacy `curiosity` 镜像列仍在读路径上 |

### 19.4 顺带发现的既有问题（不属本特性，未修）

`employees.memory_namespace`（`emp_<slug>`）有全局唯一约束，而 `slug` 只在**公司内**唯一。
跨公司创建同名 slug 的员工会在 `POST /employees/onboard` 触发
`UNIQUE constraint failed: employees.memory_namespace` → 500（真机验证第二轮时撞到）。
**已收敛的部分**（commit `fix(lifecycle): 招聘撞全局唯一列时返回 409`）：onboard 前的存在性
检查改为仓库层 `org_repo.slug_taken_anywhere()`（全局口径），冲突从 500 变成带 slug 的 409，
`memory_namespace` 格式仍是 `emp_{slug}` —— **数据语义没动**。

**仍未做的部分**：把命名空间真正做成公司内唯一（例如 `c{company_id}_emp_{slug}`，需要一次
数据迁移 + 运行时命名空间消费方一起改）。这属于身份语义变更，不该夹在行为策略里顺手做。

### 19.5 尚未做的部分（明确不背 v1 的名）

- **PR7 的真第二特质**：只做扩展性证明，没有真的注册 `risk_tolerance`（半用的人格比不用更糟）。
- **UI 上的技能评价按钮**：v1 只有 API（§18 决策 2），评审室页面未加按钮。
- **Company 设置界面的 `behavior_policy` 覆盖编辑器**：后端读取
  `Company.settings["behavior_policy"]`，非法覆盖整体回落默认配置（半个阈值集比没有更糟），
  但 UI 还没给公司改这块的入口。
