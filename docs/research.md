# Eidolon 技术调研报告（2026-09）

> 说明：Hermes/OpenClaw 部分主要来自官方文档站，可信度高；skywork 等社区文章的历史叙述（改名时间线、star 数）可能含渲染偏差，仅作背景参考。

---

## 1. Hermes Agent（NousResearch/hermes-agent）

这是真正叫 "Hermes" 的 **agent runtime**（区别于同名 LLM 模型系列 Hermes 1-4）。GitHub: `NousResearch/hermes-agent`，自我改进型自主 agent，内置学习循环（自动创建/改进 skills、记忆落盘）。

**核心域模型：**

- **Profile = 实例/员工**：一个 profile 就是一个独立 Hermes home（`~/.hermes/profiles/<name>`），各有独立 `config.yaml`、`.env`、`SOUL.md`、记忆、sessions、skills、cron、state.db。创建即得命令别名（`coder chat`、`coder gateway start`）。`hermes profile create/export/import/install`（可打包成 tar.gz 或 git 仓库分发，API keys 会被剥离）。**重要警告：同一 profile 不可被两个进程并发写入** —— profile 即隔离边界，但**不是沙箱**（文件系统权限仍是 OS 用户级，需配 `terminal.backend: docker/ssh/daytona/modal/singularity` 隔离）。
- **隔离机制**：`HERMES_HOME` 环境变量是所有状态的路径边界（代码里 119+ 处 `get_hermes_home()`）；工具执行目录由 `terminal.cwd` 控制；`terminal.home_mode: profile` 可给每个 profile 独立 `HOME`。
- **Bot Mode**（v0.20.3 起默认开启）：把 profile 变成花名册里的具名 Bot，各 Bot 有自己的角色/模型/记忆/skills/头像，bot 间可通过持久 Agent Inbox 互发消息（CLI handoff 实现），2-6 个 bot 组 group chat（最多 3 轮串行 deliberation），支持 Routines（cron 驱动的周期性任务）。
- **Sessions**：session DB（SQLite）+ `hermes sessions list`、`--resume/-r`、`--continue/-c`；`-q/--query-file` 非交互单发；`--worktree` 并行 git worktree 隔离。
- **编程驱动方式**：
  - CLI：`hermes chat -q "..."`、`-Q` quiet、`--query-file`、`hermes -p <profile> ...`（最稳的外部驱动接口）；
  - **`hermes serve`**：headless 后端 API server（v0.19.0 起），另有 `hermes dashboard`（backend + 浏览器 UI）；网关侧有 `gateway/platforms/api_server.py`（8.5k 行 HTTP API，含 `run_agent.py` 的 `AIAgent.run_conversation` —— 曾有 DoS CVE-2026-14626，说明这是真实暴露面）。
  - 官方 Docker 镜像用 s6-overlay 监督 per-profile gateway 服务。
- **技能系统**：兼容 agentskills.io 开放标准，Skills Hub 社区注册表；skill 可自我改进。
- 来源：[Docs 首页](https://hermes-agent.nousresearch.com/docs/)、[Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)、[Bot Mode](https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode)、[CLI 参考](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)、[Profile Builder 发布](https://www.marktechpost.com/2026/06/11/nous-research-ships-hermes-agent-profile-builder-identity-model-skills-and-mcp-servers-in-one-dashboard-flow/)、[api_server.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py)

**对 Eidolon 的意义**：Hermes profile 几乎是 "AI 员工" 的直接对应物——identity（SOUL.md）+ memory + workspace + skills + cron 都挂在 profile 上。适配方式：`hermes -p <employee> chat -q/--query-file` 发任务，或 `hermes serve` HTTP API。

## 2. OpenClaw（openclaw/openclaw）

开源个人 AI 助手/网关（前身 Clawdbot→Moltbot，作者 Peter Steinberger，2026-02 加入 OpenAI 后移交独立基金会）。架构是 **hub-and-spoke 网关**：

- **Gateway**：长驻进程（默认 `127.0.0.1:18789`），**WebSocket JSON RPC 是唯一控制平面**——CLI、Web UI、macOS app、移动节点、headless 节点全部连它。帧格式：`{type:"req"/"res"/"event"}`；npm 官方包 `@openclaw/gateway-protocol`（TypeBox schemas + `protocol.schema.json` 机器可读契约）和 `@openclaw/gateway-client`（Node 参考实现 + 浏览器版）。方法族：`status / channels / models / chat / agent / sessions / nodes / approvals` 等；带副作用的方法要求 idempotency key；鉴权用 challenge→token→hello-ok + scope（`MISSING_SCOPE` 结构化错误）。另有 **admin-http-rpc 插件**（默认关）把 RPC 暴露为 `POST /api/v1/admin/rpc`。
- **Config**：`~/.openclaw/openclaw.json`（JSON5），key 空间 `agents.* / multiAgent.* / session.* / channels.* / gateway.*`。
- **Agents（多 agent 原生支持）**：`agents.entries.<agentId>` 声明 fleet，`agents.ownership:"explicit"`；每个 agent 有独立 workspace（`<state-dir>/workspace-<agentId>`）、独立 SQLite（`~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`：会话行、transcript、auth profile、standing intents），还有 per-agent Codex runtime home。channel 通过 bindings 路由到 agent；session key 形如 `agent:<agentId>:main`。支持 `--profile <name>` 和 `--dev` 把整个安装状态隔离到 `~/.openclaw-<profile>/`（含端口偏移）。
- **Workspace（agent 的家 = 记忆）**：固定文件集 `AGENTS.md`（操作指令）、`SOUL.md`（人格）、`IDENTITY.md`（名字/emoji）、`USER.md`（用户模型，4k 字符预算）、`MEMORY.md`（精编长期记忆）、`memory/YYYY-MM-DD.md`（每日日志）、`skills/`（workspace 级 skill，最高优先级）；建议放私有 git 仓库备份。这与 Eidolon 的 "员工私有 workspace" 几乎一一对应。
- **Sessions**：`session.scope`（per-sender）、`dmScope`（main/per-peer/...）、`groupScope`、`identityLinks`（跨渠道身份归一）、reset 策略（daily/idle/compaction）、session 存储与维护（pruneAfter、maxDiskBytes）、thread bindings。
- **Channels**：WhatsApp/Telegram/Discord/Signal/Slack 等 20+ 适配器挂在外圈。
- **Skills**：`SKILL.md` + YAML frontmatter，优先级分层 workspace > project > personal > managed（`~/.openclaw/skills`）> bundled；官方注册表 **ClawHub**（`clawhub search/install/pin`）。
- 来源：[Gateway protocol](https://docs.openclaw.ai/gateway/protocol)、[Config agents](https://docs.openclaw.ai/gateway/config-agents)、[Agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)、[External apps 集成](https://docs.openclaw.ai/gateway/external-apps)、[admin-http-rpc](https://docs.openclaw.ai/plugins/admin-http-rpc)、[openclaw/clawhub](https://github.com/openclaw/clawhub)、[架构分析](https://www.teacherandtask.com/blog/how-peter-steinberger-built-openclaw-viral-ai-agent)

**对 Eidolon 的意义**：OpenClaw 的 `agent` RPC + `agent.wait` + `chat.send(sessionKey)` + `chat.delta` 事件流就是 Agent Runtime Adapter 的最佳参照；`agents.entries` + per-agent workspace/sqlite 是多员工隔离的现成模型。

## 3. 可嵌入 agent runtime 对比

| Runtime | 无头/编程驱动 | Session 模型 | 隔离 |
|---|---|---|---|
| **OpenAI Codex** | `codex exec`（headless，`--json` JSONL 事件流；社区已提 `codex exec fork`）；**官方 SDK `@openai/codex-sdk`（npm，当前 0.152.0，Node 18+）**：spawn CLI、stdin/stdout JSONL 事件，`startThread/resumeThread(threadId)/run/runStreamed`，structured output | Thread（rollout JSONL），可 resume；fork/backtrack API 尚在请求阶段 | `cwd` + sandbox 审批模式；worktree API 在 issue 阶段 |
| **Claude Code / Agent SDK** | `claude -p`（`--output-format json/stream-json`）；**Agent SDK：`@anthropic-ai/claude-agent-sdk`（npm 0.3.x）/ `claude-agent-sdk`（pip）**：`query(prompt, options)` 异步迭代器产出类型化消息（AssistantMessage/ResultMessage...），支持 `can_use_tool` 回调、编程 hooks、`agents` 子代理定义 | session 文件 JSONL，可 resume/fork；SDK/headless 会话与 CLI 同格式 | per-process cwd；权限回调控制 |
| **OpenCode** | `opencode run [msg]`（`--format json`、`--session/--continue/--fork`）；**`opencode serve --port`**：headless HTTP server，OpenAPI spec 在 `/doc`，SSE 事件流；`@opencode-ai/sdk`（npm 1.18.x）TS client | `POST /session` 建独立 session；plan/build 双 agent + subagents | 进程+目录级；provider 无关（75+ 厂商） |
| **Aider** | `aider --message/--message-file + --yes-always`（纯文本输出、无 PTY）；`--auto-test --test-cmd`；官方有 [Scripting aider](https://aider.chat/docs/scripting.html)（CLI + Python API） | `.aider.chat.history.md`，repo-map 为中心；无多 session 概念 | git 原生（自动 commit），无 sandbox |

来源：[Codex SDK](https://github.com/openai/codex/tree/main/sdk/typescript)、[codex-sdk 版本](https://registry.npmjs.org/@openai/codex-sdk/latest)、[Claude Agent SDK 对比](https://github.com/hezi2020/dsh-plugin-wiki/blob/main/wiki/docs/comparison/claude-code.md)、[headless 对比表](https://infragap.com/headless-agents-ci/)、[OpenCode CLI](https://opencode.ai/docs/cli/)、[galpt/you 的 OpenCode 编排实例](https://github.com/galpt/you)

## 4. 多智能体 / 虚拟公司先行研究

| 框架 | 域模型 | 编排 | 隔离 | 状态 |
|---|---|---|---|---|
| **ChatDev** | 公司=CEO/CTO/程序员/测试角色，phase 链（设计→编码→测试→文档），chat chain 驱动 | 固定 phase 流水线 | 无（共享会话） | 学术项目 |
| **MetaGPT** | 软件公司隐喻：PM 写 PRD→Architect→Engineer→QA，角色 = 提示词 + SOP 编码，message pool 订阅制 | SOP 流水线 | 无 | MIT，活跃 |
| **AutoGen** | GroupChat 对话即工作流；Core 层 actor 模型 | LLM/规则选下一个发言者 | 无 | **2026-02 起维护模式**，后继为 Microsoft Agent Framework（1.0 已 GA，合并 Semantic Kernel），社区分叉 AG2 |
| **CrewAI** | Agent(role/goal/backstory) + Task + Crew + Process（sequential/hierarchical） | 角色流水线，工具委托 | 无 | 活跃，原型友好 |
| **OpenHands** | Agent + Conversation 生命周期状态机（CREATE→STARTING→PREPARING_SANDBOX→READY→RUNNING→PAUSED/STOPPED），**sandbox 状态与 execution 状态分离** | Software Agent SDK + agent-server，事件流 | **Docker/Apptainer rootless sandbox per conversation** | 活跃，生产向 |
| **GPT-Pilot** | Agent 角色流水线：Architect→Tech Lead→Developer→Code Monkey/Debugger，Task 分解为 Steps | 严格分步，可人工接管 | 无 | 已转为 Pythagora 商业产品 |
| **Agency Swarm** | Agency = Agent 集合 + **communicationFlows 有向图**（谁能跟谁说话），基于 OpenAI Agents SDK | 显式通信图 | threads 级 | 活跃 |

来源：[OpenHands 架构剖析](https://zhanghao.work/en/articles/openhands-system-architecture/)、[OpenHands SDK docs](https://docs.openhands.dev/sdk/arch/overview)、[Agency Swarm](https://github.com/VRSEN/agency-swarm)、[AutoGen→MAF 迁移](https://kanerika.com/blogs/crewai-vs-autogen-microsoft-agent-framework/)、[框架对比 2026](https://nomadx.ae/ai-agent-framework-comparison-2026/)

**借 / 避**：
- **借**：Agency Swarm 的显式通信图（员工间的可见性/汇报关系）；OpenHands 的 conversation 生命周期状态机 + sandbox/execution 状态分离（Eidolon 的任务执行状态应照抄）；MetaGPT 的 SOP/产物（artifact）驱动角色交接；Hermes/OpenClaw 的 workspace-文件即记忆。
- **避**：AutoGen GroupChat 式自由对话（成本不可控、终止难）；ChatDev 式硬编码 phase 链（不灵活）；CrewAI 式无隔离共享上下文（没有真正的员工私有状态）。

## 5. 记忆与技能设计

学界已固化的分层（见 [CoALA](https://arxiv.org/html/2608.13574v1) 及多篇 survey）：

- **情景记忆（episodic）**：Reflexion 的 verbal self-reflection 存入 episodic buffer，避免重复犯错；Generative Agents 的 memory stream + 周期反思合成。
- **语义记忆（semantic）**：MemGPT 的 OS 式虚拟内存层级（main context + 外部存储 + 自主读写）；Mem0 类事实抽取。
- **程序记忆（procedural）= 技能库**：**Voyager** —— 经验证的代码片段技能库，按 embedding 相似度索引，是 "技能库 + 成功度量" 的鼻祖；**ExpeL** 用 ADD/EDIT/UPVOTE/DOWNVOTE 操作扁平向量库中的自然语言经验；近期工作（Trace2Skill、SkillMaster）把 trajectory 压缩成可迁移 skill。
- **落地形态已被工业界收敛**：Hermes 的 skills（自我改进 + FTS5 跨会话检索 + LLM 摘要）和 OpenClaw 的 `SKILL.md` + 分层优先级（workspace > personal > managed > bundled）就是 episodic（daily memory 日志）+ semantic（MEMORY.md 精编）+ procedural（skills/）三层最务实的实现。
- **私有 vs 共享**：Hermes 明确警告多进程共享一个记忆目录会互相污染（应走外部 memory provider，如 Honcho）；OpenClaw 把每 agent 的 sqlite/workspace 隔离、共享状态放 `<state-dir>/state/openclaw.sqlite`。Eidolon 应照此：**每员工私有记忆目录 + 显式共享知识库（org 级只读/受控写）**。
- 反思循环：任务结束 → 生成 reflection（成功/失败原因）→ 命中阈值则提炼为 skill（带成功率/使用计数指标，Voyager/ExpeL 式）→ 下次检索注入。

## 6. 前端库现状（npm registry 实查，2026-09-01）

| 库 | 当前包名 / 版本 | 备注 |
|---|---|---|
| React Flow | **`@xyflow/react` 12.11.5**（旧包 `reactflow` 已弃用） | 组织图/流程图首选 |
| TanStack Query | **`@tanstack/react-query` 5.102.8**（v5） | |
| Zustand | **`zustand` 5.0.15**（v5） | |
| OpenAI Codex SDK | `@openai/codex-sdk` 0.152.0 | |
| Claude Agent SDK | `@anthropic-ai/claude-agent-sdk` 0.3.252 | |
| OpenCode SDK | `@opencode-ai/sdk` 1.18.25 | |

**shadcn/ui + Tailwind**：当前主版本组合是 **Tailwind CSS v4（4.1.x）+ shadcn/ui**，官方脚手架为 **`npx shadcn@latest init` / shadcn/create preset 生成 Vite 项目**，Tailwind v4 走 `@tailwindcss/vite` 插件 + CSS-first 配置（`@import "tailwindcss"`），不再用 `tailwind.config.js`；注意 2025 年初 v4 切换期文档有过兼容坑，现已稳定。来源：[shadcn/ui Vite 安装](https://ui.shadcn.com/docs/installation/vite)、[Tailwind v4 指南](https://ui.shadcn.com/docs/tailwind-v4)。

## 7. "Agent Runtime Adapter" 抽象设计建议

综合各家接口收敛出的最小抽象：

```python
class AgentRuntimeAdapter(Protocol):
    # 实例生命周期（≈ Hermes profile / OpenClaw agents.entries）
    async def create_instance(self, spec: AgentSpec) -> InstanceHandle   # identity, model, skills, workspace, memory 路径
    async def destroy_instance(self, h) -> None
    # 会话/任务
    async def create_session(self, h, cwd: Path | None) -> SessionHandle  # ≈ Codex thread / OpenCode POST /session
    async def send_task(self, s, task: Task, *, idempotency_key: str) -> RunHandle
    async def stream_events(self, r) -> AsyncIterator[Event]  # message/tool_call/artifact/approval_request/status
    async def cancel(self, r) -> None
    async def wait(self, r) -> RunResult   # ≈ OpenClaw agent.wait
    # 产物与记忆
    async def list_artifacts(self, s) -> list[Artifact]
    async def get_state(self, h) -> AgentState  # 用于快照/迁移（≈ hermes profile export）
```

关键设计点（从调研直接借来）：

- **Idempotency key 必备**（OpenClaw 对副作用 RPC 强制要求）。
- **事件流统一为 req/res/event 三帧语义**（OpenClaw WS 协议），不要发明新事件模型。
- **Approval 是一等公民事件**（Claude SDK 的 `can_use_tool` 回调 / OpenClaw `approvals.*`）——虚拟公司里 "员工请示" 就是 approval。
- **实例级状态边界 = 目录 + 环境变量**（Hermes `HERMES_HOME`、OpenClaw `OPENCLAW_STATE_DIR/WORKSPACE_DIR`），MVP 用目录隔离即可，沙箱留给后续（backend: docker）。
- **会话与运行时状态分离**（OpenHands 的 sandbox 状态 vs execution 状态教训）。
- 配置即身份：`SOUL.md + IDENTITY.md + AGENTS.md` 文件约定跨 Hermes/OpenClaw 通用，Eidolon 的员工定义可直接采用这套文件 schema，向后兼容生态。

## 8. Python FastAPI + React MVP 推荐选型

- **Runtime 适配首批**：① **Claude Agent SDK**（pip 包，async 迭代器，事件类型化，最适合 FastAPI 进程内集成）；② **OpenCode**（`opencode serve` HTTP+SSE+OpenAPI，多 provider，远程/本地均可）；③ Codex SDK 走 JSONL 子进程包装。Hermes/OpenClaw 作为二期适配器（都天然支持多 profile/agent，值得优先做，因 profile 概念与 "员工" 完美对齐）。
- **后端**：FastAPI + WebSocket（SSE 亦可）把 adapter 的 `stream_events` 直接透传给前端；SQLite（每员工一库，照 OpenClaw 模型）或 Postgres；任务取消用 asyncio task + adapter.cancel。
- **前端**：Vite + React + TypeScript；**@xyflow/react 12.x** 画组织架构/汇报图；shadcn/ui + Tailwind v4（`@tailwindcss/vite`）；TanStack Query v5 管资源状态，Zustand v5 管 WebSocket 事件流/实时会话状态。
- **避免**：MVP 期不要上 AutoGen（维护模式）/CrewAI 式进程内编排框架——Eidolon 的编排应是自有域模型（Company/Employee/Project/Task/Artifact）+ 上述 adapter，而非把框架当底座。

**主要遗留不确定项**：① OpenClaw 早期历史细节（改名时间线、star 数）来自社区文章，未与官方交叉验证；② Hermes `hermes serve` API 的具体 endpoint 未逐一核对（8.5k 行 api_server.py 需读源码确认 surface）；③ shadcn/create preset 脚手架属 2026 新功能，建议 MVP 时仍以 `npm create vite` + `npx shadcn@latest init` 为准。

---

# Eidolon v0.2 — Technology Verification Report

**Research date:** 2026-09-01 · All facts below were re-verified against official docs/repos today. Prior-round claims are marked ✅ verified / ⚠️ corrected / ❓ unverifiable.

---

## Hermes Agent (NousResearch/hermes-agent)

**Current version observed:** `v2026.8.31` (released 2026-08-31, [GitHub releases](https://api.github.com/repos/NousResearch/hermes-agent/releases)). Rapid release cadence (~weekly).

### 1. Official Docker deployment ✅ (prior round confirmed, now precise)
- **Image (documented):** `nousresearch/hermes-agent:latest` (Docker Hub). CI also pushes to `ghcr.io/nousresearch/hermes-agent` (seen in [CI workflow](https://github.com/NousResearch/hermes-agent/actions/runs/27311124279/workflow?pr=43808) as buildcache refs — GHCR availability of the release image not explicitly documented; treat Docker Hub as the canonical reference).
- Base: `debian:13.4`, Python 3.13 (uv-managed venv), Node 26, Playwright/Chromium, ripgrep, ffmpeg, git, docker-cli, openssh-client.
- **s6-overlay v3 is PID 1** (replaced tini) ✅ — supervises per-profile gateways + dashboard with auto-restart; `/opt/hermes` install tree is **read-only** to the runtime `hermes` user (UID 10000).
- Run modes: `docker run ... nousresearch/hermes-agent setup` (wizard), `... gateway run` (persistent gateway), bare image = interactive chat.
- Source: [Docker Backend doc](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
- **Implementation decision:** pin `nousresearch/hermes-agent:<version>` per employee container; keep `/init` entrypoint chain (s6) intact.

### 2. Headless API ✅ (8642 verified)
- The API is the **OpenAI-compatible API server**, part of the gateway (`hermes gateway run`); `hermes serve` exists (docs mention `hermes serve --isolated`) but the documented container path is gateway mode.
- **Port 8642** (default `API_SERVER_PORT`), default bind `127.0.0.1` — set `API_SERVER_HOST=0.0.0.0` in container.
- **Auth:** `API_SERVER_ENABLED=true` + **required** `API_SERVER_KEY` (min 8 chars) bearer token, even on loopback; `API_SERVER_CORS_ORIGINS` for browsers. Dashboard (separate, port **9119**, `HERMES_DASHBOARD=1`) requires an auth provider on non-loopback binds — basic auth / Nous OAuth / OIDC; `HERMES_DASHBOARD_INSECURE` is now a no-op (June 2026 hardening after a real-world attack campaign).
- Key endpoints (all bearer-gated): `POST /v1/chat/completions`, `POST /v1/responses` (server-side state via `previous_response_id` or `conversation` param), **Runs API**: `POST /v1/runs` (supports `Idempotency-Key` header → 202 + `Idempotency-Replayed: true` on retry), `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (**SSE**: token deltas, tool progress, `subagent.start/complete`), `POST /v1/runs/{id}/stop`, `POST /v1/runs/{id}/approval`; **Sessions API** under `/api/sessions/*` (CRUD, fork, `chat`, `chat/stream` SSE with `assistant.delta`/`tool.started`/`tool.completed`/`run.completed`); **Jobs (cron) API** under `/api/jobs/*`; discovery: `GET /v1/capabilities`, `GET /v1/skills`, `GET /v1/toolsets`, `GET /api/model/options`; health: `GET /health` (public liveness), `GET /health/detailed` (authenticated readiness). Concurrency cap `gateway.api_server.max_concurrent_runs` (default 10, 429 on overflow).
- Artifacts/files: **no file upload** via API (400 `unsupported_content_type`); inline images only. No documented artifacts-download endpoint.
- Source: [API Server doc](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- **Implementation decision:** Eidolon drives each employee via `POST /v1/runs` + SSE `/v1/runs/{id}/events` with `Idempotency-Key`; per-request `model`/`provider` overrides honored on `/v1/runs`.

### 3. Provider configuration ✅
- Secrets in **`~/.hermes/.env`** (e.g. `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); non-secret settings in **`config.yaml`**. `hermes config set` auto-routes (keys → `.env`, rest → `config.yaml`). Container `-e` flags override `.env`.
- Main model: `model:` block in config.yaml — `provider` / `model` / `base_url` / `api_key`; `provider: custom` + `base_url` (no trailing slash) + `api_key: "none"` covers **any OpenAI-compatible endpoint** (vLLM, Ollama, LM Studio). CLI: `hermes config set model anthropic/claude-opus-4`, `hermes config set model.default ...`, `--model` per-invocation flag.
- Native providers (from [Providers doc](https://hermes-agent.nousresearch.com/docs/integrations/providers), fallback list): `nous` (Nous Portal), `openrouter`, `anthropic`, `openai-codex`, `copilot`, `gemini`, `deepseek`, `xai`, `ollama-cloud`, `lmstudio`, `bedrock`, `azure-foundry`, `zai`, `kimi-coding`, `minimax`, `alibaba`, `novita`, `nvidia`, `huggingface`, `custom`, and ~20 more. Fallback chains via top-level `fallback_providers:`; auxiliary tasks (vision/compression/titles) via `auxiliary.<task>.{provider,model,base_url,api_key}`.
- **Implementation decision:** write `config.yaml` (`model:` block) + `.env` (API keys) into the employee volume at provisioning; prefer `provider: custom` + `base_url` for anything non-native.

### 4. Persistent data ✅
- **Single mount: `/opt/data`** in the container = host `~/.hermes`. Contents: `.env`, `config.yaml`, `auth.json` (OAuth), `SOUL.md`, `memories/`, `sessions/`, `skills/`, `home/` (tool-subprocess HOME: git/ssh/gh/npm creds), `cron/`, `hooks/`, `logs/` (incl. `logs/gateways/<profile>/current` rotated s6 logs, `logs/container-boot.log`), `skins/`, `state-snapshots/`, `backups/`, `state.db` (SQLite, WAL default).
- Profiles live at `/opt/data/profiles/<name>/` (or `~/.hermes/profiles/<name>` on host); **`HERMES_HOME` env var is the entire state boundary**. For one-employee-per-container, Eidolon can mount the volume directly as the *default* profile root (`/opt/data`) — no profiles/ nesting needed.
- **Hard rule (official): never two gateway processes/containers on the same data dir** — session/memory stores corrupt under concurrent writes.
- UID: container drops to `hermes` UID 10000; `PUID`/`PGID` (or `HERMES_UID`/`HERMES_GID`) remap for bind-mount ownership.
- Source: [Docker doc](https://hermes-agent.nousresearch.com/docs/user-guide/docker), [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration), [Profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- **Implementation decision:** one named volume per employee → `/opt/data`; set `PUID/PGID` to a fixed UID; enforce single-container-per-volume in Eidolon's scheduler.

### 5. Version / update ✅
- Version: `hermes version` subcommand (docs: `docker run -it --rm nousresearch/hermes-agent:latest version`).
- **Official container update = pull + recreate** (`docker pull` → `docker rm -f` → `docker run`, or `docker compose pull && up -d`). On boot, container runs non-interactive config-schema migrations with timestamped backups (skip via `HERMES_SKIP_CONFIG_MIGRATION=1`). Host installs use `hermes update` (with `updates.pre_update_backup`: quick|full|off) — not for containers.
- **Implementation decision:** Eidolon upgrade = pull pinned tag → recreate container with same volume; run pre-upgrade volume backup (Hermes' in-container backup only covers config, not sessions).

---

## OpenClaw (openclaw/openclaw)

**Current version observed:** stable `v2026.8.1` (published 2026-08-31), beta `v2026.9.1-beta.1` ([GitHub releases](https://api.github.com/repos/openclaw/openclaw/releases)). Release train is date-based `YYYY.M.P`.

### 6. Official Docker deployment ✅
- **Images:** `ghcr.io/openclaw/openclaw:latest` (primary) and Docker Hub mirror `openclaw/openclaw:latest`. Tags: exact releases (`2026.2.26`), prereleases, moving `latest`/`main`/`extended-stable`, variants `-slim` and `-browser` (Chromium baked). Moving tags rebuilt weekly; dated immutable tags like `2026.8.1-r20260820`. Base: `node:24-bookworm-slim`, `tini` PID 1, non-root `node` (uid 1000), built-in HEALTHCHECK.
- Run via `./scripts/docker/setup.sh` (builds `openclaw:local` or pulls `OPENCLAW_IMAGE`), or manual compose. **Headless bootstrap** documented: `docker compose run -T --rm --no-deps --entrypoint node openclaw-gateway dist/index.js onboard --non-interactive --accept-risk --skip-health --mode local --auth-choice openai-api-key --secret-input-mode ref --gateway-auth token --gateway-token-ref-env OPENCLAW_GATEWAY_TOKEN --skip-channels --no-install-daemon`, then `docker compose up -d openclaw-gateway`.
- **Port 18789 verified** (`OPENCLAW_GATEWAY_PORT` host-side; container-internal always 18789). Control UI at `http://127.0.0.1:18789/`. Unauthenticated probes: `GET /healthz` (liveness, used by image HEALTHCHECK), `GET /startupz`, `GET /readyz` (channel-aware deep readiness).
- **Auth:** gateway token (`OPENCLAW_GATEWAY_TOKEN` in `.env`, generated by onboarding) or password mode; `gateway.auth.mode` = `token` | `password` | `trusted-proxy` | `none`.
- Source: [Docker install doc](https://docs.openclaw.ai/install/docker)
- **Implementation decision:** use `ghcr.io/openclaw/openclaw:<exact-version>` pinned; bootstrap with the documented non-interactive onboard flow.

### 7. Single agent per container + RPC driving ✅
- WS protocol (docs: [Gateway protocol](https://docs.openclaw.ai/gateway/protocol), verified against current page): frames `{type:"req"/"res"/"event"}`, **protocol version 4**. Handshake: server sends pre-connect `connect.challenge` event `{nonce, ts}` → client sends first frame `connect` req with `params.auth.token` (shared secret) or `auth.password`, plus optional `device` identity (sign challenge: `signedAt = challenge.ts`, echo `nonce`; payload v3 preferred via `buildDeviceAuthPayloadV3`) → server replies **hello-ok** (`{type:"hello-ok", protocol:4, server, features, snapshot, auth:{role, scopes, deviceToken?}, policy:{maxPayload: 26214400, maxBufferedBytes, tickIntervalMs:15000, attachments}}`). Device pairing (`device.pair.*`) issues reusable `deviceToken`s scoped per device+role. **`client.mode:"backend"` may omit device identity on direct loopback connections** — relevant for Eidolon connecting over a private docker network (❓ docs say "direct loopback"; whether it applies to a docker-bridge peer is unverified — safest is token auth + backend mode).
- npm packages: `@openclaw/gateway-protocol` (TypeBox schemas + `protocol.schema.json`), `@openclaw/gateway-client` (reference client). Both at verified stable `2026.8.1`.
- Method surface: `status / channels / models / chat / agent / sessions / nodes / approvals / device / cron / tasks / artifacts / config` etc. **Side-effecting methods require idempotency keys**; missing scope → `FORBIDDEN` + `{code:"MISSING_SCOPE", missingScope, requiredScopes}`.
- Verified method details: `chat.send` (params: `sessionKey` — format `agent:<agentId>:main`, message, `queueMode: steer|followup|collect|interrupt`, `fastMode`, `expectedLeafEntryId` CAS), `chat.history` (supports `deltaCursor` catch-up), `chat.abort`, `chat.inject`, `sessions.create` (optional `model`, `thinkingLevel`, `worktree:true`), `sessions.abort`, `sessions.list`, **`agent.wait`** (waits for run to finish, returns terminal snapshot), `artifacts.list/get/download` (by `sessionKey`/`runId`/`taskId`). Event frames: `session.message`, `session.operation`, `session.tool`, `sessions.changed`, `device.pair.*` lifecycle events, etc. (⚠️ prior round's `chat.delta` event name not confirmed on the current page — live streaming is `session.message` events; `deltaCursor` is a history catch-up mechanism. `agent.run` was also not confirmed as a method name; `chat.send` starts the run and returns `runId`.)
- **admin-http-rpc plugin** ([doc](https://docs.openclaw.ai/plugins/admin-http-rpc)): bundled, off by default; `plugins.entries["admin-http-rpc"].enabled: true` → `POST /api/v1/admin/rpc` with `Authorization: Bearer <gateway-token>`, body `{id?, method, params}`. Allowlisted: `health`, `status`, `config.get/set/patch/apply`, `models.list`, `agents.list/create/update/delete`, `cron.*`, `tasks.*`, `device.pair.*`, `update.status`… **chat/agent run methods are NOT in the allowlist** — WS RPC remains the only way to drive conversations.
- Minimal single-agent config: a sole agent needs **no** `agents.entries` marker (sole-agent configs need no `ownership:"explicit"`); onboarding names the first agent (`main` default, or `openclaw onboard --non-interactive --agent-name <name>`). A sole named agent uses the default workspace + shared auth store. Per-agent entry shape: `agents.entries.<id> = { name, workspace, agentDir, model, identity: {name, theme, emoji, avatar}, skills, tools, sandbox, ... }`.
- **Implementation decision:** Eidolon connects via WS JSON-RPC with shared-secret token (`connect.params.auth.token`), `client.mode:"backend"`; one agent per container, default agent id; drive with `chat.send(sessionKey="agent:<id>:main")` → subscribe `session.message` events → `agent.wait(runId)`; enable admin-http-rpc only on the private container network for config/health convenience.

### 8. Provider/model configuration ✅
- In `openclaw.json` (JSON5): global default `agents.defaults.model` = `"provider/model"` string or `{primary, fallbacks: [...]}`; catalog/aliases `agents.defaults.models["provider/model"] = {alias, params}`; per-agent override `agents.entries.<id>.model`. Wildcard entries `"openai/*": {}` expose all discovered models. Provider keys: `models.providers.<provider>` (incl. `params`, `agentRuntime`, per-model `contextTokens`/`contextWindow`).
- Credentials: env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `FAL_KEY`, …) in Compose `.env`, or OAuth auth profiles stored in per-agent SQLite; headless bootstrap passes `--auth-choice openai-api-key --secret-input-mode ref`. Local providers in containers must use `host.docker.internal` (Ollama `http://host.docker.internal:11434`, LM Studio `:1234`); compose maps it via `--add-host=host.docker.internal:host-gateway`.
- Source: [Config agents doc](https://docs.openclaw.ai/gateway/config-agents), [Docker doc](https://docs.openclaw.ai/install/docker)
- **Implementation decision:** set `agents.defaults.model` (sole agent, no entries needed) + provider key in container env via Eidolon's secret store.

### 9. Persistent state ✅
- Compose bind-mounts: `OPENCLAW_CONFIG_DIR` → `/home/node/.openclaw` (holds `openclaw.json`, `state/openclaw.sqlite` shared state+provider auth, `agents/<agentId>/agent/openclaw-agent.sqlite` per-agent sessions/auth, `.env` with `OPENCLAW_GATEWAY_TOKEN`, installed plugin roots), `OPENCLAW_WORKSPACE_DIR` → `/home/node/.openclaw/workspace` (agent workspace: `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, `memory/YYYY-MM-DD.md`, `skills/`), `OPENCLAW_AUTH_PROFILE_SECRET_DIR` → `/home/node/.config/openclaw` (auth-profile encryption key — **keep separate from config dir**). Optionally persist all of `/home/node` via `OPENCLAW_HOME_VOLUME` (needed if Claude CLI backend is installed in-container).
- Growth hotspots: `media/`, per-agent SQLite, session transcripts, shared SQLite, `/tmp/openclaw/` rolling logs.
- Bind mounts must be owned by **uid 1000** (`sudo chown -R 1000:1000`).
- **Implementation decision:** per-employee named volumes for `/home/node/.openclaw` + `/home/node/.config/openclaw` (+ `/home/node` if using CLI-backed runtimes); chown 1000:1000 at provisioning.

### 10. Version / update ✅
- Version: `openclaw --version`; health: `openclaw health`, `openclaw gateway status --deep --json`, `openclaw doctor --lint --json`.
- Channels: `stable` / `extended-stable` / `beta` / `dev`; host installs use `openclaw update [--channel] [--tag]`.
- **Container update = replace image, keep mounted state.** New gateway runs startup-safe migrations before readiness; if it can't repair safely it **exits** (restart-loop symptom) — remedy: run the same image once with `openclaw doctor --fix` as container command against the same mounts, then restart normally. Verify with `openclaw doctor --json`. Plain version tags and `-rYYYYMMDD` dated tags are immutable; pin them for deployments that must not follow moving tags.
- Source: [Updating doc](https://docs.openclaw.ai/install/updating), [Docker doc](https://docs.openclaw.ai/install/docker)
- **Implementation decision:** Eidolon upgrade flow = pull pinned tag → recreate → poll `/startupz`; on persistent failure run one-shot `openclaw doctor --fix` container, then retry.

---

## General

### 11. Docker SDK for Python ✅
- Package `docker` (docker-py), **current version 7.2.0** (PyPI, fetched 2026-09-01), `requires_python >=3.8`. Docs: https://docker-py.readthedocs.io. Repo: https://github.com/docker/docker-py.
- Streaming logs: `container.logs(stream=True, follow=True)` generator (as shown in official PyPI usage). Healthchecks: declare `healthcheck` in container config (docker-py `docker.types.Healthcheck`) and/or poll `container.attrs["State"]["Health"]["Status"]`; exec-based checks via `container.exec_run(cmd)` inspecting `exit_code` — but prefer the image's built-in HTTP probes (Hermes `/health`, OpenClaw `/healthz`) over exec healthchecks since both images already ship HEALTHCHECK definitions.
- **Implementation decision:** pin `docker~=7.2`; use `containers.run(..., detach=True, healthcheck=...)` + `logs(stream=True)`; use `exec_run` only for provisioning (e.g. `hermes profile create`, `onboard`).

### 12. Secrets guidance ✅
- **Hermes:** secrets in `.env` under the profile home; container `-e` env vars override `.env` and are the documented path for "CI/CD or secrets-manager integrations where you don't want keys on disk" ([Docker doc](https://hermes-agent.nousresearch.com/docs/user-guide/docker)). Dedicated [Secrets doc](https://hermes-agent.nousresearch.com/docs/user-guide/secrets): external secret sources (Bitwarden `secrets.bitwarden`, 1Password `op://`, command helper) resolved at process startup; precedence ladder `.env`/shell > mapped sources > bulk sources; `secrets.preserve_existing` + per-profile vault aliasing (`FOO_<PROFILE>` → `FOO`). `config.yaml` is for non-secrets only; `hermes config set` routes keys to `.env` automatically.
- **OpenClaw:** `OPENCLAW_GATEWAY_TOKEN` and provider keys in Compose `.env`; channel creds can stay env-backed (`--use-env` — "leaves credential lookup to the environment without copying the token into `openclaw.json`"); auth-profile OAuth material encrypted with a key in the separate auth-profile secret dir; `config get` redacts sensitive values. Both projects' docs treat `.env`/env-vars as the secret channel and the config file as non-secret.
- File permissions: Hermes container stage2 chowns the volume and drops to UID 10000; OpenClaw requires uid 1000 ownership of bind mounts (EACCES otherwise). Neither documents a specific chmod requirement beyond that.
- **Implementation decision:** Eidolon injects API keys as container env vars (Docker SDK `environment=`) at create time, never writes them into git-tracked config; keep gateway tokens per-employee, generated with `openssl rand -hex 32`.

---

## Unverified / caveats (explicit)

1. **Hermes GHCR release image** — CI pushes buildcache to `ghcr.io/nousresearch/hermes-agent`, but only the Docker Hub name `nousresearch/hermes-agent` is documented for users. Use Docker Hub.
2. **OpenClaw `chat.delta` / `agent.run`** (prior round) — not confirmed on the current protocol page; the current surface is `chat.send` (returns runId) + `session.message`/`session.operation`/`session.tool` events + `agent.wait`. Re-derive from `protocol.schema.json` before coding the adapter.
3. **OpenClaw `client.mode:"backend"` device-identity exemption** is documented for "direct loopback connections" — whether a Docker bridge network peer qualifies is unstated; plan on full token auth regardless.
4. **`hermes serve`** — referenced in docs (`hermes serve --isolated`) but its standalone CLI surface wasn't fully enumerated this round; the gateway's API server (port 8642) is the documented, supported headless HTTP surface and supersedes it for Eidolon's purposes.
5. The prior round's "Hermes default 8642" claim is **confirmed**; "s6-overlay supervising per-profile gateway services" is **confirmed and current**; OpenClaw "127.0.0.1:18789 WS JSON RPC + challenge→token→hello + agents.entries + per-agent workspace/sqlite" is **confirmed**, with the auth detail corrected: shared-secret token auth lives in `connect.params.auth.token`, and the challenge nonce is for *device* signing, not token exchange.