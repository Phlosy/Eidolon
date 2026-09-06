# Eidolon 架构设计 (v0.1.0)

> Eidolon = Autonomous AI Organization Runtime。
> 本文档是 MVP 的唯一架构事实来源（single source of truth），前后端实现必须与本文档保持一致。

## 0. 设计原则

- **Simple implementation, strong boundaries.** 代码可以简单，边界必须清晰。
- 第一天就存在且不可破坏的五条边界：
  1. `Employee ≠ Runtime` — Employee 是持久虚拟员工，Runtime 只是当前执行引擎，可替换。
  2. `Private Memory ≠ Company Knowledge` — 私有记忆/知识默认隔离，需 Proposal + Review 才能升级。
  3. `Task ≠ Agent Session` — Task 是领域对象，WorkSession 才绑定 Runtime Session。
  4. `Domain Logic ≠ API` — API 层只做参数校验与序列化，业务在 Service 层。
  5. `Frontend ≠ Runtime` — 前端只访问 Backend API，永不直连 Hermes/OpenClaw 等。
- **One Employee = One Persistent Agent**：每个 Employee 独占 Runtime Profile / Home / Workspace / Memory namespace，禁止共享。
- Monolith + 清晰模块边界，不上微服务、消息队列、K8s。

## 1. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| Backend | Python 3.12（conda env `eidolon`）/ FastAPI / Pydantic v2 | Agent/LLM 生态最完整；自带 OpenAPI 文档 |
| ORM / Migration | SQLAlchemy 2.0 (sync) / Alembic | 成熟；sync session 配 FastAPI 线程池，MVP 最简单 |
| DB | SQLite（默认）→ PostgreSQL 可切换 | 业务层只走 ORM，不写 SQLite 方言 |
| Backend 工具链 | conda 环境（不在仓库内建 `.venv`）/ pip + `requirements.lock` / Ruff / pytest | 解释器版本与 CI 对齐，环境来源显式可复现 |
| Frontend | React 19 / Vite / TypeScript strict | 标准 |
| Frontend 库 | TanStack Query v5（服务端状态）、Zustand v5（本地状态）、@xyflow/react（项目流程图）、Tailwind CSS v4 + shadcn 风格组件、lucide-react、React Router v7 | 全部成熟活跃，不造轮子 |
| Frontend 工具链 | pnpm / ESLint / Prettier / Vitest | — |
| 实时推送 | WebSocket `/ws/events` | 单向推送足够，不用高频轮询 |
| 进程编排 | 根目录 Makefile；Docker Compose 可选 | `make run` 一键起全栈 |

## 2. Monorepo 目录结构

```text
eidolon/
├── .github/workflows/ci.yml
├── apps/
│   ├── web/                      # React SPA
│   └── server/                   # FastAPI
├── docs/
│   ├── research.md               # Phase 0 调研
│   ├── architecture.md           # 本文档
│   └── ports.md
├── deployments/docker/           # Dockerfile.server / Dockerfile.web
├── scripts/                      # dev 辅助脚本（seed 等）
├── tests/                        # 跨服务 e2e 冒烟（MVP 可为空，避免形式主义）
├── docker-compose.yml
├── Makefile
├── .env.example
└── README.md / LICENSE / CONTRIBUTING.md / SECURITY.md
```

`packages/`（shared/agent-sdk/ui）MVP 阶段不创建：前后端共享契约以 FastAPI 自动生成的 OpenAPI (`/openapi.json`) 为准，避免过早抽象。

### 2.1 Backend 内部结构（apps/server）

```text
apps/server/
├── app/
│   ├── main.py                   # FastAPI 装配：路由、CORS、WS、启动事件
│   ├── api/v1/router.py          # 聚合路由；每个资源一个文件
│   ├── core/
│   │   ├── config.py             # pydantic-settings，EIDOLON_ 前缀
│   │   ├── database.py           # engine / SessionLocal / get_db
│   │   ├── logging.py            # 统一结构化日志
│   │   └── security.py           # （MVP 仅占位：不实现权限系统）
│   ├── models/                   # SQLAlchemy 2.0 typed models（一表一文件或按域聚合）
│   ├── schemas/                  # Pydantic 请求/响应模型
│   ├── repositories/             # 纯数据访问；所有查询强制 scope（employee_id / company_id）
│   ├── services/                 # 业务逻辑；employee/project/task/artifact/knowledge/skill/learning
│   ├── runtimes/
│   │   ├── base.py               # RuntimeAdapter 抽象 + 数据类型
│   │   ├── gateway.py            # RuntimeGateway：adapter 注册表 + per-employee 实例管理
│   │   ├── mock/adapter.py       # MockAdapter（完整实现）
│   │   ├── hermes/adapter.py     # 探测 + 明确 NotImplementedError 占位
│   │   ├── openclaw/adapter.py   # 同上
│   │   └── (codex/claude_code/opencode/custom 在 base 注册类型，adapter 后续补)
│   ├── workflow/orchestrator.py  # 事件驱动的项目流程推进器（非通用引擎）
│   ├── learning/                 # reflection、priorities
│   ├── knowledge/                # proposal / review / retrieval
│   └── events/bus.py             # 内存 EventBus + 持久化 + WS 广播
├── migrations/                   # Alembic
├── tests/
└── pyproject.toml
```

严格分层：`API → Service → Repository → DB`。Route 函数里禁止出现 SQL/业务规则。

### 2.2 Frontend 内部结构（apps/web/src）

```text
├── api/          # fetch client（含错误处理）、按资源的 endpoint 函数
├── components/   # common/ company/ employee/ project/ workflow/ runtime/（纯展示、可复用）
├── features/     # 跨页面业务组件（如 activity-feed）
├── hooks/        # useEmployees / useProject / useEventStream ...
├── layouts/      # AppLayout：侧边栏 + 顶栏 + 主题切换
├── pages/        # dashboard/ office/ employees/ projects/ artifacts/ settings（只做组装）
├── routes/       # React Router 定义
├── stores/       # zustand：theme、实时事件缓冲
├── types/        # 与后端 schema 对齐的 TS 类型（手工维护，向后端 OpenAPI 看齐）
├── utils/
├── App.tsx / main.tsx
```

页面组件只做数据装配，业务组件进 `components/`，禁止单文件巨型组件。

## 3. 领域模型

### 3.1 实体与关系

```text
Company 1─n Department 1─n Employee
Company 1─n Project 1─n Milestone 1─n Task 1─n WorkSession
Task n─n Task (task_dependencies: 前置任务，用于 React Flow 边)
Project 1─n Artifact ; Task 0─n Artifact
Employee 1─n MemoryEntry / KnowledgeItem(private) / Skill / LearningRecord / LearningPriority
Department 1─n KnowledgeItem(department) ; Company 1─n KnowledgeItem(company)
Company 1─n Event ; Project/Task 0─n Message
```

### 3.2 表结构（字段级，全部整数主键 `id` + `created_at` + `updated_at`）

- **companies**: name, slug(unique), description, industry, settings(JSON)
- **departments**: company_id(FK), name, slug, description
- **employees**: company_id, department_id, name, slug(unique), role, title, avatar, status, runtime_type, runtime_config(JSON), workspace_path, memory_namespace(unique), current_task_id(NULL FK→tasks)
- **projects**: company_id, name, description, status, goal, owner_id(NULL FK→employees，即 PM), source_order_text（原始需求）
- **milestones**: project_id, name, description, status, order
- **tasks**: project_id, milestone_id(NULL), title, description, kind, status, priority(int), assignee_id(NULL FK→employees), acceptance_criteria, sequence(int)
- **task_dependencies**: task_id, depends_on_id（联合主键）
- **work_sessions**: task_id, employee_id, runtime_type, runtime_session_ref, status, summary, started_at, ended_at, cost(JSON: tokens/duration)
- **artifacts**: company_id, project_id, task_id(NULL), type, title, content(TEXT), path(NULL), version(int), status, author_id
- **messages**: company_id, project_id(NULL), sender_id, recipient_id(NULL), channel, content
- **memory_entries**: employee_id, kind(note/observation/summary), content, source_ref, — 仓库层强制按 employee_id 过滤
- **knowledge_items**: scope(private/department/company), owner_employee_id(NULL), department_id(NULL), title, content, topic, status(active/proposed/rejected), confidence(float), sources(JSON)
- **skills**: employee_id, name, description, version, attempts, success_count, avg_duration_sec, avg_cost, last_used_at, validation_status(candidate/validated/deprecated)
- **learning_records**: employee_id, project_id(NULL), task_id(NULL), kind(reflection/research), topic, problem, observation, lesson, solution, confidence, sources(JSON)
- **learning_priorities**: employee_id, topic, score(0-100), reason, (unique: employee_id+topic)
- **events**: type, company_id, actor_employee_id(NULL), project_id(NULL), task_id(NULL), payload(JSON)

### 3.3 枚举（前后端共享字符串契约）

```text
EmployeeRole:    ceo | product_manager | researcher | engineer | qa_engineer
EmployeeStatus:  offline | idle | working | researching | learning | reflecting | meeting | error
RuntimeType:     mock | hermes | openclaw | codex | claude_code | opencode | custom
ProjectStatus:   requested | planning | in_progress | in_review | completed | cancelled | rejected
MilestoneStatus: pending | in_progress | completed
TaskStatus:      backlog | todo | in_progress | in_review | done | failed | rejected
TaskKind:        order_review | planning | research | development | testing | final_review | general
ArtifactType:    prd | research_report | architecture | source_code | test_report | readme | release | plan | other
ArtifactStatus:  draft | in_review | approved | rejected
KnowledgeScope:  private | department | company
KnowledgeStatus: active | proposed | rejected
WorkSessionStatus: running | completed | failed | cancelled
LearningKind:    reflection | research
```

展示编号：Task/Project 前端渲染为 `#EID-{id}`（不另建字段）。

### 3.4 关键不变式（需测试覆盖）

1. `memory_entries` / `scope=private 的 knowledge_items` 只能通过 owner 自己的 endpoint 访问；跨员工读取在仓库层即不可能。
2. 一个 Employee 同一时刻最多一个 `status=running` 的 WorkSession。
3. `employees.memory_namespace`、`workspace_path` 全局唯一。
4. Task 状态机合法迁移：`backlog→todo→in_progress→in_review→done/failed/rejected`（rejected→todo 允许 QA 驳回重做）。
5. Runtime 切换只改 `employees.runtime_type/runtime_config`；身份、记忆、技能、经历全部保留。

## 4. Agent Runtime Gateway

### 4.1 Adapter 接口（runtimes/base.py）

```python
class RuntimeAdapter(ABC):
    type: RuntimeType

    @abstractmethod
    async def create_instance(self, employee: EmployeeRef, config: dict) -> RuntimeInstance: ...
    @abstractmethod
    async def start(self, instance: RuntimeInstance) -> None: ...
    @abstractmethod
    async def stop(self, instance: RuntimeInstance) -> None: ...
    @abstractmethod
    async def get_status(self, instance: RuntimeInstance) -> RuntimeStatus: ...
    @abstractmethod
    async def create_session(self, instance: RuntimeInstance, task: TaskContext) -> RuntimeSession: ...
    @abstractmethod
    async def send_task(self, session: RuntimeSession, prompt: str, context: dict) -> None: ...
    @abstractmethod
    async def send_message(self, session: RuntimeSession, message: str) -> None: ...
    @abstractmethod
    def stream_events(self, session: RuntimeSession) -> AsyncIterator[RuntimeEvent]: ...
    @abstractmethod
    async def cancel_task(self, session: RuntimeSession) -> None: ...
    @abstractmethod
    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]: ...
    @abstractmethod
    async def get_runtime_info(self, instance: RuntimeInstance) -> RuntimeInfo: ...

    def detect(self) -> bool:  # 本机是否可用（默认 False）
        return False
```

数据类型：`RuntimeInstance{employee_id, profile, home_path, status}`、`RuntimeSession{id, instance, task_id, status}`、`RuntimeEvent{kind: thinking|message|tool_call|artifact|status|error|completed, data, ts}`、`ProducedArtifact{type, title, content}`。

### 4.2 RuntimeGateway（runtimes/gateway.py）

- 维护 adapter 注册表 `{RuntimeType: adapter}` 与 `employee_id → RuntimeInstance` 映射。
- 强制 **一个 Employee 一个 Instance**；instance 的 profile/home/workspace 派生自 `EIDOLON_WORKSPACE_ROOT/{employee_slug}/`。
- `EIDOLON_RUNTIME_MODE=mock` 时所有真实 adapter 不可用，统一走 MockAdapter。
- Hermes/OpenClaw adapter：实现 `detect()`（探测本机安装），其余方法抛 `NotImplementedError("adapter not implemented yet")` —— 接口先行，实现后置。

### 4.3 MockAdapter 行为

- `send_task` 后在后台 asyncio task 中按 TaskKind 生成事件流：
  `thinking(1-2s) → message(role 化工作描述) → working 若干拍 → artifact → completed`，总时长 5–20s 可配置（`EIDOLON_MOCK_TASK_SECONDS`）。
- Artifact 内容为模板化 Markdown：planning→PRD、research→调研报告（含 sources 列表）、development→架构说明+代码片段、testing→测试报告、order_review/final_review→审批意见。
- 支持 `cancel_task`；支持注入失败（`runtime_config.mock_fail_rate`）以测试 rejected/failed 路径。

## 5. Workflow 编排（workflow/orchestrator.py）

不是通用引擎，是一个事件驱动的推进器，消费 EventBus 事件：

```text
POST /projects (source_order_text) → project.status=requested, event project.created
  → 创建 task(kind=order_review, assignee=CEO)
order_review done → project.status=planning → task(planning, PM)
planning done (产出 PRD artifact) → 按模板生成 Milestones+Tasks 图：
    M1 Discovery : research(researcher)
    M2 Build     : development(engineer)  depends-on research
    M3 Verify    : testing(qa)            depends-on development
    M4 Release   : final_review(ceo)      depends-on testing → 产出 release artifact
  → project.status=in_progress
testing done → final_review → done → project.status=completed, event project.completed
QA rejected → development 任务回到 todo（重做），记录失败供 reflection
```

**Dispatcher**：task 进入 `todo` 且 assignee 当前 `idle` → 创建 WorkSession → Gateway 创建 Runtime Session → `send_task` → 消费 `stream_events`：更新员工状态/进度，收到 `completed` → 落 Artifact、task→in_review→done、触发 reflection。同一员工串行执行（一次一个 session）。

## 6. Learning 与 Knowledge

### 6.1 Reflection（Project Learning）

task done/failed 时 `learning/reflection.py` 生成 LearningRecord（MVP 为规则模板，不依赖 LLM）：
problem（任务标题+结果）、observation、lesson、solution、confidence、sources。
副作用：
- 关联 Skill（按 TaskKind 映射到技能名，如 `development→software-development`）：attempts+1，成功则 success_count+1，更新 success_rate/avg_duration；技能初始 validation_status=candidate，attempts≥3 且 success_rate≥0.8 → validated。
- 高 confidence（≥0.8）的 lesson 自动生成一条 `scope=private` KnowledgeItem。

### 6.2 Autonomous Research（占位实现）

`learning/priorities.py`：从 role gap / repeated failure / project need 计算 LearningPriority 分数并落库；提供 API 供前端展示。MVP 不自动发起真实 web research（保留 `kind=research` 的 LearningRecord 结构与 API）。

### 6.3 Knowledge 分层与晋升

- 私有知识默认 `scope=private`，他人不可见。
- 晋升：`POST /knowledge/{id}/proposals {target_scope}` → status=proposed；`POST /knowledge/{id}/review {approve}` → scope 变更 + status=active。
- 公司/部门知识不进 Agent 全量 context；`GET /knowledge?scope=&topic=` 即 MVP 的 retrieval 接口。

## 7. 事件系统（events/bus.py）

- 内存 `asyncio` 发布订阅；每条事件同时 INSERT events 表（activity feed 数据源）并广播到所有 WS 连接。
- 事件类型（payload 为相关对象摘要）：

```text
company.created
employee.created employee.status_changed
project.created project.started project.completed
task.created task.assigned task.started task.completed task.failed
runtime.started runtime.stopped
artifact.created
learning.started learning.completed
knowledge.created knowledge.proposed knowledge.promoted
skill.created skill.validated
```

- WS `/ws/events`：推送 `{type, data, ts}`；前端只增量更新，历史走 `GET /events?limit=`。

## 8. API 设计（/api/v1，REST）

统一约定：响应为资源 JSON；列表为数组（MVP 不做分页包装）；错误 `{detail: str}` + 合适状态码；写操作返回更新后对象。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /health | 存活检查 |
| GET | /company | 当前公司（含 departments） |
| GET/POST | /employees | 列表 / 创建 |
| GET/PATCH | /employees/{id} | 详情 / 更新（含状态、runtime 切换） |
| GET | /employees/{id}/memory | 私有记忆（仅本人视角，MVP 无鉴权，接口形状先行） |
| GET | /employees/{id}/knowledge | 私有知识 |
| GET | /employees/{id}/skills | 技能 |
| GET | /employees/{id}/learning-records | 学习记录 |
| GET | /employees/{id}/learning-priorities | 学习优先级 |
| GET | /employees/{id}/activity | 该员工事件流 |
| GET | /employees/{id}/performance | attempts/success/artifact 数聚合 |
| GET/POST | /projects | 列表 / 创建（= 创建 Order，body: name, description(source_order_text), goal?） |
| GET | /projects/{id} | 详情（含 milestones、tasks（每个 task 携带 dependencies: number[]）、artifacts） |
| GET | /projects/{id}/graph | React Flow 直接用：`{nodes:[{id,type,label,status}], edges:[{source,target}]}` |
| GET/PATCH | /tasks/{id} | 详情 / 状态变更（走状态机校验） |
| GET/POST | /artifacts, /artifacts/{id} | 列表（?project_id=&type=）/ 详情 |
| GET/POST | /messages | 消息（MVP 可选使用） |
| GET | /knowledge?scope=&topic= | 知识检索（private 必须带 employee_id） |
| POST | /knowledge/{id}/proposals | 提交晋升提案 |
| POST | /knowledge/{id}/review | 审批晋升 |
| GET | /runtimes | adapter 列表（type, available, implemented）+ 各 employee instance 状态 |
| GET | /events?limit= | 活动流 |
| GET | /settings | 非敏感配置回显（runtime_mode、workspace_root、ports） |
| WS | /ws/events | 实时事件 |

前端默认走同源相对路径（`/api/v1/...`、WS `/ws/events`，ws/wss 由 `window.location` 推导），由 Vite dev proxy / nginx 反代到后端；`VITE_API_BASE_URL` 仅作为指向其他 origin 的可选覆盖（空 = 同源）。

## 9. 配置（core/config.py，pydantic-settings）

```text
EIDOLON_WEB_HOST=0.0.0.0
EIDOLON_WEB_PORT=26880
EIDOLON_API_HOST=127.0.0.1
EIDOLON_API_PORT=26881
EIDOLON_METRICS_PORT=26890
EIDOLON_RUNTIME_DEBUG_PORT_START=26900
EIDOLON_RUNTIME_DEBUG_PORT_END=26999
EIDOLON_DATABASE_URL=sqlite:///./data/eidolon.db
EIDOLON_WORKSPACE_ROOT=./data/workspaces
EIDOLON_RUNTIME_MODE=mock          # mock | auto
EIDOLON_MOCK_TASK_SECONDS=8
EIDOLON_LOG_LEVEL=INFO
EIDOLON_CORS_ORIGINS=http://localhost:26880
EIDOLON_COMPANY_NAME=Eidolon Studio
```

代码中禁止散落硬编码 host/端口/路径；前端只允许 `import.meta.env.VITE_*`。
所有 secret 走 `.env`（.gitignore），仓库只提交 `.env.example`。

## 10. 日志（core/logging.py）

统一格式：`%(asctime)s %(levelname)s [%(name)s] employee=%(employee_id)s project=%(project_id)s task=%(task_id)s %(message)s`，缺省字段输出 `-`。通过 logging filter/adapter 注入上下文；生产代码禁止 `print()`。

## 11. 启动与种子数据

启动时（main.py startup）：
1. `ensure_database_schema()`（`app/core/database.py`）：空库由 Alembic `upgrade head` 初始化；
   已有库若不在仓库 head、或缺 `alembic_version`，抛 `DatabaseSchemaError` 拒绝启动。
   schema 的所有者是 Alembic，启动路径上不再有 `create_all`（仅集成测试的临时库还用）。
2. 幂等 seed：默认 Company "Eidolon Studio" + 5 个部门（Executive/Product/Research/Engineering/QA）+ 5 名员工：
   Alice(CEO)、Morgan(PM)、Bob(Researcher)、Charlie(Engineer)、Dana(QA)，
   均 runtime_type=mock，各自独立 `workspace_path=data/workspaces/{slug}`、`memory_namespace=emp_{slug}`。
3. 启动 Orchestrator 事件循环。

## 12. 测试策略

Backend（pytest + httpx/TestClient，sqlite 内存库）：
- `test_employee_isolation`：A 的 memory/private knowledge 在 B 的所有 endpoint 下不可见（硬性要求）。
- `test_mock_runtime`：事件流以 completed 结束且产出 artifact。
- `test_task_lifecycle`：状态机合法/非法迁移。
- `test_project_workflow`：mock 模式下 order→completed 全闭环，产出 PRD/调研/代码/测试报告/release。
- `test_knowledge_promotion`：proposal→review→scope 变更。

Frontend（Vitest + Testing Library）：Office 卡片状态渲染、status badge、graph 数据转换 util。

CI（GitHub Actions）：backend（ruff check + ruff format --check + pytest）、frontend（tsc --noEmit + eslint + vitest + vite build）。

## 13. 端口

见 docs/ports.md：Web 26880（0.0.0.0，唯一对外入口，反代 `/api`、`/ws`）；API 26881（宿主机开发绑 127.0.0.1，docker 仅内部网络）；metrics 26890 预留；runtime debug 段 26900–26999 预留。

## 14. 明确不做（MVP）

3D Office、真实财务/工资、权限系统、多租户 SaaS、K8s、微服务、消息队列、Vector DB/RAG 框架、自研 LLM/Workflow 框架、packages/ 目录。~~真实 Hermes/OpenClaw adapter 实现~~（v0.2 已实现，见 §15）。

## 15. v0.2 — Persistent Workforce

v0.2 把 "One Employee = One Persistent Agent" 落地为持久容器化 Runtime，并引入 Provider 管理与更新子系统。本节是总览，细节见 **docs/runtime/**（overview / docker / hermes / openclaw / providers / updates / security / troubleshooting）。

### 15.1 新边界与新模型

- 边界升级为 **Employee ≠ Runtime ≠ Provider**：Provider（模型供应商 + 凭证）成为一级领域对象，与员工、执行引擎三足解耦。
- 新增表：
  - **providers**：kind（openai/anthropic/openrouter/custom...）、base_url、default_model、scope（company/employee）、**credential_ref**（引用 secret store，不存明文）。
  - **model_bindings**：Employee ↔ Provider 关联，预留 primary/fallback 角色（fallback 语义 v0.2 预留落库）。
  - **employee_brains**：员工大脑（SOUL.md / IDENTITY.md / AGENTS.md / MEMORY.md 等身份文件的登记与版本），落盘于 `data/employees/{id}/brain/`，与 Hermes profile / OpenClaw workspace 文件约定对齐。
  - **runtime_instances**：employee ↔ 持久 runtime 实例映射（容器名、镜像 tag、状态、last_error）。
  - **runtime_images**（+ per-instance RuntimeVersionState）：镜像登记、pin tag、channel、兼容性矩阵 `tested_min/max_version`、update_policy。
- **Artifact 文件化**：除 `artifacts.content` 内联文本外，产物可落盘到员工 workspace 并在表中以 `path` 引用（真实 runtime 的产物从数据卷回收）。
- **work_sessions 扩展**：新增 runtime 实例/run 引用（Hermes runId、OpenClaw runId/sessionKey）、取消原因等字段，支撑真实 runtime 的任务执行与取消。

### 15.2 Runtime 实例与能力

- **DockerRuntimeInstanceManager**：docker-py 编排；容器命名 `eidolon-{type}-{slug}-{6hex}`；专用网络 `eidolon-runtime-net`，**不 publish host port**，Backend 走 docker DNS；per-employee 目录 `data/employees/{id}/{workspace,brain,runtime/...}` 单容器独占；默认资源限制 CPU 2 / 内存 4GB；non-privileged，禁挂 `/` 与 docker.sock。
- 实例生命周期状态：`provisioning → stopped → starting → running → stopping → stopped`，`error` 保留现场；managed update 期间 `updating`，失败 `rolled_back`。
- **RuntimeCapabilities**：adapter 声明 + 运行时探测校正；不支持的能力如实返回 Unsupported，前端置灰，业务层调用前先查能力。

### 15.3 Secret Store

- **LocalEncryptedSecretStore**：Fernet 对称加密，密钥材料来自 **`EIDOLON_SECRET_KEY`**；Provider 表只存 `credential_ref`；密钥仅以容器 env 注入 runtime；API/日志/事件一律脱敏（`sk-••••abcd`）。
- **RuntimeProviderConfigurator**：把 Provider 映射到 runtime 原生配置——Hermes `config.yaml model:` 块 + `.env`（任意 OpenAI 兼容端点走 `provider: custom` + `base_url`），OpenClaw `agents.defaults.model` + 容器 env。

### 15.4 更新子系统

- Update policy：**`notify_only`（默认）** / `managed` / `automatic`（预留，约束 only_when_idle + backup + rollback）。
- Managed update 流程：idle check → backup → pull → stop → recreate → healthcheck → verify → 失败自动 rollback（OpenClaw restart-loop 先跑一次性 `doctor --fix` 容器）。
- 兼容性矩阵：超出 `tested_max_version` 标记 **Compatibility Unverified**，禁止自动升级。
- 检查频率：启动时 + 每 6h（**`EIDOLON_UPDATE_CHECK_INTERVAL=21600`**）。
- 事件：`runtime.update_available / update_started / update_progress / update_completed / update_failed / update_rolled_back`。

### 15.5 新增 API（/api/v1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST、GET/PATCH/DELETE | /providers、/providers/{id} | Provider CRUD（响应中凭证脱敏） |
| POST | /providers/{id}/test | Test Connection |
| GET | /providers/{id}/models | Discover Models |
| GET | /runtime-types | adapter 列表 + RuntimeCapabilities |
| GET | /runtimes | 各 employee 实例状态 |
| POST | /runtimes/{employee_id}/start、/stop、/restart | 生命周期控制 |
| GET | /runtimes/{employee_id}/logs | 容器日志 |
| GET/PATCH | /employees/{id}/runtime | runtime 详情 / 切换类型、绑定 provider |
| GET | /employees/{id}/brain | EmployeeBrain（身份文件） |
| GET | /runtime-images | 镜像登记与版本状态 |
| POST | /runtime-images/check-updates | 立即检查更新 |
| POST | /runtime-images/{type}/update | 触发 managed update |
